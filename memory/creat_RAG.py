"""Build long-memory chunks and optionally update local vector DB for RAG.

Usage:
1) Update one run's chunks:
    python memory/creat_RAG.py 20260418_154035
2) Update one run + rebuild vector index:
    python memory/creat_RAG.py 20260418_154035 --build-vector --rebuild
3) Delete one run from index/runs/events/chunks/vector DB:
    python memory/creat_RAG.py 20260418_154035 --delete-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestration import RunChunkBuilder

try:
    from memory.RAG import ChunkRAGPipeline
except Exception:
    ChunkRAGPipeline = None


RUNS_DIR = PROJECT_ROOT / "memory" / "long_memory" / "runs"
INDEX_PATH = PROJECT_ROOT / "memory" / "long_memory" / "index.json"
EVENTS_DIR = PROJECT_ROOT / "memory" / "long_memory" / "events"
CHUNKS_OUTPUT_PATH = PROJECT_ROOT / "memory" / "long_memory" / "chunks" / "chunks.jsonl"
VECTOR_DB_DIR = PROJECT_ROOT / "memory" / "RAG" / "vector_db"
VECTOR_COLLECTION = "automd_chunks"


def _normalize_run_id(run_id: str) -> str:
    text = str(run_id).strip()
    if text.startswith("run_"):
        text = text[4:]
    if text.endswith(".json"):
        text = text[:-5]
    return text


def _load_run(run_id: str) -> dict:
    run_file = RUNS_DIR / f"run_{run_id}.json"
    if not run_file.exists():
        raise FileNotFoundError(f"run file not found: {run_file}")
    with run_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"run file is not a JSON object: {run_file}")
    return data


def _safe_load_json(path: Path) -> dict:
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _safe_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_existing_chunks() -> list[dict]:
    if not CHUNKS_OUTPUT_PATH.exists():
        return []

    chunks: list[dict] = []
    with CHUNKS_OUTPUT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue
            if isinstance(payload, dict):
                chunks.append(payload)
    return chunks


def update_chunk(run_id: str, build_vector: bool = False, rebuild_vector: bool = False) -> None:
    # 根据给定的 run_id 增量更新 chunks.jsonl，避免覆盖其它 run 的记录。
    run_id = _normalize_run_id(run_id)
    builder = RunChunkBuilder(RUNS_DIR, CHUNKS_OUTPUT_PATH)
    run = _load_run(run_id)
    new_chunks = builder.build_chunks_from_run(run)

    existing_chunks = _load_existing_chunks()
    merged_chunks = [chunk for chunk in existing_chunks if str(chunk.get("run_id", "")) != run_id]
    merged_chunks.extend(new_chunks)

    output_path = builder.write_chunks_jsonl(merged_chunks)
    print(f"[RAG] updated chunks for run_id={run_id}: {len(new_chunks)} records")
    print(f"[RAG] chunks saved to: {output_path}")

    if build_vector:
        if ChunkRAGPipeline is None:
            print("[RAG] vector update skipped: ChunkRAGPipeline import failed")
            return

        pipeline = ChunkRAGPipeline(
            chunks_path=CHUNKS_OUTPUT_PATH,
            db_dir=VECTOR_DB_DIR,
            collection_name=VECTOR_COLLECTION,
            rebuild_index=rebuild_vector,
        )
        indexed = pipeline.build_index(rebuild=rebuild_vector)
        print(
            f"[RAG] vector DB updated: dir={VECTOR_DB_DIR}, collection={VECTOR_COLLECTION}, indexed={indexed}"
        )


def _delete_event_files_for_run(run_id: str, run_payload: dict) -> int:
    deleted = 0
    candidates: list[Path] = []

    for key in ("event_log_path", "event_path"):
        value = run_payload.get(key)
        if isinstance(value, str) and value.strip():
            p = Path(value)
            if not p.is_absolute():
                p = PROJECT_ROOT / p
            candidates.append(p)

    candidates.append(EVENTS_DIR / f"run_{run_id}.jsonl")

    for path in candidates:
        try:
            if path.exists() and path.is_file():
                path.unlink()
                deleted += 1
        except Exception:
            continue
    return deleted


def _delete_chunks_for_run(run_id: str) -> int:
    chunks = _load_existing_chunks()
    before = len(chunks)
    kept = [chunk for chunk in chunks if str(chunk.get("run_id", "")) != run_id]
    removed = before - len(kept)
    if removed > 0:
        builder = RunChunkBuilder(RUNS_DIR, CHUNKS_OUTPUT_PATH)
        builder.write_chunks_jsonl(kept)
    return removed


def _delete_vector_for_run(run_id: str) -> str:
    try:
        chromadb = __import__("chromadb")
    except Exception as exc:
        return f"skip (chromadb unavailable: {exc})"

    try:
        client = chromadb.PersistentClient(path=str(VECTOR_DB_DIR))
        collection = client.get_or_create_collection(name=VECTOR_COLLECTION)
        collection.delete(where={"run_id": run_id})
        return "ok"
    except Exception as exc:
        return f"failed ({exc})"


def delete_run_artifacts(run_id: str, delete_vector: bool = True) -> None:
    """Delete one run across index/runs/events/chunks/vector DB."""
    run_id = _normalize_run_id(run_id)

    run_file = RUNS_DIR / f"run_{run_id}.json"
    run_payload = _safe_load_json(run_file)

    # 1) index.json
    index_payload = _safe_load_json(INDEX_PATH)
    runs = index_payload.get("runs", []) if isinstance(index_payload.get("runs", []), list) else []
    before_runs = len(runs)
    runs = [item for item in runs if not (isinstance(item, dict) and str(item.get("run_id", "")) == run_id)]
    removed_from_index = before_runs - len(runs)
    index_payload["runs"] = runs
    _safe_write_json(INDEX_PATH, index_payload)

    # 2) run file
    removed_run_file = False
    if run_file.exists() and run_file.is_file():
        run_file.unlink()
        removed_run_file = True

    # 3) event files
    removed_events = _delete_event_files_for_run(run_id, run_payload)

    # 4) chunks
    removed_chunks = _delete_chunks_for_run(run_id)

    # 5) vector DB
    vector_state = "skip"
    if delete_vector:
        vector_state = _delete_vector_for_run(run_id)

    print(f"[RAG] delete run_id={run_id}")
    print(f"[RAG] index removed entries: {removed_from_index}")
    print(f"[RAG] run file removed: {removed_run_file}")
    print(f"[RAG] event files removed: {removed_events}")
    print(f"[RAG] chunks removed: {removed_chunks}")
    print(f"[RAG] vector delete: {vector_state}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage chunks/vector DB by run_id.")
    parser.add_argument("run_id", help="The run_id to build chunks for.")
    parser.add_argument("--delete-run", action="store_true", help="Delete this run from index/runs/events/chunks/vector DB.")
    parser.add_argument("--build-vector", action="store_true", help="Also update local vector DB.")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild vector collection from scratch.")
    parser.add_argument("--skip-vector-delete", action="store_true", help="When deleting a run, skip vector DB cleanup.")
    args = parser.parse_args()
    if args.delete_run:
        delete_run_artifacts(args.run_id, delete_vector=not args.skip_vector_delete)
    else:
        update_chunk(args.run_id, build_vector=args.build_vector, rebuild_vector=args.rebuild)