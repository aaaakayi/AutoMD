# 疏水接触 (蛋白-配体)

**Q**: 怎么识别并显示蛋白-配体之间的疏水接触?

---

## 答案: 没有专门命令, 用 `distance` + `elem C` 限制

```pymol
# 蛋白的 C 原子 (排除极性原子) 与配体 C 原子, 距离 ≤ 4.0 Å
select hydro_contacts, (receptor and elem C and not (name C=O)) within 4.0 of ligand
distance hydro, (ligand and elem C), hydro_contacts, 4.0
```

## 距离判定标准

| 距离 (Å) | 含义 |
|----------|------|
| < 3.5 | 紧密疏水接触 |
| 3.5 - 4.0 | 标准疏水接触 |
| 4.0 - 4.5 | 弱疏水接触 |
| > 4.5 | 不是疏水接触 |

## CLI 写法

```pymol
# 1. 蛋白 C 原子 (排除主链 C=O, 仅疏水 C)
select protein_hydrophobic_C, (receptor and elem C) and not (name C and resn PRO)

# 2. 配体 C 原子
select ligand_C, ligand and elem C

# 3. 4Å 内的疏水接触
distance hydro, ligand_C, protein_hydrophobic_C, 4.0
set dash_color, orange
set dash_radius, 0.04
```

## 简化的等价做法

```pymol
# 一步: 配体周围 4Å 内的所有 C 原子 (蛋白 + 配体自己的 C)
select hydro_atom, (receptor and elem C) within 4.0 of ligand
distance hydro, ligand, hydro_atom, 4.0
```

**这个做法有个小瑕疵**: mode=0, 会显示所有 ligand 到 hydro_atom 的距离对 (可能很多)。但对于小配体 (几十个原子) 还可以接受。

**更好**: 用 mode=2 替代 (虽然疏水不严格符合 H 键几何, mode=2 仍会过滤)

```pymol
distance hydro, ligand, hydro_atom, 4.0, mode=2
```

## 高亮接触残基 (sticks 模式)

```pymol
# 1. 找配体周围 4Å 内的 C
select hydro_atoms, (receptor and elem C) within 4.0 of ligand

# 2. 扩展到整个残基
select hydro_residues, byres hydro_atoms

# 3. 显示侧链 sticks (不显示主链)
show sticks, hydro_residues and not name N+CA+C+O
color orange, hydro_residues

# 4. 半透明主链
set cartoon_transparency, 0.7, receptor
```

## 完整示例 (标准可视化 + 疏水高亮)

```pymol
# 1. 加载
load D:\path\protein.pdbqt, receptor
load D:\path\ligand.pdbqt, ligand

# 2. 基础
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.3, receptor

show sticks, ligand
color yellow, ligand

# 3. 疏水接触残基
select hydro_residues, byres ((receptor and elem C) within 4.0 of ligand)
show sticks, hydro_residues and not name N+CA+C+O
color orange, hydro_residues

# 4. 视图
zoom ligand, 6
```

## 关键 ❌ 错

```pymol
# ❌ 不排除 C=O (主链羰基)
select receptor_C, receptor and elem C
# → 主链 C=O 也会算, 不算疏水

# ❌ 距离过大
distance hydro, ligand, receptor_C, 6.0
# → 太多接触, 没意义

# ❌ 不选 C, 直接用 receptor
distance hydro, ligand, receptor, 4.0
# → N-C, O-C 等也算进来, 不纯
```

## ✅ 对

```pymol
# ✅ 排除 C=O, 限制 4Å
select protein_hydro_C, (receptor and elem C) and not (name C and bonded_to name O)
distance hydro, (ligand and elem C), protein_hydro_C, 4.0

# ✅ 用 byres 扩展到残基高亮
select hydro_residues, byres ((receptor and elem C) within 4.0 of ligand)
show sticks, hydro_residues and not name N+CA+C+O
color orange, hydro_residues
```

## 速查

```pymol
# 找疏水接触
select hydro, (receptor and elem C) within 4.0 of ligand
distance hydro, ligand, hydro, 4.0

# 高亮疏水残基侧链
select hydro_res, byres hydro
show sticks, hydro_res and not name N+CA+C+O
color orange, hydro_res
```

## 相关

- `interactions/h-bond.md` — H 键 (N, O, S)
- `interactions/pi-stacking.md` — 芳香环 π 堆积
- `interactions/pocket.md` — 完整口袋高亮
- `analysis-recipes/standard-binding-view.md` — 蛋白+配体+H 键+疏水完整示例
