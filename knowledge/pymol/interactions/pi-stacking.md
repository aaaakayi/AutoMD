# π-π 堆积 (芳香环平面距离)

**Q**: 怎么识别并显示蛋白-配体之间的 π-π 堆积 (芳香环相互作用)?

---

## 答案: 用 `distance` + 芳香原子选择

**没有专门 command**。标准做法: 找出两侧的芳香环原子, 测距离 + 显示环。

## 距离判定标准

| 距离 (Å) | 含义 |
|----------|------|
| 3.4 - 4.0 | 平行 π-π 堆积 (face-to-face) |
| 4.0 - 4.5 | 平行偏移堆积 (offset/parallel displaced) |
| 4.5 - 5.5 | T-shaped 堆积 |
| > 5.5 | 不是 π 堆积 |

## 蛋白侧芳香原子 (PHE / TYR / TRP / HIS)

| 残基 | 芳香原子 |
|------|---------|
| PHE | CG, CD1, CD2, CE1, CE2, CZ |
| TYR | CG, CD1, CD2, CE1, CE2, CZ |
| TRP | CG, CD1, NE1, CE2, CE3, CZ2, CH2 |
| HIS | CG, ND1, CD2, CE1, NE2 |

```pymol
# 蛋白芳香原子 (PHE + TYR 的苯环, TRP 的吲哚, HIS 的咪唑)
select aromatic_receptor, receptor and (
    (resn PHE and (name CG+CD1+CD2+CE1+CE2+CZ)) or
    (resn TYR and (name CG+CD1+CD2+CE1+CE2+CZ)) or
    (resn TRP and (name CG+CD1+NE1+CE2+CE3+CZ2+CH2)) or
    (resn HIS and (name CG+ND1+CD2+CE1+NE2))
)
```

## 配体芳香原子

**注意**: 配体 GAFF 原子类型用 `c*` 标识芳香碳 (c, ca, cp 等)。配体芳香环原子在 PyMOL 选择语法里是 `name c*`。

```pymol
# 配体芳香碳 (GAFF 习惯用 c* 命名)
select aromatic_ligand, ligand and name c*
```

## 距离检测

```pymol
# 芳香环原子之间距离 ≤ 4.5 Å (涵盖平行 + T-shape)
distance pi_stacking, aromatic_ligand, aromatic_receptor, 4.5
```

## 高亮芳香残基 sticks

```pymol
# 高亮蛋白芳香残基 (侧链)
show sticks, aromatic_receptor
color palegreen, resn PHE
color paleyellow, resn TYR
color slate, resn TRP
color palecyan, resn HIS
```

## 完整示例

```pymol
# 1. 加载
load D:\path\protein.pdbqt, receptor
load D:\path\ligand.pdbqt, ligand

# 2. 基础显示
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.5, receptor  # 半透明, 让 aromatic sticks 突出
show sticks, ligand
color yellow, ligand

# 3. 找芳香原子
select aromatic_receptor, receptor and (
    (resn PHE and (name CG+CD1+CD2+CE1+CE2+CZ)) or
    (resn TYR and (name CG+CD1+CD2+CE1+CE2+CZ)) or
    (resn TRP and (name CG+CD1+NE1+CE2+CE3+CZ2+CH2)) or
    (resn HIS and (name CG+ND1+CD2+CE1+NE2))
)
select aromatic_ligand, ligand and name c*

# 4. 距离 (T-shape 上限 5.5, 严格 face-to-face 4.0)
distance pi_stacking, aromatic_ligand, aromatic_receptor, 4.5
set dash_color, magenta
set dash_radius, 0.04
set dash_gap, 0.3

# 5. 高亮芳香残基
show sticks, aromatic_receptor
color palegreen, resn PHE
color paleyellow, resn TYR
color slate, resn TRP
```

## 更精细: 算环中心距离 (用 Python)

```pymol
# 复杂, 但更准确 (环中心距离 vs 环原子平均距离)
python
# 找环中心
receptor_rings = cmd.get_model("aromatic_receptor")
ligand_rings = cmd.get_model("aromatic_ligand")
# 用 centerofmass 算每个残基的环中心
# ... 然后 distance 测环中心距离
python end
```

(对 LLM 来说, 上面 `distance` 命令够用, 不用复杂化)

## 关键 ❌ 错

```pymol
# ❌ 配体芳香用错选择 (PDB 习惯是 C* 但 GAFF 用 c*)
select aromatic_ligand, ligand and name C*
# → 在 PDB 文件里对, 在 PDBQT/MOL2 里不一定对
# 正确: 配体 GAFF 原子类型通常用 c* (小写) 或 ca/cp 等

# ❌ 距离 cutoff 太大
distance pi, aromatic_ligand, aromatic_receptor, 7.0
# → 太多接触, 没意义

# ❌ 不选芳香原子, 直接 ligand vs receptor
distance pi, ligand, receptor, 4.5
# → 包含非芳香接触
```

## ✅ 对

```pymol
# ✅ 配体芳香用 c* (GAFF 习惯)
select aromatic_ligand, ligand and name c*

# ✅ 蛋白芳香用残基 + 原子名 (PDB 标准)
select aromatic_receptor, receptor and (resn PHE+TYR and name CG+CD1+CD2+CE1+CE2+CZ)

# ✅ 距离 4.5 Å 涵盖平行 + 偏移
distance pi, aromatic_ligand, aromatic_receptor, 4.5
```

## 速查

```pymol
# 蛋白芳香
select aromatic_prot, receptor and (resn PHE+TYR+TRP+HIS) and (
    (name CG+CD1+CD2+CE1+CE2+CZ) or
    (name CG+CD1+NE1+CE2+CE3+CZ2+CH2) or
    (name CG+ND1+CD2+CE1+NE2)
)

# 配体芳香
select aromatic_lig, ligand and name c*

# 距离 (face-to-face + offset)
distance pi, aromatic_lig, aromatic_prot, 4.5

# 配色
set dash_color, magenta, pi
set dash_radius, 0.04, pi
```

## 相关

- `interactions/h-bond.md` — H 键
- `interactions/hydrophobic.md` — 疏水接触
- `interactions/salt-bridge.md` — 盐桥
- `select/basic.md` — `name c*` 选择语法
