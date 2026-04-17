#!/usr/bin/env python3
import sys

def remove_duplicate_bonds(mol2_file, output_file):
    """去除mol2文件中的重复键"""
    with open(mol2_file, 'r') as f:
        lines = f.readlines()
    
    in_bond_section = False
    bond_lines = []
    other_lines = []
    seen_bonds = set()
    
    for line in lines:
        if '@<TRIPOS>BOND' in line:
            in_bond_section = True
            other_lines.append(line)
        elif '@<TRIPOS>SUBSTRUCTURE' in line:
            in_bond_section = False
            other_lines.append(line)
        elif in_bond_section:
            # 处理键行
            parts = line.strip().split()
            if len(parts) >= 3:
                atom1 = int(parts[1])
                atom2 = int(parts[2])
                # 创建规范化的键标识符（小的原子编号在前）
                bond_key = tuple(sorted([atom1, atom2]))
                if bond_key not in seen_bonds:
                    seen_bonds.add(bond_key)
                    bond_lines.append(line)
                else:
                    print(f"发现重复键: {atom1}-{atom2}，已跳过")
            else:
                bond_lines.append(line)
        else:
            other_lines.append(line)
    
    # 写入修复后的文件
    with open(output_file, 'w') as f:
        for line in other_lines:
            if '@<TRIPOS>BOND' in line:
                f.write(line)
                for bond_line in bond_lines:
                    f.write(bond_line)
            else:
                f.write(line)
    
    print(f"原始键数: {len([l for l in lines if '@<TRIPOS>BOND' in l or (in_bond_section and l.strip())])}")
    print(f"修复后键数: {len(bond_lines)}")
    print(f"已去除 {len([l for l in lines if '@<TRIPOS>BOND' in l or (in_bond_section and l.strip())]) - len(bond_lines)} 个重复键")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python fix_mol2.py 输入.mol2 输出.mol2")
        sys.exit(1)
    
    remove_duplicate_bonds(sys.argv[1], sys.argv[2])