# 蛋白质预处理报告

## 执行时间
$(date)

## PDB信息
- PDB ID: 1IEP
- 原始PDB文件: ./output/protein_preparation/1IEP.pdb
- 清洗后PDB文件: ./output/protein_preparation/1IEP_clean.pdb
- 蛋白质专用PDB文件: ./output/protein_preparation/1IEP_protein_only.pdb
- 蛋白质PDBQT文件: ./output/protein_preparation/1IEP.pdbqt
- AMBER拓扑文件: ./output/protein_preparation/1IEP.prmtop
- AMBER坐标文件: ./output/protein_preparation/1IEP.inpcrd

## 处理步骤
1. **下载原始PDB**: 使用prepare_pure_protein工具从RCSB PDB下载1IEP结构
2. **蛋白质清洗**: 使用pdb4amber进行清洗，包括：
   - 去除非标准残基
   - 添加氢原子
   - 处理质子化状态（pH 7.4）
   - 处理二硫键
3. **生成拓扑文件**: 使用tleap生成AMBER格式的拓扑和坐标文件
4. **转换为PDBQT**: 使用MGLTools的prepare_receptor4.py将蛋白质转换为PDBQT格式用于对接

## 文件统计
$(wc -l ./output/protein_preparation/1IEP.pdb | awk '{print "原始PDB行数: " $1}')
$(wc -l ./output/protein_preparation/1IEP_clean.pdb | awk '{print "清洗后PDB行数: " $1}')
$(wc -l ./output/protein_preparation/1IEP_protein_only.pdb | awk '{print "蛋白质专用PDB行数: " $1}')
$(wc -l ./output/protein_preparation/1IEP.pdbqt | awk '{print "PDBQT文件行数: " $1}')

## 非标准残基处理
prepare_pure_protein工具自动删除了非标准残基，清洗后的蛋白质仅包含标准氨基酸残基。

## 验证
- 蛋白质PDBQT文件已成功生成，可用于AutoDock Vina对接
- AMBER拓扑和坐标文件已生成，可用于后续分子动力学模拟

