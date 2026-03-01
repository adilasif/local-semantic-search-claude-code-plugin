"""File watching with debouncing for continuous indexing.

Matches Roo Code's FileWatcher pattern:
- 500ms debounce delay
- Event accumulation before batch processing
- Concurrent file processing (10 files)
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Awaitable

from watchfiles import awatch, Change

# Import from parent package
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from mcp_server.cache import generate_collection_name
from mcp_server.chunker import should_index_file, is_path_ignored
from mcp_server.indexer import Indexer

# Constants (from Roo Code)
BATCH_DEBOUNCE_DELAY_MS = 500
FILE_PROCESSING_CONCURRENCY = 10


class FileChangeType(Enum):
    """Type of file change."""
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class FileChange:
    """Represents a file change event."""
    path: Path
    change_type: FileChangeType
    timestamp: float = field(default_factory=lambda: asyncio.get_event_loop().time())


class DebouncedWatcher:
    """Watches directory for changes with debouncing."""

    def __init__(
        self,
        directory: Path,
        collection: str | None = None,
        debounce_ms: int = BATCH_DEBOUNCE_DELAY_MS,
        on_batch_start: Callable[[int], Awaitable[None]] | None = None,
        on_file_indexed: Callable[[Path, int], Awaitable[None]] | None = None,
        on_batch_complete: Callable[[int, int, int], Awaitable[None]] | None = None,
    ):
        """Initialize watcher.

        Args:
            directory: Directory to watch
            collection: Collection name (auto-generated if not provided)
            debounce_ms: Debounce delay in milliseconds
            on_batch_start: Callback when batch processing starts (file_count)
            on_file_indexed: Callback when file is indexed (path, chunks)
            on_batch_complete: Callback when batch completes (processed, skipped, failed)
        """
        self.directory = directory.resolve()
        self.collection = collection or generate_collection_name(self.directory)
        self.debounce_ms = debounce_ms
        self.on_batch_start = on_batch_start
        self.on_file_indexed = on_file_indexed
        self.on_batch_complete = on_batch_complete

        self._pending_changes: dict[str, FileChange] = {}
        self._debounce_task: asyncio.Task | None = None
        self._indexer: Indexer | None = None
        self._running = False

    async def _get_indexer(self) -> Indexer:
        """Get or create indexer."""
        if self._indexer is None:
            self._indexer = Indexer()
        return self._indexer

    def _map_change_type(self, change: Change) -> FileChangeType:
        """Map watchfiles Change to FileChangeType."""
        if change == Change.added:
            return FileChangeType.ADDED
        elif change == Change.deleted:
            return FileChangeType.DELETED
        else:
            return FileChangeType.MODIFIED

    async def _process_batch(self) -> None:
        """Process accumulated file changes."""
        if not self._pending_changes:
            return

        # Take snapshot and clear pending
        changes = dict(self._pending_changes)
        self._pending_changes.clear()

        # Filter to indexable files
        indexable = []
        deletions = []

        for path_str, change in changes.items():
            path = change.path

            if change.change_type == FileChangeType.DELETED:
                deletions.append(path)
            elif should_index_file(path):
                indexable.append(path)

        total_files = len(indexable) + len(deletions)
        if total_files == 0:
            return

        if self.on_batch_start:
            await self.on_batch_start(total_files)

        indexer = await self._get_indexer()
        processed = 0
        skipped = 0
        failed = 0

        # Handle deletions
        for path in deletions:
            try:
                await indexer.delete_file_chunks(path, self.collection)
                indexer.cache.remove_file(path, self.collection)
                processed += 1
            except Exception:
                failed += 1

        # Process indexable files with concurrency limit
        semaphore = asyncio.Semaphore(FILE_PROCESSING_CONCURRENCY)

        async def process_file(path: Path) -> tuple[bool, int]:
            async with semaphore:
                try:
                    result = await indexer.index_file(path, self.collection)
                    if result.files_processed > 0:
                        if self.on_file_indexed:
                            await self.on_file_indexed(path, result.chunks_created)
                        return (True, result.chunks_created)
                    elif result.files_skipped > 0:
                        return (None, 0)  # Skipped
                    else:
                        return (False, 0)  # Failed
                except Exception:
                    return (False, 0)

        tasks = [process_file(p) for p in indexable]
        results = await asyncio.gather(*tasks)

        for success, chunks in results:
            if success is True:
                processed += 1
            elif success is None:
                skipped += 1
            else:
                failed += 1

        if self.on_batch_complete:
            await self.on_batch_complete(processed, skipped, failed)

    async def _schedule_batch(self) -> None:
        """Schedule batch processing after debounce delay."""
        await asyncio.sleep(self.debounce_ms / 1000)
        await self._process_batch()

    def _accumulate_change(self, path: Path, change_type: FileChangeType) -> None:
        """Accumulate a file change event."""
        # Skip ignored paths
        if is_path_ignored(path):
            return

        path_str = str(path)
        self._pending_changes[path_str] = FileChange(path, change_type)

        # Reset debounce timer
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        self._debounce_task = asyncio.create_task(self._schedule_batch())

    async def watch(self) -> None:
        """Start watching for file changes."""
        self._running = True

        # Ensure collection exists
        indexer = await self._get_indexer()
        await indexer.ensure_collection(self.collection)

        try:
            async for changes in awatch(
                self.directory,
                recursive=True,
                step=100,  # Check every 100ms
            ):
                if not self._running:
                    break

                for change_type, path_str in changes:
                    path = Path(path_str)
                    file_change_type = self._map_change_type(change_type)
                    self._accumulate_change(path, file_change_type)

        finally:
            # Process any remaining changes
            if self._pending_changes:
                await self._process_batch()

            if self._indexer:
                await self._indexer.close()

    def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()


async def watch_directory(
    directory: Path,
    collection: str | None = None,
    verbose: bool = False,
) -> None:
    """Watch a directory for changes and index continuously.

    Args:
        directory: Directory to watch
        collection: Collection name (auto-generated if not provided)
        verbose: If True, print detailed progress
    """
    directory = Path(directory).resolve()
    collection = collection or generate_collection_name(directory)

    print(f"Watching: {directory}")
    print(f"Collection: {collection}")
    print("Press Ctrl+C to stop\n")

    async def on_batch_start(count: int) -> None:
        if verbose:
            print(f"Processing {count} file(s)...")

    async def on_file_indexed(path: Path, chunks: int) -> None:
        if verbose:
            rel_path = path.relative_to(directory) if path.is_relative_to(directory) else path
            print(f"  Indexed: {rel_path} ({chunks} chunks)")

    async def on_batch_complete(processed: int, skipped: int, failed: int) -> None:
        print(f"Batch complete: {processed} indexed, {skipped} skipped, {failed} failed")

    watcher = DebouncedWatcher(
        directory,
        collection=collection,
        on_batch_start=on_batch_start,
        on_file_indexed=on_file_indexed,
        on_batch_complete=on_batch_complete,
    )

    try:
        await watcher.watch()
    except KeyboardInterrupt:
        print("\nStopping watcher...")
        watcher.stop()
