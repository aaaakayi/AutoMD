# distance 命令 mode 区别

**Q**: `distance` 命令的 `mode=0/1/2` 有什么区别?

---

## 答案: mode 控制距离对象显示

| mode | 行为 |
|------|------|
| 0 | 不显示 (但 distance 对象被记录) |
| 1 | 显示为实线 |
| 2 | 显示为虚线 (dashed, 默认) |
| 3 | 显示为短线 (half-bond) |

## 完整语法

```pymol
distance name, selection1, selection2 [, cutoff [, mode [, labels ]]]

# 常用
distance hb, ligand, (receptor and (name N+O+S)), 3.5, mode=2
# 1. name = "hb" (距离对象名, 用 set dash_color, hb 调样式)
# 2. selection1, selection2: 两个选择
# 3. cutoff: 距离阈值 (Å)
# 4. mode: 0/1/2/3
```

## mode 视觉对比

```pymol
# 实线 (mode=1)
distance d1, lig, rec, 4.0, mode=1

# 虚线 (mode=2, 默认, 用于 H 键)
distance d2, lig, rec, 4.0, mode=2

# 短线 (mode=3, 半键长, 用于配体-配体)
distance d3, lig, rec, 4.0, mode=3

# 不画 (mode=0, 仅记录距离值)
distance d4, lig, rec, 4.0, mode=0
```

## 调距离对象样式

```pymol
# 改所有 distance 对象
set dash_color, yellow
set dash_radius, 0.05
set dash_gap, 0.2

# 改单个 distance 对象
set dash_color, red, hb
set dash_radius, 0.08, hb
```

## labels (距离数值标签)

```pymol
# 显示距离数值 (Å)
distance hb, lig, rec, 3.5, mode=2
# 距离线上默认标 "2.345" 之类的数字

# 关闭标签
set label_size, 0, hb
# 或
set dash_length, 0.0, hb   # 不用这个, 控虚线长度
```

## H 键 (mode=2 + 黄色 + 3.5Å)

```pymol
# H 键标准
distance hbonds, ligand, (receptor and (name N+O+S)), 3.5, mode=2
set dash_color, yellow, hbonds
set dash_radius, 0.05, hbonds

# 含 N/O/S, 排除水
```

## 疏水 (mode=2 + 橙色 + 4.0Å)

```pymol
# 疏水接触
select hydro_atom, (receptor and elem C) within 4.0 of ligand
distance hydro, ligand, hydro_atom, 4.0, mode=2
set dash_color, orange, hydro
set dash_radius, 0.04, hydro
```

## 盐桥 (mode=2 + 红色 + 4.0Å)

```pymol
# 盐桥 (阳离子-阴离子)
select pos, (receptor and (resn ARG or resn LYS) and name NZ+NH*)
select neg, (receptor and (resn ASP or resn GLU) and name OD+OE*)
distance sb, pos, neg, 4.0, mode=2
set dash_color, red, sb
```

## 速查

```pymol
# H 键 (推荐)
distance hb, ligand, (receptor and (name N+O+S)), 3.5, mode=2
set dash_color, yellow, hb
set dash_radius, 0.05, hb

# 疏水
select hydro, (receptor and elem C) within 4.0 of ligand
distance hydro_d, ligand, hydro, 4.0, mode=2
set dash_color, orange, hydro_d

# 盐桥
distance sb, pos, neg, 4.0, mode=2
set dash_color, red, sb

# 配体-配体 (mode=3, 半键长)
distance int, ligand, ligand, 1.0, mode=3
```

## 相关

- `interactions/h-bond.md` — H 键详细
- `interactions/hydrophobic.md` — 疏水详细
- `interactions/salt-bridge.md` — 盐桥详细
