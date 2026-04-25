import json
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CHUNKS_PATH = Path(__file__).resolve().parents[1] / "long_memory" / "chunks" / "chunks.jsonl"
DEFAULT_DB_DIR = Path(__file__).resolve().parent / "vector_db"
DEFAULT_LOCAL_MODEL_DIR = Path(__file__).resolve().parent / "local_sbert_model"
DEFAULT_COLLECTION_NAME = "automd_chunks"
DEFAULT_EMBEDDING_MODEL = str(DEFAULT_LOCAL_MODEL_DIR)


@dataclass(frozen=True)
class ChunkRecord:
    # chunk 结构
    chunk_id: str       # 唯一标识符
    chunk_type: str     # chunk 的类型，例如 "task_flow", "tool_param", "outcome_summary" 等
    source_id: str      # chunk 来源的原始记录 ID，例如 agent_message 的 event_id 或 coordinator_assignment 的 seq
    run_id: str         # chunk 来源的 run_id，方便后续关联到具体的执行记录
    text: str           # chunk 的文本内容，通常是从原始记录中提取的关键信息片段
    metadata: dict[str, Any]


class ChunkCorpusLoader:
    # 从 chunks.jsonl 文件中加载 chunk 记录，构建 ChunkRecord 列表
    def __init__(self, chunks_path: str | Path = DEFAULT_CHUNKS_PATH):
        self.chunks_path = Path(chunks_path)

    @staticmethod
    def _safe_load_jsonl_line(line: str) -> dict[str, Any] | None:
        text = line.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def load(self) -> list[ChunkRecord]:
        if not self.chunks_path.exists():
            return []

        records: list[ChunkRecord] = []
        with self.chunks_path.open("r", encoding="utf-8") as f:
            for line in f:
                payload = self._safe_load_jsonl_line(line)
                if not payload:
                    continue
                records.append(
                    ChunkRecord(
                        chunk_id=str(payload.get("chunk_id", "")),
                        chunk_type=str(payload.get("chunk_type", "")),
                        source_id=str(payload.get("source_id", "")),
                        run_id=str(payload.get("run_id", "")),
                        text=str(payload.get("text", "")),
                        metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {},
                    )
                )
        return records


class ChunkRAGRetriever:
    # 基于 ChromaDB 和 SentenceTransformer 实现的 RAG 检索器，用于构建向量索引和执行相似度搜索
    def __init__(
        self,
        records: list[ChunkRecord],
        *,
        db_dir: str | Path = DEFAULT_DB_DIR,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        rebuild_index: bool = False,
        auto_build_if_empty: bool = False,
    ):
        try:
            chromadb_module = importlib.import_module("chromadb")
            sentence_transformers_module = importlib.import_module("sentence_transformers")
        except ImportError as exc:
            raise ImportError(
                "Missing dependencies for real RAG. Install: sentence-transformers, chromadb"
            ) from exc

        sentence_transformer_cls = getattr(sentence_transformers_module, "SentenceTransformer", None)
        if sentence_transformer_cls is None:
            raise ImportError("sentence-transformers is installed but SentenceTransformer is unavailable")

        self.records = records
        self.db_dir = Path(db_dir)
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model

        model_path = Path(embedding_model)
        if model_path.exists():
            model_ref = str(model_path)
        else:
            model_ref = embedding_model

        # Prefer local/offline inference to avoid network dependency during runtime.
        self._embedding_model = sentence_transformer_cls(model_ref, local_files_only=True)
        self._client = chromadb_module.PersistentClient(path=str(self.db_dir))
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        if rebuild_index:
            self.rebuild_index()
        elif auto_build_if_empty and self._collection.count() == 0 and self.records:
            self.build_index()

    @staticmethod
    def _to_chroma_metadata(record: ChunkRecord) -> dict[str, Any]:
        # Chroma metadata values should be scalar-compatible types.
        return {
            "chunk_type": record.chunk_type,
            "run_id": record.run_id,
            "source_id": record.source_id,
            "metadata_json": json.dumps(record.metadata, ensure_ascii=False),
        }

    @staticmethod
    def _extract_metadata(raw_metadata: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(raw_metadata, dict):
            return {}
        payload = raw_metadata.get("metadata_json")
        if not isinstance(payload, str) or not payload.strip():
            return {}
        try:
            data = json.loads(payload)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _to_similarity(distance: float | int | None) -> float:
        # For cosine distance in Chroma, lower is better. Convert to [0, 1] similarity.
        if distance is None:
            return 0.0
        value = float(distance)
        similarity = 1.0 - (value / 2.0)
        if similarity < 0.0:
            return 0.0
        if similarity > 1.0:
            return 1.0
        return similarity

    def build_index(self) -> int:
        if not self.records:
            return 0

        ids = [record.chunk_id for record in self.records]
        documents = [record.text for record in self.records]
        metadatas = [self._to_chroma_metadata(record) for record in self.records]
        embeddings = self._embedding_model.encode(documents, normalize_embeddings=True).tolist()

        self._collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        return len(ids)

    def rebuild_index(self) -> int:
        try:
            self._client.delete_collection(self.collection_name)
        except Exception:
            pass

        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        return self.build_index()

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        chunk_types: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        query_text = query.strip()
        if not query_text:
            return []

        where_clause: dict[str, Any] | None = None
        if chunk_types:
            where_clause = {"chunk_type": {"$in": sorted(chunk_types)}}

        query_embedding = self._embedding_model.encode(
            [query_text],
            normalize_embeddings=True,
        ).tolist()[0]

        payload = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=max(1, int(top_k)),
            where=where_clause,
            include=["documents", "metadatas", "distances"],
        )

        ids = payload.get("ids", [[]])[0]
        documents = payload.get("documents", [[]])[0]
        metadatas = payload.get("metadatas", [[]])[0]
        distances = payload.get("distances", [[]])[0]

        results: list[dict[str, Any]] = []
        for idx, chunk_id in enumerate(ids):
            raw_metadata = metadatas[idx] if idx < len(metadatas) else {}
            metadata = self._extract_metadata(raw_metadata)
            distance = distances[idx] if idx < len(distances) else None

            results.append(
                {
                    "score": round(self._to_similarity(distance), 6),
                    "distance": distance,
                    "chunk_id": str(chunk_id),
                    "chunk_type": str((raw_metadata or {}).get("chunk_type", "")),
                    "run_id": str((raw_metadata or {}).get("run_id", "")),
                    "source_id": str((raw_metadata or {}).get("source_id", "")),
                    "text": str(documents[idx]) if idx < len(documents) else "",
                    "metadata": metadata,
                }
            )

        return results


