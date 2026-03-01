#!/usr/bin/env python3
"""MCP server for local semantic code search with indexing capabilities."""

from pathlib import Path

import os

import httpx
from mcp.server.fastmcp import FastMCP

from cache import generate_collection_name
from indexer import Indexer, index_codebase as _index_codebase

# Configuration - override via environment for remote access
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
EMBEDDING_URL = os.environ.get("EMBEDDING_URL", "http://localhost:1335")

mcp = FastMCP("local-semantic-search")

# Shared indexer instance
_indexer: Indexer | None = None


def get_indexer() -> Indexer:
    """Get or create shared indexer instance."""
    global _indexer
    if _indexer is None:
        _indexer = Indexer()
    return _indexer


async def get_embedding(text: str) -> list[float]:
    """Generate embedding for query text."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{EMBEDDING_URL}/v1/embeddings",
            json={"input": text},
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]


@mcp.tool()
async def semantic_search(
    query: str,
    collection: str,
    limit: int = 10,
    include_code: bool = True,
) -> dict:
    """Search indexed codebase semantically.

    Args:
        query: Natural language query describing the code behavior you're looking for.
               Use complete sentences like "How does the system handle authentication?"
               rather than keywords like "auth handler".
        collection: Name of the indexed codebase collection to search.
        limit: Maximum number of results to return (default: 10).
        include_code: Whether to include code snippets in results (default: True).
                      Set to False when you only need file locations.

    Returns:
        Search results with file paths, line numbers, and optionally code chunks.
    """
    try:
        # Generate query embedding
        embedding = await get_embedding(query)

        # Search Qdrant
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{QDRANT_URL}/collections/{collection}/points/search",
                json={
                    "vector": embedding,
                    "limit": limit,
                    "with_payload": True,
                },
            )
            response.raise_for_status()
            data = response.json()

        # Format results
        results = []
        for point in data.get("result", []):
            payload = point.get("payload", {})
            result = {
                "score": round(point.get("score", 0), 4),
                "file_path": payload.get("filePath", payload.get("file_path", "unknown")),
                "start_line": payload.get("startLine", payload.get("start_line", 0)),
                "end_line": payload.get("endLine", payload.get("end_line", 0)),
            }
            if include_code:
                result["code_chunk"] = payload.get("codeChunk", payload.get("code_chunk", ""))
            results.append(result)

        return {"results": results, "query": query, "collection": collection}

    except httpx.ConnectError:
        return {
            "error": "Cannot connect to semantic search services (Qdrant on :6333 or embeddings on :1335).",
            "hint": "Ensure Qdrant and embeddings services are running. See plugin README for setup.",
        }
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP error: {e.response.status_code}", "detail": str(e)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_collections() -> dict:
    """List available indexed codebase collections.

    Returns:
        List of collections with names and source directories.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{QDRANT_URL}/collections")
            response.raise_for_status()
            data = response.json()

            collections = []
            for c in data.get("result", {}).get("collections", []):
                name = c["name"]
                # Fetch collection details to get metadata
                try:
                    detail_resp = await client.get(f"{QDRANT_URL}/collections/{name}")
                    if detail_resp.status_code == 200:
                        detail = detail_resp.json().get("result", {})
                        metadata = detail.get("config", {}).get("metadata", {})
                        collections.append({
                            "name": name,
                            "directory": metadata.get("directory"),
                        })
                    else:
                        collections.append({"name": name, "directory": None})
                except Exception:
                    collections.append({"name": name, "directory": None})

        return {"collections": collections}

    except httpx.ConnectError:
        return {
            "error": "Cannot connect to Qdrant on localhost:6333.",
            "hint": "Ensure Qdrant is running. See plugin README for setup.",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_collection_info(collection: str) -> dict:
    """Get details about a collection.

    Args:
        collection: Name of the collection to get info for.

    Returns:
        Collection details including point count, vector size, status, and source directory.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{QDRANT_URL}/collections/{collection}")
            response.raise_for_status()
            data = response.json()

        result = data.get("result", {})
        config = result.get("config", {})
        metadata = config.get("metadata", {})

        return {
            "name": collection,
            "points_count": result.get("points_count", 0),
            "vectors_count": result.get("vectors_count", 0),
            "status": result.get("status", "unknown"),
            "vector_size": config.get("params", {}).get("vectors", {}).get("size"),
            "directory": metadata.get("directory"),
            "indexed_at": metadata.get("indexed_at"),
        }

    except httpx.ConnectError:
        return {"error": "Cannot connect to Qdrant."}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"error": f"Collection '{collection}' not found."}
        return {"error": f"HTTP error: {e.response.status_code}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def index_codebase(
    directory: str,
    collection: str | None = None,
    force: bool = False,
) -> dict:
    """Index a codebase for semantic search.

    This indexes all supported code files in the directory, creating embeddings
    and storing them in Qdrant. Use this before semantic_search to make a
    codebase searchable.

    Args:
        directory: Absolute path to the directory to index.
        collection: Optional collection name. If not provided, auto-generates
                   using ws-{hash16} pattern from the directory path.
        force: If True, re-index all files even if unchanged. Default False
               enables incremental indexing (only changed files).

    Returns:
        Indexing statistics including files processed, chunks created, and any errors.
    """
    try:
        path = Path(directory).resolve()

        if not path.exists():
            return {"error": f"Directory not found: {directory}"}

        if not path.is_dir():
            return {"error": f"Path is not a directory: {directory}"}

        # Generate collection name if not provided
        if collection is None:
            collection = generate_collection_name(path)

        # Run indexing
        result = await _index_codebase(path, collection=collection, force=force)

        return {
            "success": True,
            "collection": collection,
            "directory": str(path),
            "files_processed": result.files_processed,
            "files_skipped": result.files_skipped,
            "files_failed": result.files_failed,
            "chunks_created": result.chunks_created,
            "errors": result.errors[:10] if result.errors else [],  # Limit errors shown
        }

    except httpx.ConnectError:
        return {
            "error": "Cannot connect to semantic search services (Qdrant on :6333 or embeddings on :1335).",
            "hint": "Ensure Qdrant and embeddings services are running. See plugin README for setup.",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def reindex_file(
    file_path: str,
    collection: str,
) -> dict:
    """Re-index a single file.

    Use this to update the index after modifying a specific file,
    without re-indexing the entire codebase.

    Args:
        file_path: Absolute path to the file to re-index.
        collection: Name of the collection to update.

    Returns:
        Indexing result for the file.
    """
    try:
        path = Path(file_path).resolve()

        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        if not path.is_file():
            return {"error": f"Path is not a file: {file_path}"}

        indexer = get_indexer()
        result = await indexer.index_file(path, collection, force=True)

        return {
            "success": result.files_failed == 0,
            "file_path": str(path),
            "collection": collection,
            "chunks_created": result.chunks_created,
            "errors": result.errors,
        }

    except httpx.ConnectError:
        return {"error": "Cannot connect to semantic search services."}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def delete_collection(
    collection: str,
    confirm: bool = False,
) -> dict:
    """Delete an indexed collection.

    This permanently removes all indexed data for the collection.

    Args:
        collection: Name of the collection to delete.
        confirm: Must be True to actually delete. Safety measure.

    Returns:
        Deletion result.
    """
    if not confirm:
        return {
            "error": "Deletion not confirmed. Set confirm=True to delete.",
            "collection": collection,
            "warning": "This will permanently delete all indexed data for this collection.",
        }

    try:
        indexer = get_indexer()
        success = await indexer.delete_collection(collection)

        if success:
            return {
                "success": True,
                "message": f"Collection '{collection}' deleted successfully.",
            }
        else:
            return {
                "success": False,
                "error": f"Failed to delete collection '{collection}'. It may not exist.",
            }

    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def index_status(
    collection: str,
) -> dict:
    """Get indexing status for a collection.

    Args:
        collection: Name of the collection to check.

    Returns:
        Status information including point count, cached files, and collection status.
    """
    try:
        indexer = get_indexer()
        status = await indexer.get_index_status(collection)
        return status

    except httpx.ConnectError:
        return {"error": "Cannot connect to semantic search services."}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run()
