# 光线追踪 (ray) — 出版级截图

**Q**: 怎么用 PyMOL 渲染出出版级质量的图?

---

## ray + png 组合

```pymol
# 1. 调整背景和样式
bg_color white

# 2. 设置光追参数
set ray_opaque_background, off
set ray_trace_mode, 1
set ray_shadows, 0

# 3. 光线追踪 (大图, 慢)
ray 2400, 1600

# 4. 保存 PNG
png D:\path\to\output.png, dpi=300
```

## ray 参数

| 参数 | 值 | 说明 |
|------|---|------|
| 宽 (W) | 整数 | 输出图像宽度像素 |
| 高 (H) | 整数 | 输出图像高度像素 |
| 默认 | 视口大小 | 不指定 W/H 用 viewport |

**推荐**: `viewport W H; ray W*2 H*2` —— ray 用 2x viewport, 输出更清晰。

## 光追参数调优

```pymol
# 阴影
set ray_shadows, 0          # 关闭阴影 (默认开)
set ray_shadows, 1          # 开启阴影

# 反射
set ray_trace_mode, 0       # 简单 (无反射)
set ray_trace_mode, 1       # 标准反射 (默认)
set ray_trace_mode, 2       # 高级反射 (慢)

# 抗锯齿
set antialias, 1            # 1 = 开, 2 = 2x, 3 = 3x
set antialias, 2

# 背景
set ray_opaque_background, on   # 不透明背景
set ray_opaque_background, off  # 透明背景 (PNG alpha)

# 光线深度
set ray_max_recursion, 2       # 反射次数 (默认 2)
set ray_max_recursion, 4       # 多次反射 (慢)
```

## 完整出版级工作流

```pymol
# 1. 加载 + 基础
load D:\path\protein.pdbqt, receptor
load D:\path\ligand.pdbqt, ligand
show cartoon, receptor
color cyan, receptor
set cartoon_transparency, 0.3, receptor
show sticks, ligand
color yellow, ligand

# 2. 调整
orient receptor
zoom ligand, 6
bg_color white

# 3. 光追设置
set ray_opaque_background, off
set ray_trace_mode, 1
set ray_shadows, 0
set antialias, 2

# 4. 渲染 (2x viewport)
viewport 1200, 800
ray 2400, 1600

# 5. 输出
png D:\path\to\publication.png, dpi=300
```

## 耗时估计

| 体系大小 | viewport | ray 2x | 耗时 |
|---------|----------|--------|------|
| 小 (1 个蛋白) | 1200×800 | 2400×1600 | 5-15s |
| 中 (复合物 5K 残基) | 1200×800 | 2400×1600 | 30-60s |
| 大 (复合物 10K+ 残基, 表面) | 1200×800 | 2400×1600 | 60-180s |

**调节**: 缩小 viewport / 关闭 antialias / 简化显示 (减少 surface) 提速。

## 关键 ❌ 错

```pymol
# ❌ ray 后用 png 不带 ray=1 (PyMOL 用 OpenGL 状态)
ray 2400, 1600
png D:\out.png
# → 用的是 OpenGL 状态, 不是 ray 渲染的
# 解决: png ... ray=1 (注意是 png 内部参数)
# 或: 用 png (无参数), 因为 ray 已经更新内部缓冲
# 实际: PyMOL 0.99+ 之后 ray 之后 png 自动用 ray 缓冲, 不需要 ray=1

# ❌ 不调光追参数
ray 2400, 1600
png D:\out.png
# → 默认 antialias=0, 锯齿明显
```

## ✅ 对

```pymol
# ✅ 完整光追配置
set ray_trace_mode, 1
set ray_shadows, 0
set antialias, 2
ray 2400, 1600
png D:\path\to\output.png, dpi=300
```

## 速查

```pymol
# 出版级 PNG
set ray_trace_mode, 1
set ray_shadows, 0
set antialias, 2
viewport 1200, 800
ray 2400, 1600
png D:\path\to\output.png, dpi=300

# 透明背景出版级
set ray_opaque_background, off
... (其余同上)
```

## 相关

- `screenshot/png.md` — 普通截图
- `screenshot-recipes/publication-quality.md` — 完整出版级工作流
- `conventions/paths.md` — 截图路径模板
