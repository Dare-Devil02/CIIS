# main.py (snippet)
from fastapi import FastAPI
from path_router import init_graph, router as path_router
# assume you have a function build_graph() that returns NetworkX graph G
from graph_builder import build_graph  # your existing code

app = FastAPI()

# Build or load graph at startup
G = build_graph(...)   # must include edge attrs: amount, anomaly_score, optional id, etc.
init_graph(G)

app.include_router(path_router)
# ... other routers and startup logic
