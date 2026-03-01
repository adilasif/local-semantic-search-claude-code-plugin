#!/usr/bin/env python3
"""CLI for local-semantic-search codebase indexer.

Commands:
    index   - Index a codebase
    watch   - Watch and continuously index changes
    status  - Get indexing status
    delete  - Delete a collection
"""

import asyncio
from pathlib import Path
from typing import Optional

import typer

# Import from parent package
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from mcp_server.cache import generate_collection_name, CacheManager
from mcp_server.indexer import Indexer, index_codebase

app = typer.Typer(
    name="codebase-index",
    help="Index codebases for local semantic search.",
    no_args_is_help=True,
)


def get_collection_name(directory: Path, collection: Optional[str] = None) -> str:
    """Get collection name, auto-generating if not provided."""
    if collection:
        return collection
    return generate_collection_name(directory.resolve())


@app.command()
def index(
    directory: Path = typer.Argument(
        ...,
        help="Directory to index",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    collection: Optional[str] = typer.Option(
        None,
        "--collection", "-c",
        help="Collection name (auto-generated from path hash if not provided)",
    ),
    force: bool = typer.Option(
        False,
        "--force", "-f",
        help="Re-index all files even if unchanged",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Show detailed progress",
    ),
) -> None:
    """Index a codebase for semantic search.

    Indexes all supported code files in DIRECTORY, creating embeddings
    and storing them in Qdrant. Supports incremental updates - only
    changed files are re-indexed unless --force is used.

    Example:
        codebase-index index /path/to/project
        codebase-index index . --collection my-project
    """
    collection_name = get_collection_name(directory, collection)

    typer.echo(f"Indexing: {directory}")
    typer.echo(f"Collection: {collection_name}")

    if force:
        typer.echo("Mode: Full re-index (--force)")
    else:
        typer.echo("Mode: Incremental (only changed files)")

    typer.echo()

    async def run_index():
        try:
            result = await index_codebase(directory, collection=collection_name, force=force)

            typer.echo("Indexing complete!")
            typer.echo(f"  Files processed: {result.files_processed}")
            typer.echo(f"  Files skipped:   {result.files_skipped}")
            typer.echo(f"  Files failed:    {result.files_failed}")
            typer.echo(f"  Chunks created:  {result.chunks_created}")

            if result.errors:
                typer.echo()
                typer.echo("Errors:")
                for error in result.errors[:10]:
                    typer.echo(f"  - {error}")
                if len(result.errors) > 10:
                    typer.echo(f"  ... and {len(result.errors) - 10} more")

            return result.files_failed == 0

        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

    success = asyncio.run(run_index())
    if not success:
        raise typer.Exit(1)


@app.command()
def watch(
    directory: Path = typer.Argument(
        ...,
        help="Directory to watch",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    collection: Optional[str] = typer.Option(
        None,
        "--collection", "-c",
        help="Collection name (auto-generated from path hash if not provided)",
    ),
    initial_index: bool = typer.Option(
        True,
        "--initial-index/--no-initial-index",
        help="Run initial index before watching",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Show detailed progress",
    ),
) -> None:
    """Watch a directory and continuously index changes.

    Monitors DIRECTORY for file changes and automatically re-indexes
    modified files. Uses 500ms debouncing to batch rapid changes.

    Example:
        codebase-index watch /path/to/project
        codebase-index watch . --no-initial-index
    """
    from .watcher import watch_directory

    collection_name = get_collection_name(directory, collection)

    async def run_watch():
        # Initial index if requested
        if initial_index:
            typer.echo("Running initial index...")
            result = await index_codebase(directory, collection=collection_name)
            typer.echo(f"Initial index: {result.files_processed} files, {result.chunks_created} chunks")
            typer.echo()

        # Start watching
        await watch_directory(directory, collection=collection_name, verbose=verbose)

    try:
        asyncio.run(run_watch())
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def status(
    directory: Path = typer.Argument(
        ...,
        help="Directory to check status for",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    collection: Optional[str] = typer.Option(
        None,
        "--collection", "-c",
        help="Collection name (auto-generated from path hash if not provided)",
    ),
) -> None:
    """Get indexing status for a codebase.

    Shows collection statistics including point count, cached files,
    and collection status.

    Example:
        codebase-index status /path/to/project
    """
    collection_name = get_collection_name(directory, collection)

    async def run_status():
        indexer = Indexer()
        try:
            status = await indexer.get_index_status(collection_name)

            typer.echo(f"Directory:    {directory}")
            typer.echo(f"Collection:   {collection_name}")
            typer.echo()

            if status.get("status") == "not_found":
                typer.echo("Status: Not indexed")
                typer.echo("Run 'codebase-index index' to create the index.")
            else:
                typer.echo(f"Status:       {status.get('status', 'unknown')}")
                typer.echo(f"Points:       {status.get('points_count', 0)}")
                typer.echo(f"Vectors:      {status.get('vectors_count', 0)}")
                typer.echo(f"Cached files: {status.get('cached_files', 0)}")

        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)
        finally:
            await indexer.close()

    asyncio.run(run_status())


@app.command()
def delete(
    directory: Path = typer.Argument(
        ...,
        help="Directory whose collection to delete",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    collection: Optional[str] = typer.Option(
        None,
        "--collection", "-c",
        help="Collection name (auto-generated from path hash if not provided)",
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Confirm deletion (required)",
    ),
) -> None:
    """Delete an indexed collection.

    Permanently removes all indexed data for the collection.
    Requires --confirm flag as a safety measure.

    Example:
        codebase-index delete /path/to/project --confirm
    """
    collection_name = get_collection_name(directory, collection)

    if not confirm:
        typer.echo(f"This will delete collection: {collection_name}")
        typer.echo("Use --confirm to proceed.")
        raise typer.Exit(1)

    async def run_delete():
        indexer = Indexer()
        try:
            success = await indexer.delete_collection(collection_name)

            if success:
                typer.echo(f"Deleted collection: {collection_name}")
            else:
                typer.echo(f"Failed to delete collection: {collection_name}")
                typer.echo("Collection may not exist.")
                raise typer.Exit(1)

        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)
        finally:
            await indexer.close()

    asyncio.run(run_delete())


@app.command()
def collections() -> None:
    """List all indexed collections.

    Example:
        codebase-index collections
    """
    import httpx

    async def run_list():
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("http://localhost:6333/collections")
                response.raise_for_status()
                data = response.json()

            collections = data.get("result", {}).get("collections", [])

            if not collections:
                typer.echo("No collections found.")
                return

            typer.echo("Collections:")
            for coll in collections:
                typer.echo(f"  - {coll['name']}")

        except httpx.ConnectError:
            typer.echo("Error: Cannot connect to Qdrant on localhost:6333.", err=True)
            typer.echo("Ensure Qdrant is running. See plugin README for setup.", err=True)
            raise typer.Exit(1)
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

    asyncio.run(run_list())


if __name__ == "__main__":
    app()
