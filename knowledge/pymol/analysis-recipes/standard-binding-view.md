# 标准 binding view (蛋白 + 配体 + H 键 + 疏水 + 标签)

**Q**: 给一个蛋白-配体对接结果, 出标准的"结合模式"图 (含 H 键、疏水接触、口袋残基标签)?

---

## 完整 CLI 序列 (复制即用)

```pymol
# === 1. 加载 ===
load D:\AutoMD\AutoMD_LangGraph\output\03452596\protein\1HX0\receptor\1HX0_protein_only.pdbqt, receptor
load D:\AutoMD\AutoMD_LangGraph\output\03452596\docking\1HX0\vina\docked.pdbqt, ligand

# === 2. 基础显示 (AutoMD `_load_scene` 风格) ===
hide everything, all
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.4, receptor
show sticks, ligand
color yellow, ligand
hide everything, solvent
hide everything, inorganic

# === 3. 找口袋 + 高亮 ===
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site

# === 4. H 键 (黄虚线) ===
distance h_bonds, ligand, (receptor and (name N+O+S)), 3.5, mode=2
set dash_color, yellow
set dash_radius, 0.05
set dash_gap, 0.2

# === 5. 疏水接触 (橙虚线) ===
select hydro_atom, (receptor and elem C) within 4.0 of ligand
distance hydro, ligand, hydro_atom, 4.0
set dash_color, orange
set dash_radius, 0.04
set dash_gap, 0.3

# === 6. 残基标签 (mutation suggestion) ===
label binding_site and name CA, "%n%r"
set label_color, white
set label_size, 12

# === 7. 视图 ===
orient receptor
zoom ligand, 6
center ligand

# === 8. 输出 ===
bg_color white
viewport 1200, 800
png D:\AutoMD\AutoMD_LangGraph\output\03452596\pymol\standard_view.png, dpi=300
```

## 输出图内容

- **蛋白主链**: cyan 卡通, 半透明 (透明度 0.4)
- **配体**: yellow sticks
- **口袋残基侧链**: magenta sticks (5Å 内, byres)
- **H 键**: yellow 虚线
- **疏水接触**: orange 虚线
- **残基标签**: 白色, 12pt (3字母代码 + 编号)

## 变体: 不要 H 键 / 疏水 (只要 pocket 视图)

```pymol
# 简化版, 只高亮口袋
load D:\path\protein.pdbqt, receptor
load D:\path\ligand.pdbqt, ligand
hide everything, all
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.5, receptor
show sticks, ligand
color yellow, ligand
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site
zoom ligand, 6
png D:\path\to\pocket.png, dpi=300
```

## 变体: 突出疏水 (去除 H 键, 只看疏水)

```pymol
# 只显示疏水残基
select hydro_res, byres ((receptor and elem C) within 4.0 of ligand)
show sticks, hydro_res and not name N+CA+C+O
color orange, hydro_res
# 不画 H 键
```

## 变体: 配体居中, 周围 8Å 全部

```pymol
# 配体周围 8Å
select extended, byres ((receptor and not hydrogens) within 8.0 of ligand)
show sticks, extended and not name N+CA+C+O
color palegreen, extended   # 用不同色
```

## 关键 ❌ 错

```pymol
# ❌ binding_site 没排除配体自己的原子
select binding_site, byres (receptor within 5.0 of ligand)
# → 包含 LIG 残基(如果配体是 1 个残基)
# 正确: not hydrogens 已经过滤了, 但 binding_site 还含 LIG 残基
# 解决: select binding_site, byres (((receptor and not hydrogens) within 5.0 of ligand) and not ligand)
# 简化: ligand 一般不是 protein 残基名 (LIG, UNL), 不会混
```

## ✅ 对

```pymol
# ✅ 标准 binding view
hide everything, all
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.4, receptor
show sticks, ligand
color yellow, ligand
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site
distance h_bonds, ligand, (receptor and (name N+O+S)), 3.5, mode=2
set dash_color, yellow
select hydro, (receptor and elem C) within 4.0 of ligand
distance hydro_d, ligand, hydro, 4.0
set dash_color, orange
zoom ligand, 6
png D:\path\to\out.png, dpi=300
```

## 速查 (精简版)

```pymol
# 蛋白 + 配体 + pocket
load D:\p.pdbqt, receptor
load D:\l.pdbqt, ligand
hide everything, all
show cartoon, receptor
color cyan, receptor
show sticks, ligand
color yellow, ligand
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site
zoom ligand, 6
png D:\out.png, dpi=300
```

## 相关

- `screenshot-recipes/publication-quality.md` — 完整出版级
- `interactions/h-bond.md` — H 键详细
- `interactions/hydrophobic.md` — 疏水详细
- `interactions/pocket.md` — pocket 高亮
- `analysis-recipes/mutation-suggestion.md` — 含突变位点
