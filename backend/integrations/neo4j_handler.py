from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable

from neo4j import GraphDatabase


class GraphStoreError(RuntimeError):
    pass


def graph_checksum(nodes: list[dict], edges: list[dict]) -> str:
    payload = json.dumps(
        {"nodes": nodes, "edges": edges},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Neo4jHandler:
    """Backend-only, graph-scoped Neo4j storage.

    Graph payloads are replaced atomically per graph id. Node and relationship
    payload_json fields preserve the complete API objects; promoted properties
    exist only for graph lookup and future indexing.
    """

    def __init__(self, uri: str, user: str, password: str, *, database: str = "neo4j"):
        if not uri or not user or not password:
            raise GraphStoreError("Neo4j URI, user, and password are required")
        self.database = database
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.driver.verify_connectivity()
        self.ensure_constraints()

    @classmethod
    def from_environment(cls) -> "Neo4jHandler":
        uri = os.environ.get("NEO4J_URI", "").strip()
        user = os.environ.get("NEO4J_USER", "neo4j").strip() or "neo4j"
        password_file = os.environ.get("NEO4J_PASSWORD_FILE", "").strip()
        if password_file:
            password = Path(password_file).read_text(encoding="utf-8").strip()
        else:
            password = os.environ.get("NEO4J_PASSWORD", "").strip()
        database = os.environ.get("NEO4J_DATABASE", "neo4j").strip() or "neo4j"
        return cls(uri, user, password, database=database)

    def close(self) -> None:
        self.driver.close()

    def ensure_constraints(self) -> None:
        statements = (
            "CREATE CONSTRAINT graph_id_unique IF NOT EXISTS "
            "FOR (g:Graph) REQUIRE g.id IS UNIQUE",
            "CREATE CONSTRAINT graph_node_unique IF NOT EXISTS "
            "FOR (n:KnowledgeNode) REQUIRE (n.graph_id, n.node_id) IS UNIQUE",
        )
        with self.driver.session(database=self.database) as session:
            for statement in statements:
                session.run(statement).consume()

    def health(self) -> dict[str, Any]:
        expected = {"graph_id_unique", "graph_node_unique"}
        try:
            with self.driver.session(database=self.database) as session:
                row = session.run("RETURN 1 AS ok").single()
                constraints = session.run(
                    "SHOW CONSTRAINTS YIELD name "
                    "WHERE name IN ['graph_id_unique', 'graph_node_unique'] "
                    "RETURN collect(name) AS names"
                ).single()
            names = set(constraints["names"] if constraints else [])
            return {
                "ok": bool(row and row["ok"] == 1 and names == expected),
                "database": self.database,
                "constraints": sorted(names),
            }
        except Exception as exc:
            return {"ok": False, "database": self.database, "error": str(exc)}

    def replace_graph(
        self,
        graph_id: str,
        kind: str,
        nodes: list[dict],
        edges: list[dict],
        *,
        revision: int,
        schema_version: int = 1,
    ) -> dict[str, Any]:
        normalized_nodes, normalized_edges = self._validate_payload(nodes, edges)
        checksum = graph_checksum(normalized_nodes, normalized_edges)
        node_rows = [self._node_row(graph_id, index, node) for index, node in enumerate(normalized_nodes)]
        edge_rows = [self._edge_row(graph_id, index, edge) for index, edge in enumerate(normalized_edges)]
        metadata = {
            "graph_id": graph_id,
            "kind": kind,
            "revision": int(revision),
            "schema_version": int(schema_version),
            "node_count": len(node_rows),
            "edge_count": len(edge_rows),
            "checksum": checksum,
        }
        with self.driver.session(database=self.database) as session:
            session.execute_write(self._replace_graph_tx, metadata, node_rows, edge_rows)
        verified = self.verify_graph(graph_id)
        if not verified["ok"] or verified["checksum"] != checksum:
            raise GraphStoreError(f"Neo4j verification failed for graph {graph_id}")
        return metadata

    @staticmethod
    def _replace_graph_tx(tx, metadata: dict, nodes: list[dict], edges: list[dict]) -> None:
        tx.run(
            "MATCH (old:KnowledgeNode {graph_id: $graph_id}) DETACH DELETE old",
            graph_id=metadata["graph_id"],
        ).consume()
        tx.run(
            "MERGE (g:Graph {id: $graph_id}) "
            "SET g.kind = $kind, g.revision = $revision, "
            "g.schema_version = $schema_version, g.node_count = $node_count, "
            "g.edge_count = $edge_count, g.checksum = $checksum",
            **metadata,
        ).consume()
        if nodes:
            tx.run(
                "MATCH (g:Graph {id: $graph_id}) "
                "UNWIND $nodes AS item "
                "CREATE (n:KnowledgeNode {"
                "graph_id: $graph_id, node_id: item.node_id, position: item.position, "
                "global_id: item.global_id, node_type: item.node_type, "
                "title_zh: item.title_zh, title_en: item.title_en, label: item.label, "
                "payload_json: item.payload_json}) "
                "CREATE (g)-[:CONTAINS {position: item.position}]->(n)",
                graph_id=metadata["graph_id"],
                nodes=nodes,
            ).consume()
        if edges:
            tx.run(
                "UNWIND $edges AS item "
                "MATCH (a:KnowledgeNode {graph_id: $graph_id, node_id: item.from_id}) "
                "MATCH (b:KnowledgeNode {graph_id: $graph_id, node_id: item.to_id}) "
                "CREATE (a)-[r:RELATES_TO {"
                "graph_id: $graph_id, edge_index: item.edge_index, label: item.label, "
                "strength: item.strength, description: item.description, "
                "payload_json: item.payload_json}]->(b)",
                graph_id=metadata["graph_id"],
                edges=edges,
            ).consume()

    def get_graph(self, graph_id: str) -> dict[str, Any] | None:
        with self.driver.session(database=self.database) as session:
            graph = session.run(
                "MATCH (g:Graph {id: $graph_id}) RETURN g",
                graph_id=graph_id,
            ).single()
            if not graph:
                return None
            node_rows = session.run(
                "MATCH (:Graph {id: $graph_id})-[c:CONTAINS]->(n:KnowledgeNode) "
                "RETURN n.payload_json AS payload ORDER BY c.position ASC",
                graph_id=graph_id,
            )
            edge_rows = session.run(
                "MATCH (:KnowledgeNode {graph_id: $graph_id})"
                "-[r:RELATES_TO {graph_id: $graph_id}]->"
                "(:KnowledgeNode {graph_id: $graph_id}) "
                "RETURN r.payload_json AS payload ORDER BY r.edge_index ASC",
                graph_id=graph_id,
            )
            nodes = [json.loads(row["payload"]) for row in node_rows]
            edges = [json.loads(row["payload"]) for row in edge_rows]
            props = dict(graph["g"])
        return {
            "graph_id": graph_id,
            "kind": props.get("kind"),
            "revision": int(props.get("revision") or 0),
            "schema_version": int(props.get("schema_version") or 1),
            "node_count": int(props.get("node_count") or 0),
            "edge_count": int(props.get("edge_count") or 0),
            "checksum": props.get("checksum") or "",
            "nodes": nodes,
            "edges": edges,
        }

    def verify_graph(self, graph_id: str) -> dict[str, Any]:
        graph = self.get_graph(graph_id)
        if not graph:
            return {"ok": False, "graph_id": graph_id, "error": "not_found"}
        checksum = graph_checksum(graph["nodes"], graph["edges"])
        ok = (
            graph["node_count"] == len(graph["nodes"])
            and graph["edge_count"] == len(graph["edges"])
            and graph["checksum"] == checksum
        )
        return {
            "ok": ok,
            "graph_id": graph_id,
            "revision": graph["revision"],
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
            "checksum": checksum,
        }

    def delete_graph(self, graph_id: str) -> None:
        with self.driver.session(database=self.database) as session:
            session.execute_write(self._delete_graph_tx, graph_id)

    @staticmethod
    def _delete_graph_tx(tx, graph_id: str) -> None:
        tx.run(
            "MATCH (n:KnowledgeNode {graph_id: $graph_id}) DETACH DELETE n",
            graph_id=graph_id,
        ).consume()
        tx.run(
            "MATCH (g:Graph {id: $graph_id}) DETACH DELETE g",
            graph_id=graph_id,
        ).consume()

    @staticmethod
    def _validate_payload(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise GraphStoreError("nodes and edges must be lists")
        node_ids: set[int] = set()
        normalized_nodes: list[dict] = []
        for position, raw in enumerate(nodes):
            if not isinstance(raw, dict):
                raise GraphStoreError(f"node {position} is not an object")
            node_id = raw.get("id")
            if isinstance(node_id, bool) or not isinstance(node_id, int):
                raise GraphStoreError(f"node {position} has a non-integer id")
            if node_id in node_ids:
                raise GraphStoreError(f"duplicate node id: {node_id}")
            node_ids.add(node_id)
            normalized_nodes.append(dict(raw))
        normalized_edges: list[dict] = []
        for position, raw in enumerate(edges):
            if not isinstance(raw, dict):
                raise GraphStoreError(f"edge {position} is not an object")
            from_id, to_id = raw.get("from"), raw.get("to")
            if from_id not in node_ids or to_id not in node_ids:
                raise GraphStoreError(f"edge {position} references a missing endpoint")
            normalized_edges.append(dict(raw))
        return normalized_nodes, normalized_edges

    @staticmethod
    def _node_row(graph_id: str, position: int, node: dict) -> dict[str, Any]:
        return {
            "graph_id": graph_id,
            "node_id": int(node["id"]),
            "position": position,
            "global_id": str(node.get("global_id") or ""),
            "node_type": str(node.get("node_type") or ""),
            "title_zh": str(node.get("title_zh") or ""),
            "title_en": str(node.get("title_en") or ""),
            "label": str(node.get("label") or ""),
            "payload_json": json.dumps(node, ensure_ascii=False, separators=(",", ":")),
        }

    @staticmethod
    def _edge_row(graph_id: str, edge_index: int, edge: dict) -> dict[str, Any]:
        strength = edge.get("strength")
        if isinstance(strength, bool) or not isinstance(strength, (int, float)):
            strength = 0
        return {
            "graph_id": graph_id,
            "edge_index": edge_index,
            "from_id": int(edge["from"]),
            "to_id": int(edge["to"]),
            "label": str(edge.get("label") or ""),
            "strength": float(strength),
            "description": str(edge.get("description") or ""),
            "payload_json": json.dumps(edge, ensure_ascii=False, separators=(",", ":")),
        }

    # Compatibility for the inactive legacy server surface.
    def create_graph(self, nodes, edges, pdf_name=""):
        graph_id = str(uuid.uuid4())
        result = self.replace_graph(graph_id, "legacy", nodes, edges, revision=1)
        return {
            "graph_id": graph_id,
            "nodes_created": result["node_count"],
            "edges_created": result["edge_count"],
        }


GraphStore = Neo4jHandler
_GRAPH_STORE: GraphStore | None = None
_GRAPH_STORE_LOCK = threading.Lock()


def get_graph_store() -> GraphStore:
    global _GRAPH_STORE
    with _GRAPH_STORE_LOCK:
        if _GRAPH_STORE is None:
            _GRAPH_STORE = GraphStore.from_environment()
        return _GRAPH_STORE


def reset_graph_store() -> None:
    global _GRAPH_STORE
    with _GRAPH_STORE_LOCK:
        if _GRAPH_STORE is not None:
            _GRAPH_STORE.close()
        _GRAPH_STORE = None
