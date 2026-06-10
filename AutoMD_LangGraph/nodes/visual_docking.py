"""
Visual docking node: launch PyMOL via XML-RPC, let user set the docking box
visually, read box parameters back, and feed them to downstream Vina docking.

Replaces pocket_detection (P2Rank) when docking_mode == "visual_box".
"""

from __future__ import annotations

import os
import re
import sys
import time
import platform
import subprocess
from pathlib import Path

nodes_dir = os.path.dirname(__file__)
package_root = os.path.abspath(os.path.join(nodes_dir, ".."))
project_root = Path(os.path.abspath(os.path.join(nodes_dir, "..", "..")))
if package_root not in sys.path:
    sys.path.insert(0, package_root)
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import xmlrpc.client
from langgraph.types import Command, interrupt

from State import AutoMDState
from .common import work_root, ensure_dir
import nodes.print_utils as pu

from dotenv import load_dotenv
load_dotenv()


def _resolve_rpc_url() -> str:
    """解析 PyMOL RPC 地址, 3 优先级:
      1. PYMOL_RPC_HOST env (用户显式覆盖, 优先级最高)
      2. 容器内自动检测 (/.dockerenv 存在) → host.docker.internal
      3. 本地 dev → 127.0.0.1
    """
    explicit_host = os.getenv("PYMOL_RPC_HOST", "").strip()
    if explicit_host:
        host = explicit_host
    elif os.path.exists("/.dockerenv"):
        # Docker 容器内, 用 host.docker.internal 连宿主
        host = "host.docker.internal"
    else:
        host = "127.0.0.1"
    port = int(os.getenv("PYMOL_RPC_PORT", "9123").strip())
    return f"http://{host}:{port}"


RPC_URL = _resolve_rpc_url()
_RPC_TIMEOUT = float(os.getenv("PYMOL_RPC_TIMEOUT", "30.0"))


def _is_wsl() -> bool:
    return "microsoft" in platform.release().lower() or "wsl" in platform.release().lower()


def _to_win_path(linux_path: str) -> str:
    """Convert WSL Linux path to Windows path for PyMOL on Windows side.
    /mnt/d/AutoMD/... -> D:\\AutoMD\\...
    """
    if not linux_path or not linux_path.startswith("/mnt/") or not _is_wsl():
        return linux_path
    drive = linux_path[5].upper()
    rest = linux_path[7:].replace("/", "\\")
    return f"{drive}:\\{rest}"


def _to_wsl_path(win_path: str) -> str:
    """Convert Windows path to WSL Linux path.
    D:\\AutoMD\\... -> /mnt/d/AutoMD/...
    Uses `wslpath -u` if available, otherwise falls back to simple conversion.
    """
    if not win_path:
        return win_path

    # If already WSL-style, normalize
    if win_path.startswith("/mnt/"):
        return str(Path(win_path).resolve())

    # Try wslpath locally (if running in WSL) or via `wsl` helper on Windows
    try:
        out = subprocess.check_output(["wslpath", "-u", win_path], text=True).strip()
        if out:
            return out
    except Exception:
        try:
            out = subprocess.check_output(["wsl", "wslpath", "-u", win_path], text=True).strip()
            if out:
                return out
        except Exception:
            pass

    # Pure-Python fallback: C:\foo\bar -> /mnt/c/foo/bar
    p = win_path.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2)
        return f"/mnt/{drive}/{rest}"

    # Best-effort: convert backslashes and ensure leading slash
    return p if p.startswith("/") else f"/mnt/{p.replace(':','').replace('/','/')}"


# Normalize project_root and RPC path representations
project_root = Path(__file__).resolve().parents[2]
_RPC_WSL_CHECK = (project_root / "AutoMD_LangGraph" / "scripts" / "pymol_rpc_server.py").resolve()
_RPC_WIN = _to_win_path(str(_RPC_WSL_CHECK))

# Load PyMOL path from .env and normalize
pymol_path = os.getenv("PYMOL_PATH", "").strip()
if pymol_path:
    # If user provided a Windows-style absolute path (e.g. D:\\pymol\\PyMOLWin.exe),
    # detect it and treat as Windows absolute so we don't prepend project_root in WSL.
    if re.match(r"^[A-Za-z]:[\\/].*", pymol_path):
        _PYMOL_WIN = pymol_path
        _PYMOL_WSL_CHECK = _to_wsl_path(_PYMOL_WIN)
    else:
        p = Path(pymol_path)
        if not p.is_absolute():
            p = (project_root / p).resolve()
        _PYMOL_WIN = str(p)
        _PYMOL_WSL_CHECK = _to_wsl_path(_PYMOL_WIN)
else:
    _PYMOL_WIN = ""
    _PYMOL_WSL_CHECK = "/mnt/d/pymol/PyMOLWin.exe"


def _find_pymol() -> str:
    if _is_wsl():
        if Path(_PYMOL_WSL_CHECK).exists():
            return _PYMOL_WIN  # PyMOL expects Windows-style paths in WSL interop
        raise FileNotFoundError(
            f"Windows PyMOL not found at {_PYMOL_WSL_CHECK} (Windows: {_PYMOL_WIN})"
        )
    import shutil
    found = shutil.which("pymol")
    if found:
        return found
    for p in ["/usr/bin/pymol", "/usr/local/bin/pymol"]:
        if Path(p).exists():
            return p
    raise FileNotFoundError("PyMOL not found. Install pymol or set PATH.")


