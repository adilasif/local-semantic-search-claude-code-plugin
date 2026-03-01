"""Core indexing logic for semantic code search.

Matches Roo Code's DirectoryScanner pattern with:
- Concurrent file processing (10 files)
- Batch embedding (60 segments per batch)
- UUID v5 for deterministic point IDs
- Incremental updates via file hash comparison
"""

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from cache import CacheManager, generate_collection_name
from chunker import Chunk, Chunker, should_index_file, SUPPORTED_EXTENSIONS

# Configuration - override via environment for remote access
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
EMBEDDING_URL = os.environ.get("EMBEDDING_URL", "http://localhost:1335")

# Concurrency settings (from Roo Code)
FILE_PROCESSING_CONCURRENCY = 10
BATCH_SEGMENT_THRESHOLD = 60
EMBEDDING_BATCH_CONCURRENCY = 10

# Limits
MAX_FILES = 50_000

# UUID namespace for deterministic point IDs
NAMESPACE_UUID = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # DNS namespace


@dataclass
class IndexResult:
    """Result of an indexing operation."""

    files_processed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    chunks_created: int = 0
    chunks_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "IndexResult") -> "IndexResult":
        """Merge another result into this one."""
        return IndexResult(
            files_processed=self.files_processed + other.files_processed,
            files_skipped=self.files_skipped + other.files_skipped,
            files_failed=self.files_failed + other.files_failed,
            chunks_created=self.chunks_created + other.chunks_created,
            chunks_deleted=self.chunks_deleted + other.chunks_deleted,
            errors=self.errors + other.errors,
        )


def generate_point_id(file_path: str, segment_hash: str) -> str:
    """Generate deterministic UUID v5 for a chunk.

    Combines file path and segment hash to create unique, reproducible IDs.
    """
    name = f"{file_path}:{segment_hash}"
    return str(uuid.uuid5(NAMESPACE_UUID, name))


