---
name: index-manage
description: >-
  This skill should be used when the user wants to create, update, delete,
  or troubleshoot semantic search indexes. Trigger phrases include "index this
  codebase", "update the index", "refresh the index", "delete the collection",
  "check index status", "semantic search isn't working", or "why isn't it finding".
  Use for index administration, not for searching.
---

# Index Management

Create, update, and troubleshoot semantic search indexes.

## Create Index

Index a new codebase for semantic search:

```
index_codebase(directory="/absolute/path/to/project")
```

Returns collection name and statistics.

## Update Index

### Single File (after editing)

```
reindex_file(
  file_path="/path/to/modified/file.py",
  collection="collection-name"
)
```

### Full Re-index (after major changes)

```
index_codebase(directory="/path/to/project", force=True)
```

## Check Status

### List All Collections

```
list_collections()
```

Returns collection names with their source directory paths.

### Collection Details

```
get_collection_info(collection="collection-name")
```

Returns point count, vector size, status, source directory, and indexing timestamp.

### Indexing Status

```
index_status(collection="collection-name")
```

Shows cached files and whether updates are needed.

## Delete Collection

Permanently remove an index:

```
delete_collection(collection="collection-name", confirm=True)
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Cannot connect to services" | Start Qdrant (port 6333) and embeddings (port 1335) |
| "Collection not found" | Run `list_collections()` to verify name; may need to index |
| Search returns no results | Check collection has points; verify query is behavioral |
| Stale results | Run `reindex_file` or `index_codebase(force=True)` |

## Service Health Check

Verify services are running:
- Qdrant: `http://localhost:6333/collections`
- Embeddings: `http://localhost:1335/v1/embeddings`

## When to Use

- Setting up semantic search for a new project
- After bulk file changes (refactoring, merging)
- When search results seem outdated
- Troubleshooting search issues
