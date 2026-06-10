# 按属性着色 (spectrum 命令) — B-factor / 能量 / 距离

**Q**: 怎么按 B-factor、结合能、距离等数值属性给残基/原子着色?

---

## spectrum 命令基础

```pymol
# 语法
spectrum <expression>, <palette>, <selection>, [minimum=<min>], [maximum=<max>]
```

- `<expression>`: 数值表达式 (通常是 `b` B-factor, 或 `count` 残基号, 或自定义)
- `<palette>`: 色板名
- `<selection>`: 应用对象
- `minimum/maximum`: 数值范围 (超出范围用边界色)

## 常用色板

| 名称 | 含义 |
|------|------|
| `blue_white_red` | 蓝→白→红 (冷-暖) |
| `red_white_blue` | 红→白→蓝 (反向) |
| `rainbow` | 彩虹 (按数值) |
| `rainbow_rev` | 反向彩虹 |
| `blue_red` | 蓝→红 (无中点) |
| `green_white_magenta` | 绿→白→紫红 |
| `yellow_white_blue` | 黄→白→蓝 |
| `tv_blue` / `tv_red` / `tv_green` / `tv_yellow` | 单色渐变 |

## B-factor 着色 (AlphaFold pLDDT, MD B-factor)

```pymol
# AlphaFold pLDDT (0-100): 蓝=低置信, 红=高置信
spectrum b, blue_white_red, receptor, minimum=0, maximum=100

# MD B-factor (温度因子, 0-50): 蓝=刚性, 红=柔性
spectrum b, blue_white_red, receptor, minimum=0, maximum=50

# 反向 (蓝=柔性, 红=刚性)
spectrum b, red_white_blue, receptor, minimum=0, maximum=50
```

## 距离着色 (显示接触频率)

```pymol
# 配体到残基的距离
distance dist_to_lig, receptor, ligand
spectrum count, rainbow_rev, dist_to_lig, minimum=0, maximum=10
```

**但** `distance` 对象的 count 是 frame 序列号, 不是距离值。要按距离着色需要先 `alter`:

```pymol
# 先用 alter 算距离
alter receptor, b=0
distance tmp_d, receptor, ligand
# 把距离值复制到 b
python
for at in cmd.get_model("receptor").atom:
    d = cmd.get_distance(f"receptor and resi {at.resi} and name {at.name}", "ligand")
    cmd.alter(f"receptor and resi {at.resi} and name {at.name}", f"b={d}")
python end
spectrum b, blue_white_red, receptor, minimum=2, maximum=8
```

(这是 Python 嵌入, 详细见 PyMOL Wiki "alter_with_python")

## 自定义数值 (结合能、pLDDT 等)

```pymol
# 假设已经为每个残基的 b 设了结合能
alter receptor, b=your_energy_value
spectrum b, blue_white_red, receptor, minimum=-15, maximum=5
# 蓝=有利 (负), 白=中性, 红=不利 (正)
```

## 残基序号 (按链 N→C 端彩虹)

```pymol
# 链 A 按残基号 rainbow
spectrum count, rainbow, chain A and receptor
```

## 原子序号 (chain rainbow)

```pymol
# 整条链按原子序号 rainbow (类似 rainbow_NC 命令)
spectrum count, rainbow, receptor
```

## 静电势 (需要 APBS 工具, 高级用法)

```pymol
# 需要先跑 APBS 算电势到 .dx 文件, 然后:
set surface_color, atomic
load D:\path\to\potential.dx
cmd.ramp_new("elepot", "receptor", range=[-5, 0, 5])
# 电势 -5 = 红, 0 = 白, +5 = 蓝
```

## 速查

| 用途 | 命令 |
|------|------|
| B-factor | `spectrum b, blue_white_red, X, minimum=A, maximum=B` |
| pLDDT | `spectrum b, blue_white_red, X, minimum=0, maximum=100` |
| 自定义能量 | `alter X, b=value; spectrum b, ...` |
| 残基号 rainbow | `spectrum count, rainbow, X` |
| 静电势 | 需要 APBS 预先算 |

## 关键 ❌ 错

```pymol
# ❌ 表达式不存在的属性
spectrum foo, rainbow, receptor
# → "No property named 'foo'"

# ❌ 不指定范围, 数值会被压缩到默认 0-1
spectrum b, rainbow, receptor
# 实际只用了 0-1 范围 (因为 default minimum=0, maximum=auto)
# → 所有颜色挤在一起

# ❌ 颜色作用错对象
spectrum b, rainbow, foo   # foo 不存在
```

## ✅ 对

```pymol
# ✅ B-factor 显式指定范围
spectrum b, blue_white_red, receptor, minimum=0, maximum=50

# ✅ AlphaFold pLDDT
spectrum b, blue_white_red, receptor, minimum=0, maximum=100

# ✅ 自定义能量: 先设值, 再 spectrum
alter receptor, b=energy_data
spectrum b, blue_white_red, receptor, minimum=-15, maximum=5
```

## 相关

- `color/basic.md` — 12 基础色 + 自定义 RGB
- `conventions/color-palette.md` — AutoMD 配色约定
- `show/representations.md` — 配 surface 后用 spectrum
