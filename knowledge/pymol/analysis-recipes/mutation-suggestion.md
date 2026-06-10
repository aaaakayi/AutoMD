# 突变位点建议视图 (binding site 残基 sticks + 标签)

**Q**: 怎么给出 "这些残基可能可以突变来增强结合" 的可视化建议?

---

## 完整 CLI 序列 (复制即用)

```pymol
# === 1. 加载 ===
load D:\AutoMD\AutoMD_LangGraph\output\03452596\protein\1HX0\receptor\1HX0_protein_only.pdbqt, receptor
load D:\AutoMD\AutoMD_LangGraph\output\03452596\docking\1HX0\vina\docked.pdbqt, ligand

# === 2. 基础显示 ===
hide everything, all
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.5, receptor
show sticks, ligand
color yellow, ligand

# === 3. 找 binding site (5Å) + 高亮侧链 + 标签 ===
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color hotpink, binding_site

# 残基标签 (3 字母代码 + 编号)
label binding_site and name CA, "%n%r"
set label_color, white
set label_size, 14, binding_site

# === 4. H 键 (突出可破坏/形成的 H 键残基) ===
distance hb, ligand, (receptor and (name N+O+S)), 3.5, mode=2
set dash_color, yellow
set dash_radius, 0.05

# === 5. 视图 ===
orient receptor
zoom ligand, 6
center ligand

# === 6. 输出 ===
bg_color white
viewport 1200, 800
png D:\AutoMD\AutoMD_LangGraph\output\03452596\pymol\mutation_suggestion.png, dpi=300
```

## 关键元素

- **配体周围 5Å 内的所有蛋白残基侧链**: magenta/hotpink sticks (重点关注)
- **3 字母残基名 + 编号标签**: 白色, 14pt (大, 易读)
- **H 键**: yellow 虚线 (知道哪些残基在形成 H 键)
- **半透明主链**: 让侧链和标签更突出

## 用户看到的图能告诉用户什么

- **哪些残基在配体周围** → 突变候选
- **哪些残基在形成 H 键** → 突变时不能破坏这些 (除非想破坏)
- **哪些残基在形成疏水** → 突变成更大疏水残基可增强
- **哪些残基离配体远** → 突变意义不大

## 变体: 不同距离的口袋

```pymol
# 紧密口袋 (4Å, 强接触)
select tight, byres ((receptor and not hydrogens) within 4.0 of ligand)
show sticks, tight and not name N+CA+C+O
color red, tight
label tight and name CA, "%n%r"
set label_color, white
set label_size, 14

# 紧密 + 扩展
select extended, byres ((receptor and not hydrogens) within 6.0 of ligand)
show sticks, extended and not name N+CA+C+O
color palegreen, extended
```

## 变体: 残基编号加大 (期刊用)

```pymol
# 大字体, 黑底白字 (高对比)
set label_size, 16
set label_color, white
set label_outline_color, black
set label_background, on
```

## 变体: 残基带二级结构标记

```pymol
# α 螺旋残基标 (H), β 折叠标 (S), loop 标 (-)
label binding_site and name CA, "%n%r %ss"
# %ss = 二级结构 (H/S/-)
set label_color, white
```

## 完整示例 (含 H 键 + 疏水)

```pymol
# 1. 加载
load D:\p.pdbqt, receptor
load D:\l.pdbqt, ligand

# 2. 基础
hide everything, all
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.5, receptor
show sticks, ligand
color yellow, ligand

# 3. Pocket
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color hotpink, binding_site

# 4. H 键
distance hb, ligand, (receptor and (name N+O+S)), 3.5, mode=2
set dash_color, yellow
set dash_radius, 0.05

# 5. 疏水
select hydro, (receptor and elem C) within 4.0 of ligand
distance hydro_d, ligand, hydro, 4.0
set dash_color, orange
set dash_radius, 0.04

# 6. 标签
label binding_site and name CA, "%n%r"
set label_color, white
set label_size, 14

# 7. 视图
orient receptor
zoom ligand, 6

# 8. 输出
bg_color white
viewport 1200, 800
png D:\path\to\out.png, dpi=300
```

## 速查

```pymol
# 突变位点建议 (精简版)
load D:\p.pdbqt, receptor
load D:\l.pdbqt, ligand
hide everything, all
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.5, receptor
show sticks, ligand
color yellow, ligand
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color hotpink, binding_site
label binding_site and name CA, "%n%r"
set label_color, white
set label_size, 14
zoom ligand, 6
png D:\out.png, dpi=300
```

## 相关

- `interactions/pocket.md` — pocket 高亮
- `interactions/h-bond.md` — H 键
- `analysis-recipes/standard-binding-view.md` — 完整 binding view
- `screenshot-recipes/publication-quality.md` — 出版级
