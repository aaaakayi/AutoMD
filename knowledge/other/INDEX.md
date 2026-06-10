# other Knowledge Index

> AutoMD 项目中其它的知识库模板
> 用途: ....
> 维护: .. 个原子 .md, 每个文件聚焦一个特定问题

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

- ....

## 问题 1 

...

## 问题 2

...

## 问题 3

....

---

## 关键词速查

| 想做的 | 找 |
|--------|-----|
| 问题 | `问题1/xxx.md` |
| 问题 | `问题1/xxx.md` |