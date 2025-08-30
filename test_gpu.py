import cudf
import cugraph
import numpy as np

print("🚀 Starting GPU verification...")

# 1. Create a GPU DataFrame using cuDF
# This operation happens entirely on your RTX 4060
df = cudf.DataFrame({
    'source': [0, 1, 2, 0],
    'destination': [1, 2, 3, 3]
})
print("\n✅ cuDF DataFrame created successfully on the GPU:")
print(df)

# 2. Create a Graph from the DataFrame using cuGraph
G = cugraph.Graph()
G.from_cudf_edgelist(df, source='source', destination='destination')
print("\n✅ cuGraph Graph created successfully on the GPU:")
print(f"   - Number of nodes: {G.number_of_nodes()}")
print(f"   - Number of edges: {G.number_of_edges()}")

# 3. Perform a GPU-accelerated calculation (PageRank)
pagerank_scores = cugraph.pagerank(G)
print("\n✅ cuGraph PageRank calculated successfully on the GPU:")
print(pagerank_scores.head())

print("\n🎉 Your GPU environment is working perfectly!")
