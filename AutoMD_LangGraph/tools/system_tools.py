import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from tools.shared import success, failed, ToolResult


_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_DANGEROUS_COMMAND_PATTERNS = (
    r"\brm\s+-rf\s+/",                # rm -rf / (root)
    r"\brm\s+-rf\s+\$",               # rm -rf $VAR (unexpanded)
    r"\brm\s+-rf\s+~",                # rm -rf ~ (home)
    r"\bsudo\b",
    r"\beval\b",
    r"\bchmod\s+777\b",
    r"\bcurl\s+.*\|\s*(?:ba)?sh\b",   # curl pipe shell
    r"\bwget\s+.*\|\s*(?:ba)?sh\b",   # wget pipe shell
    r"\b/dev/null\b.*>\s*/dev/",       # writing to device files
    r"\bdd\s+if=",
    r"\bmkfs\b",
    r"\bshutdown\b",
    r"\brestart\b",
    r"\bformat\s+C:\\",
)


def _is_dangerous_command(command: str) -> bool:
    normalized = (command or "").lower()
    for pattern in _DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return True
    return False


def _resolve_path(path: str, *, allow_outside_project: bool) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (_PROJECT_ROOT / p).resolve()
    else:
        p = p.resolve()

    if not allow_outside_project:
        try:
            p.relative_to(_PROJECT_ROOT)
        except ValueError as e:
            raise ValueError(f"不允许访问项目目录外的路径: {p}") from e
    return p


def run_shell_command(
    command: str,
    cwd: Optional[str] = None,
    timeout_seconds: int = 120,
    max_output_chars: int = 20000,
    env: Optional[dict] = None,
) -> ToolResult:
    """
    执行任意 Shell 命令并返回输出（stdout+stderr）、退出码与工作目录信息。

    说明：
    - 在 Windows 下使用 PowerShell/CMD 的语法；在 Linux/WSL 下使用 /bin/sh 语法。
    - 本函数是“任意命令执行”，请只在受信任环境使用。
    """
    if not command or not command.strip():
        return failed(errors=["command 不能为空。"])

    if _is_dangerous_command(command):
        return failed(errors=[f"命令被安全策略拒绝：检测到潜在危险操作 - {command}"])

    resolved_cwd = None
    if cwd:
        resolved_cwd = str(_resolve_path(cwd, allow_outside_project=False))
    else:
        resolved_cwd = str(_PROJECT_ROOT)

    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=resolved_cwd,
            env=merged_env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="replace") + (e.stderr or b"").decode(errors="replace"))
        out = out[-max_output_chars:] if max_output_chars > 0 else out
        return failed(
            errors=[f"命令执行超时（>{timeout_seconds}s）"],
            data={"command": command, "partial_output": out},
        )
    except Exception as e:
        return failed(errors=[f"命令执行失败：{type(e).__name__}: {e}"])

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = stdout + (("\n" if stdout and stderr else "") + stderr if stderr else "")
    if max_output_chars > 0 and len(combined) > max_output_chars:
        combined = combined[-max_output_chars:]

    return success(
        data={
            "exit_code": completed.returncode,
            "cwd": resolved_cwd or os.getcwd(),
            "output": combined,
        }
    )


# ---------------------------------------------------------------------------
# File summarizers — extract key metadata from large domain-specific files
# ---------------------------------------------------------------------------


def _summarize_pdb(p: Path, ext: str) -> str:
    """PDB/PDBQT: 残基范围、原子数、MODEL 数、CONECT 键连信息。"""
    chains: dict = {}
    hetatms: list[str] = []
    model_count = 0
    conect_count = 0
    conect_pairs: set = set()
    conect_dup = 0
    anisou_count = 0
    total_atoms = 0

    with open(p, errors="replace") as f:
        for line in f:
            if line.startswith("MODEL"):
                model_count += 1
            elif line.startswith("ATOM") or line.startswith("HETATM"):
                total_atoms += 1
                chain = line[21] or "_"
                try:
                    resnum = int(line[22:26])
                except ValueError:
                    resnum = 0
                resname = line[17:20].strip()
                chains.setdefault(chain, {})[(resnum, resname)] = True
                if line.startswith("HETATM"):
                    hetatms.append(resname)
            elif line.startswith("ANISOU"):
                anisou_count += 1
            elif line.startswith("CONECT"):
                conect_count += 1
                atoms = tuple(sorted(line[6:].split()))
                if atoms in conect_pairs:
                    conect_dup += 1
                else:
                    conect_pairs.add(atoms)

    lines: list[str] = [f"[{ext} SUMMARY] {p.name}", f"  Size: {p.stat().st_size}B, Atoms: {total_atoms}"]

    for chain, residues in sorted(chains.items()):
        nums = sorted(set(r[0] for r in residues))
        names = sorted(set(r[1] for r in residues))
        if nums:
            lines.append(f"  Chain {chain}: {names[0] if names else '?'}{nums[0]} → {names[-1] if names else '?'}{nums[-1]} ({len(residues)} residues)")

    if model_count:
        lines.append(f"  MODELs: {model_count} (multi-model file)")
    if conect_count:
        lines.append(f"  CONECT: {conect_count} records, unique pairs={len(conect_pairs)}, duplicates={conect_dup}")
    else:
        lines.append(f"  CONECT: 0 records (no bond info — may cause bondtype errors)")
    if anisou_count:
        lines.append(f"  ANISOU: {anisou_count} records (warning: some tools choke on ANISOU)")
    het_names = sorted(set(hetatms))
    if het_names:
        lines.append(f"  HETATM residues: {het_names}")

    return "\n".join(lines)


