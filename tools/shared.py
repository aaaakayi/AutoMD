"""
AutoMD 工具共享配置与统一返回类型。

提供:
- ToolResult: 统一工具返回类型，标注成功/降级/失败状态与降级路径
- 共享路径常量: PROJECT_ROOT, MGLTools 路径, conda 环境名
- 工厂函数: success(), degraded(), failed()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ============================================================================
# 共享路径常量
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMP_ROOT = PROJECT_ROOT / "temp"

MGLTOOLS_ROOT = PROJECT_ROOT / "dock_tools" / "mgltools" / "mgltools_x86_64Linux2_1.5.7"
MGLTOOLS_PCKGS_PATH = MGLTOOLS_ROOT / "MGLToolsPckgs"
CONDA_MGLTOOLS_ENV = "mgltools"

# ============================================================================
# MGLTools 脚本路径
# ============================================================================

def _mgltools_script(*parts: str) -> Path:
    return MGLTOOLS_PCKGS_PATH.joinpath(*parts)


PREPARE_RECEPTOR4_SCRIPT = _mgltools_script("AutoDockTools", "Utilities24", "prepare_receptor4.py")
PREPARE_LIGAND4_SCRIPT = _mgltools_script("AutoDockTools", "Utilities24", "prepare_ligand4.py")

# ============================================================================
# ToolResult
# ============================================================================


@dataclass
class ToolResult:
    """统一的工具返回类型。

    Attributes:
        status: "success" | "degraded" | "failed"
        data: 实际返回值（dict、str 等）
        degradation: 降级步骤列表，如 ["antechamber bcc→gas", "MGLTools→OpenBabel"]
        errors: 遇到的错误列表（即使已恢复也会记录）
        warnings: 非致命警告
    """

    status: str
    data: Any = None
    degradation: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("success", "degraded")

    def format_for_agent(self) -> str:
        """格式化为 Agent 可读的文本。"""
        lines = [f"[{self.status.upper()}]"]
        if self.degradation:
            lines.append(f"降级路径: {' → '.join(self.degradation)}")
        if self.warnings:
            lines.append(f"警告: {'; '.join(self.warnings)}")
        if self.errors:
            lines.append(f"错误(已处理): {'; '.join(self.errors)}")
        if self.data is not None:
            if isinstance(self.data, dict):
                for k, v in self.data.items():
                    if v is not None:
                        lines.append(f"  {k}: {v}")
            else:
                lines.append(str(self.data))
        return "\n".join(lines)


def success(data: Any = None, warnings: list[str] | None = None) -> ToolResult:
    return ToolResult(status="success", data=data, warnings=warnings or [])


def degraded(
    data: Any = None,
    *,
    degradation: list[str] | None = None,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> ToolResult:
    return ToolResult(
        status="degraded",
        data=data,
        degradation=degradation or [],
        errors=errors or [],
        warnings=warnings or [],
    )


def failed(
    data: Any = None,
    *,
    errors: list[str] | None = None,
    degradation: list[str] | None = None,
    warnings: list[str] | None = None,
) -> ToolResult:
    return ToolResult(
        status="failed",
        data=data,
        errors=errors or [],
        degradation=degradation or [],
        warnings=warnings or [],
    )
