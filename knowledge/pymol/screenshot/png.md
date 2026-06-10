# 普通 PNG 截图

**Q**: 怎么从 PyMOL 截一张 PNG 图?

---

## 基本 PNG 截图

```pymol
# 默认尺寸 (与当前 viewport 一致)
png D:\path\to\output.png

# 指定 dpi (出版建议 ≥ 300)
png D:\path\to\output.png, dpi=300
```

## 设置 viewport (画布大小)

```pymol
# 设置画布大小 (像素)
viewport 1200, 800

# 截到文件
png D:\path\to\output.png, dpi=300
```

**viewport 与 dpi 的关系**:
- viewport 1200×800, dpi=300 → 输出 4×3 inch, 实际像素 1200×800
- dpi 只影响**打印尺寸**, 不影响像素

## Ray vs 不 Ray

| 模式 | 命令 | 速度 | 质量 |
|------|------|------|------|
| 直接 | `png` | 快 (秒) | 一般 (OpenGL 直出) |
| 光线追踪 | `ray` + `png` | 慢 (10-60s) | 出版级 |

普通截图**用 `png` 即可**, 出图用 `ray + png` 组合 (见 `ray.md`)。

## 透明背景

```pymol
# 透明背景 (PNG alpha 通道)
set ray_opaque_background, off
png D:\path\to\output.png, dpi=300, ray=1
```

## DPI 选择指南

| 用途 | dpi |
|------|-----|
| 屏幕显示 | 72-150 |
| 报告/PPT | 150-200 |
| 论文 (期刊要求) | 300-600 |
| 海报 | 600+ |

## 关键 ❌ 错

```pymol
# ❌ 没设 viewport, 截图尺寸是当前窗口大小 (可能很小)
png D:\out.png
# → 截图可能 400x300, 模糊

# ❌ 没指定 dpi, 印刷质量低
png D:\out.png
# → 默认 72 dpi, 印刷糊

# ❌ 路径错
png /mnt/d/AutoMD/out.png
# → "ERROR: Could not open file"
```

## ✅ 对

```pymol
# ✅ 显式设 viewport + dpi
viewport 1200, 800
png D:\path\to\output.png, dpi=300

# ✅ 透明背景
set ray_opaque_background, off
png D:\path\to\output_transparent.png, dpi=300
```

## 速查

```pymol
# 标准截图
viewport 1200, 800
png D:\path\to\out.png, dpi=300

# 透明背景
set ray_opaque_background, off
png D:\path\to\out.png, dpi=300

# 出版级 (用 ray)
ray 2400, 1600
png D:\path\to\out.png, dpi=300
```

## 相关

- `screenshot/ray.md` — 光线追踪高质量
- `screenshot-recipes/publication-quality.md` — 完整出版级工作流
- `pitfalls/path-errors.md` — 路径错
