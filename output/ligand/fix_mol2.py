with open('./output/ligand/lig_dedup2.mol2', 'r') as f:
    lines = f.readlines()

# The molecule header line is line 2 (0-indexed)
# Format: "   68    85     1     0     0"
# Change 85 to 72
lines[2] = "   68    72     1     0     0\n"

with open('./output/ligand/lig_dedup3.mol2', 'w') as f:
    f.writelines(lines)

print('Written lig_dedup3.mol2')
