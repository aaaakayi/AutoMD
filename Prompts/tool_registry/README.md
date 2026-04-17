# AutoMD 工具注册拆分说明

本目录用于存放单工具元数据文件。主入口索引在 [../tool_registry.json](../tool_registry.json) 中。

## 维护规则

1. 修改某个 `tools/` 下工具的参数、描述或行为时，优先更新本目录下对应的 `*.json` 文件。
2. 若新增或删除工具，只需要：
   - 新建/删除对应的单工具 JSON；
   - 在 [../tool_registry.json](../tool_registry.json) 的 `categories` 中增删 `include` 项。
3. `tools/use_tools.py` 兼容旧版列表和当前的索引 + include 结构。

## 文件命名建议

- 文件名建议与 `function` 保持一致，例如 `prepare_protein.json` 对应 `function: prepare_protein`。
- 一个文件只描述一个工具，便于 diff、审查和局部修改。
