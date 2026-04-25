from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .chunk_rag import ChunkRAGPipeline
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from chunk_rag import ChunkRAGPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and query the embedding-based chunk RAG index.")
    parser.add_argument(
        "query",
        nargs="*",
        help="Search query for retrieval.",
    )
    parser.add_argument(
        "--chunks-path",
        default=str(Path(__file__).resolve().parents[1] / "long_memory" / "chunks" / "chunks.jsonl"),
        help="Path to chunks.jsonl.",
    )
    parser.add_argument(
        "--db-dir",
        default=str(Path(__file__).resolve().parent / "vector_db"),
        help="Persistent vector DB directory.",
    )
    parser.add_argument(
        "--collection",
        default="automd_chunks",
        help="Vector DB collection name.",
    )
    parser.add_argument(
        "--embedding-model",
        default=str(Path(__file__).resolve().parent / "local_sbert_model"),
        help="SentenceTransformer model path/name. Default uses local model directory.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of chunks to retrieve.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild vector index from chunks before querying.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    query = " ".join(args.query).strip() or "1HVR 1HSG 交叉对接 Indinavir"
    pipeline = ChunkRAGPipeline(
        args.chunks_path,
        db_dir=args.db_dir,
        collection_name=args.collection,
        embedding_model=args.embedding_model,
        rebuild_index=args.rebuild,
    )
    if args.rebuild:
        print(f"Indexed {len(pipeline.records)} chunks into collection '{args.collection}'.")
    print(pipeline.format_for_prompt(query, top_k=args.top_k, chunk_types={"task_flow", "tool_param", "outcome_summary"}))


if __name__ == "__main__":
    main()
