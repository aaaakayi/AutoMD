# 野生型 vs 突变型 对比

**Q**: 怎么把野生型和突变型蛋白叠在一起, 高亮结构差异?

---

## 完整 CLI 序列

```pymol
# === 1. 加载两个结构 ===
load D:\path\wildtype.pdbqt, wt
load D:\path\mutant.pdbqt, mut

# === 2. 对齐 (mut 叠到 wt) ===
align mut, wt

# === 3. 基础显示 ===
hide everything, all
show cartoon, wt
show cartoon, mut
color tv_blue, wt          # 野生型蓝
color tv_red, mut          # 突变型红
set cartoon_transparency, 0.3, wt
set cartoon_transparency, 0.3, mut

# === 4. 显示差异残基 (CA 位置不同) ===
# 找 RMSD > 1Å 的残基
select wt_diff_ca, wt and name CA and not (wt and name CA within 1.0 of (mut and name CA))
select mut_diff_ca, mut and name CA and not (mut and name CA within 1.0 of (wt and name CA))

# 显示差异残基为 sticks
show sticks, wt_diff_ca and not name N+CA+C+O
color yellow, wt_diff_ca
show sticks, mut_diff_ca and not name N+CA+C+O
color magenta, mut_diff_ca

# === 5. 计算并显示 RMSD ===
python
rmsd_val = cmd.rms_cur("wt and name CA", "mut and name CA")
print(f"蛋白质 Cα RMSD: {rmsd_val:.3f} Å")
python end

# === 6. 配体定位 (如果有) ===
load D:\path\ligand.pdbqt, ligand
show sticks, ligand
color yellow, ligand
zoom ligand, 5
```

## 用 `align` 命令

```pymol
# 基础对齐
align mobile, target

# 完整参数
align mobile, target [, cutoff [, cycles [, gap [, extend [, max_gap [, object [, matrix [, mobile_state [, target_state [, quiet [, maxskip [, transform [, reset ]]]]]]]]]]]]

# 常用
align mut, wt, cutoff=2.0, cycles=5
# cutoff=2.0: 原子距离 > 2Å 算异常值
# cycles=5: 最多 5 轮迭代
```

**重要**: `align` 是叠合 (superposition), 不仅对齐序号, 还做刚体变换让两结构重叠。返回 RMSD。

## 变体: 叠合后单独显示差异

```pymol
# 1. 加载
load D:\wt.pdbqt, wt
load D:\mut.pdbqt, mut

# 2. 对齐
align mut, wt

# 3. 显示差异 Cα 位置
# 配对 wt Cα 和 mut Cα, 距离 > 2Å 的标红
python
wt_ca = cmd.get_model("wt and name CA")
mut_ca = cmd.get_model("mut and name CA")
# 用距离对象可视化
cmd.distance("ca_diff", "wt and name CA", "mut and name CA", 2.0)
cmd.set("dash_color", "red", "ca_diff")
cmd.set("dash_radius", "0.05", "ca_diff")
python end
```

## 变体: 突变位点 sticks 高亮

```pymol
# 1. 加载
load D:\wt.pdbqt, wt
load D:\mut.pdbqt, mut

# 2. 对齐
align mut, wt

# 3. 假设突变在 50-60 位
select mut_site, mut and resi 50-60
show sticks, mut_site
color red, mut_site
label mut_site and name CA, "%n%r"
set label_color, red
set label_size, 14

# 4. 同位点野生型作对比
select wt_site, wt and resi 50-60
show sticks, wt_site
color blue, wt_site
label wt_site and name CA, "%n%r"
set label_color, blue
```

## 变体: 全原子差异 (用 align 后的 B-factor)

```pymol
# 1. 加载 + 对齐
load D:\wt.pdbqt, wt
load D:\mut.pdbqt, mut
align mut, wt

# 2. 把 wt 的 Cα 到 mut Cα 的距离写到 wt 的 b
python
# 把 wt 所有 Cα 的 b 设为与最近 mut Cα 的距离
for wt_resi in cmd.get_model("wt and name CA").atom:
    d = cmd.get_distance(
        f"wt and resi {wt_resi.resi} and name CA",
        f"mut and resi {wt_resi.resi} and name CA"
    )
    cmd.alter(f"wt and resi {wt_resi.resi} and name CA", f"b={d}")
python end

# 3. spectrum 按 b 着色
spectrum b, blue_white_red, wt and name CA, minimum=0, maximum=5
```

## 完整工作流 (野生型 vs 突变型, 含配体)

```pymol
# 1. 加载
load D:\wt.pdbqt, wt
load D:\mut.pdbqt, mut
load D:\ligand.pdbqt, ligand

# 2. 对齐
align mut, wt

# 3. 卡通
hide everything, all
show cartoon, wt
show cartoon, mut
color tv_blue, wt
color tv_red, mut
set cartoon_transparency, 0.3, wt
set cartoon_transparency, 0.3, mut

# 4. 配体 sticks
show sticks, ligand
color yellow, ligand

# 5. 突变位点 sticks 高亮 (假设 50-60)
select mut_site, mut and resi 50-60 and not name N+CA+C+O
show sticks, mut_site
color red, mut_site
label mut_site and name CA, "%n%r"
set label_color, red
set label_size, 14

# 6. RMSD
python
rmsd_val = cmd.rms_cur("wt and name CA", "mut and name CA")
print(f"Cα RMSD: {rmsd_val:.3f} Å")
python end

# 7. 视图
zoom ligand, 5

# 8. 输出
bg_color white
viewport 1200, 800
png D:\path\to\wt_vs_mut.png, dpi=300
```

## 速查

```pymol
# 基础对比
load D:\wt.pdbqt, wt
load D:\mut.pdbqt, mut
align mut, wt
hide everything, all
show cartoon, wt
show cartoon, mut
color tv_blue, wt
color tv_red, mut
set cartoon_transparency, 0.3, wt
set cartoon_transparency, 0.3, mut
show sticks, ligand
color yellow, ligand
zoom ligand, 5
png D:\out.png, dpi=300
```

## 关键 ❌ 错

```pymol
# ❌ align 后没用 (没显示差异)
align mut, wt
# 仅仅 align, 不会自动显示差异
# → 用户看不到任何区别

# ❌ 对齐对象名错
align wt, mut   # wt 叠到 mut, 反了
# 应该是: align mut, wt (让 mut 去对 wt)
```

## ✅ 对

```pymol
# ✅ align 后明确显示差异
align mut, wt
show cartoon, wt
show cartoon, mut
color tv_blue, wt
color tv_red, mut

# ✅ 配体居中
show sticks, ligand
zoom ligand, 5
```

## 相关

- `select/basic.md` — 残基选择
- `select/spatial.md` — within 选择
- `screenshot-recipes/publication-quality.md` — 出版级输出
