import polars as pl
import cudf
import cugraph
import time

# --- Configuration ---
TRANSACTIONS_FILE = 'transactions.csv'
TOP_N_COMMUNITIES = 10
TOP_N_ACCOUNTS = 20

def run_triage():
    """
    Main function to perform high-speed triage on transaction data using CPU and GPU.
    """
    total_start_time = time.time()

    # --- 1. Data Ingestion (CPU with Polars) ---
    print("\n--- Phase 1: Data Ingestion (CPU) ---")
    start_ingest = time.time()
    try:
        pl_df_lazy = pl.scan_csv(TRANSACTIONS_FILE)
    except FileNotFoundError:
        print(f"Error: '{TRANSACTIONS_FILE}' not found.")
        print("Please run 'python generate_data.py' first to create the dataset.")
        return

    # --- 2. Data Preparation (CPU with Polars) ---
    print("--- Phase 2: Data Preparation (CPU) ---")
    pl_df_prepared = pl_df_lazy.select(
        ["SENDER_ACCOUNT_ID", "RECEIVER_ACCOUNT_ID", "TX_AMOUNT", "TIMESTAMP"]
    ).rename({
        "SENDER_ACCOUNT_ID": "sender",
        "RECEIVER_ACCOUNT_ID": "receiver",
        "TX_AMOUNT": "amount",
        "TIMESTAMP": "timestamp"
    }).drop_nulls(
        subset=["sender", "receiver"]
    ).collect()
    end_ingest_prep = time.time()
    print(f"Polars loaded and prepared {len(pl_df_prepared):,} rows in {end_ingest_prep - start_ingest:.2f} seconds.")

    # --- 3. GPU Offload & Manual Renumbering ---
    print("\n--- Phase 3: Offloading Data to GPU & Manual Renumbering ---")
    start_gpu_offload = time.time()
    pandas_df = pl_df_prepared.to_pandas()
    gdf = cudf.DataFrame.from_pandas(pandas_df)

    # --- Create our own mapping from account names to integers ---
    # 1. Get all unique account names from both sender and receiver columns.
    all_accounts = cudf.concat([gdf['sender'], gdf['receiver']]).unique()

    # 2. Create a mapping DataFrame from the unique account names to an integer ID.
    account_map = cudf.DataFrame({
        'account': all_accounts,
        'vertex_id': cudf.Series(range(len(all_accounts)), dtype='int32')
    })

    # 3. Merge this map back into the main DataFrame to get integer IDs for senders.
    gdf = gdf.merge(account_map, left_on='sender', right_on='account', how='left')
    # FIX: Explicitly drop the 'account' COLUMN to avoid ambiguity.
    gdf = gdf.rename(columns={'vertex_id': 'sender_id'}).drop(columns=['account'])

    # 4. Merge again to get integer IDs for receivers.
    gdf = gdf.merge(account_map, left_on='receiver', right_on='account', how='left')
    # FIX: Explicitly drop the 'account' COLUMN here as well.
    gdf = gdf.rename(columns={'vertex_id': 'receiver_id'}).drop(columns=['account'])

    end_gpu_offload = time.time()
    print(f"Data prepared for GPU in {end_gpu_offload - start_gpu_offload:.2f} seconds.")
    print(f"GPU Memory Usage: {gdf.memory_usage().sum() / 1e9:.2f} GB")

    # --- 4. Algorithmic Analysis (GPU with cuGraph) ---
    print("\n--- Phase 4: Graph Analysis on GPU ---")

    print("Building graphs from transactions...")
    start_graph_build = time.time()

    # Create graphs using our manually created integer IDs.
    G_directed = cugraph.Graph(directed=True)
    G_directed.from_cudf_edgelist(gdf, source='sender_id', destination='receiver_id', edge_attr='amount', store_transposed=True)

    G_undirected = cugraph.Graph(directed=False)
    G_undirected.from_cudf_edgelist(gdf, source='sender_id', destination='receiver_id', edge_attr='amount')

    end_graph_build = time.time()
    print(f"Graphs built in {end_graph_build - start_graph_build:.2f}s.")
    print(f"Graph has {G_directed.number_of_nodes():,} nodes and {G_directed.number_of_edges():,} directed edges.")

    # Run Louvain.
    print("Running Community Detection (Louvain)...")
    start_louvain = time.time()
    louvain_df, modularity_score = cugraph.louvain(G_undirected)
    end_louvain = time.time()
    print(f"Louvain completed in {end_louvain - start_louvain:.2f} seconds with modularity {modularity_score:.4f}.")

    # Run PageRank.
    print("Running Centrality Analysis (PageRank)...")
    start_pagerank = time.time()
    pagerank_df = cugraph.pagerank(G_directed)
    end_pagerank = time.time()
    print(f"PageRank completed in {end_pagerank - start_pagerank:.2f} seconds.")

    # --- 5. Outcome Generation & Lead Prioritization ---
    print("\n--- OUTCOME: Prioritized Lead List ---")

    # Analyze Louvain results.
    community_counts = louvain_df['partition'].value_counts().reset_index()
    community_counts.columns = ['community_id', 'account_count']
    top_communities = community_counts.nlargest(TOP_N_COMMUNITIES, 'account_count')

    print(f"\n✅ Top {TOP_N_COMMUNITIES} Largest Suspicious Communities (by member count):")
    print(top_communities.to_pandas().to_string(index=False))

    # Get the top accounts from PageRank (the 'vertex' column has our integer IDs).
    top_accounts_df = pagerank_df.nlargest(TOP_N_ACCOUNTS, 'pagerank')

    # Merge the PageRank results with our manual account map to get the names back.
    final_accounts_df = top_accounts_df.merge(
        account_map,
        left_on='vertex',
        right_on='vertex_id',
        how='left'
    )

    print(f"\n✅ Top {TOP_N_ACCOUNTS} Most Influential 'Broker' Accounts (by PageRank score):")
    # Display the final, readable list of accounts and their scores.
    print(final_accounts_df[['account', 'pagerank']].to_pandas().to_string(index=False))

    total_end_time = time.time()
    print(f"\n--- Total Triage Time: {total_end_time - total_start_time:.2f} seconds ---")

if __name__ == "__main__":
    run_triage()

