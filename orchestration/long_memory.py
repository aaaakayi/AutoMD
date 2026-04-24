import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .event_store import StructuredEventStore


class LongMemoryMaterializer:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.memory_root = self.project_root / "memory" / "long_memory"
        self.runs_dir = self.memory_root / "runs"
        self.index_path = self.memory_root / "index.json"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _ensure_dirs(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _build_agent_sequence(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sequence: list[dict[str, Any]] = []
        for evt in events:
            event_type = evt.get("event_type")
            if event_type == "coordinator_assignment":
                sequence.append(
                    {
                        "seq": evt.get("seq"),
                        "kind": "assignment",
                        "targets": evt.get("targets", []),
                        "task_excerpt": evt.get("task_excerpt", ""),
                    }
                )
            elif event_type == "agent_message":
                sequence.append(
                    {
                        "seq": evt.get("seq"),
                        "kind": "response",
                        "agent": evt.get("agent"),
                        "status": evt.get("status", "unknown"),
                        "degraded": bool(evt.get("degraded", False)),
                        "resolved_assignment_seq": (evt.get("resolved_assignment") or {}).get("from_event_seq"),
                    }
                )
        return sequence

    @staticmethod
    def _extract_command_exit_code(result_excerpt: str) -> int | None:
        if not isinstance(result_excerpt, str) or not result_excerpt:
            return None
        match = re.search(r"exit_code\s*[:=]\s*(-?\d+)", result_excerpt)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _derive_tool_semantic_status(transport_status: str, command_exit_code: int | None) -> str:
        normalized_transport = str(transport_status or "unknown").lower()
        if normalized_transport == "failed":
            return "failed"
        if command_exit_code is not None and command_exit_code != 0:
            return "failed"
        if normalized_transport in {"success", "unknown"}:
            return normalized_transport
        return "unknown"

    @staticmethod
    def _build_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        results_by_agent: dict[str, list[dict[str, Any]]] = {}

        for evt in events:
            event_type = evt.get("event_type")
            if event_type == "tool_call_request":
                requests.append(evt)
            elif event_type == "tool_call_result":
                agent = str(evt.get("agent", ""))
                results_by_agent.setdefault(agent, []).append(evt)

        merged: list[dict[str, Any]] = []
        for req in requests:
            agent = str(req.get("agent", ""))
            tool_name = str(req.get("tool_name", ""))
            matched_result = None
            for idx, result_evt in enumerate(results_by_agent.get(agent, [])):
                if str(result_evt.get("tool_name", "")) == tool_name:
                    matched_result = results_by_agent[agent].pop(idx)
                    break

            transport_status = matched_result.get("status", "unknown") if matched_result else "unknown"
            result_excerpt = matched_result.get("result_excerpt", "") if matched_result else ""
            command_exit_code = LongMemoryMaterializer._extract_command_exit_code(result_excerpt)
            semantic_status = LongMemoryMaterializer._derive_tool_semantic_status(transport_status, command_exit_code)

            merged.append(
                {
                    "request_seq": req.get("seq"),
                    "agent": agent,
                    "tool_name": tool_name,
                    "arguments": req.get("arguments", ""),
                    "status": semantic_status,
                    "tool_transport_status": transport_status,
                    "command_exit_code": command_exit_code,
                    "result_excerpt": result_excerpt,
                }
            )
        return merged

    @staticmethod
    def _derive_overall_status(latest_agent_outcomes: dict[str, Any]) -> str:
        statuses = [str((v or {}).get("status", "unknown")) for v in latest_agent_outcomes.values()]
        if any(s == "failed" for s in statuses):
            return "failed"
        if any(s in {"partial", "pending"} for s in statuses):
            return "partial"
        if statuses and all(s == "resolved" for s in statuses):
            return "success"
        return "unknown"

    def materialize(
        self,
        *,
        event_store: StructuredEventStore,
        raw_task: str,
        transcript: list[tuple[str, str]],
    ) -> Path:
        self._ensure_dirs()
        events = list(event_store.events)

        run_memory = {
            "schema_version": "1.0",
            "run_id": event_store.run_id,
            "created_at": self._now_iso(),
            "raw_task": raw_task,
            "overall_status": self._derive_overall_status(event_store.latest_agent_outcomes),
            "agent_execution_order": self._build_agent_sequence(events),
            "tool_calls": self._build_tool_calls(events),
            "latest_agent_outcomes": event_store.latest_agent_outcomes,
            "unresolved_assignments": event_store.pending_assignments,
            "event_log_path": str(event_store.event_log_path),
            "transcript_messages": len(transcript),
        }

        out_path = self.runs_dir / f"run_{event_store.run_id}.json"
        out_path.write_text(json.dumps(run_memory, ensure_ascii=False, indent=2), encoding="utf-8")

        index = {
            "updated_at": self._now_iso(),
            "runs": [],
        }
        if self.index_path.exists():
            try:
                index = json.loads(self.index_path.read_text(encoding="utf-8"))
            except Exception:
                index = {"updated_at": self._now_iso(), "runs": []}

        runs = [r for r in index.get("runs", []) if r.get("run_id") != event_store.run_id]
        runs.append(
            {
                "run_id": event_store.run_id,
                "raw_task": raw_task,
                "overall_status": run_memory["overall_status"],
                "memory_path": str(out_path),
                "event_path": str(event_store.event_log_path),
                "updated_at": self._now_iso(),
            }
        )
        index["runs"] = runs[-200:]
        index["updated_at"] = self._now_iso()
        self.index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path
