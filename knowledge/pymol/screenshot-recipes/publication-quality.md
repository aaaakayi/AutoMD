# 出版级 PNG 完整工作流 (从加载到保存)

**Q**: 给一个标准任务 (蛋白-配体), 完整地走一遍出版级截图的 PyMOL CLI 流程?

---

## 完整工作流 (复制即用)

```pymol
# === 1. 加载 ===
load D:\AutoMD\AutoMD_LangGraph\output\03452596\protein\1HX0\receptor\1HX0_protein_only.pdbqt, receptor
load D:\AutoMD\AutoMD_LangGraph\output\03452596\docking\1HX0\vina\docked.pdbqt, ligand

# === 2. 基础显示 ===
hide everything, all
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.3, receptor

show sticks, ligand
color yellow, ligand
util.cbaw ligand

hide everything, solvent
hide everything, inorganic

# === 3. 找口袋 + 高亮 ===
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site

# === 4. H 键 ===
distance hbonds, ligand, (receptor and (name N+O+S)), 3.5, mode=2
set dash_color, yellow
set dash_radius, 0.05
set dash_gap, 0.2

# === 5. 疏水接触 ===
select hydro_atom, (receptor and elem C) within 4.0 of ligand
distance hydro, ligand, hydro_atom, 4.0
set dash_color, orange
set dash_radius, 0.04

# === 6. 标签 (突变位点建议) ===
label binding_site and name CA, "%n%r"
set label_color, white
set label_size, 12

# === 7. 视图调整 ===
orient receptor
zoom ligand, 6
center ligand

# === 8. 光追 + 出版级输出 ===
bg_color white
set ray_opaque_background, off
set ray_trace_mode, 1
set ray_shadows, 0
set antialias, 2
viewport 1200, 800
ray 2400, 1600
png D:\AutoMD\AutoMD_LangGraph\output\03452596\pymol\final_view.png, dpi=300
```

## 输出

**`final_view.png`**: 一张出版级图, 包含:
- 蛋白 (cyan 卡通, 半透明)
- 配体 (yellow sticks, 元素着色)
- 口袋残基 (magenta sticks)
- H 键 (yellow 虚线)
- 疏水接触 (orange 虚线)
- 残基编号标签 (用于突变位点建议)

## 变体: 简化版 (快速出图, 不带光追)

```pymol
# 简化的可视化 (秒级出图)
load D:\path\protein.pdbqt, receptor
load D:\path\ligand.pdbqt, ligand
show cartoon, receptor
color cyan, receptor
show sticks, ligand
color yellow, ligand
select binding_site, byres (receptor within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site
distance hb, ligand, (receptor and (name N+O+S)), 3.5, mode=2
zoom ligand, 6
viewport 1200, 800
png D:\path\to\out.png, dpi=200
```

## 变体: 透明背景 (用于拼图)

```pymol
set ray_opaque_background, off
ray 2400, 1600
png D:\path\to\out_transparent.png, dpi=300
```

## 变体: 高 DPI (海报级)

```pymol
viewport 1200, 800
ray 2400, 1600    # 2x
# 仍可加 dpi=600 提升印刷质量
png D:\path\to\out_poster.png, dpi=600
```

## 变体: 多个视角 (多角度图)

```pymol
# 正面
viewport 1200, 800
zoom ligand, 6
png D:\path\to\front.png, dpi=300

# 旋转 90° 后侧面
rotate y, 90
png D:\path\to\side.png, dpi=300

# 顶部视图
rotate y, -90
rotate x, 90
png D:\path\to\top.png, dpi=300
```

## 变体: 不同 binding site 距离的对比

```pymol
# 4Å (紧密)
select tight, byres (receptor within 4.0 of ligand)
show sticks, tight and not name N+CA+C+O
color red, tight
png D:\path\to\tight_4A.png, dpi=300

# 6Å (扩大)
select extended, byres (receptor within 6.0 of ligand)
show sticks, extended and not name N+CA+C+O
color yellow, extended
png D:\path\to\extended_6A.png, dpi=300
```

## 速查

```pymol
# 完整工作流 (从 0 到出版级 PNG)
load D:\path\protein.pdbqt, receptor
load D:\path\ligand.pdbqt, ligand
hide everything, all
show cartoon, receptor
color cyan, receptor
show sticks, ligand
color yellow, ligand
set cartoon_transparency, 0.3, receptor
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
show sticks, binding_site and not name N+CA+C+O
color magenta, binding_site
distance hb, ligand, (receptor and (name N+O+S)), 3.5, mode=2
set dash_color, yellow
set dash_radius, 0.05
select hydro, (receptor and elem C) within 4.0 of ligand
distance hydro_d, ligand, hydro, 4.0
set dash_color, orange
zoom ligand, 6
bg_color white
set ray_opaque_background, off
set ray_trace_mode, 1
set ray_shadows, 0
set antialias, 2
viewport 1200, 800
ray 2400, 1600
png D:\path\to\output.png, dpi=300
```

## 相关

- `screenshot/png.md` — 普通 PNG
- `screenshot/ray.md` — 光线追踪
- `conventions/paths.md` — AutoMD 截图输出路径
- `analysis-recipes/standard-binding-view.md` — 蛋白-配体标准视图
- `analysis-recipes/mutation-suggestion.md` — 含突变位点标签
