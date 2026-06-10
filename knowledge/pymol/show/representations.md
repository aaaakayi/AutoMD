# 显示样式 (cartoon / sticks / surface 等) + show vs as 区别

**Q**: 怎么改变对象的显示样式? `show` 和 `as` 有什么区别?

---

## 常见 representations

| 名称 | 用途 | 适合 |
|------|------|------|
| `cartoon` | 蛋白主链 (默认) | 蛋白整体 |
| `sticks` | 配体 / 关键残基侧链 | 配体, 突变位点 |
| `lines` | 细线条 (默认, 不太好看) | 很少用 |
| `surface` | 表面 | 口袋, 静电势 |
| `spheres` | 球状 (大原子) | 金属, 关键原子 |
| `mesh` | 网状表面 | 风格化 |
| `dots` | 点状表面 | 风格化 |
| `ribbon` | ribbon (老式, 已被 cartoon 取代) | 几乎不用 |
| `labels` | 文字标签 | 残基名/距离 |

## show 累加 vs as 替换

```pymol
# show 累加: 之前的样子 + 新的
show cartoon, receptor     # 只有 cartoon
show sticks, receptor      # 既有 cartoon 又有 sticks
show surface, receptor     # 三种都有

# as 替换: 取消其他, 只剩新的
as cartoon, receptor        # 只剩 cartoon (替换)
as sticks, receptor         # 只剩 sticks
as surface, receptor        # 只剩 surface
```

**规律**:
- 想**叠加** 用 `show`
- 想**只保留一种** 用 `as`

## 隐藏

```pymol
hide lines, all             # 隐藏所有对象的 lines
hide everything, all       # 全部隐藏
hide everything, receptor  # 隐藏 receptor 的所有
hide sticks, receptor      # 单独移除 receptor 的 sticks
```

## 经典组合 (AutoMD `_load_scene` 风格)

```pymol
# 蛋白: 卡通
hide everything, receptor
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.3, receptor

# 配体: 棍
hide everything, ligand
show sticks, ligand
color yellow, ligand

# 隐藏水
hide everything, solvent
hide everything, inorganic
```

## 样式参数微调

```pymol
# 卡通透明度 (0=不透明, 1=完全透明)
set cartoon_transparency, 0.3, receptor

# 棍粗细
set stick_radius, 0.2

# 球大小
set sphere_scale, 1.5

# 表面质量 (1-3, 数字越大质量越好但更慢)
set surface_quality, 1

# 透明度
set transparency, 0.5, surface_object

# 自动平滑 loop
set cartoon_smooth_loops, 1
```

## 关键 ❌ 错

```pymol
# ❌ 隐藏所有然后加想保留的, 但写错顺序
hide everything, receptor
as cartoon, receptor
show sticks, receptor
# 实际: hide 清空所有 → as cartoon 设置只有 cartoon → show sticks 加上 sticks
# 正确顺序: hide everything 后 as/show 都可以, 写顺序不重要

# ❌ 用 cartoon 试图给配体
as cartoon, ligand
# 配体不是 protein, 没 cartoon
# → 静默无效果

# ❌ 用 as 替代已存在的 cartoon
show cartoon, receptor
as surface, receptor
# → 只剩 surface, 之前 show 的 cartoon 没了
# 如果想"加上 surface", 应该是 show surface, receptor
```

## ✅ 对

```pymol
# ✅ 蛋白 + 配体的标准样式
hide everything, all
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.3, receptor

show sticks, ligand
color yellow, ligand

# ✅ 口袋高亮
select pocket, byres (receptor within 5.0 of ligand)
show sticks, pocket and not name N+CA+C+O
color magenta, pocket
set cartoon_transparency, 0.7, receptor
```

## 相关

- `conventions/color-palette.md` — AutoMD 配色
- `interactions/pocket.md` — 口袋 sticks 高亮
- `analysis-recipes/standard-binding-view.md` — 完整可视化
