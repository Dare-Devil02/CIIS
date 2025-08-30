# path_router.py
import math
import networkx as nx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

router = APIRouter()
G = None  # will be set with init_graph(graph) at startup

def init_graph(graph: nx.Graph):
    """
    Call this during FastAPI startup with your pre-built NetworkX graph `G`.
    Example:
        from path_router import init_graph, router as path_router
        init_graph(G)
        app.include_router(path_router)
    """
    global G
    G = graph

class PathNode(BaseModel):
    id: str
    attrs: Optional[Dict[str, Any]] = None

class PathEdge(BaseModel):
    id: str
    source: str
    target: str
    attrs: Optional[Dict[str, Any]] = None
    cost: float

@router.get("/path", response_model=Dict[str, Any])
def shortest_path(
    src: str,
    tgt: str,
    alpha: float = Query(0.7, ge=0.0, le=1.0, description="weight for anomaly_score component"),
    beta: float = Query(0.3, ge=0.0, le=1.0, description="weight for amount component"),
    directed: bool = Query(True, description="Use directed graph (True) or undirected (False)")
):
    """
    Compute a prioritized path from `src` to `tgt`.

    Cost function used on each edge:
        cost = alpha * (1 - anomaly_score) + beta * (1 / log10(amount + 10)) + epsilon

    - anomaly_score: prefer edges with HIGH anomaly_score (lowers cost)
    - amount: prefer larger amounts (lower cost via log)
    - alpha, beta are tunable (sum does not have to equal 1)
    """

    if G is None:
        raise HTTPException(status_code=500, detail="Server graph not initialized. Call init_graph(G) at startup.")

    # Choose working graph (directed or undirected)
    graph = G if directed else (G.to_undirected() if not isinstance(G, nx.MultiGraph) else nx.MultiGraph(G).to_undirected())

    # verify nodes exist
    if not graph.has_node(src):
        raise HTTPException(status_code=404, detail=f"Source node '{src}' not found in graph")
    if not graph.has_node(tgt):
        raise HTTPException(status_code=404, detail=f"Target node '{tgt}' not found in graph")

    # weight function used by networkx shortest_path
    def edge_cost(u, v, edge_attrs):
        """
        edge_attrs: dict of attributes for a single edge.
        This function is robust to missing 'amount'/'anomaly_score'.
        """
        anomaly = float(edge_attrs.get("anomaly_score", edge_attrs.get("anomaly", 0.0)) or 0.0)
        # amount fallback: look for common keys
        amount = edge_attrs.get("amount", edge_attrs.get("amt", edge_attrs.get("value", 0.0)) )
        try:
            amount = float(amount or 0.0)
        except Exception:
            amount = 0.0

        # cost components:
        # anomaly component: (1 - anomaly) -> lower if anomaly high
        anomaly_comp = 1.0 - anomaly

        # amount component: use inverse-log so larger amounts => smaller cost
        # add +10 inside log to avoid large cost for tiny amounts
        amount_comp = 1.0 / (math.log10(amount + 10.0) + 1e-9)

        # composite cost:
        cost = alpha * anomaly_comp + beta * amount_comp

        # small epsilon to avoid 0-cost edges
        return float(cost + 1e-12)

    # NetworkX support for MultiGraph: there can be multiple edges between u and v.
    # We must choose the minimal cost edge between two nodes when asked for shortest path.
    # networkx.shortest_path accepts a 'weight' function that will be called with (u, v, edge_attrs).
    # For MultiGraph, networkx will call this for each parallel edge if we use MultiGraph directly.
    try:
        # Compute shortest path (list of node ids)
        path_nodes = nx.shortest_path(graph, source=src, target=tgt, weight=edge_cost)
    except nx.NetworkXNoPath:
        raise HTTPException(status_code=404, detail=f"No path found between '{src}' and '{tgt}'")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error computing shortest path: {str(e)}")

    # Build edge sequence corresponding to node path: pick the edge (or key) with minimal computed cost between each pair
    path_edges: List[Dict[str, Any]] = []
    total_cost = 0.0
    total_amount = 0.0
    total_anomaly = 0.0

    for i in range(len(path_nodes) - 1):
        u = path_nodes[i]
        v = path_nodes[i + 1]

        # For MultiGraphs: iterate possible edges and pick min-cost one.
        candidate = None
        min_cost = float("inf")
        chosen_key = None
        chosen_attrs = None

        if graph.is_multigraph():
            # MultiGraph / MultiDiGraph: edge data is keyed by (u, v, key)
            for key, attrs in graph[u][v].items():
                c = edge_cost(u, v, attrs)
                if c < min_cost:
                    min_cost = c
                    candidate = (u, v, key)
                    chosen_attrs = attrs
                    chosen_key = key
        else:
            # Simple graph
            attrs = graph.get_edge_data(u, v) or {}
            min_cost = edge_cost(u, v, attrs)
            candidate = (u, v)
            chosen_attrs = attrs

        total_cost += min_cost
        amt = float(chosen_attrs.get("amount", 0.0) or 0.0)
        total_amount += amt
        total_anomaly += float(chosen_attrs.get("anomaly_score", chosen_attrs.get("anomaly", 0.0)) or 0.0)

        # Create an edge id for response: prefer stored 'id' attribute if present
        edge_id = None
        if chosen_attrs and "id" in chosen_attrs:
            edge_id = chosen_attrs["id"]
        else:
            # fallback id
            if chosen_key is not None:
                edge_id = f"{u}_{v}_{chosen_key}"
            else:
                edge_id = f"{u}_{v}"

        path_edges.append({
            "id": edge_id,
            "source": u,
            "target": v,
            "attrs": dict(chosen_attrs) if chosen_attrs is not None else {},
            "cost": round(min_cost, 12)
        })

    # Compose node objects with attributes for response
    response_nodes = []
    for n in path_nodes:
        node_attrs = dict(graph.nodes[n]) if hasattr(graph, "nodes") else {}
        response_nodes.append({"id": n, "attrs": node_attrs})

    response = {
        "path_nodes": response_nodes,
        "path_edges": path_edges,
        "total_cost": round(total_cost, 12),
        "total_amount": round(total_amount, 2),
        "avg_edge_anomaly": round((total_anomaly / max(1, len(path_edges))), 6),
        "path_length": len(path_nodes)
    }
    return response
