# 颜色约定 (AutoMD 配色方案)

**Q**: AutoMD 项目的标准配色?

---

## 核心约定

| 元素 | 颜色 | 用途 |
|------|------|------|
| 受体 (receptor) | cyan | 卡通, 主链 |
| 配体 (ligand) | yellow | sticks (元素着色用 util.cbaw) |
| 口袋残基 (binding site) | magenta / hotpink | sticks (突变位点建议) |
| H 键 (hbonds) | yellow | 虚线 |
| 疏水 (hydrophobic) | orange | 虚线 |
| 盐桥 (salt bridge) | red | 虚线 |
| π-堆叠 (pi stacking) | green | 虚线 |
| 水分子 | 隐藏 | `hide everything, solvent` |

## 默认 binding view 配色

```pymol
# 受体
color cyan, receptor

# 配体 (元素着色更化学)
color yellow, ligand
# 或
util.cbaw ligand    # C 灰白, N 蓝, O 红, S 黄 (C atom coloring)

# 口袋
color magenta, binding_site
# 或 hotpink 更醒目
color hotpink, binding_site

# H 键
set dash_color, yellow, hbonds

# 疏水
set dash_color, orange, hydro
```

## 颜色名 vs RGB

```pymol
# 颜色名 (PyMOL 预定义)
color cyan, X
color magenta, X
color hotpink, X
color yellow, X
color orange, X
color red, X
color green, X
color blue, X

# RGB (自定义, 0-1)
set_color mycolor, [0.5, 0.2, 0.8]
color mycolor, X
```

## 卡通 (cartoon) 配色

```pymol
# 按二级结构上色
util.cbss receptor
# α 螺旋: 粉红
# β 折叠: 黄色
# loop: 绿色
# (C atom by secondary structure)

# 按链上色
util.cbcc receptor
# chain A: 蓝, chain B: 绿, ... (C atom by chain)

# 按原子 (链式 rainbow)
util.rainbow receptor
# N 端蓝 → C 端红
```

## PyMOL 预定义色

```
# 基础色
red, green, blue, yellow, cyan, magenta, orange, white, black, gray

# 细节色
tv_red, tv_green, tv_blue    # TV 调色版 (略暗)
hotpink, palegreen, slate, salmon, firebrick

# 二级结构 (默认)
helix_default (粉红)
sheet_default (黄)
loop_default (绿)
```

## 出图配色 (论文/PPT 推荐)

```pymol
# 出版级配色
set bg_color, white           # 白底 (默认)
color cyan, receptor         # 蛋白主色
color yellow, ligand         # 配体
color magenta, binding_site  # 口袋
```

## 黑白打印友好

```pymol
# 全用灰度 (会议海报 / 黑白打印)
color gray80, receptor
color gray20, ligand
color black, binding_site
set dash_color, black
```

## spectrum (连续着色)

```pymol
# 按 B-factor 渐变
spectrum b, blue_white_red, receptor
# 0 蓝 → 0.5 白 → 1 红

# 按残基编号渐变
spectrum count, rainbow, receptor

# 自定义
spectrum b, red_white_blue, receptor, minimum=0, maximum=100
```

## 速查

```pymol
# AutoMD 标准配色
color cyan, receptor
color yellow, ligand
color magenta, binding_site
set dash_color, yellow, hbonds
set dash_color, orange, hydro
set dash_color, red, saltbridge

# 元素着色配体
util.cbaw ligand

# 二级结构着色
util.cbss receptor
```

## 相关

- `color/basic.md` — 颜色命令
- `color/spectrum.md` — spectrum 渐变
- `analysis-recipes/standard-binding-view.md` — 标准 binding view 配色
