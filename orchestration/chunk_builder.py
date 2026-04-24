import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class RunChunkBuilder:
    """Build lightweight RAG chunks from run-level long memory JSON files."""

    def __init__(self, runs_dir: str | Path, chunks_output_path: str | Path | None = None):
        self.runs_dir = Path(runs_dir)
        self.chunks_output_path = Path(chunks_output_path) if chunks_output_path else self.runs_dir.parent / "chunks" / "chunks.jsonl"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _safe_load_json(path: Path) -> dict[str, Any]:
        try:
            if not path.exists():
                return {}
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _shorten(text: str, limit: int = 260) -> str:
        normalized = " ".join(str(text).split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1] + "…"

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
    def _extract_keywords(text: str, limit: int = 8) -> list[str]:
        if not text:
            return []
        stopwords = {
            "请",
            "你",
            "我们",
            "进行",
            "使用",
            "以及",
            "然后",
            "并且",
            "这个",
            "那个",
            "任务",
            "工具",
            "output",
            "input",
            "true",
            "false",
            "none",
        }
        tokens = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_+-]+", text.lower())
        keywords: list[str] = []
        for token in tokens:
            if len(token) <= 1 or token in keywords or token in stopwords:
                continue
            keywords.append(token)
            if len(keywords) >= limit:
                break
        return keywords

    def _extract_task_intent(self, raw_task: str) -> str:
        if not raw_task:
            return ""
        lines = [line.strip() for line in raw_task.splitlines() if line.strip()]
        if not lines:
            return ""
        return self._shorten(" ".join(lines[:2]), 220)

    def _extract_task_entities(self, raw_task: str) -> list[str]:
        if not raw_task:
            return []
        entities: list[str] = []

        def load_rdkit_chem():
            try:
                from rdkit import Chem as rdkit_chem  # type: ignore
                return rdkit_chem
            except Exception:
                return None

        chem_module = load_rdkit_chem()

        def add_entity(value: str) -> None:
            normalized = value.strip()
            if not normalized:
                return
            if normalized in entities:
                return
            entities.append(normalized)

        def normalize_smiles(smiles_text: str) -> str:
            if chem_module is None:
                return smiles_text.strip()
            try:
                molecule = chem_module.MolFromSmiles(smiles_text, sanitize=True)
            except Exception:
                molecule = None
            if molecule is None:
                return smiles_text.strip()
            try:
                return chem_module.MolToSmiles(molecule, canonical=True)
            except Exception:
                return smiles_text.strip()

        def collect_smiles_candidates(text: str) -> list[str]:
            candidates: list[str] = []
            lines = [line.strip() for line in text.splitlines() if line.strip()]

            for line in lines:
                lowered = line.lower()
                if "smiles" in lowered:
                    payload = line.split(":", 1)[-1] if ":" in line else line
                    for token in re.split(r"[\s,;，；]+", payload):
                        token = token.strip()
                        if len(token) >= 6:
                            candidates.append(token)

            # Fallback scan for contiguous SMILES-like tokens in free text.
            candidates.extend(re.findall(r"[A-Za-z0-9@+\-\[\]\(\)=#$\\/%.]{6,}", text))
            return candidates

        if chem_module is not None:
            seen_smiles: set[str] = set()
            for candidate in collect_smiles_candidates(raw_task):
                compact_candidate = candidate.strip().replace(" ", "")
                if not compact_candidate or compact_candidate in seen_smiles:
                    continue
                seen_smiles.add(compact_candidate)
                try:
                    molecule = chem_module.MolFromSmiles(compact_candidate, sanitize=True)
                except Exception:
                    molecule = None
                if molecule is None:
                    continue
                add_entity(normalize_smiles(compact_candidate))

        # Whitelist non-SMILES entities: keep canonical PDB IDs only.
        for pdb_id in re.findall(r"\b[0-9][A-Za-z0-9]{3}\b", raw_task):
            add_entity(pdb_id.upper())

        return entities[:12]

    @staticmethod
    def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if not isinstance(arguments, str) or not arguments.strip():
            return {}
        try:
            parsed = json.loads(arguments)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _is_successful_tool_call(item: dict[str, Any]) -> bool:
        status = str(item.get("status", "unknown")).lower()
        transport = str(item.get("tool_transport_status", status)).lower()
        exit_code = item.get("command_exit_code")

        if transport == "failed" or status == "failed":
            return False
        if exit_code is not None:
            try:
                return int(exit_code) == 0
            except Exception:
                return False
        return status == "success" or transport == "success"

    @staticmethod
    def _request_seq_value(item: dict[str, Any], fallback: int) -> int:
        seq = item.get("request_seq")
        if isinstance(seq, int):
            return seq
        try:
            return int(seq)
        except Exception:
            return fallback

    def _extract_constraints_from_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[str]:
        keys_of_interest = {
            "net_charge",
            "charge_method",
            "force_field",
            "exhaustiveness",
            "num_modes",
            "energy_range",
            "center_x",
            "center_y",
            "center_z",
            "size_x",
            "size_y",
            "size_z",
        }

        winners: dict[str, tuple[tuple[int, int], str]] = {}

        for index, item in enumerate(tool_calls):
            if not isinstance(item, dict):
                continue
            parsed = self._parse_tool_arguments(item.get("arguments", ""))
            if not parsed:
                continue

            success_rank = 1 if self._is_successful_tool_call(item) else 0
            seq_rank = self._request_seq_value(item, index + 1)

            for key in keys_of_interest:
                if key in parsed:
                    value = parsed[key]
                    token = f"{key}={value}"
                    rank = (success_rank, seq_rank)
                    previous = winners.get(key)
                    if previous is None or rank > previous[0]:
                        winners[key] = (rank, token)

        ordered = sorted(winners.values(), key=lambda item: item[0][1], reverse=True)
        return [token for _, token in ordered[:12]]

    @staticmethod
    def _make_chunk_id(run_id: str, chunk_type: str, suffix: str) -> str:
        return f"{run_id}:{chunk_type}:{suffix}"

    def _build_task_flow_chunk(self, run: dict[str, Any]) -> dict[str, Any]:
        """Summarize the task flow and agent execution sequence of a run into a single chunk."""
        run_id = str(run.get("run_id", ""))
        raw_task = str(run.get("raw_task", ""))
        overall_status = str(run.get("overall_status", "unknown"))
        agent_sequence = run.get("agent_execution_order", []) if isinstance(run.get("agent_execution_order", []), list) else []
        tool_calls = run.get("tool_calls", []) if isinstance(run.get("tool_calls", []), list) else []

        task_intent = self._extract_task_intent(raw_task)
        task_entities = self._extract_task_entities(raw_task)
        constraints = self._extract_constraints_from_tool_calls(tool_calls)

        steps: list[str] = []
        seen_steps = set()
        for item in agent_sequence:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind", "response")
            seq = item.get("seq")
            if kind == "assignment":
                targets_list = item.get("targets", []) if isinstance(item.get("targets", []), list) else []
                if not targets_list:
                    continue
                targets = ", ".join(targets_list)
                step = f"assignment(seq={seq}) -> {targets}"
            else:
                agent = str(item.get("agent", ""))
                status = str(item.get("status", "unknown"))
                degraded = " degraded" if item.get("degraded", False) else ""
                step = f"response(seq={seq}) -> {agent}: status={status}{degraded}"

            if step in seen_steps:
                continue
            seen_steps.add(step)
            steps.append(step)

        summary_parts = [
            f"run_id: {run_id}",
            f"overall_status: {overall_status}",
        ]

        if task_intent:
            summary_parts.append(f"task_intent: {task_intent}")
        if task_entities:
            summary_parts.append(f"task_entities: {', '.join(task_entities)}")
        if constraints:
            summary_parts.append(f"task_constraints: {', '.join(constraints)}")

        if steps:
            summary_parts.append("workflow:")
            summary_parts.extend(f"- {step}" for step in steps[:12])

        task_tags = self._extract_keywords(
            " ".join([task_intent, " ".join(task_entities), " ".join(constraints)]),
            limit=12,
        )

        return {
            "chunk_id": self._make_chunk_id(run_id, "task_flow", "summary"),
            "chunk_type": "task_flow",
            "source_type": "run_summary",
            "source_id": run_id,
            "run_id": run_id,
            "text": "\n".join(summary_parts),
            "created_at": str(run.get("created_at", self._now_iso())),
            "updated_at": self._now_iso(),
            "metadata": {
                "overall_status": overall_status,
                "task_intent": task_intent,
                "task_entities": task_entities,
                "task_constraints": constraints,
                "tags": task_tags,
            },
        }

    def _build_outcome_chunk(self, run: dict[str, Any]) -> dict[str, Any]:
        run_id = str(run.get("run_id", ""))
        overall_status = str(run.get("overall_status", "unknown"))
        latest_agent_outcomes = run.get("latest_agent_outcomes", {}) if isinstance(run.get("latest_agent_outcomes", {}), dict) else {}
        tool_calls = run.get("tool_calls", []) if isinstance(run.get("tool_calls", []), list) else []

        outcome_lines = [
            f"run_id: {run_id}",
            f"overall_status: {overall_status}",
        ]

        if latest_agent_outcomes:
            outcome_lines.append("agent_outcomes:")
            for agent, outcome in latest_agent_outcomes.items():
                if not isinstance(outcome, dict):
                    continue
                status = outcome.get("status", "unknown")
                degraded = bool(outcome.get("degraded", False))
                outcome_lines.append(f"- {agent}: status={status}, degraded={degraded}")

        if tool_calls:
            outcome_lines.append("tool_calls:")
            for item in tool_calls[:12]:
                if not isinstance(item, dict):
                    continue
                outcome_lines.append(
                    f"- {item.get('agent', '')}:{item.get('tool_name', '')} -> status={item.get('status', 'unknown')}"
                )

        return {
            "chunk_id": self._make_chunk_id(run_id, "outcome_summary", "summary"),
            "chunk_type": "outcome_summary",
            "source_type": "run_summary",
            "source_id": run_id,
            "run_id": run_id,
            "text": "\n".join(outcome_lines),
            "created_at": str(run.get("created_at", self._now_iso())),
            "updated_at": self._now_iso(),
            "metadata": {
                "overall_status": overall_status,
                "tags": [overall_status],
            },
        }

    def _build_tool_chunks(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        run_id = str(run.get("run_id", ""))
        tool_calls = run.get("tool_calls", []) if isinstance(run.get("tool_calls", []), list) else []
        chunks: list[dict[str, Any]] = []

        for index, item in enumerate(tool_calls, start=1):
            if not isinstance(item, dict):
                continue

            tool_name = str(item.get("tool_name", "unknown_tool"))
            agent = str(item.get("agent", ""))
            status = str(item.get("status", "unknown"))
            tool_transport_status = str(item.get("tool_transport_status", status))
            command_exit_code = item.get("command_exit_code")
            arguments = str(item.get("arguments", ""))
            result_excerpt = str(item.get("result_excerpt", ""))

            if command_exit_code is None:
                command_exit_code = self._extract_command_exit_code(result_excerpt)

            semantic_status = status
            if command_exit_code is not None and int(command_exit_code) != 0:
                semantic_status = "failed"

            text_parts = [
                f"run_id: {run_id}",
                f"agent: {agent}",
                f"tool_name: {tool_name}",
                f"status: {semantic_status}",
                f"tool_transport_status: {tool_transport_status}",
                f"command_exit_code: {command_exit_code}",
                f"arguments: {self._shorten(arguments, 500)}",
            ]
            if result_excerpt:
                text_parts.append(f"result: {self._shorten(result_excerpt, 500)}")

            chunks.append(
                {
                    "chunk_id": self._make_chunk_id(run_id, "tool_param", f"{index:03d}"),
                    "chunk_type": "tool_param",
                    "source_type": "run_summary",
                    "source_id": run_id,
                    "run_id": run_id,
                    "text": "\n".join(text_parts),
                    "created_at": str(run.get("created_at", self._now_iso())),
                    "updated_at": self._now_iso(),
                    "metadata": {
                        "agent": agent,
                        "tool_name": tool_name,
                        "status": semantic_status,
                        "tool_transport_status": tool_transport_status,
                        "command_exit_code": command_exit_code,
                        "seq": item.get("request_seq"),
                        "tags": self._extract_keywords(arguments, limit=6),
                    },
                }
            )

        return chunks

    def build_chunks_from_run(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        chunks = [self._build_task_flow_chunk(run), self._build_outcome_chunk(run)]
        chunks.extend(self._build_tool_chunks(run))
        return chunks

    def build_all_chunks(self) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        if not self.runs_dir.exists():
            return chunks

        for run_file in sorted(self.runs_dir.glob("run_*.json")):
            run = self._safe_load_json(run_file)
            if not run:
                continue
            chunks.extend(self.build_chunks_from_run(run))
        return chunks

    def write_chunks_jsonl(self, chunks: list[dict[str, Any]] | None = None) -> Path:
        payload = chunks if chunks is not None else self.build_all_chunks()
        self.chunks_output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.chunks_output_path.open("w", encoding="utf-8") as f:
            for chunk in payload:
                f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        return self.chunks_output_path


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[1]
    runs_dir = project_root / "memory" / "long_memory" / "runs"
    builder = RunChunkBuilder(runs_dir)
    output_path = builder.write_chunks_jsonl()
    print(f"Wrote chunks to: {output_path}")