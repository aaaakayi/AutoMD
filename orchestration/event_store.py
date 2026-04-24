import json
from datetime import datetime
from pathlib import Path
from typing import Any

from autogen_agentchat.messages import ToolCallExecutionEvent, ToolCallRequestEvent, ToolCallSummaryMessage


class StructuredEventStore:
    def __init__(self, event_log_dir: Path):
        self.event_log_dir = event_log_dir
        self.event_log_dir.mkdir(parents=True, exist_ok=True)
        self.schema_version = "1.1"
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.event_log_path = self.event_log_dir / f"run_{self.run_id}.jsonl"
        self.event_seq = 0
        self.message_seq = 0
        self.pending_assignments: dict[str, dict[str, Any]] = {}
        self.latest_agent_outcomes: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def shorten_text(text: str, limit: int = 240) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1] + "…"

    def _next_event_seq(self) -> int:
        self.event_seq += 1
        return self.event_seq

    @staticmethod
    def _extract_target_agents(text: str) -> list[str]:
        known_agents = [
            "env_setup_agent",
            "protein_pre_agent",
            "ligand_pre_agent",
            "dock_agent",
            "memory_agent",
        ]
        found = [name for name in known_agents if name in text]
        return list(dict.fromkeys(found))

    @staticmethod
    def _detect_degrade_flag(text: str) -> bool:
        lowered = text.lower()
        if "失败或降级的步骤及原因：无" in lowered or "失败或降级的步骤及原因:无" in lowered:
            return False
        if "降级" in lowered and ("无" in lowered or "否" in lowered):
            return False
        keywords = ["降级", "fallback", "degrade", "degraded", "替代方案", "回退方案"]
        return any(keyword in lowered for keyword in keywords)

    @staticmethod
    def _detect_problem_status(text: str) -> str:
        lowered = text.lower()
        lowered = lowered.replace("失败或降级的步骤及原因：无", "")
        lowered = lowered.replace("失败或降级的步骤及原因:无", "")

        pending_hits = ["未开始", "尚未开始", "待执行", "未执行"]
        success_hits = ["成功", "已完成", "完成", "resolved", "success"]
        failure_hits = ["失败", "报错", "error", "exception", "未完成", "无法"]

        if any(token in lowered for token in pending_hits):
            if not any(token in lowered for token in failure_hits + success_hits):
                return "pending"

        if any(token in lowered for token in failure_hits):
            if any(token in lowered for token in success_hits):
                return "partial"
            return "failed"
        if any(token in lowered for token in success_hits):
            return "resolved"
        return "unknown"

    def _append_event(self, event: dict[str, Any]) -> None:
        payload = {
            "schema_version": self.schema_version,
            **event,
        }
        self.events.append(payload)
        try:
            self.event_log_dir.mkdir(parents=True, exist_ok=True)
            with self.event_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            return

    def record_run_started(self, raw_task: str, debug_tool_calls: bool) -> None:
        self._append_event(
            {
                "seq": self._next_event_seq(),
                "event_type": "run_started",
                "timestamp": self._now_iso(),
                "run_id": self.run_id,
                "raw_task": raw_task,
                "debug_tool_calls": debug_tool_calls,
            }
        )

    def record_run_finished(self, transcript_count: int) -> None:
        self._append_event(
            {
                "seq": self._next_event_seq(),
                "event_type": "run_finished",
                "timestamp": self._now_iso(),
                "run_id": self.run_id,
                "messages": transcript_count,
                "pending_assignments": self.pending_assignments,
                "latest_agent_outcomes": self.latest_agent_outcomes,
            }
        )

    def record_coordinator_assignment(self, content: str, raw_task: str | None = None) -> None:
        targets = self._extract_target_agents(content)
        assignment_event = {
            "seq": self._next_event_seq(),
            "event_type": "coordinator_assignment",
            "timestamp": self._now_iso(),
            "run_id": self.run_id,
            "source": "Coordinator",
            "targets": targets,
            "task_content": content,
            "task_excerpt": self.shorten_text(content, 600),
        }
        self._append_event(assignment_event)

        for target in targets:
            self.pending_assignments[target] = {
                "from_event_seq": assignment_event["seq"],
                "assigned_at": assignment_event["timestamp"],
                "task_content": content,
            }

    def record_agent_message(self, source: str, content: str, raw_task: str | None = None) -> None:
        assignment = self.pending_assignments.pop(source, None)
        status = self._detect_problem_status(content)
        degraded = self._detect_degrade_flag(content)

        self.message_seq += 1
        event = {
            "seq": self._next_event_seq(),
            "event_type": "agent_message",
            "timestamp": self._now_iso(),
            "run_id": self.run_id,
            "agent": source,
            "message_content": content,
            "message_excerpt": self.shorten_text(content, 800),
            "message_index": self.message_seq,
            "status": status,
            "degraded": degraded,
            "resolved_assignment": assignment,
        }
        self._append_event(event)

        self.latest_agent_outcomes[source] = {
            "status": status,
            "degraded": degraded,
            "last_message_excerpt": self.shorten_text(content, 400),
            "updated_at": event["timestamp"],
            "resolved_assignment_seq": assignment["from_event_seq"] if assignment else None,
        }

    @staticmethod
    def _is_path_like(text: str) -> bool:
        if not isinstance(text, str):
            return False
        t = text.strip()
        if not t:
            return False
        if t.startswith("/") or t.startswith("./") or t.startswith("../"):
            return True
        if "\\" in t or "/" in t:
            return True
        if len(t) >= 3 and t[1:3] == ":\\":
            return True
        return False

    @staticmethod
    def _iter_path_candidates(value: Any, key_hint: str = ""):
        file_keys = {
            "path",
            "file",
            "file_path",
            "filepath",
            "input_file",
            "output_file",
            "protein_file",
            "ligand_file",
            "protein_pdb",
            "output_dir",
            "work_dir",
            "directory",
            "dir",
        }

        if isinstance(value, dict):
            for k, v in value.items():
                k_lower = str(k).lower()
                if isinstance(v, str) and (k_lower in file_keys or StructuredEventStore._is_path_like(v)):
                    yield v
                else:
                    yield from StructuredEventStore._iter_path_candidates(v, k_lower)
            return

        if isinstance(value, list):
            for item in value:
                yield from StructuredEventStore._iter_path_candidates(item, key_hint)
            return

        if isinstance(value, str) and (key_hint in file_keys or StructuredEventStore._is_path_like(value)):
            yield value

    def _extract_paths_from_arguments(self, arguments: str) -> list[str]:
        if not isinstance(arguments, str) or not arguments.strip():
            return []
        try:
            parsed = json.loads(arguments)
        except Exception:
            return []

        seen = set()
        paths: list[str] = []
        for p in self._iter_path_candidates(parsed):
            pp = str(p).strip()
            if not pp or pp in seen:
                continue
            seen.add(pp)
            paths.append(pp)
        return paths

    def record_tool_call_request(self, source: str, event: ToolCallRequestEvent) -> list[str]:
        display_lines: list[str] = []
        for call in event.content:
            tool_name = getattr(call, "name", "unknown_tool")
            args = getattr(call, "arguments", "")
            paths = self._extract_paths_from_arguments(args)
            self._append_event(
                {
                    "seq": self._next_event_seq(),
                    "event_type": "tool_call_request",
                    "timestamp": self._now_iso(),
                    "run_id": self.run_id,
                    "agent": source,
                    "tool_name": tool_name,
                    "arguments": args,
                    "argument_paths": paths,
                }
            )
            display_lines.extend([
                f"\n[{source}] 调用工具: {tool_name}",
            ])
            if isinstance(args, str) and args.strip():
                display_lines.append(f"参数: {self.shorten_text(args, 300)}")
            if paths:
                display_lines.append("访问文件:")
                for p in paths[:8]:
                    display_lines.append(f"- {p}")
        return display_lines

    def record_tool_call_execution(self, source: str, event: ToolCallExecutionEvent) -> list[str]:
        display_lines: list[str] = []
        for result in event.content:
            tool_name = getattr(result, "name", "unknown_tool")
            status = "失败" if getattr(result, "is_error", False) else "成功"
            content = getattr(result, "content", "")
            self._append_event(
                {
                    "seq": self._next_event_seq(),
                    "event_type": "tool_call_result",
                    "timestamp": self._now_iso(),
                    "run_id": self.run_id,
                    "agent": source,
                    "tool_name": tool_name,
                    "status": "failed" if getattr(result, "is_error", False) else "success",
                    "result_excerpt": self.shorten_text(content, 1000) if isinstance(content, str) else str(content),
                }
            )
            display_lines.append(f"\n[{source}] 工具返回: {tool_name} ({status})")
            if isinstance(content, str) and content.strip():
                display_lines.append(f"结果: {self.shorten_text(content, 800)}")
        return display_lines

    def record_tool_call_summary(self, source: str, event: ToolCallSummaryMessage) -> list[str]:
        calls = getattr(event, "tool_calls", [])
        results = getattr(event, "results", [])
        self._append_event(
            {
                "seq": self._next_event_seq(),
                "event_type": "tool_call_summary",
                "timestamp": self._now_iso(),
                "run_id": self.run_id,
                "agent": source,
                "call_count": len(calls),
                "failed_count": sum(1 for result in results if getattr(result, "is_error", False)),
            }
        )

        display_lines = [f"\n[{source}] 工具调用汇总: {len(calls)} 次"]
        for idx, call in enumerate(calls):
            tool_name = getattr(call, "name", "unknown_tool")
            status = "成功"
            if idx < len(results) and getattr(results[idx], "is_error", False):
                status = "失败"
            display_lines.append(f"- {tool_name}: {status}")
        return display_lines
