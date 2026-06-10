# 加载 PDB / PDBQT 文件

**Q**: 怎么加载蛋白 PDB/PDBQT 文件? 怎么用 `fetch` 直接拉 PDB?

---

## 加载 PDBQT (最常见 — AutoMD 流程产物)

```pymol
# 加载蛋白
load D:\AutoMD\AutoMD_LangGraph\output\03452596\protein\1HX0\receptor\1HX0_protein_only.pdbqt, receptor
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.3, receptor

# 加载配体
load D:\AutoMD\AutoMD_LangGraph\output\03452596\docking\1HX0\vina\docked.pdbqt, ligand
show sticks, ligand
color yellow, ligand
util.cbaw ligand
```

## 加载 PDB (AutoMD 通常不直接产生 PDB, 但 fetch 后可以保存)

```pymol
load D:\path\to\protein.pdb, receptor
show cartoon, receptor
color cyan, receptor
```

## `fetch` 直接拉 PDB (离线备选)

```pymol
# 拉 PDB 到默认对象名 (4 字符 PDB ID)
fetch 1hsg

# 拉 PDB 并重命名
fetch 1hsg, myprot
show cartoon, myprot
color cyan, myprot
```

**注意**:
- `fetch` 走 PDB 官方 (RCSB) 网络下载
- 仅支持 4 字符 PDB ID, 不支持自定义路径
- 大 PDB (如核糖体) 会等几秒到几十秒
- 离线场景用 `load` + 本地文件

## 多文件加载

```pymol
# 顺序加载多个
load D:\path\to\file1.pdbqt, prot1
load D:\path\to\file2.pdbqt, prot2
load D:\path\to\ligand.pdbqt, ligand

# 检查加载了哪些
get_names
# 输出: ['prot1', 'prot2', 'ligand']
```

## 对象命名 (AutoMD 约定)

| 文件类型 | 默认对象名 | 备注 |
|---------|---------|------|
| 蛋白 | `receptor` | 来自 `_load_scene` 命名 |
| 配体 | `ligand` | 同上 |
| 其他 | 自定义 | 建议用描述性名字: `wt`, `mut`, `inhibitor_a` |

## 参数速查

| 命令 | 用途 | 例子 |
|------|------|------|
| `load <path>, <name>` | 加载文件, 可选命名 | `load D:\foo.pdb, obj1` |
| `fetch <pdb_id>` | 拉 PDB, 默认名 = pdb_id | `fetch 1hsg` |
| `fetch <pdb_id>, <name>` | 拉 PDB 并重命名 | `fetch 1hsg, myprot` |
| `get_names` | 列出所有对象名 | (无参数) |
| `delete <name>` | 删除对象 | `delete obj1` |
| `remove waters` | 删水 (PDB 有水时) | (无参数, 默认 all) |
| `remove solvent` | 删溶剂 (alias) | 同上 |

## 默认显示样例 (AutoMD `_load_scene` 风格)

```pymol
# 蛋白
show cartoon, receptor
color cyan, receptor
hide everything, receptor; show cartoon, receptor  # 强制只有 cartoon

# 配体
show sticks, ligand
color yellow, ligand

# 隐藏水
hide everything, solvent
hide everything, inorganic
```

## 关键 ❌ 错

```pymol
# ❌ WSL 路径
load /mnt/d/AutoMD/foo.pdb, receptor
# → "ERROR: Could not open file"

# ❌ 相对路径 (不知道当前目录)
load foo.pdb, receptor
# → 找不到文件

# ❌ 加载完不用
load D:\foo.pdb, receptor
# → receptor 被定义了但没 show/color
```

## ✅ 对

```pymol
# ✅ Windows 绝对路径
load D:\AutoMD\AutoMD_LangGraph\output\03452596\protein\1HX0\receptor\1HX0_protein_only.pdbqt, receptor

# ✅ 加载 + 立即显示 + 配色
load D:\foo.pdbqt, receptor
show cartoon, receptor
color cyan, receptor
```

## 相关

- `0-overview.md` — RPC 接口与路径转换总览
- `conventions/paths.md` — `output/{session_id}/...` 路径模板
- `conventions/color-palette.md` — AutoMD 配色约定
- `load/mol2-sdf.md` — 配体 mol2/sdf 加载
- `pitfalls/path-errors.md` — 路径错误典型
