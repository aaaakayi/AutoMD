# 结合位点 / 口袋残基识别与高亮

**Q**: 怎么找出配体周围 X 埃的所有蛋白残基, 并高亮显示?

---

## 答案: byres + around + sticks 高亮

```pymol
# 1. 配体周围 5Å 内的所有蛋白原子
select binding_site_atoms, (receptor and not hydrogens) within 5.0 of ligand

# 2. 扩展到整个残基
select binding_site, byres binding_site_atoms

# 3. 半透明主链, 让 binding site 突出
set cartoon_transparency, 0.7, receptor

# 4. 显示 binding site 残基的侧链 sticks
show sticks, binding_site and not name N+CA+C+O

# 5. 配色 (AutoMD 约定: magenta)
color magenta, binding_site
```

## 距离阈值经验值

| 距离 (Å) | 含义 | 用途 |
|----------|------|------|
| 4.0 | 紧密接触 | 突变位点核心 |
| 5.0 | 标准 binding site | 默认 |
| 6.0 | binding site + 第一层水壳 | 扩大 |
| 8.0 | 长程相互作用 | 盐桥尾巴, 水桥 |

## CLI 完整写法

```pymol
# 标准 binding site (5Å)
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)

# 紧密 binding site (4Å)
select tight_site, byres ((receptor and not hydrogens) within 4.0 of ligand)

# 扩展 binding site (6Å)
select extended_site, byres ((receptor and not hydrogens) within 6.0 of ligand)
```

## 高亮样式

```pymol
# 1. 隐藏线框, 只留卡通
hide everything, receptor
show cartoon, receptor
color cyan, receptor

# 2. 半透明主链
set cartoon_transparency, 0.5, receptor

# 3. 高亮 binding site
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site

# 4. (可选) 加标签
label binding_site and name CA, "%n%r"
set label_color, white
set label_size, 12
```

## 与 H 键 / 疏水结合 (高亮 + 距离)

```pymol
# 1. 加载
load D:\path\protein.pdbqt, receptor
load D:\path\ligand.pdbqt, ligand

# 2. 基础
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.5, receptor

show sticks, ligand
color yellow, ligand

# 3. 找口袋
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)

# 4. 高亮口袋
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site

# 5. H 键
distance hb, ligand, (receptor and (name N+O+S)), 3.5, mode=2
set dash_color, yellow
set dash_radius, 0.05

# 6. 疏水
select hydro, byres ((receptor and elem C) within 4.0 of ligand)
show sticks, hydro and not name N+CA+C+O
color orange, hydro

# 7. 视图
zoom ligand, 6
```

## 标签 (突变位点建议用)

```pymol
# 显示残基编号 + 名
label binding_site and name CA, "%n%r"
# %n = 残基名, %r = 残基编号

# 三字母 (默认)
set label_format, "%s%s%s"   # 不常用

# 一字母
alter receptor and name CA and binding_site, resn = one_letter[resn]
# 复杂, 一般用三字母即可
```

## ❌ 错

```pymol
# ❌ byres 单独用
select byres ligand
# → 语法错

# ❌ byres 修饰错
select byres ligand around 5.0
# → 解析为 byres (ligand around 5.0) 但括号缺失会错
# 正确: byres (...)

# ❌ 范围过宽
select binding_site, byres (receptor within 8.0 of ligand)
# → 太多残基, 不像 pocket 像 patch
```

## ✅ 对

```pymol
# ✅ 标准 5Å binding site
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)

# ✅ 配体排除自己 (避免把配体残基算进去)
select binding_site, byres ((receptor and ligand within 5.0) and not ligand)

# ✅ 配 binding site 显示
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site
```

## 速查

```pymol
# 找 binding site (标准)
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)

# 高亮
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site

# 标签
label binding_site and name CA, "%n%r"
set label_color, white
```

## 相关

- `interactions/h-bond.md` — pocket 内 H 键
- `interactions/hydrophobic.md` — pocket 内疏水
- `analysis-recipes/standard-binding-view.md` — 完整 binding site 视图
- `analysis-recipes/mutation-suggestion.md` — 用 binding site 标签做突变位点建议
- `select/spatial.md` — `around` / `within` / `byres` 详细
