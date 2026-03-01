"""MCP server for local semantic code search."""

__all__ = [
    "CacheManager",
    "generate_collection_name",
    "Chunker",
    "Chunk",
    "should_index_file",
    "is_path_ignored",
    "Indexer",
    "IndexResult",
    "index_codebase",
]


def __getattr__(name):
    """Lazy import to avoid loading dependencies until needed."""
    if name in ("CacheManager", "generate_collection_name"):
        from .cache import CacheManager, generate_collection_name
        return CacheManager if name == "CacheManager" else generate_collection_name

    if name in ("Chunker", "Chunk", "should_index_file", "is_path_ignored"):
        from .chunker import Chunker, Chunk, should_index_file, is_path_ignored
        return {"Chunker": Chunker, "Chunk": Chunk, "should_index_file": should_index_file, "is_path_ignored": is_path_ignored}[name]

    if name in ("Indexer", "IndexResult", "index_codebase"):
        from .indexer import Indexer, IndexResult, index_codebase
        return {"Indexer": Indexer, "IndexResult": IndexResult, "index_codebase": index_codebase}[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
