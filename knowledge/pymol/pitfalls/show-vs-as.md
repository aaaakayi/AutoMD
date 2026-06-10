# show vs as — 显式表示 vs 别名

**Q**: `show sticks, X` 和 `as sticks, X` 有什么区别?

---

## 答案: 几乎没区别, 但推荐 `show`

| 命令 | 等价 | 行为 |
|------|------|------|
| `show sticks, X` | `as sticks, X` | 显示 X 的 sticks 表示 |
| `hide sticks, X` | — | 隐藏 X 的 sticks 表示 |
| `show cartoon, X` | `as cartoon, X` | 显示 X 的 cartoon 表示 |

**核心区别**:
- `show X` 是**"添加表示"** (叠加, 不会移除已有)
- `as X` 是**"替换表示"** (清除其他, 只留 X)
- `hide X` 是**"移除单个表示"**

## 默认推荐: `show`

```pymol
# ✅ 推荐: 用 show (可叠加)
show cartoon, receptor
show sticks, binding_site
# 配体: cartoon + sticks 都显示
```

## `as` 何时用

```pymol
# 想"只显示 sticks, 移除其他表示"
as sticks, X
# 等价于
hide everything, X
show sticks, X
```

**实际**: 大多数情况用 `show` 更安全 (`as` 容易把卡通等一起清除)。

## `hide everything` + `show X` 模式

```pymol
# 标准模式: 先清空, 再加需要
hide everything, all
show cartoon, receptor
show sticks, ligand
show sticks, binding_site
```

## ❌ 错

```pymol
# ❌ 期望"显示 sticks, 移除 cartoon"用 as
as sticks, binding_site
# → 移除所有 binding_site 的卡通/线条等
#   如果 binding_site 是受体子集, 整个受体就只剩 sticks
# → 失去受体主链

# ❌ show everything 错
show everything, X
# → X 显示所有表示 (叠加), 不会"全部加"
```

## ✅ 对

```pymol
# ✅ 显式 hide + show
hide everything, all
show cartoon, receptor
show sticks, binding_site

# ✅ 复用已有表示, 加新的
show sticks, binding_site
# binding_site 已经有 cartoon 的话, 现在加 sticks (叠加)
```

## 表示叠加顺序 (重要)

PyMOL 多个表示**可以叠加**:
- 卡通 + sticks 同显示 (卡通半透明, sticks 在表层)
- cartoon + surface (表面包卡通)
- sticks + spheres (球棒模型)

**优先级** (从底到顶): cartoon → lines → sticks → spheres → surface

**实际**: 配体常用 `show sticks`, 口袋残基用 `show sticks` (叠加到受体卡通上)。

## 速查

```pymol
# 基础
show cartoon, X         # 加卡通
show sticks, X          # 加 sticks
hide lines, X           # 移除 lines
hide everything, X      # 移除所有表示

# as 替代 (慎用)
as sticks, X            # 移除 X 的其他表示, 只留 sticks
```

## 相关

- `show/representations.md` — 所有表示类型
- `color/basic.md` — 颜色
