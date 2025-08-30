import React, { useEffect, useRef, useState } from "react";
import axios from "axios";
import Cytoscape from "cytoscape";

// Default export React component — single-file visualizer for your Graph Viz role.
// Features implemented:
// - Load graph from backend (/detect or /job/{id}/graph?mode=sample)
// - Render with Cytoscape (supports preset x,y or cose layout)
// - Node click → load /node/{id} (ego network)
// - Path tracing (client-side Dijkstra) between two selected nodes
// - What-If simulator UI: posts to /simulate (if present) or falls back to client-side heuristic

export default function AMLGraphVisualizer({ apiBase = "http://localhost:8000" }) {
  const cyRef = useRef(null);
  const containerRef = useRef(null);
  const [loading, setLoading] = useState(false);
  const [graphMeta, setGraphMeta] = useState(null);
  const [nodesList, setNodesList] = useState([]);
  const [selectedNode, setSelectedNode] = useState(null);
  const [pathFrom, setPathFrom] = useState("");
  const [pathTo, setPathTo] = useState("");
  const [pathResult, setPathResult] = useState(null);
  const [whatIfResp, setWhatIfResp] = useState(null);

  // Utility: transform backend graph -> cytoscape elements
  function graphToElements(graph) {
    const els = [];
    if (!graph) return els;
    for (const n of graph.nodes || []) {
      const data = { id: n.id, label: n.id, risk: n.risk_score || 0 };
      const node = { data };
      if (n.x !== undefined && n.y !== undefined) node.position = { x: n.x, y: n.y };
      els.push(node);
    }
    for (const e of graph.edges || []) {
      els.push({ data: { id: e.id, source: e.source, target: e.target, amount: e.amount || 1, predicted: e.prediction || "normal", score: e.anomaly_score || 0 } });
    }
    return els;
  }

  // Initialize cytoscape instance
  function initCytoscape(elements) {
    if (cyRef.current) {
      cyRef.current.destroy();
      cyRef.current = null;
    }
    cyRef.current = Cytoscape({
      container: containerRef.current,
      elements,
      style: [
        { selector: "node", style: { label: "data(label)", width: "mapData(risk,0,1,16,48)", height: "mapData(risk,0,1,16,48)", "text-valign": "center", "text-halign": "center", "background-color": "#1976d2" } },
        { selector: "edge", style: { width: 2, "line-color": "#999", "curve-style": "bezier" } },
        { selector: "edge[predicted='suspicious']", style: { "line-color": "#d32f2f", width: 3 } },
        { selector: ".highlight", style: { "background-color": "#ffb300", "line-color": "#ffb300" } },
      ],
      layout: { name: "preset", animate: true }
    });

    // If no positions were provided, run a cose layout to place nodes
    const hasPositions = elements.some(el => el.position);
    if (!hasPositions) cyRef.current.layout({ name: "cose", animate: true, randomize: true }).run();

    // Node click — set selected node and fetch ego-network
    cyRef.current.on("tap", "node", async (evt) => {
      const node = evt.target;
      setSelectedNode(node.id());
      // fetch ego network
      try {
        const res = await axios.get(`${apiBase}/node/${encodeURIComponent(node.id())}`);
        if (res.data && res.data.edges) {
          // merge ego subgraph into the graph (simple approach)
          const egElems = graphToElements(res.data);
          // add only missing elements
          cyRef.current.add(egElems.filter(el => !cyRef.current.getElementById(el.data.id).length));
          // re-run layout on the clicked node neighborhood
          cyRef.current.layout({ name: "cose", fit: true }).run();
        }
      } catch (err) {
        // ignore if endpoint missing — client still works
        console.warn("/node/{id} fetch failed or not provided", err?.message || err);
      }
    });
  }

  // Load initial sample graph from backend
  async function loadSampleGraph() {
    setLoading(true);
    try {
      // prefer sample endpoint; fallback to /detect or /sample-data
      const endpoints = [
        `${apiBase}/job/1/graph?mode=sample&limit=1000`,
        `${apiBase}/detect`,
        `${apiBase}/sample-data`,
      ];
      let graph = null;
      for (const url of endpoints) {
        try {
          const res = await axios.get(url);
          if (res.status === 200 && (res.data.nodes || res.data.elements)) {
            graph = res.data.nodes ? res.data : { nodes: res.data.nodes || [], edges: res.data.edges || [] };
            break;
          }
        } catch (e) { /* try next */ }
      }
      if (!graph) {
        // fallback: minimal mock graph
        graph = {
          nodes: [ { id: "1_ACC001", risk_score: 0.9, x: 0, y: 0 }, { id: "2_ACC002", risk_score: 0.2, x: 200, y: 0 }, { id: "3_ACC003", risk_score: 0.6, x: 100, y: 150 } ],
          edges: [ { id: "T1", source: "1_ACC001", target: "2_ACC002", amount: 1000, prediction: "suspicious", anomaly_score: 0.87 }, { id: "T2", source: "2_ACC002", target: "3_ACC003", amount: 50, prediction: "normal", anomaly_score: 0.12 } ]
        };
      }
      setGraphMeta({ total_nodes: graph.nodes.length, total_edges: graph.edges.length });
      setNodesList(graph.nodes.map(n => n.id));
      initCytoscape(graphToElements(graph));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadSampleGraph();
    // cleanup on unmount
    return () => { if (cyRef.current) cyRef.current.destroy(); };
  }, []);

  // Simple Dijkstra (weights: use 1/amount if available else 1)
  function shortestPath(source, target) {
    if (!cyRef.current) return null;
    const nodes = cyRef.current.nodes().map(n => n.id());
    const adj = {};
    cyRef.current.edges().forEach(e => {
      const s = e.data("source");
      const t = e.data("target");
      const amount = e.data("amount") || 1;
      const w = amount > 0 ? 1 / amount : 1; // larger amounts -> smaller cost
      adj[s] = adj[s] || [];
      adj[t] = adj[t] || [];
      adj[s].push({ to: t, w, eid: e.id() });
      // assume undirected for path-finding convenience (you can change)
      adj[t].push({ to: s, w, eid: e.id() });
    });

    if (!adj[source] || !adj[target]) return null;

    const dist = {}, prev = {}, pq = new Set(nodes);
    nodes.forEach(n => (dist[n] = Infinity, prev[n] = null));
    dist[source] = 0;

    while (pq.size) {
      // extract min
      let u = null;
      let best = Infinity;
      pq.forEach(x => { if (dist[x] < best) { best = dist[x]; u = x; } });
      if (u === null) break;
      pq.delete(u);
      if (u === target) break;
      const neighbors = adj[u] || [];
      for (const nb of neighbors) {
        const alt = dist[u] + nb.w;
        if (alt < dist[nb.to]) { dist[nb.to] = alt; prev[nb.to] = { from: u, via: nb.eid }; }
      }
    }

    if (dist[target] === Infinity) return null;
    // reconstruct
    const pathNodes = [];
    const pathEdges = [];
    let cur = target;
    while (cur) {
      pathNodes.unshift(cur);
      if (prev[cur]) { pathEdges.unshift(prev[cur].via); cur = prev[cur].from; } else break;
    }
    return { nodes: pathNodes, edges: pathEdges, cost: dist[target] };
  }

  function runPathTrace() {
    const res = shortestPath(pathFrom, pathTo);
    setPathResult(res);
    if (res && cyRef.current) {
      cyRef.current.elements().removeClass("highlight");
      res.nodes.forEach(id => cyRef.current.getElementById(id).addClass("highlight"));
      res.edges.forEach(eid => cyRef.current.getElementById(eid).addClass("highlight"));
    }
  }

  // What-if: post to /simulate; fallback to client-side quick simulation
  async function runWhatIf(form) {
    setWhatIfResp(null);
    try {
      const url = `${apiBase}/simulate`;
      const res = await axios.post(url, form, { timeout: 5000 });
      setWhatIfResp({ fromServer: true, data: res.data });
      // if server returns new graph pieces, merge
      if (res.data.nodes || res.data.edges) {
        const els = graphToElements(res.data);
        cyRef.current.add(els.filter(el => !cyRef.current.getElementById(el.data.id).length));
        cyRef.current.layout({ name: "cose", fit: true }).run();
      }
    } catch (err) {
      // fallback: simulate locally
      console.warn("/simulate not available — running local heuristic");
      const local = localWhatIfSim(form);
      setWhatIfResp({ fromServer: false, data: local });
    }
  }

  // Local heuristic: temporarily add edge, compute degree/risk delta
  function localWhatIfSim({ FromBank, Account, ToBank, Account1, Amount }) {
    if (!cyRef.current) return { error: "No graph loaded" };
    const src = `${FromBank}_${Account}`;
    const tgt = `${ToBank}_${Account1}`;
    // degree change heuristic
    const nodesBefore = {};
    cyRef.current.nodes().forEach(n => nodesBefore[n.id()] = n.degree());
    // pretend to add edge
    const tempEdgeId = `temp_${Date.now()}`;
    const el = { data: { id: tempEdgeId, source: src, target: tgt, amount: Amount, prediction: "simulated" } };
    cyRef.current.add(el);
    // compute new degrees
    const nodesAfter = {};
    cyRef.current.nodes().forEach(n => nodesAfter[n.id()] = n.degree());
    // remove temp
    cyRef.current.getElementById(tempEdgeId).remove();

    const deltas = [];
    for (const id in nodesBefore) {
      const d0 = nodesBefore[id] || 0;
      const d1 = nodesAfter[id] || 0;
      if (d1 !== d0) deltas.push({ id, before: d0, after: d1, delta: d1 - d0 });
    }
    deltas.sort((a,b) => Math.abs(b.delta) - Math.abs(a.delta));
    return { summary: `Simulated edge ${src} -> ${tgt} amount=${Amount}`, impact: deltas.slice(0,10) };
  }

  return (
    <div className="p-4 font-sans">
      <h2 className="text-xl font-semibold mb-2">AML Graph Visualizer</h2>
      <div className="flex gap-4 mb-4">
        <div className="bg-gray-50 p-3 rounded shadow-sm w-72">
          <div className="text-sm">Nodes: {graphMeta?.total_nodes ?? "-"}</div>
          <div className="text-sm">Edges: {graphMeta?.total_edges ?? "-"}</div>
          <div className="mt-2">
            <label className="text-xs">Path trace from</label>
            <select className="w-full p-1 mt-1" value={pathFrom} onChange={e => setPathFrom(e.target.value)}>
              <option value="">--select--</option>
              {nodesList.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
            <label className="text-xs mt-2">to</label>
            <select className="w-full p-1 mt-1" value={pathTo} onChange={e => setPathTo(e.target.value)}>
              <option value="">--select--</option>
              {nodesList.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
            <button className="mt-2 w-full p-2 bg-blue-600 text-white rounded" onClick={runPathTrace}>Run Path Trace</button>
          </div>
        </div>

        <div className="bg-gray-50 p-3 rounded shadow-sm w-80">
          <div className="text-sm font-medium">What-If Simulator</div>
          <WhatIfForm onSubmit={runWhatIf} />
          {whatIfResp && (
            <pre className="mt-2 text-xs h-32 overflow-auto bg-white p-2 rounded border">{JSON.stringify(whatIfResp, null, 2)}</pre>
          )}
        </div>

        <div className="flex-1">
          <div ref={containerRef} style={{ width: "100%", height: 520, border: '1px solid #e5e7eb', borderRadius: 8 }} />
        </div>
      </div>

      {pathResult && (
        <div className="bg-gray-100 p-3 rounded">
          <div className="font-medium">Path Result</div>
          <div className="text-sm">Nodes: {pathResult.nodes.join(" → ")}</div>
          <div className="text-sm">Cost: {pathResult.cost.toFixed(4)}</div>
        </div>
      )}

      <div className="mt-4 text-xs text-gray-600">Notes: Cytoscape is used for reliable layouts. For very large graphs (>50k nodes) use server-side aggregation, precomputed layouts, and progressive loading.</div>
    </div>
  );
}

function WhatIfForm({ onSubmit }) {
  const [FromBank, setFromBank] = useState(1);
  const [Account, setAccount] = useState("ACC_NEW");
  const [ToBank, setToBank] = useState(2);
  const [Account1, setAccount1] = useState("ACC_TARGET");
  const [Amount, setAmount] = useState(1000);

  function submit(e) {
    e.preventDefault();
    onSubmit({ FromBank, Account, ToBank, Account1, Amount });
  }

  return (
    <form onSubmit={submit} className="mt-2">
      <input className="w-full p-1 mb-1" value={Account} onChange={e => setAccount(e.target.value)} placeholder="Sender Account" />
      <input className="w-full p-1 mb-1" value={Account1} onChange={e => setAccount1(e.target.value)} placeholder="Receiver Account" />
      <input className="w-full p-1 mb-1" value={Amount} onChange={e => setAmount(Number(e.target.value))} placeholder="Amount" type="number" />
      <button className="w-full p-1 bg-green-600 text-white rounded" type="submit">Simulate Transaction</button>
    </form>
  );
}
