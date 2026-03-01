---
name: codebase-onboard
description: >-
  This skill should be used when the user is new to a codebase and wants
  to understand its structure, architecture, or main components. Trigger
  phrases include "help me understand this codebase", "I'm new to this project",
  "give me an overview", "what are the main components", "how is this structured",
  or "onboard me". Use for initial exploration, not for specific questions.
---

# Codebase Onboarding

Use semantic search to systematically explore and understand a new codebase.

## Onboarding Workflow

### 1. Index the Codebase

```
index_codebase(directory="/path/to/project")
```

Note the collection name for subsequent searches.

### 2. Discover Architecture

Run these semantic queries to map the codebase:

| Query | Purpose |
|-------|---------|
| "Where is the main entry point?" | Find application start |
| "How is the application initialized?" | Understand bootstrap |
| "What are the core data models?" | Find domain objects |
| "How does routing work?" | Find request handling |
| "Where is configuration loaded?" | Find settings/env handling |

### 3. Identify Patterns

| Query | Purpose |
|-------|---------|
| "How are errors handled?" | Error handling patterns |
| "How is logging done?" | Logging conventions |
| "How are dependencies injected?" | DI patterns |
| "How are tests structured?" | Testing patterns |

### 4. Summarize Findings

After exploration:
- List key entry points and their purposes
- Describe the main architectural layers
- Note unusual patterns or conventions
- Identify areas needing more exploration

## When to Use

- First time working with a codebase
- After significant refactoring
- When documentation is sparse or outdated

## When NOT to Use

- For specific questions (use semantic-explore)
- For finding exact symbol names (use grep)
