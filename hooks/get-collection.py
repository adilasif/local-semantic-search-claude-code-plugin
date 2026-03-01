#!/usr/bin/env python3
"""Check if a directory has an indexed collection.

Outputs the collection name if the directory is indexed in Qdrant, otherwise outputs nothing.
Uses only stdlib for portability (no venv needed).
"""

import hashlib
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")


def generate_collection_name(directory: Path) -> str:
    """Generate collection name from directory path (ws-{sha256(path)[:16]})."""
    path_str = str(directory.resolve())
    hash_hex = hashlib.sha256(path_str.encode()).hexdigest()[:16]
    return f"ws-{hash_hex}"


def collection_exists(collection: str) -> bool:
    """Check if collection exists in Qdrant."""
    try:
        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{collection}",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


def main() -> int:
    if len(sys.argv) < 2:
        return 1

    directory = Path(sys.argv[1]).resolve()
    if not directory.is_dir():
        return 1

    collection = generate_collection_name(directory)

    if collection_exists(collection):
        print(collection)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
