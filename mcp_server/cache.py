"""File hash cache for incremental indexing.

Matches Roo Code's CacheManager pattern - stores file hashes to determine
which files need re-indexing.
"""

import hashlib
import json
from pathlib import Path
from typing import Optional


class CacheManager:
    """Manages file hash cache for incremental indexing."""

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize cache manager.

        Args:
            cache_dir: Directory to store cache files. Defaults to ~/.cache/onlylocals-indexer/
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "onlylocals-indexer"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, collection: str) -> Path:
        """Get path to cache file for a collection."""
        return self.cache_dir / f"{collection}.json"

    def _load_cache(self, collection: str) -> dict[str, str]:
        """Load cache from disk."""
        cache_path = self._get_cache_path(collection)
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_cache(self, collection: str, cache: dict[str, str]) -> None:
        """Save cache to disk."""
        cache_path = self._get_cache_path(collection)
        cache_path.write_text(json.dumps(cache, indent=2))

    def get_file_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash of file contents."""
        hasher = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(8192):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except OSError:
            return ""

    def is_file_changed(
        self, file_path: Path, collection: str, current_hash: Optional[str] = None
    ) -> bool:
        """Check if file has changed since last indexing.

        Args:
            file_path: Path to the file
            collection: Collection name
            current_hash: Pre-computed hash (optional, computed if not provided)

        Returns:
            True if file needs re-indexing, False if unchanged
        """
        cache = self._load_cache(collection)
        path_key = str(file_path.resolve())

        if current_hash is None:
            current_hash = self.get_file_hash(file_path)

        cached_hash = cache.get(path_key)
        return cached_hash != current_hash

    def update_file_hash(
        self, file_path: Path, collection: str, file_hash: Optional[str] = None
    ) -> str:
        """Update cached hash for a file.

        Args:
            file_path: Path to the file
            collection: Collection name
            file_hash: Pre-computed hash (optional)

        Returns:
            The file hash that was stored
        """
        cache = self._load_cache(collection)
        path_key = str(file_path.resolve())

        if file_hash is None:
            file_hash = self.get_file_hash(file_path)

        cache[path_key] = file_hash
        self._save_cache(collection, cache)
        return file_hash

    def remove_file(self, file_path: Path, collection: str) -> bool:
        """Remove a file from the cache.

        Args:
            file_path: Path to the file
            collection: Collection name

        Returns:
            True if file was in cache and removed, False otherwise
        """
        cache = self._load_cache(collection)
        path_key = str(file_path.resolve())

        if path_key in cache:
            del cache[path_key]
            self._save_cache(collection, cache)
            return True
        return False

    def get_cached_files(self, collection: str) -> set[str]:
        """Get all file paths in cache for a collection."""
        cache = self._load_cache(collection)
        return set(cache.keys())

    def clear_collection_cache(self, collection: str) -> None:
        """Clear all cached hashes for a collection."""
        cache_path = self._get_cache_path(collection)
        if cache_path.exists():
            cache_path.unlink()

    def get_stats(self, collection: str) -> dict:
        """Get cache statistics for a collection."""
        cache = self._load_cache(collection)
        return {
            "collection": collection,
            "cached_files": len(cache),
            "cache_path": str(self._get_cache_path(collection)),
        }


def generate_collection_name(directory: Path) -> str:
    """Generate collection name from directory path.

    Matches Roo Code's pattern: ws-{sha256(path)[:16]}

    Args:
        directory: Directory path to hash

    Returns:
        Collection name in format ws-{hash16}
    """
    path_str = str(directory.resolve())
    hash_hex = hashlib.sha256(path_str.encode()).hexdigest()[:16]
    return f"ws-{hash_hex}"
