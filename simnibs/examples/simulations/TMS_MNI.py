import simnibs
from simnibs import sim_struct

### General Infoarmation
S = sim_struct.SESSION()
S.fnamehead = 'ernie.msh'  # head mesh
S.pathfem = 'tms_hand'  # Directory for the simulation


## Define the TMS simulation
tms = S.add_tmslist()
tms.fnamecoil = 'Magstim_70mm_Fig8.nii.gz'  # Choose a coil from the ccd-files folder

# Define the coil position
pos = tms.add_position()
# Place coil over the hand knob
# Here, the hand knob is defined in MNI coordinates (https://www.ncbi.nlm.nih.gov/pmc/articles/PMC2034289/)
# And transformed to subject coordinates
# We can use positions in the cortex. SimNIBS will automatically project them to the skin surface
# and add the specified distance
pos.centre = simnibs.mni2subject_coords([-37, -21, 58], 'm2m_ernie') 
# Point the coil handle posteriorly, we just add 10 mm to the original M1 y coordinate
pos.pos_ydir = simnibs.mni2subject_coords([-37, -21-10, 58], 'm2m_ernie')
pos.distance = 4  #  4 mm distance from coil surface to head surface


# Run Simulation
simnibs.run_simnibs(S)
