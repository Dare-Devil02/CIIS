// File: SigmaGraphViewer.jsx
import React, { useEffect, useRef, useState } from "react";
import Graph from "graphology";
import Sigma from "sigma";
import forceAtlas2 from "graphology-layout-forceatlas2";
import chroma from "chroma-js";

/**
 * Props:
 *  - apiUrl (optional): fetch json graph from this url
 *  - data (optional): already-loaded graph JSON {nodes:[], edges:[]}
 *  - initialSampleLimit (optional): number to cap initial nodes if API doesn't provide sampling
 */
export default function SigmaGraphViewer({ apiUrl = null, data = null, initialSampleLimit = 2000 }) {
    const containerRef = useRef(null);
    const sigmaRef = useRef(null);
    const graphRef = useRef(null);
    const [loading, setLoading] = useState(false);

    // color scale: green -> yellow -> red
    const colorScale = chroma.scale(["#2ecc71", "#f1c40f", "#e74c3c"]).domain([0, 1]);

    // utility mappings
    const mapNodeColor = (risk) => colorScale(risk || 0).hex();
    const mapNodeSize = (risk, degree=0) => {
        const base = 4 + (24 * (risk || 0));           // 4..28
        const degBoost = Math.sqrt(degree) * 0.6;     // mild boost by degree
        return Math.min(48, base + degBoost);
    };
    const mapEdgeWidth = (amount) => {
        // map amount to width using log scale
        const w = Math.log10((amount || 1) + 1);
        return Math.min(12, Math.max(0.6, w)); // clamp
    };
    const edgeColorFor = (prediction) => (prediction === "suspicious" ? "#d9534f" : "#9aa5b1");

    // build graphology graph from JSON
    function buildGraphFromJson(json) {
        const g = new Graph();
        // add nodes
        for (const n of json.nodes || []) {
            const id = n.id;
            // initial degree 0; we will update size after edges added
            const attrs = {
                label: id,
                risk: n.risk_score ?? 0,
                color: mapNodeColor(n.risk_score ?? 0),
                size: 6,
                x: n.x !== undefined ? n.x : undefined,
                y: n.y !== undefined ? n.y : undefined,
                attrs: n.attrs ?? {}
            };
            g.addNode(id, attrs);
        }
        // add edges
        for (const e of json.edges || []) {
            // ensure nodes exist
            if (!g.hasNode(e.source)) g.addNode(e.source, { label: e.source, risk: 0, color: mapNodeColor(0), size: 6 });
            if (!g.hasNode(e.target)) g.addNode(e.target, { label: e.target, risk: 0, color: mapNodeColor(0), size: 6 });
            const eid = e.id || `${e.source}_${e.target}_${Math.random().toString(36).slice(2,8)}`;
            const edgeAttrs = {
                label: `${e.amount ?? ""}`,
                amount: e.amount ?? 0,
                anomaly_score: e.anomaly_score ?? 0,
                prediction: e.prediction ?? "normal",
                color: edgeColorFor(e.prediction),
                size: mapEdgeWidth(e.amount)
            };
            // use multi-edges safely
            try { g.addEdgeWithKey(eid, e.source, e.target, edgeAttrs); } catch (err) {
                // fallback: if duplicated key, just add a plain edge
                try { g.addEdge(e.source, e.target, edgeAttrs); } catch (e2) {}
            }
        }

        // recompute degree-informed size & colors
        g.forEachNode((node, attr) => {
            const degree = g.degree(node);
            g.setNodeAttribute(node, "size", mapNodeSize(attr.risk || 0, degree));
        });

        return g;
    }

    async function initializeGraph(json) {
        // free previous
        if (sigmaRef.current) {
            sigmaRef.current.kill();
            sigmaRef.current = null;
        }
        // build graphology graph
        const g = buildGraphFromJson(json);
        graphRef.current = g;

        // if nodes lack positions and graph is small, run ForceAtlas2 in worker
        const nodesHavePos = Array.from(g.nodes()).some(n => g.hasNodeAttribute(n, "x") && g.getNodeAttribute(n, "x") !== undefined);
        if (!nodesHavePos && g.order <= 5000) {
            // run ForceAtlas2 for a modest number of iterations in a non-blocking manner
            try {
                await new Promise(resolve => {
                    const settings = { iterations: 100, settings: { gravity: 1, scalingRatio: 2 } };
                    forceAtlas2.assign(g, { iterations: 100, settings: { gravity: 1, scalingRatio: 2 } });
                    resolve();
                });
            } catch (err) {
                console.warn("ForceAtlas2 failed (client). Server-side layout recommended for large graphs.", err);
            }
        }

        // init sigma
        sigmaRef.current = new Sigma(g, containerRef.current, {
            renderEdgeLabels: false,
            allowInvalidContainer: true
        });

        // Interaction: hover & click
        sigmaRef.current.on("enterNode", ({ node }) => {
            // spotlight node: increase size temporarily
            graphRef.current.setNodeAttribute(node, "color", "#ffb300");
            sigmaRef.current.refresh();
        });
        sigmaRef.current.on("leaveNode", ({ node }) => {
            // restore color by risk
            const risk = graphRef.current.getNodeAttribute(node, "risk") || 0;
            graphRef.current.setNodeAttribute(node, "color", mapNodeColor(risk));
            sigmaRef.current.refresh();
        });
        sigmaRef.current.on("clickNode", async ({ node }) => {
            // fetch ego network from backend and merge if API available
            try {
                const res = await fetch(`/node/${encodeURIComponent(node)}`);
                if (res.ok) {
                    const sub = await res.json(); // expects same graph schema
                    // merge nodes/edges
                    for (const n of sub.nodes || []) {
                        if (!graphRef.current.hasNode(n.id)) {
                            graphRef.current.addNode(n.id, {
                                label: n.id,
                                risk: n.risk_score ?? 0,
                                color: mapNodeColor(n.risk_score ?? 0),
                                                     size: mapNodeSize(n.risk_score ?? 0)
                            });
                        }
                    }
                    for (const e of sub.edges || []) {
                        if (!graphRef.current.hasEdge(e.id)) {
                            graphRef.current.addEdgeWithKey(e.id, e.source, e.target, {
                                amount: e.amount,
                                anomaly_score: e.anomaly_score,
                                prediction: e.prediction,
                                color: edgeColorFor(e.prediction),
                                                            size: mapEdgeWidth(e.amount)
                            });
                        }
                    }
                    sigmaRef.current.refresh();
                }
            } catch (err) {
                // backend may not provide /node/{id}; ignore
            }
        });
    }

    // load JSON from apiUrl or props.data
    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            try {
                let json = data;
                if (!json && apiUrl) {
                    const res = await fetch(apiUrl);
                    if (!res.ok) throw new Error("Failed to fetch graph JSON");
                    json = await res.json();
                }
                if (!json) throw new Error("No graph JSON provided");
                // optionally sample if huge
                if ((json.nodes?.length ?? 0) > initialSampleLimit) {
                    console.warn("Server should return a sampled graph. Frontend will truncate to initialSampleLimit for initial render.");
                    json = {
                        meta: json.meta,
                        nodes: json.nodes.slice(0, initialSampleLimit),
         edges: json.edges.filter(e => json.nodes.slice(0, initialSampleLimit).some(n => n.id === e.source || n.id === e.target))
                    };
                }
                if (!cancelled) await initializeGraph(json);
            } catch (err) {
                console.error(err);
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; if (sigmaRef.current) { sigmaRef.current.kill(); sigmaRef.current = null; } };
    }, [apiUrl, data]);

    return (
        <div style={{ display: "flex", gap: 12 }}>
        <div style={{ width: 320, minWidth: 260 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>AML — Graph</div>
        <div>Legend:</div>
        <div style={{ marginTop: 8 }}>
        <div><span style={{ display: "inline-block", width: 12, height: 12, background: colorScale(1).hex(), marginRight: 8 }}></span> High risk</div>
        <div><span style={{ display: "inline-block", width: 12, height: 12, background: colorScale(0.5).hex(), marginRight: 8 }}></span> Medium risk</div>
        <div><span style={{ display: "inline-block", width: 12, height: 12, background: colorScale(0).hex(), marginRight: 8 }}></span> Low risk</div>
        <div style={{ marginTop: 8 }}><span style={{ background: "#d9534f", color: "#fff", padding: "2px 6px", borderRadius: 4 }}>Suspicious</span> transactions are highlighted in red</div>
        </div>
        <div style={{ marginTop: 12 }}>
        <small>Tip: provide precomputed x,y in your JSON for best performance on large graphs.</small>
        </div>
        </div>

        <div ref={containerRef} style={{ flex: 1, height: 640, border: "1px solid #e6e8eb", borderRadius: 6 }} />
        {loading && <div style={{ position: "absolute", left: "50%", top: "50%" }}>loading…</div>}
        </div>
    );
}
