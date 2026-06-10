# 加载配体 MOL2 / SDF 文件

**Q**: 怎么加载配体的 MOL2 / SDF 文件? 配体文件路径在哪?

---

## 加载 MOL2 (AutoMD 标准产物)

```pymol
load D:\AutoMD\AutoMD_LangGraph\output\03452596\ligand\1HX0\amber\ligand.mol2, ligand
show sticks, ligand
color yellow, ligand
util.cbaw ligand
```

**AutoMD 默认**: `ligand.mol2` 是 antechamber 输出, 含 GAFF 原子类型 + 元素信息。

## 加载 SDF (RCSB / PubChem 拉的配体)

```pymol
load D:\path\to\compound.sdf, ligand
show sticks, ligand
color yellow, ligand
```

## MOL2 vs PDBQT vs SDF 区别

| 格式 | 来源 | 元素信息 | 原子类型 | 氢 |
|------|------|---------|---------|---|
| `.mol2` | antechamber (AutoMD 流程) | ✅ | ✅ GAFF | ✅ |
| `.pdbqt` | Vina / MGLTools (AutoMD 对接产物) | ✅ | ✅ AutoDock | 极性氢 |
| `.sdf` | RCSB / PubChem (AutoMD 备选) | ✅ | ❌ | ❌ (需 h_add) |
| `.pdb` | 任意 | ✅ | ❌ | 看源 |

**AutoMD 工作流最常见**: 配体最终用 **PDBQT** (对接产物) 或 **MOL2** (Amber 产物), 不会用 SDF 渲染 (SDF 元素信息不全, PyMOL 显示会有问题)。

## 给 SDF 配体加氢 (如果必须用 SDF)

```pymol
load D:\path\to\ligand.sdf, ligand
h_add ligand              # PyMOL 自动加氢
show sticks, ligand
color yellow, ligand
```

## MOL2 / SDF 对象的常见处理

```pymol
# 重新居中
center ligand
zoom ligand, 5

# 配体内部 bond 显示
show sticks, ligand
hide lines, ligand

# 球+棒 (更大)
show spheres, ligand
set sphere_scale, 0.3, ligand
```

## 多配体叠合 (罕见, 但有需求)

```pymol
# 加载两个配体
load D:\ligand1.pdbqt, lig1
load D:\ligand2.pdbqt, lig2

# 用 align 命令叠合
align lig2, lig1

# 两者同时 sticks
show sticks, lig1
show sticks, lig2
color yellow, lig1
color magenta, lig2
```

## ❌ 错

```pymol
# ❌ 加载 SDF 后没加氢, 直接 distance
load D:\ligand.sdf, ligand
distance h_bond, ligand, receptor, 3.0
# SDF 无 H 原子, H 键检测失效

# ❌ MOL2 当 PDB 用
load D:\ligand.mol2, ligand
show cartoon, ligand
# 配体是 ligand 不是 protein, 没 cartoon
```

## ✅ 对

```pymol
# ✅ MOL2 直接加载 (有 GAFF type + 元素)
load D:\ligand.mol2, ligand
show sticks, ligand

# ✅ SDF 加氢后再分析
load D:\ligand.sdf, ligand
h_add ligand
show sticks, ligand
distance h, ligand, receptor, 3.5, mode=2
```

## 相关

- `load/pdb-pdbqt.md` — 蛋白/PDBQT 加载
- `conventions/paths.md` — 配体文件路径
- `interactions/h-bond.md` — 配体加氢后再做 H 键
- `pitfalls/path-errors.md` — 路径错误
