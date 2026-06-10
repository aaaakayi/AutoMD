# PyMOL 在 AutoMD 中的工作方式

**Q**: PyMOL 在 AutoMD 项目里是怎么被控制的? 写 `pymol_execute(cli_list=[...])` 时要注意什么?

---

## 总体架构

AutoMD 通过 **XML-RPC** 远程控制 PyMOL, 不是直接命令行。

| 项 | 值 |
|----|---|
| RPC URL | `http://127.0.0.1:9123` |
| RPC server 脚本 | `AutoMD_LangGraph/scripts/pymol_rpc_server.py` |
| 调用方式 | `server.do("pymol_cli_string")` |
| 路径要求 | **Windows 风格** (PyMOL 在 Windows 下运行) |

## 关键约束 (写 CLI 时必须遵守)

1. **路径必须用 Windows 风格**: `D:\AutoMD\...` 而不是 `/mnt/d/AutoMD/...`
2. **不要调用 GUI 操作**: PyMOL GUI 没有 CLI 等价, 用了会失败
3. **对象命名约定**: 蛋白 = `receptor`, 配体 = `ligand` (来自 `_load_scene`)
4. **每次 `pymol_execute` 是独立的 CLI 列表**: 用 `;` 分隔或每条独立行
5. **返回值**: 字符串 (OK 提示/错误信息) 或 None

## 不允许的 CLI 模式

```pymol
# ❌ GUI 操作 (无 CLI 版本)
click W
wizard measurement
menu pick
action ligand, find polar contacts, ...

# ❌ 启动窗口 (LLM 应该走 RPC 而不是 GUI)
pymol
open D:\foo.pdb
```

## 允许的 CLI 模式

```pymol
# ✅ 原子操作
load D:\foo.pdb, name
show cartoon, name
hide lines, name
color red, name
select resi 50

# ✅ 距离/相互作用 (代替 GUI 的 find polar contacts)
distance hb, ligand, receptor, 3.5, mode=2

# ✅ 选择语法
select pocket, byres (receptor and ligand around 5.0)

# ✅ 截图
png D:\out.png, dpi=300
ray 1600, 1200

# ✅ 会话保存
save D:\session.pse
```

## 工具调用 LLM 写 pymol_execute 的标准流程

1. **先调用 `query_pymol_knowledge(question)`** 拿到 CLI 指导 (本知识库)
2. **再把 CLI 列表喂给 pymol_execute**: `pymol_execute(cli_list=["load ...", "show ...", ...])`
3. **查看返回**: `embed_pymol` 返回每条 CLI 的"OK/ERROR" + 详情 (修复 1 后)
4. **如果某条失败**: 看错误信息, 调出对应知识文件改命令
5. **下一轮重试**: 用改正后的 CLI 列表再调一次 pymol_execute

## 颜色/命名约定速记 (详细见 `conventions/`)

| 元素 | 颜色 | 备注 |
|------|------|------|
| 蛋白主链 | `cyan` | 默认 |
| 配体 | `yellow` | 默认 |
| 关键残基 sticks | `magenta` / `hotpink` | 突变位点 |
| 口袋残基 sticks | `magenta` | 5Å 内 byres |

## 相关

- `load/pdb-pdbqt.md` — 加载具体写法
- `conventions/paths.md` — 路径模板
- `conventions/color-palette.md` — 配色完整表
- `pitfalls/path-errors.md` — WSL 路径错 (项目最常踩的坑)
