import json
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LONG_MEMORY_DIR = PROJECT_ROOT / "memory" / "long_memory"
USER_MEMORY_PATH = LONG_MEMORY_DIR / "user_profile.json"
ALLOWED_USER_CATEGORIES = {"profile", "preference", "long_term_goal"}


def _looks_like_work_progress(text: str) -> bool:
    lowered = text.lower()
    workflow_markers = [
        "蛋白质处理",
        "配体处理",
        "分子对接",
        "对接任务",
        "执行参数",
        "结合能",
        "rmsd",
        "run_id",
        "output/",
        "prepare_",
        "tool",
        "workflow",
        "work_progress",
    ]
    return any(marker in lowered for marker in workflow_markers)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_memory_root() -> None:
    LONG_MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_user_memory() -> dict:
    _ensure_memory_root()
    if not USER_MEMORY_PATH.exists():
        return {"facts": [], "updated_at": _now_iso()}
    try:
        return json.loads(USER_MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"facts": [], "updated_at": _now_iso()}


def _save_user_memory(data: dict) -> None:
    _ensure_memory_root()
    data["updated_at"] = _now_iso()
    USER_MEMORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_user_memory(content: str, category: str = "profile") -> str:
    """Persist a user fact so coordinator can recall it across runs."""
    text = (content or "").strip()
    if not text:
        return "未写入：content 为空。"

    normalized_category = (category or "profile").strip().lower() or "profile"
    if normalized_category not in ALLOWED_USER_CATEGORIES:
        return (
            "未写入：category 不属于用户画像记忆类型。"
            "仅允许 profile/preference/long_term_goal。"
        )

    if _looks_like_work_progress(text):
        return "未写入：检测到任务流程/执行进展内容，请写入 runs 级长期记忆而非 user_profile。"

    data = _load_user_memory()
    facts = data.setdefault("facts", [])

    normalized = text.lower()
    for item in facts:
        if isinstance(item, dict) and str(item.get("content", "")).strip().lower() == normalized:
            return "已存在同样记忆，跳过重复写入。"

    facts.append(
        {
            "content": text,
            "category": normalized_category,
            "created_at": _now_iso(),
            "source": "coordinator_chat",
        }
    )
    _save_user_memory(data)
    return f"已写入长期记忆，共 {len(facts)} 条。"


def recall_user_memory(query: str = "", top_k: int = 5) -> str:
    """Search saved user facts with simple substring matching."""
    data = _load_user_memory()
    facts = data.get("facts", [])
    if not facts:
        return "暂无长期记忆。"

    q = (query or "").strip().lower()
    if q:
        matched = [f for f in facts if q in str(f.get("content", "")).lower()]
    else:
        matched = facts

    if not matched:
        return "没有匹配到相关长期记忆。"

    limited = matched[-max(1, int(top_k)) :]
    lines = ["命中的长期记忆："]
    for idx, item in enumerate(limited, start=1):
        lines.append(
            f"{idx}. [{item.get('category', 'profile')}] {item.get('content', '')}"
            f" (记录于 {item.get('created_at', '')})"
        )
    return "\n".join(lines)
