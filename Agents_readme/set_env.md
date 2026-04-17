# AutoMD 环境配置与 Agent 调用指南

## 1. 文档目标

本指南用于统一 AutoMD 的环境安装、验证与故障排查策略，并与 `tools/set_env.py` 中的 `setup_environment` 工具保持一致。

适用场景：

- 配体参数化（`antechamber/sqm/openbabel/rdkit`）
- 蛋白预处理（`openmm/pdbfixer`）
- 对接（`vina`）
- Agent 自动补依赖（通过 `setup_environment`）

## 2. setup_environment 工具行为（以代码为准）

`setup_environment(packages=None, method="conda", target_env=None)`

- `packages`：必须是非空列表；默认 `['ambertools']`
- `method`：仅支持 `conda` 或 `pip`
- `target_env`：仅 `conda` 模式有效，可指定环境名

关键逻辑：

1. `conda` 模式下，工具会优先把包安装到当前激活环境（`CONDA_PREFIX` / `CONDA_DEFAULT_ENV`）。
2. 若未检测到激活环境，返回错误并提示先执行 `conda activate AutoMD`。
3. `pip` 模式下安装到当前 Python 解释器（`sys.executable`）。

说明：该工具当前只负责安装，不负责渠道配置与卸载策略编排。

## 3. 推荐基线环境

### 3.1 创建与激活
- **检查环境是否存在**：使用 `conda env list` 查看现有环境列表。
- **如果环境已存在且需要重建**：必须先删除旧环境再创建，否则 conda 会报错。
```bash
conda create -n AutoMD python=3.10 -y
conda activate AutoMD
```

### 3.2 推荐渠道

```bash
conda config --add channels conda-forge
conda config --set channel_priority flexible
```

### 3.3 核心依赖建议

```bash
conda install -c conda-forge ambertools openbabel rdkit openmm vina -y
pip install pdbfixer
```

## 4. 包与用途映射

| 包名 | 主要用途 | 推荐安装方式 |
|---|---|---|
| `ambertools` | `antechamber`、`sqm`、GAFF 参数化 | conda |
| `openbabel` | 格式转换、Gasteiger 电荷兜底 | conda |
| `rdkit` | SMILES 解析、电荷估算、构象生成 | conda |
| `openmm` | 蛋白预处理、力场与体系构建 | conda |
| `pdbfixer` | PDB 结构修复 | pip（或 conda 可用时优先 conda-forge） |
| `vina` | 分子对接 | conda |

## 5. Agent 工具调用模板

### 5.1 安装 ambertools（最常见）

```python
setup_environment(packages=["ambertools"], method="conda")
```

### 5.2 一次补齐多个 conda 依赖

```python
setup_environment(
    packages=["ambertools", "openbabel", "rdkit", "openmm", "vina"],
    method="conda",
)
```

### 5.3 指定环境名安装

```python
setup_environment(packages=["ambertools"], method="conda", target_env="AutoMD")
```

### 5.4 安装 Python 包

```python
setup_environment(packages=["pdbfixer"], method="pip")
```

## 6. 常见问题与修复策略

### 6.1 `antechamber: command not found`

- 原因：`ambertools` 未安装或 conda 环境未激活。
- 处理：
  1. `conda activate AutoMD`
  2. `setup_environment(packages=["ambertools"], method="conda")`

### 6.2 `sqm: command not found` 或 `sqm: Fatal Error`

- 原因：`ambertools` 安装不完整或环境库冲突。
- 处理：
  1. 重新安装 `ambertools`
  2. WSL 下必要时修复 `ncurses`：

```bash
conda install -c conda-forge ncurses=6.4 --force-reinstall
```

### 6.3 `obabel: command not found`

- 处理：`setup_environment(packages=["openbabel"], method="conda")`

### 6.4 `No module named 'openmm'` / `import rdkit` 失败

- 处理：优先使用 conda 安装对应包，避免混用 pip 版本。

### 6.5 `prepare_ligand` 出现连通性/多单元结构错误

- 这通常是输入结构质量问题，不是纯环境问题。
- 处理建议（来自配体流程规范）：
  1. 确保配体输入为单一分子、连接信息完整
  2. 必要时先用 `openbabel` 转 `mol2`
  3. 若 antechamber 仍失败，走降级方案（Open Babel/Gasteiger）或改用 CGenFF/SwissParam

### 6.6 `Permission denied`（安装阶段）

- 原因：conda 安装目录权限不足。
- 处理：
  1. 使用用户目录下的 Miniconda/Anaconda
  2. 确认当前 shell 对目标环境有写权限

## 7. 验证清单

```bash
which conda
which antechamber
which sqm
which obabel
which vina
python -c "import openmm, rdkit, pdbfixer; print('OK')"
```

验证通过标准：

1. 关键可执行文件均可定位。
2. Python 关键模块均可导入。
3. Agent 调用 `setup_environment` 返回“成功安装”而非环境未激活错误。

## 8. Agent 执行建议

1. 优先最小安装：按报错缺什么补什么，不要盲目全量重装。
2. `conda` 安装失败后，再考虑 `pip`（且只用于纯 Python 包）。
3. 对“结构质量错误”与“环境缺依赖”分流处理，避免错误路径反复重试。
4. 安装后必须执行验证命令，再进入参数化或对接步骤。