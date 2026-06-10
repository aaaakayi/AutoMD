# 颜色 (12 基础色 + 自定义 RGB + 按元素)

**Q**: 怎么给对象/残基/原子着色? 怎么用自定义 RGB 颜色?

---

## 12 基础色 (PyMOL 内置)

```pymol
color red, receptor
color green, ligand
color blue, resi 50
color yellow, name CA
color cyan, elem Zn
color magenta, chain A
color orange, pocket
color purple, hydro_atoms
color white, all
color black, receptor        # 纯黑 (慎用)
color grey50, receptor       # 50% 灰
color density, receptor      # 原子序数 (C 灰, N 蓝, O 红, S 黄)
```

**常用色名** (大小写敏感, 全小写):
- `red` / `green` / `blue` / `yellow` / `cyan` / `magenta`
- `orange` / `purple` / `white` / `black` / `grey`
- `tv_red` / `tv_green` / `tv_blue` / `tv_yellow` (电视色调)
- `density` / `atomic` (按元素着色)

## 自定义 RGB 颜色

```pymol
# 定义自定义颜色 (RGB 0-255)
set_color my_blue, [100, 150, 200]
set_color hot_pink, [255, 105, 180]

# 使用
color my_blue, pocket
color hot_pink, key_residues
```

## 按元素着色

```pymol
# PyMOL 内置按元素着色
util.cbaw receptor
# C 灰白 + N 蓝 + O 红 + S 黄

# 手动按元素
color atomic, not (elem C)
# 只把非 C 元素按 atomic 色
```

## 多对象不同颜色

```pymol
# 蛋白 A
color tv_red, receptor
# 配体
color tv_green, ligand
# 关键残基
color tv_yellow, key_residues
```

## AutoMD 配色约定

| 元素 | 颜色 | 备注 |
|------|------|------|
| 蛋白主链 | `cyan` | 默认 |
| 配体 | `yellow` | 默认 |
| 关键残基 sticks | `magenta` / `hotpink` | 突变位点 |
| 口袋残基 | `magenta` | 5Å 内 byres |
| 疏水接触线 | `orange` | |
| H 键线 | `yellow` | |
| 盐桥线 | `salmon` | |
| 芳香环 | `palegreen` (PHE) / `paleyellow` (TYR) / `slate` (TRP) | |

**详细见** `conventions/color-palette.md`

## 关键 ❌ 错

```pymol
# ❌ 颜色名大小写错
color Cyan, receptor        # "Cyan" 不识别 (识别 "cyan")
color HotPink, foo          # 同上

# ❌ 颜色作用错对象
color red, foo              # foo 不存在
# → Selector-Error: No selection found

# ❌ set_color RGB 顺序错 (PyMOL 是 [R, G, B], 不是 [B, G, R])
set_color my_color, [255, 0, 0]   # 红色 ✓
set_color my_color, [0, 0, 255]   # 蓝色 ✓
set_color my_color, [0, 255, 0]   # 绿色 ✓
```

## ✅ 对

```pymol
# ✅ 标准命名 + 选择语法
color cyan, receptor
color hotpink, (receptor and resi 50+100)

# ✅ 配体元素着色 (PyMOL 内置)
util.cbaw ligand

# ✅ 自定义 + RGB
set_color my_color, [100, 150, 200]
color my_color, pocket
```

## 相关

- `color/spectrum.md` — 按 B-factor / 能量 着色
- `conventions/color-palette.md` — AutoMD 完整配色
- `show/representations.md` — show 之后用 color
