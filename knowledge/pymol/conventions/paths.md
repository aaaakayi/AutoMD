# 路径与文件命名约定 (AutoMD 项目)

**Q**: AutoMD 项目的输入/输出文件在哪儿?

---

## 核心规则

1. **PyMOL 跑在 Windows** → 路径必须是 `D:\` 反斜杠
2. **绝对路径** (不要相对)
3. **对象名用 `receptor` / `ligand`** (不是文件名前缀)

## 项目根目录

```
D:\AutoMD\AutoMD_LangGraph\
```

## 输入文件路径 (典型)

```pymol
# 蛋白
load D:\AutoMD\AutoMD_LangGraph\output\03452596\protein\1HX0\receptor\1HX0_protein_only.pdbqt, receptor

# 配体 (对接结果)
load D:\AutoMD\AutoMD_LangGraph\output\03452596\docking\1HX0\vina\docked.pdbqt, ligand
```

## 输出路径约定

```pymol
# 截图 (PNG) 放 pymol 子目录
png D:\AutoMD\AutoMD_LangGraph\output\03452596\pymol\view.png, dpi=300

# 输出目录必须存在
import os
os.makedirs("D:\\AutoMD\\AutoMD_LangGraph\\output\\03452596\\pymol", exist_ok=True)
```

## 命名空间

```
output/<session_id>/<stage>/<task_id>/<...>/
```

| stage | 用途 | 例子 |
|-------|------|------|
| `protein/` | 蛋白准备 | `protein/1HX0/receptor/1HX0_protein_only.pdbqt` |
| `ligand/` | 配体准备 | `ligand/1HX0/amber/ligand_dedup.mol2` |
| `docking/` | 对接结果 | `docking/1HX0/vina/docked.pdbqt` |
| `complex/` | 复合物 | `complex/1HX0/tleap/complex.prmtop` |
| `md/` | MD 结果 | `md/1HX0/prod.nc` |
| `analysis/` | 分析 | `analysis/1HX0/rmsd.dat` |
| `pymol/` | PyMOL 截图 | `pymol/view.png` |

## 在 PyMOL CLI 里创目录

```pymol
# 截图前确保目录存在
python
import os
out_dir = r"D:\AutoMD\AutoMD_LangGraph\output\03452596\pymol"
os.makedirs(out_dir, exist_ok=True)
print(f"Output dir ready: {out_dir}")
python end

# 然后再 png
png D:\AutoMD\AutoMD_LangGraph\output\03452596\pymol\view.png, dpi=300
```

## 多 session 隔离

```pymol
# 不同 session 输出不同子目录
# session_id 来自 Agent State (生成时唯一)

# 例子: 03452596
png D:\AutoMD\AutoMD_LangGraph\output\03452596\pymol\view.png, dpi=300

# 另一个 session: abc123
png D:\AutoMD\AutoMD_LangGraph\output\abc123\pymol\view.png, dpi=300
```

## 在 Python 里生成 Windows 路径

```python
# ❌ 错 (WSL 路径, PyMOL 找不到)
path = "/mnt/d/AutoMD/output/view.png"

# ✅ 对 (Windows 路径, PyMOL 能用)
path = r"D:\AutoMD\AutoMD_LangGraph\output\pymol\view.png"
# 或
path = "D:\\AutoMD\\AutoMD_LangGraph\\output\\pymol\\view.png"

# 从 WSL 路径转换
import os
wsl_path = "/mnt/d/AutoMD/output/view.png"
win_path = "D:" + wsl_path[5:].replace("/", "\\")
# "D:\\AutoMD\\output\\view.png"
```

## 速查

```pymol
# ✅ 输入
load D:\AutoMD\AutoMD_LangGraph\output\{session_id}\protein\{task_id}\receptor\*.pdbqt, receptor
load D:\AutoMD\AutoMD_LangGraph\output\{session_id}\docking\{task_id}\vina\docked.pdbqt, ligand

# ✅ 输出
png D:\AutoMD\AutoMD_LangGraph\output\{session_id}\pymol\view.png, dpi=300

# ❌ 错
load /mnt/d/AutoMD/.../receptor.pdbqt
png /home/user/.../view.png
load ./file.pdbqt
```

## 相关

- `0-overview.md` — PyMOL RPC 基础
- `pitfalls/path-errors.md` — 路径错误排查
- `load/pdb-pdbqt.md` — load 完整路径示例
