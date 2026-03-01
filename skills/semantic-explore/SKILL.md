---
name: semantic-explore
description: >-
  This skill should be used when the user asks to understand code behavior,
  explore how something works, find where functionality is implemented, or
  asks questions like "how does X work", "where is Y handled", "what does Z do",
  "show me the code that", "find the implementation of", or "trace the flow of".
  Also use for exploring unfamiliar codebases or when grep/glob returns too many
  irrelevant results. Use BEFORE or ALONGSIDE keyword search.
---

# Semantic Code Exploration

Search code by meaning rather than keywords using semantic embeddings.

## Workflow

### 1. Ensure Index Exists

Before searching, verify the codebase is indexed:

```
list_collections()
```

If the current working directory is not indexed:

```
index_codebase(directory="/absolute/path/to/project")
```

### 2. Search Semantically

Use complete behavioral questions, not keywords:

```
semantic_search(
  query="How does the system handle user authentication?",
  collection="collection-name"
)
```

To get only file locations without code chunks:

```
semantic_search(
  query="Where is authentication implemented?",
  collection="collection-name",
  include_code=False
)
```

### 3. Read Results

Review top results by score (>0.7 = strong match). Use file paths and line numbers to read relevant code.

## Query Patterns

| Instead of | Use |
|------------|-----|
| "auth handler" | "How does the system authenticate users?" |
| "error" | "What happens when a request fails?" |
| "database" | "How does the application connect to and query the database?" |

## Score Interpretation

- **>0.7**: Strong match - read this code
- **0.5-0.7**: Moderate - review for relevance
- **<0.5**: Weak - refine query or use grep instead

## When to Use Semantic vs Grep

| Semantic Search | Grep/Glob |
|-----------------|-----------|
| "How does X work?" | Known symbol names |
| Behavior/intent questions | Specific strings |
| Exploring unfamiliar code | File patterns |

## Collection Identification

Match collection to current project:
- Check `list_collections()` output
- Collection names often include project directory name or use `ws-{hash}` pattern
- Use `get_collection_info(collection)` to verify point count

## Requirements

Services must be running:
- Qdrant: `localhost:6333`
- Embeddings: `localhost:1335`
