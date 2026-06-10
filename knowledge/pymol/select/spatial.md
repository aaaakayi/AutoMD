# 空间与残基扩展选择 (around / within / byres)

**Q**: 怎么选配体周围 X 埃的残基? `around` / `within` / `byres` 怎么用?

---

## 三种空间修饰符对比

| 修饰符 | 含义 | 例子 |
|--------|------|------|
| `around` | 单向: A **附近** B (A 在 B 周围) | `ligand around 5.0` |
| `within` | 双向: A **在** B **的** X Å 内 | `A within 5.0 of B` (可省略 of) |
| `byres` | 扩展到**整个残基** | `byres (ligand around 5.0)` |

## around (单向: 主体在客体附近)

```pymol
# 配体周围 5Å 的所有原子
select ligand_around_5A, ligand around 5.0

# 配体周围 5Å 的蛋白原子 (排除配体自己)
select receptor_near_ligand, (receptor and not hydrogens) and (ligand around 5.0) and not ligand
```

**语法**: `<A> around <distance>` = A 中所有在 B (隐含) 周围 distance Å 内的原子。
**注意**: 第一个对象名是"主体", 第二个是"被围绕者"。`ligand around 5.0` 中的"5.0"实际是 receiver 距离限制。

## within (双向: 互相)

```pymol
# 配体在 receptor 5Å 内 (等价于 receptor around 5.0)
select A_in_B, ligand within 5.0 of receptor

# 等价: 简写 of
select A_in_B, ligand within 5.0 receptor

# A 同时在 B 和 C 内 (交集)
select A_in_B_and_C, A within 5.0 of B and A within 5.0 of C
```

**语法**: `A within <distance> of B` = A 中所有在 B 周围 distance Å 内的原子。

## byres (扩展到整个残基)

```pymol
# 选配体周围 5Å 的原子, 然后扩展到整个残基
select binding_site_atoms, (receptor and not hydrogens) within 5.0 of ligand
select binding_site, byres binding_site_atoms

# 一行写法 (注意 byres 修饰的是整个括号内的选择)
select binding_site, byres ((receptor and not hydrogens) within 5.0 of ligand)
```

**关键**: `byres` 是**修饰符**, 必须修饰一个完整选择 (用括号包住)。

## 实际场景

```pymol
# 场景 1: 配体周围 5Å 蛋白原子 (含残基侧链)
select pocket_atoms, (receptor and not name N+CA+C+O) within 5.0 of ligand
# 仅侧链 (排除主链), 用 sticks 高亮

# 场景 2: 配体周围 5Å 的所有蛋白残基 (含主链, 用于 labels)
select pocket_residues, byres (receptor within 5.0 of ligand)
# byres 把原子选择扩展到所属残基

# 场景 3: 两个残基之间的接触原子 (用于距离检测)
select interface, (receptor and chain A) within 4.0 of (receptor and chain B)

# 场景 4: 配体 + 周围 4Å 的氢键极性原子
select ligand_and_polar_neighbours, ligand or ((receptor and (name N+O+S)) within 4.0 of ligand)
```

## 关键 ❌ 错

```pymol
# ❌ byres 单独用, 后面不跟选择
select byres ligand
# → 语法错

# ❌ byres 修饰错 (语法解析会失败)
select byres ligand around 5.0
# → 解析为 byres (ligand around 5.0) 但括号缺失会错
# 正确: select byres (ligand around 5.0)

# ❌ around 用错方向
select receptor around ligand
# → 这是 "receptor 周围 ligand" 永远不会成立
# 正确: select ligand around 5.0 (然后在括号里限制 receptor)

# ❌ within 距离单位
select A within 5 of B
# → 5 Å (无单位隐含 Å), OK, 但写 "5.0" 更稳
```

## ✅ 对

```pymol
# ✅ byres 修饰完整选择
select binding_site, byres (receptor within 5.0 of ligand)

# ✅ around 方向正确
select around_ligand, ligand around 5.0

# ✅ 复合: 排除 + 限制范围
select receptor_around_ligand, (receptor and not hydrogens) within 5.0 of ligand
```

## 距离阈值经验值

| 用途 | 距离 (Å) | 说明 |
|------|---------|------|
| 直接接触 | 3.5 | 接触面 (binding interface) |
| 紧密接触 | 4.0 | 疏水 + 氢键混合 |
| 口袋边界 | 5.0 | 经典 "binding site" 定义 |
| 远端相互作用 | 8.0 | 长程 (盐桥尾巴, 水桥) |
| 配体 + 口袋整体 | 6.0 | 折中值 |

## 相关

- `select/basic.md` — `resi` / `resn` / `name` / `chain` / `elem` 基础
- `interactions/pocket.md` — byres + around 组合用法
- `interactions/h-bond.md` — 选 N/O/S 极性原子
