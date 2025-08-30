# file: simulate_endpoint.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import networkx as nx
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

class SimRequest(BaseModel):
    FromBank: int
    Account: str
    ToBank: int
    Account1: str
    Amount: float
    ReceivingCurrency: Optional[str] = None
    PaymentFormat: Optional[str] = None

# These should exist in your project
# from model_handler import load_model, model_predict_proba
# from preprocessing import transform_single_record
# from main_app_graph import G  # your NetworkX graph built at startup

MODEL = None
G = None

def init_dependencies(model, graph):
    global MODEL, G
    MODEL = model
    G = graph

@router.post("/simulate")
def simulate(tx: SimRequest):
    """
    Simulate a hypothetical transaction:
    1) Score the transaction with the trained model (if available).
    2) Add it temporarily to a graph copy, recompute PageRank to estimate risk propagation.
    3) Return model score (if available) and top-k delta nodes by propagated risk.
    """
    src = f"{tx.FromBank}_{tx.Account}"
    tgt = f"{tx.ToBank}_{tx.Account1}"

    if G is None:
        raise HTTPException(status_code=500, detail="Graph not initialized on server")

    # 1) Model-based scoring (if model available)
    model_score = None
    model_prediction = None
    try:
        if MODEL is not None:
            # transform using same pipeline as training
            rec = {
                "From Bank": tx.FromBank,
                "Account": tx.Account,
                "To Bank": tx.ToBank,
                "Account.1": tx.Account1,
                "Amount Received": tx.Amount,
                "Receiving Currency": tx.ReceivingCurrency or "UNK",
                "Payment Format": tx.PaymentFormat or "UNK"
            }
            # transform -> numeric features (this function should mirror notebook preprocessing)
            X = transform_single_record(rec)            # implement in preprocessing.py
            prob = MODEL.predict_proba(X)[0, 1]        # probability of 'suspicious' class
            model_score = float(round(prob, 5))
            model_prediction = "suspicious" if model_score >= 0.5 else "normal"
    except Exception as e:
        logger.exception("Model scoring failed; falling back to heuristic: %s", e)
        model_score = None
        model_prediction = None

    # 2) Graph propagation: clone graph and add simulated edge
    H = G.copy()
    # ensure nodes exist
    if not H.has_node(src):
        H.add_node(src)
    if not H.has_node(tgt):
        H.add_node(tgt)
    # add edge with amount metadata
    H.add_edge(src, tgt, amount=tx.Amount, simulated=True)

    # compute PageRank as proxy for propagated risk (use amount as weight if meaningful)
    try:
        pr_H = nx.pagerank(H, weight='amount')
        pr_G = nx.pagerank(G, weight='amount')
    except Exception:
        # fallback to unweighted pagerank
        pr_H = nx.pagerank(H)
        pr_G = nx.pagerank(G)

    # compute deltas and return top 20 affected nodes
    deltas = []
    all_nodes = set(pr_H.keys()).union(pr_G.keys())
    for n in all_nodes:
        before = pr_G.get(n, 0.0)
        after = pr_H.get(n, 0.0)
        diff = after - before
        if abs(diff) > 1e-9:
            deltas.append({"node": n, "before": round(before, 8), "after": round(after, 8), "delta": round(diff, 8)})

    deltas.sort(key=lambda x: -abs(x["delta"]))
    top_impacts = deltas[:20]

    return {
        "simulated_edge": {"source": src, "target": tgt, "amount": tx.Amount},
        "model": {"score": model_score, "prediction": model_prediction},
        "impact_summary": {
            "affected_nodes": len(deltas),
            "top_impacts": top_impacts
        }
    }
