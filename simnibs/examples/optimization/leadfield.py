''' Example of a SimNIBS tDCS leadfield in Python
    Run with:

    simnibs_python leadfield.py

    Copyright (C) 2019 Guilherme B Saturnino
    
    place script in the “ernie” folder of the example dataset
'''
from simnibs import sim_struct, run_simnibs

tdcs_lf = sim_struct.TDCSLEADFIELD()
# head mesh
tdcs_lf.fnamehead = 'ernie.msh'
# output directory
tdcs_lf.pathfem = 'leadfield'


# Uncoment to use the pardiso solver
#tdcs_lf.solver_options = 'pardiso'
# This solver is faster than the default. However, it requires much more
# memory (~12 GB)


run_simnibs(tdcs_lf)
