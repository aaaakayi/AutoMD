# 氢键检测 (蛋白-配体氢键)

**Q**: 怎么在 PyMOL 里显示蛋白-配体之间的氢键?

---

## 答案: 用 `distance mode=2`

PyMOL **没有**直接对应 `find_polar_contacts` 这个 GUI action 的 CLI 命令。**标准做法是 `distance` + `mode=2`**。

## CLI 写法

```pymol
# 配体与蛋白之间, 只看 H 键几何 (mode=2)
distance hbonds, ligand, (receptor and (name N+O+S)), 3.5, mode=2

# 配色 + 样式
set dash_color, yellow
set dash_radius, 0.05
set dash_gap, 0.2
```

## 参数说明

| 参数 | 值 | 说明 |
|------|---|------|
| 对象名 | `hbonds` | 新建对象, 含所有 H 键虚线 |
| 选择1 | `ligand` | 配体原子 |
| 选择2 | `(receptor and (name N+O+S))` | 蛋白 H 键供体/受体 |
| cutoff | `3.5` | Å |
| mode | `2` | H 键几何筛选 (推荐) |

## mode 取值

| mode | 含义 | 推荐度 |
|------|------|-------|
| `0` | 显示**所有**距离对 | ❌ 默认, N×M 太多 |
| `1` | 显示最短路径 | ⚠️ 较少用 |
| `2` | H 键几何筛选 (距离 ≤ cutoff, 角度 ≥ 90°) | ✅ **推荐** |

## 距离判定标准

| 距离 (Å) | 含义 |
|----------|------|
| < 3.0 | 强 H 键 |
| 3.0 - 3.5 | 标准 H 键 |
| 3.5 - 4.0 | 弱 H 键 / 长 H 键 |
| > 4.0 | 不是 H 键 |

## 常见变体

```pymol
# 1. 限制供体/受体原子 (更严格)
distance h_strict, (ligand and (name N+O+S)), (receptor and (name N+O+S)), 3.2, mode=2

# 2. 包含 S (较少见)
distance h_with_s, ligand, (receptor and (name N+O+S)), 3.5, mode=2

# 3. 显示距离标签 (默认就有)
# distance 对象自带距离标签
set label_color, yellow, hbonds
set label_size, 12

# 4. 隐藏蛋白主链, 只看配体+关键残基
set cartoon_transparency, 0.7, receptor
show sticks, binding_site and not name N+CA+C+O
```

## 完整示例 (蛋白+配体+H 键高亮)

```pymol
# 1. 加载
load D:\path\protein.pdbqt, receptor
load D:\path\ligand.pdbqt, ligand

# 2. 基础显示
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.4, receptor

show sticks, ligand
color yellow, ligand

# 3. 隐藏水
hide everything, solvent
hide everything, inorganic

# 4. H 键
distance hbonds, ligand, (receptor and (name N+O+S)), 3.5, mode=2
set dash_color, yellow
set dash_radius, 0.05

# 5. 视图
zoom ligand, 6
```

## 关键 ❌ 错

```pymol
# ❌ 用 GUI action 当 CLI (没有 CLI 版本)
action ligand, find polar contacts, ...

# ❌ 不指定 mode
distance h, ligand, receptor, 3.5
# → mode=0, 显示 N×M 条虚线, 几十上百条, 没法看

# ❌ 不限制为极性原子
distance h, ligand, receptor, 3.5, mode=2
# → 任意 C-C 等也算进来, 不是 H 键

# ❌ cutoff 太大
distance h, ligand, receptor, 5.0, mode=2
# → 实际不是 H 键的距离也被算进来
```

## ✅ 对

```pymol
# ✅ 标准 H 键
distance h, ligand, (receptor and (name N+O+S)), 3.5, mode=2

# ✅ 含 S (金属-配体)
distance h, ligand, (receptor and (name N+O+S)), 3.5, mode=2

# ✅ 显示标签
set label_color, yellow, h
```

## 速查

```pymol
# 基础 H 键
distance <obj>, <sel1>, (receptor and (name N+O+S)), 3.5, mode=2

# 颜色
set dash_color, yellow, <obj>
set dash_radius, 0.05, <obj>
set dash_gap, 0.2, <obj>

# 标签
set label_color, yellow, <obj>
set label_size, 12, <obj>
```

## 相关

- `interactions/hydrophobic.md` — 疏水接触 (C-C)
- `interactions/pi-stacking.md` — π-π 堆积
- `interactions/salt-bridge.md` — 盐桥
- `select/basic.md` — `name N+O+S` 选择语法
- `interactions/pocket.md` — 口袋残基高亮
