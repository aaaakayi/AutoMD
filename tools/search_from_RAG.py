# 从 RAG 检索所需内容

import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.RAG.chunk_rag import ChunkRAGPipeline

DEFAULT_CHUNKS_PATH = PROJECT_ROOT / "memory" / "long_memory" / "chunks" / "chunks.jsonl"
DEFAULT_DB_DIR = PROJECT_ROOT / "memory" / "RAG" / "vector_db"
DEFAULT_COLLECTION_NAME = "automd_chunks"
DEFAULT_EMBEDDING_MODEL = str(PROJECT_ROOT / "memory" / "RAG" / "local_sbert_model")

def search_from_rag(
    query: str,
    *,
    top_k: int = 5,
    chunk_types: Iterable[str] | None = None,
) -> str:
    """根据查询从本地 RAG 检索上下文并返回格式化文本。"""

    chunks_path = DEFAULT_CHUNKS_PATH
    db_dir = DEFAULT_DB_DIR
    collection_name = DEFAULT_COLLECTION_NAME
    embedding_model = DEFAULT_EMBEDDING_MODEL


    query_text = (query or "").strip()
    if not query_text:
        return "检索查询为空，请提供有效 query。"

    selected_types = set(chunk_types) if chunk_types is not None else {"task_flow", "tool_param", "outcome_summary"}

    try:
        chunk_pipeline = ChunkRAGPipeline(
            chunks_path=chunks_path,
            db_dir=db_dir,
            collection_name=collection_name,
            embedding_model=embedding_model,
            rebuild_index=False,
            auto_build_if_empty=False,
        )
    except Exception as exc:
        return f"RAG pipeline 初始化失败: {exc}"

    try:
        results = chunk_pipeline.retrieve(query_text, top_k=max(1, int(top_k)), chunk_types=selected_types)
    except Exception as exc:
        return f"RAG 检索失败: {exc}"

    return chunk_pipeline.context_builder.format_results(results)


if __name__ == "__main__":
    query = "处理配体失败"

    # tool_param, outcome_summary, task_flow
    print(search_from_rag(query, top_k=5, chunk_types={"tool_param"}))

