from fastapi import FastAPI
from pydantic import BaseModel
import networkx as nx

app = FastAPI()

class SimTx(BaseModel):
    FromBank: int
    Account: str
    ToBank: int
    Account1: str
    Amount: float

# Suppose you already build a NetworkX graph from your dataset on startup
G = nx.DiGraph()
# G should be prepopulated with nodes and edge attributes (amount, anomaly_score, etc.)

@app.post("/simulate")
def simulate(tx: SimTx):
    src = f"{tx.FromBank}_{tx.Account}"
    tgt = f"{tx.ToBank}_{tx.Account1}"
    # clone G to avoid mutation
    H = G.copy()
    H.add_edge(src, tgt, amount=tx.Amount, simulated=True)
    # simple propagation: recompute PageRank as a proxy for risk propagation
    pr = nx.pagerank(H, weight='amount')  # may need normalization in real life
    # compute deltas vs original
    orig_pr = nx.pagerank(G, weight='amount')
    deltas = []
    for node in set(pr.keys()).union(orig_pr.keys()):
        before = orig_pr.get(node, 0.0)
        after = pr.get(node, 0.0)
        if abs(after - before) > 1e-6:
            deltas.append({"node": node, "before": before, "after": after, "delta": after - before})
    deltas.sort(key=lambda x: -abs(x['delta']))
    return {"summary": f"Simulated {src} -> {tgt} amount={tx.Amount}", "impact": deltas[:20]}
