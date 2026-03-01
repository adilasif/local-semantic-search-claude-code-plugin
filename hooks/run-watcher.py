#!/usr/bin/env python3
"""Run the debounced file watcher for automatic index updates.

This script wraps the DebouncedWatcher and adds:
- SIGUSR1 handling for immediate flush of pending changes
- SIGTERM/SIGINT handling for graceful shutdown
- Logging to a temp file for debugging
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

# Add parent directories to path for imports
SCRIPT_DIR = Path(__file__).parent.resolve()
PLUGIN_ROOT = SCRIPT_DIR.parent  # hooks/ is directly under plugin root
sys.path.insert(0, str(PLUGIN_ROOT / "cli"))
sys.path.insert(0, str(PLUGIN_ROOT / "mcp_server"))

from watcher import DebouncedWatcher

# Set up logging
LOG_DIR = Path("/tmp/semantic-watcher")
LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging(directory: Path) -> logging.Logger:
    """Set up logging to file."""
    # Use directory hash for unique log file
    import hashlib
    dir_hash = hashlib.sha256(str(directory).encode()).hexdigest()[:16]
    log_file = LOG_DIR / f"{dir_hash}.log"

    logger = logging.getLogger("semantic-watcher")
    logger.setLevel(logging.INFO)

    # Rotate log if too large (>1MB)
    if log_file.exists() and log_file.stat().st_size > 1_000_000:
        log_file.unlink()

    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)

    return logger


class FlushableWatcher(DebouncedWatcher):
    """DebouncedWatcher that can be signaled to flush immediately."""

    def __init__(self, *args, logger: logging.Logger | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._logger = logger
        self._flush_requested = False
        self._shutdown_requested = False

    def request_flush(self) -> None:
        """Request immediate flush of pending changes."""
        self._flush_requested = True
        if self._logger:
            self._logger.info("Flush requested")

        # Cancel debounce timer if running
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

    def request_shutdown(self) -> None:
        """Request graceful shutdown."""
        self._shutdown_requested = True
        self._running = False
        self.request_flush()

    async def watch(self) -> None:
        """Start watching with flush support."""
        self._running = True

        # Ensure collection exists
        indexer = await self._get_indexer()
        await indexer.ensure_collection(self.collection)

        if self._logger:
            self._logger.info(f"Started watching: {self.directory}")
            self._logger.info(f"Collection: {self.collection}")

        try:
            from watchfiles import awatch

            async for changes in awatch(
                self.directory,
                recursive=True,
                step=100,  # Check every 100ms
            ):
                if self._shutdown_requested:
                    break

                for change_type, path_str in changes:
                    path = Path(path_str)
                    file_change_type = self._map_change_type(change_type)
                    self._accumulate_change(path, file_change_type)

                # Check if flush was requested during processing
                if self._flush_requested:
                    self._flush_requested = False
                    if self._pending_changes:
                        await self._process_batch()

                if not self._running:
                    break

        except asyncio.CancelledError:
            if self._logger:
                self._logger.info("Watch cancelled")
        finally:
            # Process any remaining changes
            if self._pending_changes:
                if self._logger:
                    self._logger.info(f"Processing {len(self._pending_changes)} remaining changes")
                await self._process_batch()

            if self._indexer:
                await self._indexer.close()

            if self._logger:
                self._logger.info("Watcher stopped")


# Global watcher reference for signal handlers
_watcher: FlushableWatcher | None = None
_loop: asyncio.AbstractEventLoop | None = None
_logger: logging.Logger | None = None


def handle_flush_signal(signum, frame):
    """Handle SIGUSR1 - flush pending changes immediately."""
    global _watcher, _loop, _logger
    if _logger:
        _logger.info(f"Received signal {signum}")
    if _watcher and _loop:
        _loop.call_soon_threadsafe(_watcher.request_flush)


def handle_shutdown_signal(signum, frame):
    """Handle SIGTERM/SIGINT - graceful shutdown."""
    global _watcher, _loop, _logger
    if _logger:
        _logger.info(f"Received shutdown signal {signum}")
    if _watcher and _loop:
        _loop.call_soon_threadsafe(_watcher.request_shutdown)


async def run_watcher(directory: Path, collection: str) -> None:
    """Run the file watcher."""
    global _watcher, _loop, _logger

    _logger = setup_logging(directory)
    _loop = asyncio.get_running_loop()

    # Set up callbacks
    async def on_batch_start(count: int) -> None:
        if _logger:
            _logger.info(f"Processing batch of {count} file(s)")

    async def on_file_indexed(path: Path, chunks: int) -> None:
        if _logger:
            rel_path = path.relative_to(directory) if path.is_relative_to(directory) else path
            _logger.info(f"Indexed: {rel_path} ({chunks} chunks)")

    async def on_batch_complete(processed: int, skipped: int, failed: int) -> None:
        if _logger:
            _logger.info(f"Batch complete: {processed} indexed, {skipped} skipped, {failed} failed")

    _watcher = FlushableWatcher(
        directory,
        collection=collection,
        logger=_logger,
        on_batch_start=on_batch_start,
        on_file_indexed=on_file_indexed,
        on_batch_complete=on_batch_complete,
    )

    # Set up signal handlers
    signal.signal(signal.SIGUSR1, handle_flush_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    await _watcher.watch()


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: run-watcher.py <directory> <collection>", file=sys.stderr)
        return 1

    directory = Path(sys.argv[1]).resolve()
    collection = sys.argv[2]

    if not directory.is_dir():
        print(f"Error: {directory} is not a directory", file=sys.stderr)
        return 1

    # Write PID to file for signal sending
    pid_hash = os.popen(f"echo -n '{directory}' | sha256sum | cut -c1-16").read().strip()
    pid_file = LOG_DIR / f"{pid_hash}.pid"
    pid_file.write_text(str(os.getpid()))

    try:
        asyncio.run(run_watcher(directory, collection))
    except KeyboardInterrupt:
        pass
    finally:
        # Clean up PID file
        if pid_file.exists():
            pid_file.unlink()

    return 0


if __name__ == "__main__":
    sys.exit(main())
