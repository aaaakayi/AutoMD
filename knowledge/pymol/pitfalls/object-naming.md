# 对象命名 (object name) — select 必须存在

**Q**: 选不到对象? `ObjectName` 报"object not found"?

---

## 答案: 对象必须用 `load` 命令显式创建

```pymol
# 创建对象
load D:\path\file.pdbqt, myobj
# 对象名 = "myobj" (空格后第二个字段)

# 用对象名选
select all_myobj, myobj
show sticks, myobj
color cyan, myobj
```

## 命名规则

```pymol
# 标准命名 (字母 + 数字 + 下划线)
load D:\p.pdbqt, receptor      # ✅
load D:\p.pdbqt, my_protein    # ✅
load D:\p.pdbqt, obj1          # ✅

# ❌ 不允许
load D:\p.pdbqt, my protein    # ❌ 含空格
load D:\p.pdbqt, my-protein    # ❌ 含连字符
load D:\p.pdbqt, 1obj          # ❌ 数字开头
```

## select 错的对象名

```pymol
# ❌ 用错的名称
load D:\p.pdbqt, receptor
select binding_site, byres (my_protein within 5.0 of ligand)
# → ERROR: 'my_protein' not found
# 因为对象叫 "receptor", 不是 "my_protein"
```

## ❌ 错 (没 load 就选)

```pymol
# ❌ 对象不存在
select binding_site, byres (receptor within 5.0 of ligand)
# → ERROR: "Selector-Error: No such object: 'receptor'"
# 因为 receptor 没被 load
```

## 查看当前对象

```pymol
# 列出所有对象
python
for obj in cmd.get_names("objects"):
    print(obj)
python end
```

```pymol
# PyMOL 命令
get_names
# 或
python end; print(cmd.get_names()); python
```

## 重命名对象

```pymol
# 改对象名
set_name old_name, new_name
```

## 删除对象

```pymol
# 删除单个
delete obj_name

# 删除所有
delete all
```

## 默认对象 (没显式命名)

```pymol
# 不写对象名, PyMOL 默认用文件名前缀
load D:\path\receptor.pdbqt
# → 对象名 "receptor" (从文件名)

load D:\path\my_complex.pdbqt
# → 对象名 "my_complex" (从文件名)
```

## 配体的对象名约定

```pymol
# AutoMD 约定: 用 "ligand" (不是 LIG, 不是 UNL)
load D:\path\docked.pdbqt, ligand
# 之后所有 select / show 都用 "ligand"
```

## 多对象场景

```pymol
# 加载 3 个对象
load D:\p1.pdbqt, prot1
load D:\p2.pdbqt, prot2
load D:\lig.pdbqt, ligand

# 选 "所有非 ligand"
select all_protein, prot1 prot2

# 选 prot1 + prot2
select all_prot, prot1 or prot2
```

## 速查

```pymol
# 加载时命名
load D:\file.pdbqt, obj_name
# 之后: select, show, color 都用 obj_name

# 列出对象
get_names

# 改名
set_name old, new

# 删
delete obj_name
```

## 相关

- `load/pdb-pdbqt.md` — load 语法
- `load/mol2-sdf.md` — load 语法
- `select/basic.md` — select 语法
