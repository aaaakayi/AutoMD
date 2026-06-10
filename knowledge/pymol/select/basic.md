# 选择语法基础 (resi / resn / name / chain / elem) + 布尔组合

**Q**: 怎么按残基编号/名/链/原子名/元素做选择? 怎么组合多个条件?

---

## 基础选择子语法

```pymol
# 按残基编号
select resi 100                  # 1 个残基
select resi 100-110              # 范围
select resi 100+200+300          # 多选 (用 + 分隔, 不是 ,)

# 按残基名
select resn LIG                  # 1 个残基 (配体常用)
select resn ALA+GLY+VAL         # 多个残基 (用 +)

# 按链
select chain A
select chain A+B                 # 多链 (不是 A or B, 是 "A 和 B 链上所有残基")

# 按原子名
select name CA                  # 所有 Cα
select name N+O+NE              # 多个原子名
select name C*                  # 通配 (所有 C 开头的原子: CA, CB, CG, ...)
select name *1                  # 以 1 结尾 (HG1, HD1, etc.)

# 按元素
select elem Zn
select elem C and resi 50-60

# 按 B-factor / 其他属性
select b > 50
```

## 布尔组合

```pymol
# 与 (用 and 或 &)
select chain A and resi 50-60
select (chain A) & (resi 50-60)   # 同样

# 或 (用 or)
select resn LIG or resi 50

# 非 (用 not)
select not hydrogens              # 所有非 H 原子
select chain A and not resi 50    # A 链但不是 50 号

# 复合
select (resn LIG) and (chain A or chain B) and not hydrogens
# 配体 + A 或 B 链 + 非 H
```

## 实际场景示例

```pymol
# 配体所有原子
select ligand

# 配体所有重原子 (非 H)
select ligand and not hydrogens

# 蛋白主链
select name N+CA+C+O

# 蛋白侧链 (非主链)
select (receptor and not name N+CA+C+O) and not hydrogens

# 某几个特定残基
select resi 50+100+150+200

# 某几个特定原子
select name CA and (resi 50+100+150)

# 配体某原子的特定配体 (残基 LIG 的 C1)
select resn LIG and name C1

# 蛋白 N 端前 20 个
select receptor and resi 1-20
```

## AutoMD 典型选择

```pymol
# 配体周围 5Å 内的蛋白原子 (经典 "binding site")
select binding_site_atoms, (receptor and not hydrogens) within 5.0 of ligand

# 扩展到整个残基 (byres)
select binding_site, byres binding_site_atoms

# 配体的极性原子 (H 键筛选用)
select ligand_polar, ligand and (name N+O+S)

# 蛋白的极性原子
select receptor_polar, receptor and (name N+O+S)
```

## 关键 ❌ 错

```pymol
# ❌ 范围用逗号
select resi 1, 10, 100            # 这不是范围, 是个错的选择
# 正确: select resi 1+10+100

# ❌ 链名用 or (PyMOL 链选择有特殊语义)
select chain A or chain B          # 这实际是 "chain A 且 B"
# 正确: select chain A+B  (用 + 表示 "在 A 或 B 链上")

# ❌ byres 单独用
select byres ligand               # 语法错
# 正确: select byres (ligand around 5.0)
```

## ✅ 对

```pymol
# ✅ 范围用 +
select resi 1+10+100

# ✅ 多链用 +
select chain A+B+C

# ✅ byres 修饰符用法
select byres (ligand around 5.0)
```

## 速查表

| 选择子 | 例子 | 说明 |
|--------|------|------|
| `resi` | `resi 100-110` / `resi 50+100` | 残基编号 (范围用 `-`, 多选用 `+`) |
| `resn` | `resn ALA` / `resn LIG` | 残基名 (3 字母) |
| `chain` | `chain A` / `chain A+B` | 链标识 |
| `name` | `name CA` / `name C*` | 原子名 (通配 `*` 任意字符) |
| `elem` | `elem Zn` / `elem C` | 元素符号 |
| `hydrogens` | `not hydrogens` | H 原子 (内置宏) |
| `hetero` | `not hetero` | 非蛋白原子 (内置宏) |
| `byres` | `byres (...)` | 扩展到整个残基 (修饰符) |
| `byatom` | `byatom (...)` | 扩展到整个原子 (修饰符) |
| `around` | `A around 5.0` | A 选择附近 5Å |
| `within` | `A within 5.0 of B` | A 在 B 5Å 内 (双向) |
| `and` / `or` / `not` | `A and B` / `A or B` / `not A` | 布尔 |

## 相关

- `select/spatial.md` — `around` / `within` / `byres` 详细
- `interactions/pocket.md` — byres + around 组合
- `interactions/h-bond.md` — 用 `name N+O+S` 选极性原子