class ChunkRAGContextBuilder:
    # 将检索到的 chunk 结果格式化为适合 LLM prompt 的文本块，通常会包含 chunk 的类型、来源、文本内容等信息，帮助 LLM 理解这些上下文信息的意义和关联
    @staticmethod
    def format_results(results: list[dict[str, Any]]) -> str:
        if not results:
            return "暂无可召回的 chunks。"

        lines: list[str] = ["相关历史 chunks："]
        top_score = max((float(item.get("score", 0) or 0) for item in results), default=0.0)
        if top_score < 0.2:
            lines.append("注意：本次召回相似度较低，仅能作为历史经验弱参考，不能替代当前任务事实或关键参数判断。")
        for index, item in enumerate(results, start=1):
            lines.append(f"{index}. score={item.get('score', 0)} | type={item.get('chunk_type', '')} | run={item.get('run_id', '')}")
            lines.append(str(item.get("text", "")).strip())
        return "\n".join(lines)


def load_chunk_records(chunks_path: str | Path = DEFAULT_CHUNKS_PATH) -> list[ChunkRecord]:
    # 加载 chunk 记录的工具函数，返回一个 ChunkRecord 列表，供 RAG 检索器使用
    return ChunkCorpusLoader(chunks_path).load()


class ChunkRAGPipeline:
    # 封装了从加载 chunk 记录、构建向量索引、执行检索到格式化结果的完整流程，提供一个简单的接口供外部调用
    def __init__(
        self,
        chunks_path: str | Path = DEFAULT_CHUNKS_PATH,
        *,
        db_dir: str | Path = DEFAULT_DB_DIR,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        rebuild_index: bool = False,
        auto_build_if_empty: bool = False,
    ):
        self.chunks_path = Path(chunks_path)
        self.records = load_chunk_records(self.chunks_path)
        self.retriever = ChunkRAGRetriever(
            self.records,
            db_dir=db_dir,
            collection_name=collection_name,
            embedding_model=embedding_model,
            rebuild_index=rebuild_index,
            auto_build_if_empty=auto_build_if_empty,
        )
        self.context_builder = ChunkRAGContextBuilder()

    def build_index(self, rebuild: bool = False) -> int:
        if rebuild:
            return self.retriever.rebuild_index()
        return self.retriever.build_index()

    def retrieve(self, query: str, *, top_k: int = 5, chunk_types: set[str] | None = None) -> list[dict[str, Any]]:
        return self.retriever.search(query, top_k=top_k, chunk_types=chunk_types)

    def format_for_prompt(self, query: str, *, top_k: int = 5, chunk_types: set[str] | None = None) -> str:
        return self.context_builder.format_results(self.retrieve(query, top_k=top_k, chunk_types=chunk_types))


if __name__ == "__main__":
    pipeline = ChunkRAGPipeline(rebuild_index=False)
    results = pipeline.retrieve("你做过哪些工作？都遇到了什么困难？你都是怎么解决的？", top_k=5, chunk_types={"task_flow", "tool_param"})
    print(pipeline.context_builder.format_results(results))