def _summarize_prmtop(p: Path) -> str:
    """prmtop: 提取 %FLAG sections 的元数据。"""
    version = ""; pointers: dict = {}; atom_types: list[str] = []
    residue_labels: list[str] = []; charge_vals: list[float] = []
    current_flag = ""; flag_count = 0; in_section = False
    lines_buf: list[str] = []

    with open(p, errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("%FLAG"):
                flag_count += 1
                current_flag = stripped[len("%FLAG"):].strip()
                in_section = True
                lines_buf = []
            elif stripped.startswith("%FORMAT") or stripped.startswith("%COMMENT"):
                continue
            elif in_section:
                if not stripped and lines_buf:
                    data_str = " ".join(lines_buf)
                    if current_flag == "VERSION":
                        version = data_str[:120]
                    elif current_flag == "POINTERS":
                        vals = data_str.split()
                        keys = ["NATOM","NTYPES","NBONH","MBONA","NTHETH","MTHETA",
                                "NPHIH","MPHIA","NHPARM","NPARM","NEXT","NRES",
                                "NBONA","NTHETA","NPHIA","NUMBND","NUMANG","NPTRA",
                                "NATYP","NPHB","IFPERT","NBPER","NGPER","NDPER",
                                "MBPER","MGPER","MDPER","IFBOX","NMXRS","IFCAP","NEXTRA"]
                        for i, k in enumerate(keys):
                            if i < len(vals):
                                try:
                                    pointers[k] = int(float(vals[i]))
                                except ValueError:
                                    pointers[k] = vals[i]
                    elif current_flag == "ATOM_TYPE_INDEX":
                        atom_types = data_str.split()
                    elif current_flag == "RESIDUE_LABEL":
                        residue_labels = data_str.split()
                    elif current_flag == "CHARGE":
                        charge_vals = [float(v) for v in data_str.split()]
                    lines_buf = []
                    if current_flag != "TITLE":
                        in_section = False
                else:
                    lines_buf.append(stripped)

    lines: list[str] = [f"[prmtop SUMMARY] {p.name}"]
    lines.append(f"  Size: {p.stat().st_size}B, Sections: {flag_count}")
    if version:
        lines.append(f"  Version: {version[:100]}")
    natom = pointers.get("NATOM", "?")
    nres = pointers.get("NRES", "?")
    lines.append(f"  NATOM={natom}, NRES={nres}, NTYPES={pointers.get('NTYPES','?')}")
    if atom_types:
        unique = sorted(set(atom_types))
        lines.append(f"  Atom types ({len(atom_types)}): {', '.join(unique[:20])}{'...' if len(unique) > 20 else ''}")
    if residue_labels:
        unique_res = sorted(set(residue_labels))
        res_preview = unique_res[:15]
        lines.append(f"  Residues ({len(residue_labels)}): {', '.join(res_preview)}{'...' if len(unique_res) > 15 else ''}")
    if charge_vals:
        lines.append(f"  Charge range: [{min(charge_vals):.3f}, {max(charge_vals):.3f}], net={sum(charge_vals):.3f}")

    # 完整性检查
    expected_per_atom = 200
    if isinstance(natom, int) and natom > 0:
        ratio = p.stat().st_size / natom
        if ratio < expected_per_atom * 0.5:
            lines.append(f"  WARNING: file may be incomplete ({ratio:.0f}B/atom, expected ~{expected_per_atom})")

    return "\n".join(lines)


def _summarize_inpcrd(p: Path) -> str:
    """inpcrd: 原子数、坐标范围、box vectors。"""
    total_lines = 0; coord_count = 0
    xs: list[float] = []; ys: list[float] = []; zs: list[float] = []
    has_box = False; title = ""
    first = True

    with open(p, errors="replace") as f:
        for line in f:
            total_lines += 1
            stripped = line.strip()
            if first:
                title = stripped[:80]
                first = False
                continue
            if not stripped:
                continue
            parts = stripped.split()
            if len(parts) == 6 and not has_box:
                try:
                    floats = [float(v) for v in parts]
                    xs.extend(floats[0::3])
                    ys.extend(floats[1::3])
                    zs.extend(floats[2::3])
                    coord_count += 2
                except ValueError:
                    has_box = True
            elif len(parts) == 3:
                try:
                    floats = [float(v) for v in parts]
                    has_box = True  # box vectors
                except ValueError:
                    pass

    est_atoms = coord_count * 3
    lines: list[str] = [f"[inpcrd SUMMARY] {p.name}"]
    lines.append(f"  Size: {p.stat().st_size}B, Lines: {total_lines}, Est. atoms: {est_atoms}")
    lines.append(f"  Title: {title[:80]}")
    if xs:
        lines.append(f"  X: [{min(xs):.2f}, {max(xs):.2f}]  Y: [{min(ys):.2f}, {max(ys):.2f}]  Z: [{min(zs):.2f}, {max(zs):.2f}]")
    lines.append(f"  Box vectors: {'YES' if has_box else 'NO'}")
    return "\n".join(lines)


def _summarize_dcd(p: Path, size_bytes: int) -> str:
    """DCD: 二进制轨迹文件。"""
    return (
        f"[DCD SUMMARY] {p.name}\n"
        f"  Size: {size_bytes}B ({size_bytes/1024/1024:.1f}MB)\n"
        f"  Binary trajectory file — cannot read as text.\n"
        f"  Use mdtraj, MDAnalysis, or openmm.app.DCDFile for analysis."
    )


def _summarize_xml(p: Path, max_chars: int) -> str:
    """XML: 提取粒子数和力场，给出头尾片段。"""
    content = p.read_text(encoding="utf-8", errors="replace")
    n_particles = 0
    ff_name = ""
    import re
    m = re.search(r'<System[^>]*>', content)
    if m:
        pass
    m = re.search(r'NonbondedForce', content)
    if m:
        pass
    for line in content.splitlines():
        if "numParticles" in line:
            mm = re.search(r'numParticles="(\d+)"', line)
            if mm:
                n_particles = int(mm.group(1))
    half = max_chars // 2
    head = "\n".join(content.splitlines()[:8]) if len(content) > half else content
    tail = "\n".join(content.splitlines()[-5:]) if len(content) > half else ""

    return (
        f"[XML SUMMARY] {p.name}\n"
        f"  Size: {p.stat().st_size}B, numParticles: {n_particles}\n"
        f"  --- head ---\n{head}\n"
        f"  --- tail ---\n{tail}"
    )


def _summarize_mol2(p: Path) -> str:
    """mol2: @<TRIPOS>MOLECULE 解析 + 原子类型 + 电荷方法。"""
    content = p.read_text(encoding="utf-8", errors="replace")
    lines_out: list[str] = [f"[mol2 SUMMARY] {p.name}", f"  Size: {p.stat().st_size}B"]
    in_atom = False
    atom_types: list[str] = []
    atom_count = 0
    mol_info = ""

    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "@<TRIPOS>MOLECULE":
            mol_info = "found"
            continue
        if mol_info == "found":
            mol_info = stripped
            continue
        if stripped == "@<TRIPOS>ATOM":
            in_atom = True
            continue
        if in_atom and stripped.startswith("@<TRIPOS>"):
            in_atom = False
            continue
        if in_atom and stripped:
            parts = stripped.split()
            if len(parts) >= 6:
                atom_types.append(parts[5])
                atom_count += 1

    lines_out.append(f"  MOLECULE: {mol_info}")
    lines_out.append(f"  Atoms: {atom_count}")
    unique = sorted(set(atom_types))
    lines_out.append(f"  Atom types: {', '.join(unique)}")
    has_H = any(t == "H" or t.startswith("H ") for t in unique)
    has_hc = any(t in ("hc", "ha", "hn", "ho", "h1", "h2", "h3", "h4", "h5") for t in unique)
    if has_H and not has_hc:
        lines_out.append("  WARNING: hydrogen type is generic 'H' — may cause parmchk2/tLEaP errors (need hc/ha/hn etc.)")
    if "GASTEIGER" in content:
        lines_out.append("  Charge: GASTEIGER")
    elif "BCC" in content or "bcc" in content:
        lines_out.append("  Charge: BCC")

    full_content = f"文件内容（{p}）：\n{content}"
    return "\n".join(lines_out) + "\n\n" + full_content


def _read_tail(p: Path, max_chars: int, encoding: str) -> str:
    """LOG/TXT: 返回末尾内容（错误信息在末尾）。"""
    content = p.read_text(encoding=encoding, errors="replace")
    if len(content) <= max_chars:
        return f"文件内容（{p}）：\n{content}"
    tail = content[-max_chars:]
    return f"[尾{max_chars}字符] {p.name} ({p.stat().st_size}B):\n{tail}"


def read_text_file(
    path: str,
    max_chars: int = 20000,
    allow_outside_project: bool = False,
    encoding: str = "utf-8",
) -> ToolResult:
    """读取文件内容并返回摘要（大文件按类型分诊，避免上下文溢出）。"""
    try:
        p = _resolve_path(path, allow_outside_project=allow_outside_project)
        if not p.exists():
            return failed(errors=[f"文件不存在: {p}"])
        if p.is_dir():
            return failed(errors=[f"目标是目录而不是文件: {p}"])

        ext = p.suffix.lower()
        size_bytes = p.stat().st_size

        if ext in (".pdb", ".pdbqt"):
            return success(data=_summarize_pdb(p, ext))
        if ext == ".prmtop":
            return success(data=_summarize_prmtop(p))
        if ext == ".inpcrd":
            return success(data=_summarize_inpcrd(p))
        if ext == ".dcd":
            return success(data=_summarize_dcd(p, size_bytes))
        if ext == ".xml":
            return success(data=_summarize_xml(p, max_chars))
        if ext in (".mol2",):
            return success(data=_summarize_mol2(p))
        if ext in (".log", ".txt"):
            return success(data=_read_tail(p, max_chars, encoding))

        # Default: full content for small files, head+tail for large
        content = p.read_text(encoding=encoding, errors="replace")
        if len(content) <= max_chars:
            return success(data=f"文件内容（{p}）：\n{content}")

        half = max_chars // 2
        truncated = (
            f"[文件 {size_bytes}B, 截断显示头{half}+尾{half}字符]\n"
            + content[:half]
            + f"\n... [{len(content) - max_chars} 字符省略] ...\n"
            + content[-half:]
        )
        return success(data=f"文件内容（{p}）：\n{truncated}")

    except Exception as e:
        return failed(errors=[f"读取文件失败: {type(e).__name__}: {e}"])


def write_text_file(
    path: str,
    content: str,
    mode: str = "w",
    allow_outside_project: bool = False,
    encoding: str = "utf-8",
) -> ToolResult:
    """
    写入内容到文件。

    Args:
        path: 目标路径（相对路径默认相对项目根目录）
        content: 要写入的文本
        mode: 'w' 覆盖写入，'a' 追加写入
    """
    if mode not in {"w", "a"}:
        return failed(errors=["mode 只能是 'w' 或 'a'。"])
    try:
        p = _resolve_path(path, allow_outside_project=allow_outside_project)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open(mode, encoding=encoding, errors="replace", newline="\n") as f:
            f.write(content or "")
        return success(data={"path": str(p), "mode": mode, "chars": len(content or '')})
    except Exception as e:
        return failed(errors=[f"写入文件失败: {type(e).__name__}: {e}"])


def read_error_report(
    *,
    log_path: Optional[str] = None,
    raw_error_text: Optional[str] = None,
    max_chars: int = 20000,
    allow_outside_project: bool = False,
) -> ToolResult:
    """
    读取“相关报错”。

    用法二选一：
    - 提供 log_path：读取日志文件/终端输出文件
    - 提供 raw_error_text：直接传入报错文本（用于把报错作为上下文返回给 agent）
    """
    if (log_path is None) == (raw_error_text is None):
        return failed(errors=["请二选一提供 log_path 或 raw_error_text。"])

    if log_path is not None:
        return read_text_file(
            log_path,
            max_chars=max_chars,
            allow_outside_project=allow_outside_project,
        )

    text = raw_error_text or ""
    if max_chars > 0 and len(text) > max_chars:
        text = text[-max_chars:]
    return success(data={"error_text": text})