def _find_rpc_server() -> str:
    if _is_wsl():
        if Path(_RPC_WSL_CHECK).exists():
            return _RPC_WIN  # PyMOL expects Windows-style paths in WSL interop
        raise FileNotFoundError(
            f"RPC server not found at {_RPC_WSL_CHECK} (Windows: {_RPC_WIN})"
        )
    path = (project_root / "AutoMD_LangGraph" / "scripts" / "pymol_rpc_server.py").resolve()
    if path.exists():
        return str(path)
    raise FileNotFoundError(f"RPC server not found at {path}")


def _start_pymol():
    exe = _find_pymol()
    rpc = _find_rpc_server()
    if _is_wsl():
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", exe, "-r", rpc],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            [exe, "-r", rpc],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def _try_connect(url=RPC_URL, timeout=2.0):
    """Quick check: is PyMOL RPC already available?"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = xmlrpc.client.ServerProxy(url, allow_none=True)
            if s.ping():
                return s
        except Exception:
            time.sleep(0.2)
    return None


def _wait_for_rpc(url=RPC_URL, timeout=_RPC_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = xmlrpc.client.ServerProxy(url, allow_none=True)
            if s.ping():
                return s
        except Exception:
            time.sleep(0.3)
    raise TimeoutError(f"PyMOL RPC server not responding at {url}")


def _ensure_pymol_rpc():
    """Connect to PyMOL RPC; launch PyMOL only if not already running.

    On LangGraph interrupt re-execution, PyMOL is still alive from the first
    launch — _try_connect finds it and avoids starting a duplicate window.
    """
    server = _try_connect()
    if server is not None:
        return server
    _start_pymol()
    return _wait_for_rpc()


def _load_scene(server, receptor, ligand):
    """Load/refresh molecules in PyMOL. Idempotent — safe to call on re-exec."""
    rec_path = _to_win_path(receptor)
    lig_path = _to_win_path(ligand) if ligand else ""
    server.delete("receptor")
    server.delete("ligand")
    server.load(rec_path, "receptor")
    server.do("show cartoon, receptor")
    server.do("color cyan, receptor")
    if lig_path:
        server.load(lig_path, "ligand")
        server.do("show sticks, ligand")
        server.do("color yellow, ligand")
    server.zoom("receptor")
    server.do("deselect")


def visual_docking(state: AutoMDState) -> Command:
    pid = state.get("project_id") or "default"
    receptor = state.get("protein_receptor_pdbqt")
    ligand = state.get("ligand_pdbqt")

    ensure_dir(work_root(state) / "docking" / pid / "visual")
    pu.step("可视对接")

    # ── 1. Start PyMOL (connects to existing on interrupt re-execution) ──
    try:
        server = _ensure_pymol_rpc()
    except (FileNotFoundError, TimeoutError) as e:
        pu.warn(f"PyMOL 不可用: {e}，回退 P2Rank")
        return Command(
            update={"docking_summary": f"PyMOL not available: {e}"},
            goto="pocket_detection",
        )

    # ── 2. Load/refresh molecules ──
    try:
        _load_scene(server, receptor, ligand)
    except Exception as e:
        pu.warn(f"PyMOL load failed: {e}，回退 P2Rank")
        return Command(
            update={"docking_summary": f"PyMOL load error: {e}"},
            goto="pocket_detection",
        )

    # ── 3. Wait for user ──
    choice = interrupt(
        "PyMOL 已启动。蛋白(青色卡通)和配体(黄色棍状)已加载。\n\n"
        "在 PyMOL 中选择对接区域:\n"
        "  A) 拖动配体到你想要进行对接的位置 → 输入 'done'      (用受体包围盒 + 6A padding)\n"
        "  B) PyMOL 中点选结合位点残基 → 创建 sele 选择 → 'done'  (用选中区域包围盒)\n"
        "  C) 直接在此输入坐标: cx,cy,cz sx,sy,sz\n\n"
        "输入 'done' 读取 PyMOL 盒子, 或输入坐标, 或 'skip' 回退 P2Rank 盲对接"
    ).strip()

    # ── 5. Read result ──
    if choice.lower() in ("skip",):
        pu.info("用户跳过可视对接，回退 P2Rank")
        return Command(
            update={"docking_summary": "用户跳过可视对接"},
            goto="pocket_detection",
        )

    if choice.lower() == "done":
        box = server.get_docking_box()
        if not isinstance(box, dict) or "center_x" not in box:
            pu.warn(f"PyMOL get_docking_box 失败: {box}, 回退 P2Rank")
            return Command(
                update={"docking_summary": f"PyMOL box read failed: {box}"},
                goto="pocket_detection",
            )
        docking_box = box
    else:
        parts = choice.replace(",", " ").split()
        if len(parts) < 6:
            pu.warn(f"坐标格式错误: {choice}, 回退 P2Rank")
            return Command(
                update={"docking_summary": f"bad box coords: {choice}"},
                goto="pocket_detection",
            )
        docking_box = {
            "center_x": float(parts[0]), "center_y": float(parts[1]), "center_z": float(parts[2]),
            "size_x": float(parts[3]), "size_y": float(parts[4]), "size_z": float(parts[5]),
        }

    pu.ok("可视对接",
          f"box=({docking_box['center_x']:.1f},{docking_box['center_y']:.1f},{docking_box['center_z']:.1f}) "
          f"/ ({docking_box['size_x']:.1f},{docking_box['size_y']:.1f},{docking_box['size_z']:.1f})")

    return Command(
        update={
            "docking_box": docking_box,
            "docking_summary": (
                f"可视对接: box=({docking_box['center_x']:.1f},{docking_box['center_y']:.1f},{docking_box['center_z']:.1f}) "
                f"/ ({docking_box['size_x']:.1f},{docking_box['size_y']:.1f},{docking_box['size_z']:.1f})"
            ),
        },
        goto="docking_setup",
    )
