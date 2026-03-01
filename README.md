# Local Semantic Search

A Claude Code plugin for GPU-accelerated semantic code search. Self-hosted embedding, reranking, and vector search — accessible locally or remotely over Tailscale.

## Architecture

Three containerized services work together:

**Embedding Service** — [Jina Code Embeddings 0.5B](https://huggingface.co/jinaai/jina-code-embeddings-0.5b) running on HuggingFace's Text Embeddings Inference (TEI), with custom builds for SM 12.0 (Blackwell) and CUDA 13.1. Includes performance-optimized Flash Attention and link-time optimization (LTO).

**Reranking Proxy** — [Jina Reranker v3](https://huggingface.co/jinaai/jina-reranker-v3) with a smart proxy layer that intercepts Qdrant search requests. Fetches top 100 vector results, then reranks with the cross-encoder before returning the top N. Uses TorchAO int4 quantization, Flash Attention 2, and listwise reranking architecture for throughput.

**Vector Database** — Qdrant for persistent vector storage with incremental indexing support.

## Optimizations

### GPU & Inference
- Custom TEI builds with patched Candle, Candle-Extensions, and candle-index-select-cu for SM 12.0 (Blackwell) kernel support and CUDA 13.1
- Performance-optimized Flash Attention with link-time optimization (LTO) and CPU-side optimizations
- Flash Attention 2 with custom FBGEMM tuning for the reranker
- TorchAO Int4 weight-only quantization (group_size=128) on the reranker — cuts VRAM without quality loss
- float16 dtype for embeddings, bfloat16 for reranking
- Parallel tokenization (4 workers) on the embedding model for preprocessing throughput
- Configurable batch sizes and token limits tuned per available VRAM

### Container Tuning
- CPU pinning via `cpuset` — embedding and reranking isolated to separate core groups to prevent contention
- `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` for efficient CUDA memory allocation
- Unlimited memlock (`ulimits.memlock: -1`) and 64MB stack for GPU workloads
- `pid: host` and `ipc: host` for shared memory access between GPU processes
- `OMP_NUM_THREADS` and `MKL_NUM_THREADS` pinned to match cpuset allocation

### Reranking Pipeline
- Query-passage asymmetric prefixing for better retrieval quality
- LRU query cache (1000 entries, 60s TTL) correlates embedding vectors back to query text for reranking
- Listwise batched reranking (batch size 64) with score threshold filtering
- Selective filtering — narrow or empty results when relevance is low, forcing query refinement rather than polluting context with marginal matches
- Graceful fallback to vector-only results if reranking fails

### Indexing
- Tree-sitter AST-aware chunking for Python, TypeScript, JavaScript, Go, Rust, Java, C/C++, Ruby, and more
- Line-based fallback for unsupported languages (YAML, Markdown, SQL, etc.)
- Deterministic UUID v5 point IDs for idempotent upserts
- Incremental indexing via file hash comparison — only re-indexes changed files
- Concurrent file processing (10 files) with batched embedding (60 segments per batch)

### Reliability
- Health checks that run actual inference requests (not just HTTP pings) every 30 seconds
- `restart: unless-stopped` for automatic recovery from CUDA context corruption under WSL2
- Health-gated service dependencies — the reranker waits for the embedding model to pass inference health checks before starting

## Requirements

- Docker with NVIDIA Container Toolkit
- NVIDIA Blackwell GPU (SM 12.0) with 16GB+ VRAM
- CUDA 13.1, drivers >= 590

## Quick Start

```bash
docker compose -f docker-compose.semantic-search.yaml --profile semantic-search up -d
```

The plugin registers as a Claude Code MCP server, providing tools for semantic search, indexing, and collection management.

## Remote Access

Install [Tailscale](https://tailscale.com) on the GPU host and any remote machines. Then set environment variables on remote machines:

```bash
export QDRANT_URL="http://<tailscale-hostname>:6333"
export EMBEDDING_URL="http://<tailscale-hostname>:1335"
```

The plugin reads these at startup, falling back to `localhost` when unset.

## VRAM Usage

| Component | VRAM |
|-----------|------|
| Jina Code Embeddings 0.5B (float16) | ~1 GB |
| Jina Reranker v3 (int4 quantized) | ~6 GB |
| **Total** | **~7-8 GB** |

Configurable via `MAX_CLIENT_BATCH_SIZE` and `MAX_BATCH_TOKENS` environment variables for constrained hardware.
