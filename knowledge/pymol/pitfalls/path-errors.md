# 路径错误 (Windows + WSL)

**Q**: PyMOL 报"Could not open file"怎么办?

---

## 根本原则

PyMOL 跑在 **Windows** 上 (`D:\`), 不是 WSL (`/mnt/d/`)。
所有路径必须是 **Windows 风格** `D:\path\to\file.ext`。

## ❌ 错 (WSL/Linux 风格)

```pymol
load /mnt/d/AutoMD/output/receptor.pdbqt
load ~/project/receptor.pdbqt
load ./receptor.pdbqt
# → "ERROR: Could not open file '/mnt/d/AutoMD/output/receptor.pdbqt'"
```

## ✅ 对 (Windows 风格)

```pymol
load D:\AutoMD\AutoMD_LangGraph\output\03452596\protein\1HX0\receptor\1HX0_protein_only.pdbqt
# → OK
```

## 反斜杠 vs 正斜杠

PyMOL 都接受:
- `D:\path\file.ext` (反斜杠, Windows 原生)
- `D:/path/file.ext` (正斜杠, 跨平台)

**推荐**: 用反斜杠, 跟 Windows 资源管理器一致。

## 路径存在性

```pymol
# 加载前先检查 (PyMOL 0.99+)
import os
if os.path.exists("D:\\path\\to\\file.pdbqt"):
    load D:\path\to\file.pdbqt
else:
    print "File not found"
```

## 常见错误码

| 报错 | 原因 | 修复 |
|------|------|------|
| `Could not open file` | 路径错 / 文件不存在 | 改 Windows 路径, 检查文件 |
| `Permission denied` | 文件被占用 | 关 Excel/Notepad++, 重试 |
| `No such file or directory` | 目录不存在 | 用 `mkdir` 创建 |
| `Bad magic number` | 文件不是 PDB/PDBQT 格式 | 检查文件类型 |

## 网络/UNC 路径

```pymol
# UNC 路径 (网络共享) 支持
load \\server\share\file.pdbqt

# 映射网络驱动器也行
load Z:\path\file.pdbqt
```

## 保存路径

```pymol
# 保存到 D 盘
png D:\AutoMD\AutoMD_LangGraph\output\pymol\view.png

# 注意目录必须存在
import os
os.makedirs("D:\\AutoMD\\output\\pymol", exist_ok=True)
png D:\AutoMD\output\pymol\view.png
```

## ❌ 错 (项目代码生成 Linux 路径)

```python
# ❌ Python 代码生成 WSL 路径
output_path = "/mnt/d/AutoMD/output/view.png"  # PyMOL 在 Windows 下读不到
```

```python
# ✅ 正确: 生成 Windows 路径
output_path = "D:\\AutoMD\\AutoMD_LangGraph\\output\\pymol\\view.png"
# 或
output_path = r"D:\AutoMD\AutoMD_LangGraph\output\pymol\view.png"
```

## 速查

```pymol
# ✅ 正确路径格式
load D:\path\to\file.pdbqt, obj_name
png D:\path\to\out.png, dpi=300

# ❌ 错误格式
load /mnt/d/path/to/file.pdbqt        # WSL 风格
load /home/user/file.pdbqt            # Linux 风格
load ./file.pdbqt                      # 相对路径 (可能错)
```

## 相关

- `0-overview.md` — PyMOL RPC 接口, Windows 路径要求
- `conventions/paths.md` — AutoMD 项目输出路径约定
