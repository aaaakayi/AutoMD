from pymol import cmd,stored

set depth_cue, 1
set fog_start, 0.4

set_color b_col, [36,36,85]
set_color t_col, [10,10,10]
set bg_rgb_bottom, b_col
set bg_rgb_top, t_col      
set bg_gradient

set  spec_power  =  200
set  spec_refl   =  0

load "data/1IEP_protein_only.pdb", protein
create ligands, protein and organic
select xlig, protein and organic
delete xlig

hide everything, all

color white, elem c
color bluewhite, protein
#show_as cartoon, protein
show surface, protein
#set transparency, 0.15

show sticks, ligands
set stick_color, magenta




# SAS points

load "data/1IEP_protein_only.pdb_points.pdb.gz", points
hide nonbonded, points
show nb_spheres, points
set sphere_scale, 0.2, points
cmd.spectrum("b", "green_red", selection="points", minimum=0, maximum=0.7)


stored.list=[]
cmd.iterate("(resn STP)","stored.list.append(resi)")    # read info about residues STP
lastSTP=stored.list[-1] # get the index of the last residue
hide lines, resn STP

cmd.select("rest", "resn STP and resi 0")

for my_index in range(1,int(lastSTP)+1): cmd.select("pocket"+str(my_index), "resn STP and resi "+str(my_index))
for my_index in range(1,int(lastSTP)+1): cmd.show("spheres","pocket"+str(my_index))
for my_index in range(1,int(lastSTP)+1): cmd.set("sphere_scale","0.4","pocket"+str(my_index))
for my_index in range(1,int(lastSTP)+1): cmd.set("sphere_transparency","0.1","pocket"+str(my_index))



set_color pcol1 = [0.361,0.576,0.902]
select surf_pocket1, protein and id [4851,4896,5148,5144,5146,6950,6940,4895,5140,5837,5405,5840,6039,6959,5980,5987,6958,6953,6956,4856,4854,6771,4853,4809,5974,5921,4852,6772,4857,4810,4838,4855,4797,4795,5889,5118,5911,5902,4796,5909,5910,5907,5116,5117,5954,5924,5973,5941,5926,5877,6941,5878,5612,5454,5455,5613] 
set surface_color,  pcol1, surf_pocket1 
set_color pcol2 = [0.278,0.278,0.702]
select surf_pocket2, protein and id [1471,1470,2524,1205,2530,1046,1047,2534,998,739,741,740,2533,1502,1500,711,1482,2551,2364,1517,2365,2366,1573,1514,733,489,2549,447,2545,449,444,450,488,445,448,737,388,390,1504,1548,1566,1567,1576,1580,389,403,402,431] 
set surface_color,  pcol2, surf_pocket2 
set_color pcol3 = [0.576,0.361,0.902]
select surf_pocket3, protein and id [1203,1098,1044,1233,2186,1100,1102,2094,1205,1185,2521,2225,1188,2522,2530,2507,2523,2219,1045,1029,2202,1030,1031,1046,995,2221,1047,2537,2534,998,2533] 
set surface_color,  pcol3, surf_pocket3 
set_color pcol4 = [0.616,0.278,0.702]
select surf_pocket4, protein and id [6628,6632,6626,6609,6590,6501,6940,5405,5402,5436,5438,6928,6942,6937,6929,6941,6944,5612,5455,5509,5507,5453,5437,6593,6500,6914,5595,5610] 
set surface_color,  pcol4, surf_pocket4 
set_color pcol5 = [0.902,0.361,0.792]
select surf_pocket5, protein and id [935,775,997,741,740,917,916,929,919,918,874,872,871,930,876,2545,443,462,468,758,875,2630,796,795,2563,2565,2603] 
set surface_color,  pcol5, surf_pocket5 
set_color pcol6 = [0.702,0.278,0.447]
select surf_pocket6, protein and id [4316,1825,1889,1885,1886,1827,1870,1828,1891,3908,3856,3857,3841,3337,1946,3350,3276,3351,3907,3354,3836,3353,1872] 
set surface_color,  pcol6, surf_pocket6 
set_color pcol7 = [0.902,0.361,0.361]
select surf_pocket7, protein and id [6279,7761,7744,7683,7757,7758,8723,7687,8314,6275,6276,6353,6277,6235,6232,8264,8243,6234,8263,6296,6292,6293,8315,6298] 
set surface_color,  pcol7, surf_pocket7 
set_color pcol8 = [0.702,0.447,0.278]
select surf_pocket8, protein and id [7831,7830,6092,7843,7311,7104,6725,7310,6020,7056,6723,6019,7730] 
set surface_color,  pcol8, surf_pocket8 
set_color pcol9 = [0.902,0.792,0.361]
select surf_pocket9, protein and id [2922,3036,2972,2826,2827,2824,2758,2822,2987,2821] 
set surface_color,  pcol9, surf_pocket9 




deselect

orient
