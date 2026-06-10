# prompt/

LLM prompt 集中管理目录。每个 `.md` 文件对应一个 LLM 客户端, 内含多个 `<!-- SECTION_NAME -->` 标记的 prompt section。

## 文件

| 文件 | 用途 | Section 列表 |
|---|---|---|
| `chat.md` | chat.py 用的 3 类 LLM | `TOOL_LLM_SYSTEM`, `SUMMARY_SYSTEM`, `SUMMARY_REQUIREMENTS`, `COMPRESSOR_SYSTEM` |
| `fallback.md` | fallback_agent.py 用的 2 类 LLM | `DIAGNOSIS_SYSTEM`, `ACTION_DECIDE_PROMPT`, `ACTION_CURRENT_FAILURE`, `ACTION_FAILED_NODE` |

> 注意: 4 类 chat LLM (`tool_llm` / 总结 LLM / compressor LLM) 共用同一个 `_build_llm()` 工厂, 所以归在 `chat.md` 一份。
> 同样 2 类 fallback LLM (诊断 + 行动) 共用 `_llm`, 归在 `fallback.md`。

## 加载方式

```python
from prompt import load, list_sections, clear_cache

# 读 section
text = load("chat", "TOOL_LLM_SYSTEM")  # raw 字符串
user_msg = load("chat", "SUMMARY_REQUIREMENTS").format(session_id=sid)  # 替换占位符

# 列出某个文件的所有 section
print(list_sections("chat"))
# ['COMPRESSOR_SYSTEM', 'SUMMARY_REQUIREMENTS', 'SUMMARY_SYSTEM', 'TOOL_LLM_SYSTEM']

# 开发时改完 .md 强制重读
clear_cache()
```

## 编写约定

- **Section 标记**: `<!-- SECTION_NAME -->` (全大写下划线), 独占一行
- **Section body**: 标记之后到下一个 section 之前的所有行
- **元注释**: 任何其它 `<!-- ... -->` 注释行会被加载器自动剥除, 可在 section 顶部加 `<!-- 变量: {sid} {output_path} -->` 之类提示
- **字面 `{}` 安全**: 加载器**不**调用 `str.format()`, 字面 `{}` 保持原样; 需要占位符时调用方自己 `.format()`
- **空行**: section body 头尾的空行会被剥除
- **编码**: UTF-8

## 何时修改

| 改什么 | 改哪里 |
|---|---|
| LLM 行为不对 (调工具不准、总结格式错、...) | `prompt/*.md` |
| 加新 LLM 客户端 | 新建 `prompt/<module>.md`, 在 `__init__.py` 不需要改 |
| 加新 section 到现有文件 | 直接编辑 `.md`, 加 `<!-- NEW_SECTION -->`, 不需要改 Python |
| 改 section 名 (重命名) | `.md` 改名 + 改所有 `load("module", "OLD")` 调用 |

## 缓存

`_load_sections()` 用 `funru_cache` 缓存每个 .md 文件的解析结果。**改完 .md 不需要重启服务**, 调用 `clear_cache()` 即可重读 (开发模式可加文件 watch 自动触发)。
