"""Production storage boundaries for MathWeaver.

Submodules are intentionally not imported eagerly so migration dry-runs can
inspect SQLite sources before MySQL/Neo4j client packages are loaded.
"""

__all__ = [
    "DatabaseConnection",
    "GraphStore",
    "GraphStoreError",
    "IntegrityError",
    "connect_database",
    "get_database",
    "get_graph_store",
]


def __getattr__(name):
    if name in {"DatabaseConnection", "IntegrityError", "connect_database", "get_database"}:
        from . import database

        return getattr(database, name)
    if name in {"GraphStore", "GraphStoreError", "get_graph_store"}:
        from integrations import neo4j_handler

        return getattr(neo4j_handler, name)
    raise AttributeError(name)
