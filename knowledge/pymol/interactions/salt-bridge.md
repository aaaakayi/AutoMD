# 盐桥 (带电残基间的强相互作用)

**Q**: 怎么识别并显示蛋白-配体之间的盐桥?

---

## 答案: 用 `distance` + 带电原子选择

**没有专门 command**。标准做法: 选两侧的带电原子 (蛋白 ARG/LYS 阳离子, ASP/GLU 阴离子; 配体带电基团), 测距离 ≤ 4.0 Å。

## 距离判定标准

| 距离 (Å) | 含义 |
|----------|------|
| < 3.0 | 强盐桥 (直接接触) |
| 3.0 - 4.0 | 标准盐桥 |
| 4.0 - 5.0 | 弱盐桥 (水介导) |
| > 5.0 | 不是盐桥 |

## 蛋白侧带电残基

| 残基 | 阳/阴 | 带电原子 |
|------|-------|---------|
| ARG | + | NH1, NH2, NE |
| LYS | + | NZ |
| HIS | + (protonated) | ND1, NE2 |
| ASP | - | OD1, OD2 |
| GLU | - | OE1, OE2 |

## 配体侧带电基团 (依赖具体配体)

**AutoMD 不知道配体带电基团叫什么名字**。**LLM 需要根据 SMILES / 结构推断**:
- 羧酸 `-COOH` → `name O*` 且连接 `name C and bonded_to name O and bonded_to name O and not bonded_to name H` (复杂)
- 胺 `-NH2` / `-NH3+` → `name N*` 且不在 backbone 上
- 磷酸 / 磺酸 → `name P+O*` / `name S+O*`

**简化**: 配体 SMILES 含 `+` / `-` 形式电荷 → 找带电原子的 `name` 字段。

## CLI 写法

```pymol
# 1. 蛋白阳离子 (ARG + LYS)
select prot_cation, receptor and (
    (resn ARG and name NH1+NH2+NE) or
    (resn LYS and name NZ)
)

# 2. 蛋白阴离子 (ASP + GLU)
select prot_anion, receptor and (
    (resn ASP and name OD1+OD2) or
    (resn GLU and name OE1+OE2)
)

# 3. 配体阳离子 (示例: 胺, 名字以 N 开头, 但要排除 H)
select lig_cation, ligand and (name N and not bonded_to name C+H)
# 实际: 选配体的 N 原子, 但排除 amide N

# 4. 配体阴离子 (示例: 羧酸 O, 但要排除 ester O)
select lig_anion, ligand and (name O and not bonded_to name C+H)
# 实际: 选配体的 O 原子, 但排除 carbonyl

# 5. 距离 (盐桥)
distance salt_bridges, prot_cation, lig_anion, 4.0
set dash_color, salmon

distance salt_bridges_rev, prot_anion, lig_cation, 4.0
set dash_color, salmon
```

## 实际场景: 布洛芬 (羧酸) vs 蛋白

```pymol
# 布洛芬 (ibuprofen): 配体有 1 个 -COOH (O*)
# SMILES: CC(C)CC1=CC=C(C=C1)C(C)C(=O)O
# 带电原子: 羧酸 O (deprotonated form: -COO-)

# 1. 配体羧酸 O
select lig_carboxyl_O, ligand and name O* and resn LIG
# 实际: PyMOL 加载 PDBQT 后, 配体 O 原子按原子名, 通常是 O, O1, O2 等

# 2. 蛋白阳离子 (布洛芬盐桥常见对象)
select prot_lys_arg, receptor and (resn LYS+ARG)

# 3. 距离
distance salt, prot_lys_arg, lig_carboxyl_O, 4.0
set dash_color, salmon
set dash_radius, 0.05
```

## 完整示例 (蛋白+配体+盐桥)

```pymol
# 1. 加载
load D:\path\protein.pdbqt, receptor
load D:\path\ligand.pdbqt, ligand

# 2. 基础
show cartoon, receptor
color cyan, receptor
show sticks, ligand
color yellow, ligand

# 3. 盐桥侧链高亮
select salt_residues, byres ((receptor and (
    (resn ARG and name NH1+NH2+NE) or
    (resn LYS and name NZ) or
    (resn ASP and name OD1+OD2) or
    (resn GLU and name OE1+OE2)
)) within 4.0 of ligand)

show sticks, salt_residues and not name N+CA+C+O
color hotpink, salt_residues

# 4. 距离
select lig_charged, ligand and (name N+O and not hydrogens)
select prot_charged, receptor and (
    (resn ARG and name NH1+NH2+NE) or
    (resn LYS and name NZ) or
    (resn ASP and name OD1+OD2) or
    (resn GLU and name OE1+OE2)
)
distance salt_bridges, prot_charged, lig_charged, 4.0
set dash_color, salmon
set dash_radius, 0.05
```

## 关键 ❌ 错

```pymol
# ❌ 距离太大
distance salt, receptor, ligand, 8.0
# → 太多"假阳性"

# ❌ 不区分带电原子
distance salt, ligand, receptor, 4.0
# → 任意原子对都算
```

## ✅ 对

```pymol
# ✅ 限制为带电原子
select prot_ion, receptor and (
    (resn ARG+LYS and name NH*+NZ) or
    (resn ASP+GLU and name OD*+OE*)
)
select lig_ion, ligand and (name N+O and not hydrogens)
distance salt, prot_ion, lig_ion, 4.0
```

## 速查

```pymol
# 蛋白阳离子
select prot_cat, receptor and ((resn ARG and name NH1+NH2+NE) or (resn LYS and name NZ))

# 蛋白阴离子
select prot_ani, receptor and ((resn ASP and name OD1+OD2) or (resn GLU and name OE1+OE2))

# 配体带电 (粗筛, 实际要按 SMILES 推断)
select lig_ion, ligand and (name N+O) and not hydrogens

# 距离
distance salt, prot_cat, lig_ani, 4.0
distance salt2, prot_ani, lig_cat, 4.0
```

## 相关

- `interactions/h-bond.md` — H 键
- `interactions/hydrophobic.md` — 疏水
- `interactions/pi-stacking.md` — π 堆积
- `select/basic.md` — 选 name N/O 原子
