from flask import Flask, jsonify, abort
from neo4j import GraphDatabase
import os

# --- Configuration ---
# It's recommended to use environment variables for sensitive data
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "Abhay123#") # IMPORTANT: Change this!

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Neo4j Driver Initialization ---
# It's good practice to create a single driver instance for the application
try:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("Successfully connected to Neo4j.")
except Exception as e:
    print(f"Error connecting to Neo4j: {e}")
    driver = None

# --- Helper Function to Format Neo4j Results ---
def format_graph_data(records):
    """
    Parses Neo4j query results into a JSON format compatible with vis.js.
    """
    nodes = {}
    edges = set()

    for record in records:
        # Process nodes
        for node in record["nodes"]:
            node_id = node.element_id
            if node_id not in nodes:
                properties = dict(node)
                nodes[node_id] = {
                    "id": properties.get('id'), # The business ID of the account
                    "label": f"Account {properties.get('id', 'N/A')}",
                    "group": list(node.labels)[0].lower() if node.labels else 'unknown',
                    "details": {
                        "totalIn": properties.get('totalIn', 0),
                        "totalOut": properties.get('totalOut', 0),
                        "riskScore": properties.get('riskScore', 0)
                    }
                }

        # Process relationships (edges)
        for rel in record["relationships"]:
            start_node_id = dict(rel.start_node).get('id')
            end_node_id = dict(rel.end_node).get('id')

            # Create a unique tuple for the edge to avoid duplicates
            edge_tuple = tuple(sorted((start_node_id, end_node_id))) + (rel.type,)

            if start_node_id and end_node_id and edge_tuple not in edges:
                properties = dict(rel)
                edges.add(edge_tuple) # Add to set to track uniqueness

                # The final list of edges for vis.js
                # This list will be built at the end from the unique set

    # Re-map relationships using the business ID ('id' property)
    final_edges = []
    processed_edges = set()
    for rel in records[0]["relationships"]: # Assuming one record returns all relationships
        start_node = dict(rel.start_node)
        end_node = dict(rel.end_node)
        start_id = start_node.get('id')
        end_id = end_node.get('id')

        # Ensure we don't add duplicate edges if the query returns them
        if (start_id, end_id, rel.type) in processed_edges:
            continue

        properties = dict(rel)
        final_edges.append({
            "from": start_id,
            "to": end_id,
            "label": f"{rel.type}\n${properties.get('amount', 0)}"
        })
        processed_edges.add((start_id, end_id, rel.type))

    # The nodes dictionary values are what we want to return
    return {"nodes": list(nodes.values()), "edges": final_edges}

# --- API Endpoint ---
@app.route('/api/graph/account/<string:account_id>', methods=['GET'])
def get_account_graph(account_id):
    if not driver:
        return jsonify({"error": "Database connection not available"}), 500

    query = """
    MATCH path = (a:Account {id: $account_id})-[*1..3]-(b)
    WITH nodes(path) as path_nodes, relationships(path) as path_rels
    UNWIND path_nodes as node
    UNWIND path_rels as rel
    RETURN collect(DISTINCT node) as nodes, collect(DISTINCT rel) as relationships
    """

    try:
        with driver.session() as session:
            result = session.run(query, account_id=account_id)
            records = [record for record in result]
            if not records or not records[0]["nodes"]:
                 abort(404, description=f"Account with ID '{account_id}' not found.")

            graph_data = format_graph_data(records)
            return jsonify(graph_data)

    except Exception as e:
        # Log the exception for debugging
        app.logger.error(f"An error occurred while querying Neo4j: {e}")
        return jsonify({"error": "An internal server error occurred"}), 500

@app.teardown_appcontext
def close_driver(exception=None):
    # The driver itself doesn't need to be closed when the app context tears down.
    # The driver manages a connection pool. Sessions should be closed.
    pass

if __name__ == '__main__':
    # For development, you can run this script directly.
    # For production, use a proper WSGI server like Gunicorn or uWSGI.
    app.run(debug=True, port=5000)
