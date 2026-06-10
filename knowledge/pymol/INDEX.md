# PyMOL Knowledge Index

> AutoMD 项目中工具调用 LLM 写 `pymol_execute(cli_list=[...])` 时的知识库
> 用途: 工具调用 LLM 调用 `query_pymol_knowledge(question)` 时, 此 LLM 据此 INDEX 找到相关文件
> 维护: 25 个原子 .md, 每个文件聚焦一个特定问题

---

## 如何新建一个知识库 (本 KB 是模板)

按本 KB 的结构新建一个 `knowledge/<your-topic>/`, 必须含 `INDEX.md`, 可选含子目录和原子 .md:

1. 创建 `knowledge/<your-topic>/` 目录, 例 `knowledge/cpptraj-analysis/`
2. 写一个 `INDEX.md`, 列出所有子文件相对路径, 格式参考本文件
3. 放原子 .md 到子目录, 例 `analysis/rmsd.md`
4. 完成。`LLM/retrieval_llm.py` 模块加载时自动扫描, 会在 prompt 里列出新 KB

新 KB 的 INDEX.md 必须含:
- 顶部 1-2 句话说明 KB 是啥, 给谁用
- `## 分类` 小节, 按需分目录 (load/, select/, pitfalls/ ... 不强制)
- `## 关键词速查` 小节 (推荐, 帮助 LLM 快速定位)

---

## 项目总体

- `0-overview.md` — PyMOL 在 AutoMD 中的工作方式 (RPC 接口, 路径转换, 配色约定)

## 加载 (load/)

- `load/pdb-pdbqt.md` — 加载 PDB/PDBQT 文件, 含 `fetch` 离线备选
- `load/mol2-sdf.md` — 配体用 mol2/sdf 加载

## 选择语法 (select)

- `select/basic.md` — 残基编号/名/链/原子名/元素基础选择 + and/or/not 组合
- `select/spatial.md` — `around` / `within` / `byres` 空间与残基扩展

## 显示样式 (show)

- `show/representations.md` — `cartoon` / `sticks` / `surface` 等, `show` vs `as` 区别

## 颜色 (color)

- `color/basic.md` — 12 基础色 + 自定义 RGB + by element
- `color/spectrum.md` — 按 B-factor/能量/距离 着色

## 相互作用分析 (核心业务场景)

- `interactions/h-bond.md` — 蛋白-配体氢键 (`distance mode=2`)
- `interactions/hydrophobic.md` — 疏水接触 (C-C ≤ 4.0 Å)
- `interactions/pi-stacking.md` — 芳香环 π-π 堆积
- `interactions/salt-bridge.md` — 盐桥 (带电残基)
- `interactions/pocket.md` — 结合位点残基识别与高亮

## 截图 (screenshot)

- `screenshot/png.md` — 普通 PNG 截图
- `screenshot/ray.md` — 光线追踪高质量
- `screenshot-recipes/publication-quality.md` — 出版级 PNG 完整工作流

## 复合任务 (analysis-recipes)

- `analysis-recipes/standard-binding-view.md` — 标准蛋白+配体+H 键+疏水视图
- `analysis-recipes/mutation-suggestion.md` — 关键残基高亮 + 标签
- `analysis-recipes/wt-vs-mutant.md` — 野生型 vs 突变型对齐对比

## 常见错误 (pitfalls)

- `pitfalls/path-errors.md` — WSL 路径 vs Windows 路径
- `pitfalls/show-vs-as.md` — `show` 累加 vs `as` 替换
- `pitfalls/object-naming.md` — 对象名拼写错 / `select` 后不用
- `pitfalls/distance-mode.md` — `distance` 不指定 mode 默认会显示 N×M 条虚线

## AutoMD 项目约定 (conventions)

- `conventions/color-palette.md` — AutoMD 配色 (cyan 蛋白 / yellow 配体 / magenta 关键残基)
- `conventions/paths.md` — `output/{session_id}/...` 路径模板

---

## 关键词速查

| 想做的 | 找 |
|--------|-----|
| 加载结构 | `load/pdb-pdbqt.md` |
| 选择残基 | `select/basic.md` |
| 选配体周围 5Å 残基 | `select/spatial.md` + `interactions/pocket.md` |
| 找氢键 | `interactions/h-bond.md` |
| 找疏水接触 | `interactions/hydrophobic.md` |
| 找 π 堆积 | `interactions/pi-stacking.md` |
| 找盐桥 | `interactions/salt-bridge.md` |
| 高亮结合位点 | `interactions/pocket.md` + `conventions/color-palette.md` |
| 截图 | `screenshot/png.md` 或 `screenshot/ray.md` |
| 出版级图 | `screenshot-recipes/publication-quality.md` |
| 完整可视化 (H 键 + 疏水 + 高亮) | `analysis-recipes/standard-binding-view.md` |
| 突变位点建议 | `analysis-recipes/mutation-suggestion.md` |
| 野生型 vs 突变型 | `analysis-recipes/wt-vs-mutant.md` |
| 路径错 | `pitfalls/path-errors.md` |
| show 没生效 | `pitfalls/show-vs-as.md` |
| object not found | `pitfalls/object-naming.md` |
| 距离对象全是线 | `pitfalls/distance-mode.md` |