class Indexer:
    """Indexes code files into Qdrant vector database."""

    def __init__(
        self,
        qdrant_url: str = QDRANT_URL,
        embedding_url: str = EMBEDDING_URL,
        cache_manager: Optional[CacheManager] = None,
    ):
        self.qdrant_url = qdrant_url
        self.embedding_url = embedding_url
        self.cache = cache_manager or CacheManager()
        self.chunker = Chunker()
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def ensure_collection(
        self, collection: str, directory: Optional[Path] = None, vector_size: int = 896
    ) -> None:
        """Ensure collection exists in Qdrant with directory metadata.

        Args:
            collection: Collection name
            directory: Source directory path (stored as metadata)
            vector_size: Vector dimension (896 for nomic-embed-text)
        """
        client = await self._get_client()

        # Check if exists
        try:
            response = await client.get(f"{self.qdrant_url}/collections/{collection}")
            if response.status_code == 200:
                # Collection exists - update metadata if directory provided
                if directory:
                    await self._update_collection_metadata(collection, directory)
                return
        except httpx.HTTPError:
            pass

        # Create collection with metadata
        payload: dict = {
            "vectors": {
                "size": vector_size,
                "distance": "Cosine",
            },
        }
        if directory:
            payload["metadata"] = {
                "directory": str(directory),
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            }

        await client.put(
            f"{self.qdrant_url}/collections/{collection}",
            json=payload,
        )

    async def _update_collection_metadata(
        self, collection: str, directory: Path
    ) -> None:
        """Update collection metadata with directory path.

        Args:
            collection: Collection name
            directory: Source directory path
        """
        client = await self._get_client()
        await client.patch(
            f"{self.qdrant_url}/collections/{collection}",
            json={
                "metadata": {
                    "directory": str(directory),
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    async def embed_batch(
        self, texts: list[str], batch_size: int = BATCH_SEGMENT_THRESHOLD
    ) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Args:
            texts: List of text strings to embed
            batch_size: Size of each sub-batch for API calls

        Returns:
            List of embedding vectors
        """
        client = await self._get_client()
        all_embeddings = []

        # Process in sub-batches
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]

            response = await client.post(
                f"{self.embedding_url}/v1/embeddings",
                json={"input": batch},
            )
            response.raise_for_status()
            data = response.json()

            # Extract embeddings in order
            batch_embeddings = [item["embedding"] for item in data["data"]]
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    async def delete_file_chunks(self, file_path: Path, collection: str) -> int:
        """Delete all chunks for a file from collection.

        Args:
            file_path: Path to the file
            collection: Collection name

        Returns:
            Number of chunks deleted
        """
        client = await self._get_client()

        # Delete by filter on filePath
        response = await client.post(
            f"{self.qdrant_url}/collections/{collection}/points/delete",
            json={
                "filter": {
                    "must": [
                        {
                            "key": "filePath",
                            "match": {"value": str(file_path)},
                        }
                    ]
                }
            },
        )

        if response.status_code == 200:
            # Qdrant doesn't return count, estimate from response
            return 1  # Placeholder - actual count unknown
        return 0

    async def index_file(
        self, file_path: Path, collection: str, force: bool = False
    ) -> IndexResult:
        """Index a single file.

        Args:
            file_path: Path to file
            collection: Collection name
            force: If True, re-index even if unchanged

        Returns:
            IndexResult with statistics
        """
        result = IndexResult()

        if not should_index_file(file_path):
            result.files_skipped = 1
            return result

        # Compute file hash
        file_hash = self.cache.get_file_hash(file_path)

        # Check if changed
        if not force and not self.cache.is_file_changed(file_path, collection, file_hash):
            result.files_skipped = 1
            return result

        try:
            # Delete existing chunks for this file
            await self.delete_file_chunks(file_path, collection)

            # Chunk the file
            chunks = self.chunker.chunk_file(file_path, file_hash)

            if not chunks:
                result.files_skipped = 1
                return result

            # Generate embeddings
            texts = [chunk.codeChunk for chunk in chunks]
            embeddings = await self.embed_batch(texts)

            # Prepare points for upsert
            points = []
            for chunk, embedding in zip(chunks, embeddings):
                point_id = generate_point_id(chunk.filePath, chunk.segmentHash)
                points.append({
                    "id": point_id,
                    "vector": embedding,
                    "payload": chunk.to_payload(),
                })

            # Upsert to Qdrant
            client = await self._get_client()
            response = await client.put(
                f"{self.qdrant_url}/collections/{collection}/points",
                json={"points": points},
            )
            response.raise_for_status()

            # Update cache
            self.cache.update_file_hash(file_path, collection, file_hash)

            result.files_processed = 1
            result.chunks_created = len(chunks)

        except Exception as e:
            result.files_failed = 1
            result.errors.append(f"{file_path}: {str(e)}")

        return result

    async def index_directory(
        self,
        directory: Path,
        collection: Optional[str] = None,
        patterns: Optional[set[str]] = None,
        ignore_patterns: Optional[set[str]] = None,
        force: bool = False,
    ) -> IndexResult:
        """Index an entire directory.

        Args:
            directory: Directory to index
            collection: Collection name (auto-generated if not provided)
            patterns: File extensions to include (defaults to SUPPORTED_EXTENSIONS)
            ignore_patterns: Additional patterns to ignore
            force: If True, re-index all files regardless of cache

        Returns:
            IndexResult with statistics
        """
        directory = Path(directory).resolve()

        if collection is None:
            collection = generate_collection_name(directory)

        if patterns is None:
            patterns = SUPPORTED_EXTENSIONS

        # Ensure collection exists with directory metadata
        await self.ensure_collection(collection, directory=directory)

        # Collect files to index
        files_to_index: list[Path] = []
        for pattern in patterns:
            files_to_index.extend(directory.rglob(f"*{pattern}"))

        # Filter
        files_to_index = [
            f for f in files_to_index
            if should_index_file(f) and f.is_file()
        ]

        # Limit
        if len(files_to_index) > MAX_FILES:
            files_to_index = files_to_index[:MAX_FILES]

        # Process files concurrently
        result = IndexResult()
        semaphore = asyncio.Semaphore(FILE_PROCESSING_CONCURRENCY)

        async def process_file(file_path: Path) -> IndexResult:
            async with semaphore:
                return await self.index_file(file_path, collection, force)

        tasks = [process_file(f) for f in files_to_index]
        file_results = await asyncio.gather(*tasks, return_exceptions=True)

        for file_result in file_results:
            if isinstance(file_result, Exception):
                result.files_failed += 1
                result.errors.append(str(file_result))
            else:
                result = result.merge(file_result)

        return result

    async def get_index_status(self, collection: str) -> dict:
        """Get indexing status for a collection.

        Args:
            collection: Collection name

        Returns:
            Status information including point count
        """
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.qdrant_url}/collections/{collection}"
            )
            response.raise_for_status()
            data = response.json()

            result = data.get("result", {})
            cache_stats = self.cache.get_stats(collection)

            return {
                "collection": collection,
                "status": result.get("status", "unknown"),
                "points_count": result.get("points_count", 0),
                "vectors_count": result.get("vectors_count", 0),
                "cached_files": cache_stats["cached_files"],
            }
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"collection": collection, "status": "not_found"}
            raise

    async def delete_collection(self, collection: str) -> bool:
        """Delete a collection and its cache.

        Args:
            collection: Collection name

        Returns:
            True if deleted successfully
        """
        client = await self._get_client()

        try:
            response = await client.delete(
                f"{self.qdrant_url}/collections/{collection}"
            )
            response.raise_for_status()

            # Clear cache
            self.cache.clear_collection_cache(collection)

            return True
        except httpx.HTTPStatusError:
            return False


async def index_codebase(
    directory: str | Path,
    collection: Optional[str] = None,
    force: bool = False,
) -> IndexResult:
    """Convenience function to index a codebase.

    Args:
        directory: Directory to index
        collection: Collection name (auto-generated if not provided)
        force: If True, re-index all files regardless of cache

    Returns:
        IndexResult with statistics
    """
    indexer = Indexer()
    try:
        return await indexer.index_directory(
            Path(directory), collection=collection, force=force
        )
    finally:
        await indexer.close()
