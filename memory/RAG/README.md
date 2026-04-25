# Memory RAG

This folder contains the RAG framework over `memory/long_memory/chunks/chunks.jsonl`.

## Modules

- `chunk_rag.py`: loads chunks, builds vector index, retrieves by embedding similarity, and formats prompt context.
- `__init__.py`: package exports.
- `build_index.py`: CLI for index build/query.

## Current behavior

- Uses the existing chunks JSONL as the corpus.
- Supports chunk-type filtering.
- Uses embedding model: `sentence-transformers/all-MiniLM-L6-v2`.
- Uses vector DB: `Chroma` persistent local store in `memory/RAG/vector_db`.

## Usage

```python
from memory.RAG import ChunkRAGPipeline

pipeline = ChunkRAGPipeline(rebuild_index=True)
context = pipeline.format_for_prompt(
    "1HVR 1HSG 交叉对接 Indinavir",
    top_k=5,
    chunk_types={"task_flow", "tool_param"},
)
print(context)
```

## CLI

```bash
python -m memory.RAG.build_index --rebuild "1HVR 1HSG 交叉对接 Indinavir"
```

## Dependencies

- `sentence-transformers`
- `chromadb`
