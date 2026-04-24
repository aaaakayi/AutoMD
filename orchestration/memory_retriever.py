# 从之前的运行结果中检索并且召回

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class MemoryRetriever:
    def __init__(self, memory_storage: str | Path, user_profile: str | Path):
        self.memory_storage = Path(memory_storage)
        self.user_profile = Path(user_profile)

    @staticmethod
    def _safe_load_json(path: Path) -> dict:
        try:
            if not path.exists():
                return {}
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        if not text:
            return set()
        tokens = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_+-]+", text.lower())
        return {token for token in tokens if len(token) > 1}

    @staticmethod
    def _extract_profile_facts(user_profile_data: dict) -> list[str]:
        facts = user_profile_data.get("facts", [])
        result: list[str] = []
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            if str(fact.get("category", "")).strip().lower() not in {"profile", "preference", "long_term_goal"}:
                continue
            content = str(fact.get("content", "")).strip()
            if content:
                result.append(content)
        return result

    @staticmethod
    def _build_searchable_text(run: dict, run_memory: dict | None = None) -> str:
        pieces: list[str] = [
            str(run.get("run_id", "")),
            str(run.get("raw_task", "")),
            str(run.get("overall_status", "")),
            str(run.get("updated_at", "")),
        ]
        if run_memory:
            pieces.extend(
                [
                    str(run_memory.get("raw_task", "")),
                    str(run_memory.get("overall_status", "")),
                    str(run_memory.get("event_log_path", "")),
                ]
            )
            for item in run_memory.get("agent_execution_order", []):
                if isinstance(item, dict):
                    pieces.append(str(item.get("task_excerpt", "")))
                    pieces.append(str(item.get("agent", "")))
                    pieces.append(str(item.get("status", "")))
            for item in run_memory.get("tool_calls", []):
                if isinstance(item, dict):
                    pieces.append(str(item.get("tool_name", "")))
                    pieces.append(str(item.get("status", "")))
                    pieces.append(str(item.get("result_excerpt", "")))
        return "\n".join(pieces)

    @staticmethod
    def _score(query_tokens: set[str], query_text: str, searchable_text: str, updated_at: str = "") -> float:
        if not searchable_text:
            return 0.0

        lower_text = searchable_text.lower()
        score = 0.0

        if query_text and query_text.lower() in lower_text:
            score += 8.0

        text_tokens = MemoryRetriever._tokenize(searchable_text)
        overlap = query_tokens & text_tokens
        score += float(len(overlap)) * 2.0

        for token in query_tokens:
            if token and token in lower_text:
                score += 0.5

        if updated_at:
            try:
                dt = datetime.fromisoformat(updated_at)
                age_days = max(0.0, (datetime.now() - dt).total_seconds() / 86400.0)
                score += max(0.0, 5.0 - age_days * 0.05)
            except Exception:
                pass

        return score

    @staticmethod
    def _summarize_run(run: dict, run_memory: dict | None = None) -> str:
        overall_status = run_memory.get("overall_status") if run_memory else run.get("overall_status", "unknown")
        raw_task = str(run.get("raw_task", "")).replace("\n", " ").strip()
        if len(raw_task) > 180:
            raw_task = raw_task[:177] + "..."

        lines = [
            f"run_id: {run.get('run_id', '')}",
            f"created_at: {run_memory.get('created_at', run.get('updated_at', '')) if run_memory else run.get('updated_at', '')}",
            f"overall_status: {overall_status}",
            f"raw_task: {raw_task}",
        ]

        if run_memory:
            failed_tools = [
                f"{item.get('agent', '')}:{item.get('tool_name', '')}={item.get('status', 'unknown')}"
                for item in run_memory.get("tool_calls", [])
                if isinstance(item, dict) and str(item.get("status", "")).lower() in {"failed", "unknown"}
            ]
            if failed_tools:
                lines.append(f"failed_or_uncertain_tools: {'; '.join(failed_tools[:5])}")

        return "\n".join(lines)

    def retrieve(self, query: str = "", top_k: int = 1) -> dict[str, Any]:
        index_data = self._safe_load_json(self.memory_storage)
        user_profile_data = self._safe_load_json(self.user_profile)

        runs = index_data.get("runs", []) if isinstance(index_data, dict) else []
        if not isinstance(runs, list):
            runs = []

        user_facts = self._extract_profile_facts(user_profile_data)
        query_text = (query or "").strip()
        query_tokens = self._tokenize(query_text)

        candidates: list[dict[str, Any]] = []
        for run in runs:
            if not isinstance(run, dict):
                continue

            run_memory: dict[str, Any] = {}
            memory_path = run.get("memory_path")
            if memory_path:
                run_memory = self._safe_load_json(Path(memory_path))

            searchable_text = self._build_searchable_text(run, run_memory)
            score = self._score(query_tokens, query_text, searchable_text, str(run.get("updated_at", "")))
            candidates.append(
                {
                    "score": score,
                    "run": run,
                    "run_memory": run_memory,
                }
            )

        candidates.sort(
            key=lambda item: (
                item["score"],
                str(item["run"].get("updated_at", "")),
                str(item["run"].get("run_id", "")),
            ),
            reverse=True,
        )

        top_k = max(1, int(top_k))
        matches: list[dict[str, Any]] = []
        for item in candidates[:top_k]:
            run = item["run"]
            run_memory = item["run_memory"]
            matches.append(
                {
                    "run_id": run.get("run_id", ""),
                    "updated_at": run.get("updated_at", ""),
                    "overall_status": run.get("overall_status", run_memory.get("overall_status", "unknown")),
                    "raw_task": run.get("raw_task", ""),
                    "summary": self._summarize_run(run, run_memory),
                    "score": round(float(item["score"]), 3),
                    "memory_path": run.get("memory_path", ""),
                }
            )

        if not matches and runs:
            latest_run = max(
                (run for run in runs if isinstance(run, dict)),
                key=lambda run: (str(run.get("updated_at", "")), str(run.get("run_id", ""))),
                default=None,
            )
            if latest_run:
                run_memory = self._safe_load_json(Path(latest_run.get("memory_path", ""))) if latest_run.get("memory_path") else {}
                matches.append(
                    {
                        "run_id": latest_run.get("run_id", ""),
                        "updated_at": latest_run.get("updated_at", ""),
                        "overall_status": latest_run.get("overall_status", run_memory.get("overall_status", "unknown")),
                        "raw_task": latest_run.get("raw_task", ""),
                        "summary": self._summarize_run(latest_run, run_memory),
                        "score": 0.0,
                        "memory_path": latest_run.get("memory_path", ""),
                    }
                )

        return {
            "query": query_text,
            "matches": matches,
            "user_facts": user_facts,
            "latest_run": matches[0] if matches else None,
        }

    @staticmethod
    def format_for_prompt(retrieval: dict[str, Any]) -> str:
        matches = retrieval.get("matches", []) if isinstance(retrieval, dict) else []
        user_facts = retrieval.get("user_facts", []) if isinstance(retrieval, dict) else []

        lines: list[str] = []
        if user_facts:
            lines.append("用户长期偏好/画像：")
            for idx, fact in enumerate(user_facts, start=1):
                lines.append(f"{idx}. {fact}")

        if matches:
            lines.append("相关历史任务记忆：")
            for idx, match in enumerate(matches, start=1):
                summary = str(match.get("summary", "")).strip()
                lines.append(f"{idx}. score={match.get('score', 0)}")
                lines.append(summary)
        else:
            lines.append("暂无可召回的历史任务记忆。")

        return "\n".join(lines)


if __name__ == "__main__":
    memory_store_path = "../memory/long_memory/index.json"
    user_profile_path = "../memory/long_memory/user_profile.json"

    retriever = MemoryRetriever(memory_store_path, user_profile_path)
    result = retriever.retrieve(query="交叉对接 Indinavir 1HVR")

    print(f"Query: {result['query']}")
    print(f"User facts: {result['user_facts']}")
    for item in result["matches"]:
        print("---")
        print(item["summary"])
