
'''
    Optimization problem set-up and post-processing in SimNIBS
    This program is part of the SimNIBS package.
    Please check on www.simnibs.org how to cite our work in publications.
    Copyright (C) 2019 Guilherme B Saturnino

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.

'''


import copy
import csv
import re
import os
from collections import OrderedDict
import time
import glob
import functools
import logging

import numpy as np
import scipy.spatial
import h5py

from . import optimize_tms
from . import optimization_methods
from ..simulation import cond
from ..simulation import fem
from ..simulation.sim_struct import SESSION, TMSLIST, SimuList, save_matlab_sim_struct
from ..msh import mesh_io, gmsh_view
from ..utils.simnibs_logger import logger
from ..utils.file_finder import SubjectFiles
from ..utils.matlab_read import try_to_read_matlab_field, remove_None


class TMSoptimize():
    '''
    Attributes:
    ------------------------------
    fnamehead: str
        file name of mesh
    subpath (optional): str
        path to m2m folder
    pathfem (optional): str
        path where the leadfield should be saved
    fnamecoil: str
        name of coil file to be used
    cond (optional): list
        list of COND structures with conductivity information
    anisotropy_type (optional): property, can be 'scalar', 'vn' or 'mc'
        type of anisotropy for simulation
    target: (3x1) array
        Optimization target
    target_size (optional): float
        Size of target region, in mm. Defualt: 5
    tissues (optional): list of ints
        Tissues where the target is defined. Default: [2]
    centre(optional): (3x1) array or None
        Position in scalp to use as a reference for the search space. By dafault, will
        project the target to the scalp
    pos_ydir(optional): (3x1) array or None
        Reference position for the coil Y axis, with respect to the target (or the pos
        variable, if it is defined). If left empty, will search positions in a 360 degrees radius. Default: None
    distance (optional): float
        Distance from coil to scalp. Default: 4
    didt (optional): float
        Coil dI/dt value. Default: 1e6
    search_radius (optional): float
        Radius of area where to search for coil positions, in mm. Default: 20
    spatial_resolution (optional): float
        Spatial resolution of search area, in mm. Default: 5
    search_angle (optional): float
        Range of angles to use in search, in degrees. Default: 360
    angle_resolution (optional): float
        Resolution to use for the angles, in degrees. Default: 20
    open_in_gmsh(optional): bool
        Wether to open the results in gmsh. Default: False
    solver_options (optional): str
        Options for the FEM solver. Default: CG+AMG
    '''
    def __init__(self, matlab_struct=None):
        # : Date when the session was initiated
        self.date = time.strftime("%Y-%m-%d %H:%M:%S")
        self.time_str = time.strftime("%Y%m%d-%H%M%S")
        # Input stuff
        self.fnamehead = None
        self.subpath = None
        self.pathfem = 'tms_optimization/'
        self.fname_tensor = None
        self.mesh = None
        # Name of coil file
        self.fnamecoil = None
        # Conductivity stuff
        self.cond = cond.standard_cond()
        self.anisotropy_type = 'scalar'
        self.aniso_maxratio = 10
        self.aniso_maxcond = 2
        self.name = ''  # This is here only for leagacy reasons, it doesnt do anything
        # Optimization stuff
        self.target = None
        self.tissues = [2]
        self.target_size = 5
        self.centre = []
        self.pos_ydir = []
        self.distance = 4.
        self.didt = 1e6
        self.search_radius = 20
        self.spatial_resolution = 5
        self.search_angle = 360
        self.angle_resolution = 30

        self.open_in_gmsh = True
        self.solver_options = ''

        self._log_handlers = []
        if matlab_struct:
            self.read_mat_struct(matlab_struct)

    @property
    def fn_tensor_nifti(self):
        return self.fname_tensor

    @fn_tensor_nifti.setter
    def fn_tensor_nifti(self, value):
        self.fname_tensor = value

    @property
    def type(self):
        return self.__class__.__name__

    def _set_logger(self):
        SESSION._set_logger(self, summary=False)

    def _finish_logger(self):
        SESSION._finish_logger(self)

    def _prepare(self):
        """Prepares Leadfield for simulations
        relative paths are made absolute,
        empty fields are set to default values,
        check if required fields exist
        """
        self.fnamehead = os.path.abspath(os.path.expanduser(self.fnamehead))
        if not os.path.isfile(self.fnamehead):
            raise IOError('Cannot locate head mesh file: %s' % self.fnamehead)

        sub_files = SubjectFiles(self.fnamehead, self.subpath)
        self.fnamehead = sub_files.fnamehead
        self.subpath = sub_files.subpath

        if not os.path.isdir(self.subpath):
            logger.warning('Cannot locate subjects m2m folder')
            logger.warning('some postprocessing options might fail')
            self.subpath = None

        if not self.fname_tensor:
            self.fname_tensor = sub_files.tensor_file

        logger.info('Head Mesh: {0}'.format(self.fnamehead))
        logger.info('Subject Path: {0}'.format(self.subpath))
        self.pathfem = os.path.abspath(os.path.expanduser(self.pathfem))
        logger.info('Simulation Folder: {0}'.format(self.pathfem))

        self.mesh = mesh_io.read_msh(self.fnamehead)
        TMSLIST.resolve_fnamecoil(self)
        if self.target is None or len(self.target) != 3:
            raise ValueError('Target for optimization not defined')
        assert self.search_radius > 0
        assert self.spatial_resolution > 0
        assert self.angle_resolution > 0
        # fix a few variables
        if len(self.pos_ydir) == 0:
            self.pos_ydir = None
        if len(self.centre) == 0:
            self.centre = np.copy(self.target)

    def sim_struct2mat(self):
        mat = SimuList.cond_mat_struct(self)
        mat['type'] = self.type
        mat['date'] = remove_None(self.date)
        mat['subpath'] = remove_None(self.subpath)
        mat['fnamehead'] = remove_None(self.fnamehead)
        mat['pathfem'] = remove_None(self.pathfem)
        mat['fname_tensor'] = remove_None(self.fname_tensor)
        mat['tissues'] = remove_None(self.tissues)

        mat['target'] = remove_None(self.target)
        mat['target_size'] = remove_None(self.target_size)
        mat['centre'] = remove_None(self.centre)
        mat['pos_ydir'] = remove_None(self.pos_ydir)
        mat['distance'] = remove_None(self.distance)
        mat['didt'] = remove_None(self.didt)
        mat['search_radius'] = remove_None(self.search_radius)
        mat['spatial_resolution'] = remove_None(self.spatial_resolution)
        mat['search_angle'] = remove_None(self.search_angle)
        mat['angle_resolution'] = remove_None(self.angle_resolution)
        mat['open_in_gmsh'] = remove_None(self.open_in_gmsh)
        mat['solver_options'] = remove_None(self.solver_options)

        return mat

    @classmethod
    def read_mat_struct(self, mat):
        """ Reads form matlab structure
        Parameters
        ------------------
        mat: scipy.io.loadmat
            Loaded matlab structure
        """
        self = self()
        SimuList.read_cond_mat_struct(self, mat)
        self.date = try_to_read_matlab_field(
            mat, 'date', str, self.date
        )
        self.subpath = try_to_read_matlab_field(
            mat, 'subpath', str, self.subpath
        )
        self.fnamehead = try_to_read_matlab_field(
            mat, 'fnamehead', str, self.fnamehead
        )
        self.pathfem = try_to_read_matlab_field(
            mat, 'pathfem', str, self.pathfem
        )
        self.fnamecoil = try_to_read_matlab_field(
            mat, 'fnamecoil', str, self.fnamecoil
        )
        self.fname_tensor = try_to_read_matlab_field(
            mat, 'fname_tensor', str, self.fname_tensor
        )
        self.target = try_to_read_matlab_field(
            mat, 'target', list, self.target
        )
        self.target_size = try_to_read_matlab_field(
            mat, 'target_size', float, self.target_size
        )
        self.centre = try_to_read_matlab_field(
            mat, 'centre', list, self.centre
        )
        self.centre = try_to_read_matlab_field(
            mat, 'center', list, self.centre
        )
        self.pos_ydir = try_to_read_matlab_field(
            mat, 'pos_ydir', list, self.pos_ydir
        )
        self.distance = try_to_read_matlab_field(
            mat, 'distance', float, self.distance
        )
        self.didt = try_to_read_matlab_field(
            mat, 'didt', float, self.didt
        )
        self.search_radius = try_to_read_matlab_field(
            mat, 'search_radius', float, self.search_radius
        )
        self.spatial_resolution = try_to_read_matlab_field(
            mat, 'spatial_resolution', float, self.spatial_resolution
        )
        self.search_angle = try_to_read_matlab_field(
            mat, 'search_angle', float, self.search_angle
        )
        self.angle_resolution = try_to_read_matlab_field(
            mat, 'angle_resolution', float, self.angle_resolution
        )
        self.open_in_gmsh = try_to_read_matlab_field(
            mat, 'open_in_gmsh', bool, self.open_in_gmsh
        )
        self.solver_options = try_to_read_matlab_field(
            mat, 'solver_options', str, self.solver_options
        )
        return self

    def run(self, cpus=1, allow_multiple_runs=False, save_mat=True):
        ''' Runs the tms optimization

        Parameters
        -----------
        cpus: int (optional)
            Number of cpus to use. Not nescessaraly will use all cpus. Default: 1
        allow_multiple_runs: bool (optinal)
            Wether to allow multiple runs in one folder. Default: False
        save_mat: bool (optional)
            Whether to save the ".mat" file of this structure
        '''
        self._set_logger()
        dir_name = os.path.abspath(os.path.expanduser(self.pathfem))
        if os.path.isdir(dir_name):
            g = glob.glob(os.path.join(dir_name, 'simnibs_simulation*.mat'))
            if len(g) > 0 and not allow_multiple_runs:
                raise IOError(
                    '\nFound already existing simulation results in directory.'
                    '\nPlease run the simulation in a new directory or delete'
                    ' the simnibs_simulation*.mat files from the folder : {0}'.format(dir_name))
            logger.info(
                'Running simulations in the directory: {0}'.format(dir_name))
        else:
            logger.info('Running simulations on new directory: {0}'.dir_name)
            os.makedirs(dir_name)

        self._prepare()
        if save_mat:
            save_matlab_sim_struct(
                self,
                os.path.join(
                    dir_name,
                    'simnibs_simulation_{0}.mat'.format(self.time_str)))
        logger.info(str(self))
        pos_matrices = optimize_tms.get_opt_grid(
            self.mesh, self.centre,
            handle_direction_ref=self.pos_ydir,
            distance=self.distance, radius=self.search_radius,
            resolution_pos=self.spatial_resolution,
            resolution_angle=self.angle_resolution,
            angle_limits=[-self.search_angle/2, self.search_angle/2]
        )
        cond = SimuList.cond2elmdata(self)
        didt_list = [self.didt for i in pos_matrices]

        # Define target region
        target_region = optimize_tms.define_target_region(
            self.mesh,
            self.target,
            self.target_size,
            self.tissues
        )
        if len(target_region) == 0:
            raise ValueError('Did not find any elements within the defined target region')

        logger.info(f'Searching {len(pos_matrices)} positions')
        # write mesh with target and get file with grid
        m = self.mesh
        fn_target = os.path.join(self.pathfem, 'target.msh')
        target = np.zeros(m.elm.nr)
        target[target_region - 1] = 1
        m.add_element_field(target, 'Target')
        m.write(fn_target)
        v = m.view(
            visible_tags=self.tissues,
            visible_fields='all'
        )
        v.View[0].CustomMax = 1
        v.View[0].CustomMin = 0
        m.elmdata = []
        # Write out the grid
        optimize_tms.plot_matsimnibs_list(
            pos_matrices,
            np.ones(len(pos_matrices)),
            "Grid",
            os.path.join(self.pathfem, 'coil_positions.geo')
        )
        v.add_merge(os.path.join(self.pathfem, 'coil_positions.geo'))
        v.add_view(
            CustomMax=1, CustomMin=1,
            VectorType=4, CenterGlyphs=0,
            Visible=1, ColormapNumber=0
        )
        v.write_opt(fn_target)
        if self.open_in_gmsh:
            mesh_io.open_in_gmsh(fn_target, True)

        # Run simulations
        fn_hdf5 = os.path.join(self.pathfem, self._name_hdf5())
        if os.path.isfile(fn_hdf5):
            os.remove(fn_hdf5)
        dataset = 'tms_optimization/E_norm'
        volumes = self.mesh.elements_volumes_and_areas()[target_region]
        # Define postporcessing to calculate average field norm
        def postprocessing(E, target_region, volumes):
            return np.average(
                np.linalg.norm(E[target_region - 1], axis=1),
                weights=volumes
            )
        postpro = functools.partial(
            postprocessing,
            target_region=target_region,
            volumes=volumes
        )
        fem.tms_many_simulations(
            self.mesh, cond,
            self.fnamecoil,
            pos_matrices, didt_list,
            fn_hdf5, dataset,
            post_pro=postpro,
            solver_options=self.solver_options,
            n_workers=cpus
        )
        # Read the field norms
        with h5py.File(fn_hdf5, 'a') as f:
            normE = f[dataset][:]
        os.remove(fn_hdf5)
        # Update the .geo file with the normE value
        optimize_tms.plot_matsimnibs_list(
            pos_matrices,
            normE,
            "normE at target",
            os.path.join(self.pathfem, 'coil_positions.geo')
        )
        v.add_merge(os.path.join(self.pathfem, 'coil_positions.geo'))
        v.add_view(VectorType=4, CenterGlyphs=0)
        v.write_opt(fn_target)

        # Run one extra simulation with the best position
        logger.info('Re-running best position')
        fn_out = fn_hdf5[:-5] + '.msh'
        fn_geo = fn_hdf5[:-5] + '_coil_pos.geo'
        fem.tms_coil(
            self.mesh, cond, self.fnamecoil, 'eEjJ',
            [pos_matrices[np.argmax(normE)]],
            [self.didt],
            [fn_out],
            [fn_geo],
        )
        # Write the target to the final simulation file
        m = mesh_io.read_msh(fn_out)
        m.add_element_field(target, 'Target')
        m.write(fn_out)
        v = m.view(
            visible_tags=self.tissues,
            visible_fields=['normE']
        )
        v.View[-1].CustomMax = 1
        v.View[-1].CustomMin = 0
        v.add_merge(fn_geo)
        v.add_merge(os.path.join(self.pathfem, 'coil_positions.geo'))
        v.add_view(VectorType=4, CenterGlyphs=0)
        v.write_opt(fn_out)
        if self.open_in_gmsh:
            mesh_io.open_in_gmsh(fn_out, True)
        # Another output with coil positions, possibly also transformed

    def _name_hdf5(self):
        try:
            subid = os.path.splitext(os.path.basename(self.fnamehead))[0]
            subid += '_'
        except TypeError:
            subid = ''
        try:
            coil_name = os.path.splitext(os.path.basename(self.fnamecoil))[0]
            if coil_name.endswith('.nii'):
                coil_name = coil_name[:-4] + '_nii'
            coil_name = '_' + coil_name
        except TypeError:
            coil_name = ''
        name = '{0}TMS_optimize{1}.hdf5'.format(subid, coil_name)
        return name

    def __str__(self):
        string = 'Subject Folder: %s\n' % self.subpath
        string += 'Mesh file name: %s\n' % self.fnamehead
        string += 'Coil file: %s\n' % self.fnamecoil
        string += 'Target: %s\n' % self.target
        string += 'Centre position: %s\n' % self.centre
        string += 'Reference y: %s\n' % self.pos_ydir
        string += 'Coil distance: %s\n' % self.distance
        string += 'Search radius: %s\n' % self.search_radius
        string += 'Spatial resolution: %s\n' % self.spatial_resolution
        string += 'Search angle: %s\n' % self.search_angle
        string += 'Angle resolution: %s' % self.angle_resolution
        return string


class TDCSoptimize():
    ''' Defines a tdcs optimization problem

    Parameters
    --------------
    leadfield_hdf: str (optional)
        Name of file with leadfield
    max_total_current: float (optional)
        Maximum current across all electrodes (in Amperes). Default: 2e-3
    max_individual_current: float (optional)
        Maximum current for any single electrode (in Amperes). Default: 1e-3
    max_active_electrodes: int (optional)
        Maximum number of active electrodes. Default: no maximum
    name: str (optional)
        Name of optimization problem. Default: optimization
    target: list of TDCStarget objects (optional)
        Targets for the optimization. Default: no target
    avoid: list of TDCSavoid objects
        list of TDCSavoid objects defining regions to avoid


    Attributes
    --------------
    leadfield_hdf: str
        Name of file with leadfield
    max_total_current: float (optional)
        Maximum current across all electrodes (in Amperes). Default: 2e-3
    max_individual_current: float
        Maximum current for any single electrode (in Amperes). Default: 1e-3
    max_active_electrodes: int
        Maximum number of active electrodes. Default: no maximum

    ledfield_path: str
        Path to the leadfield in the hdf5 file. Default: '/mesh_leadfield/leadfields/tdcs_leadfield'
    mesh_path: str
        Path to the mesh in the hdf5 file. Default: '/mesh_leadfield/'

    The two above are used to define:

    mesh: simnibs.msh.mesh_io.Msh
        Mesh with problem geometry

    leadfield: np.ndarray
        Leadfield matrix (N_elec -1 x M x 3) where M is either the number of nodes or the
        number of elements in the mesh. We assume that there is a reference electrode

    Alternatively, you can set the three attributes above and not leadfield_path,
    mesh_path and leadfield_hdf

    lf_type: None, 'node' or 'element'
        Type of leadfield.

    name: str
        Name for the optimization problem. Defaults tp 'optimization'

    target: list of TDCStarget objects
        list of TDCStarget objects defining the targets of the optimization

    avoid: list of TDCSavoid objects (optional)
        list of TDCSavoid objects defining regions to avoid

    open_in_gmsh: bool (optional)
        Whether to open the result in Gmsh after the calculations. Default: False

    Warning
    -----------
    Changing leadfield_hdf, leadfield_path and mesh_path after constructing the class
    can cause unexpected behaviour
    '''
    def __init__(self, leadfield_hdf=None,
                 max_total_current=2e-3,
                 max_individual_current=1e-3,
                 max_active_electrodes=None,
                 name='optimization/tdcs',
                 target=None,
                 avoid=None,
                 open_in_gmsh=True):
        self.leadfield_hdf = leadfield_hdf
        self.max_total_current = max_total_current
        self.max_individual_current = max_individual_current
        self.max_active_electrodes = max_active_electrodes
        self.leadfield_path = '/mesh_leadfield/leadfields/tdcs_leadfield'
        self.mesh_path = '/mesh_leadfield/'
        self.open_in_gmsh = open_in_gmsh
        self._mesh = None
        self._leadfield = None
        self._field_name = None
        self._field_units = None
        self.name = name
        # I can't put [] in the arguments for weird reasons (it gets the previous value)
        if target is None:
            self.target = []
        else:
            self.target = target
        if avoid is None:
            self.avoid = []
        else:
            self.avoid = avoid


    @property
    def lf_type(self):
        if self.mesh is None or self.leadfield is None:
            return None
        if self.leadfield.shape[1] == self.mesh.nodes.nr:
            return 'node'
        elif self.leadfield.shape[1] == self.mesh.elm.nr:
            return 'element'
        else:
            raise ValueError('Could not find if the leadfield is node- or '
                              'element-based')

    @property
    def leadfield(self):
        ''' Reads the leadfield from the HDF5 file'''
        if self._leadfield is None and self.leadfield_hdf is not None:
            with h5py.File(self.leadfield_hdf, 'r') as f:
                self.leadfield = f[self.leadfield_path][:]

        return self._leadfield

    @leadfield.setter
    def leadfield(self, leadfield):
        if leadfield is not None:
            assert leadfield.ndim == 3, 'leadfield should be 3 dimensional'
            assert leadfield.shape[2] == 3, 'Size of last dimension of leadfield should be 3'
        self._leadfield = leadfield

    @property
    def mesh(self):
        if self._mesh is None and self.leadfield_hdf is not None:
            self.mesh = mesh_io.Msh.read_hdf5(self.leadfield_hdf, self.mesh_path)

        return self._mesh

    @mesh.setter
    def mesh(self, mesh):
        elm_type = np.unique(mesh.elm.elm_type)
        if len(elm_type) > 1:
            raise ValueError('Mesh has both tetrahedra and triangles')
        else:
            self._mesh = mesh

    @property
    def field_name(self):
        if self.leadfield_hdf is not None and self._field_name is None:
            try:
                with h5py.File(self.leadfield_hdf, 'r') as f:
                    self.field_name = f[self.leadfield_path].attrs['field']
            except:
                return 'Field'

        if self._field_name is None:
            return 'Field'
        else:
            return self._field_name


    @field_name.setter
    def field_name(self, field_name):
        self._field_name = field_name

    @property
    def field_units(self):
        if self.leadfield_hdf is not None and self._field_units is None:
            try:
                with h5py.File(self.leadfield_hdf, 'r') as f:
                    self.field_units = f[self.leadfield_path].attrs['field']
            except:
                return 'Au'

        if self._field_units is None:
            return 'Au'
        else:
            return self._field_units

    @field_units.setter
    def field_units(self, field_units):
        self._field_units = field_units


    def to_mat(self):
        """ Makes a dictionary for saving a matlab structure with scipy.io.savemat()

        Returns
        --------------------
        dict
            Dictionaty for usage with scipy.io.savemat
        """
        mat = {}
        mat['type'] = 'TDCSoptimize'
        mat['leadfield_hdf'] = remove_None(self.leadfield_hdf)
        mat['max_total_current'] = remove_None(self.max_total_current)
        mat['max_individual_current'] = remove_None(self.max_individual_current)
        mat['max_active_electrodes'] = remove_None(self.max_active_electrodes)
        mat['open_in_gmsh'] = remove_None(self.open_in_gmsh)
        mat['name'] = remove_None(self.name)
        mat['target'] = _save_TDCStarget_mat(self.target)
        mat['avoid'] = _save_TDCStarget_mat(self.avoid)
        return mat

    @classmethod
    def read_mat_struct(cls, mat):
        '''Reads a .mat structure

        Parameters
        -----------
        mat: dict
            Dictionary from scipy.io.loadmat

        Returns
        ----------
        p: TDCSoptimize
            TDCSoptimize structure
        '''
        t = cls()
        leadfield_hdf = try_to_read_matlab_field(
            mat, 'leadfield_hdf', str, t.leadfield_hdf)
        max_total_current = try_to_read_matlab_field(
            mat, 'max_total_current', float, t.max_total_current)
        max_individual_current = try_to_read_matlab_field(
            mat, 'max_individual_current', float, t.max_individual_current)
        max_active_electrodes = try_to_read_matlab_field(
            mat, 'max_active_electrodes', int, t.max_active_electrodes)
        open_in_gmsh = try_to_read_matlab_field(
            mat, 'open_in_gmsh', bool, t.open_in_gmsh)
        name = try_to_read_matlab_field(
            mat, 'name', str, t.name)
        target = []
        if len(mat['target']) > 0:
            for t in mat['target'][0]:
                target_struct = TDCStarget.read_mat_struct(t)
                if target_struct is not None:
                    target.append(target_struct)
        if len(target) == 0:
            target = None

        avoid = []
        if len(mat['avoid']) > 0:
            avoid = []
            for t in mat['avoid'][0]:
                avoid_struct = TDCSavoid.read_mat_struct(t)
                if avoid_struct is not None:
                    avoid.append(avoid_struct)
        if len(avoid) == 0:
            avoid = None

        return cls(leadfield_hdf, max_total_current,
                   max_individual_current, max_active_electrodes,
                   name, target, avoid, open_in_gmsh)

    def calc_energy_matrix(self):
        '''Calculates the enery matrix for optimization

        Returns:
        ----------
        Q: np.ndarray
            Energy matrix (N_elec x N_elec)
        '''
        assert self.leadfield is not None, 'Leadfield not defined'
        assert self.mesh is not None, 'Mesh not defined'
        if self.lf_type == 'node':
            weights = self.mesh.nodes_volumes_or_areas().value
        elif self.lf_type == 'element':
            weights = self.mesh.elements_volumes_and_areas().value
        else:
            raise ValueError('Cant calculate energy matrix: mesh or leadfield not set')

        weights *= self._get_avoid_field()
        return optimization_methods.energy_matrix(self.leadfield, weights)

    def _get_avoid_field(self):
        fields = []
        for a in self.avoid:
            a.mesh = self.mesh
            a.lf_type = self.lf_type
            fields.append(a.avoid_field())

        if len(fields) > 0:
            total_field = np.ones_like(fields[0])
            for f in fields:
                total_field *= f
            return total_field

        else:
            return 1.


    def add_target(self, target=None):
        ''' Adds a target to the current tDCS optimization

        Parameters:
        ------------
        target: TDCStarget (optional)
            TDCStarget structure to be added. Default: empty TDCStarget

        Returns:
        -----------
        target: TDCStarget
            TDCStarget added to the structure
        '''
        if target is None:
            target = TDCStarget(mesh=self.mesh, lf_type=self.lf_type)
        self.target.append(target)
        return target


    def add_avoid(self, avoid=None):
        ''' Adds an avoid structure to the current tDCS optimization

        Parameters:
        ------------
        target: TDCStarget (optional)
            TDCStarget structure to be added. Default: empty TDCStarget

        Returns:
        -----------
        target: TDCStarget
            TDCStarget added to the structure
        '''
        if avoid is None:
            avoid = TDCSavoid(mesh=self.mesh, lf_type=self.lf_type)
        self.avoid.append(avoid)
        return avoid


    def _assign_mesh_lf_type_to_target(self):
        for t in self.target:
            if t.mesh is None: t.mesh = self.mesh
            if t.lf_type is None: t.lf_type = self.lf_type
        for a in self.avoid:
            if a.mesh is None: a.mesh = self.a
            if a.lf_type is None: a.lf_type = self.lf_type

    def optimize(self, fn_out_mesh=None, fn_out_csv=None):
        ''' Runs the optimization problem

        Parameters
        -------------
        fn_out_mesh: str
            If set, will write out the electric field and currents to the mesh

        fn_out_mesh: str
            If set, will write out the currents and electrode names to a CSV file


        Returns
        ------------
        currents: N_elec x 1 ndarray
            Optimized currents. The first value is the current in the reference electrode
        '''
        assert len(self.target) > 0, 'No target defined'
        assert self.leadfield is not None, 'Leadfield not defined'
        assert self.mesh is not None, 'Mesh not defined'
        if self.max_active_electrodes is not None:
            assert self.max_active_electrodes > 1, \
                    'The maximum number of active electrodes should be at least 2'
        if self.max_total_current is not None:
            assert self.max_total_current > 0,\
                    'max_total_current should be positive'
        if self.max_individual_current is not None:
            assert self.max_individual_current > 0,\
                    'max_individual_current should be positive'
        Q_mat = self.calc_energy_matrix()
        l_mat = np.zeros([len(self.target), Q_mat.shape[1]])
        target_intensities = np.zeros(len(self.target))
        max_angle = None
        Q_target = None
        self._assign_mesh_lf_type_to_target()
        for i, t in enumerate(self.target):
            target_intensities[i] = t.intensity
            max_angle = t.max_angle
            t_mat, Q_target = t.calc_target_matrices(self.leadfield)
            l_mat[i] = t_mat
            if t.intensity < 0:
                target_intensities[i] *= -1.
                l_mat[i] *= -1.

        if max_angle is not None and len(self.target) > 1:
            raise ValueError("Can't apply angle constraints with multiple target (yet)")

        if max_angle is not None:
            assert max_angle > 0,\
                    'max_angle should be positive'

        currents = optimization_methods.optimize_focality(
            l_mat, Q_mat, target_intensities,
            max_total_current=self.max_total_current,
            max_el_current=self.max_individual_current,
            max_active_electrodes=self.max_active_electrodes,
            max_angle=max_angle,
            Qin=Q_target,
            log_level=10
        )

        logger.log(25, '\n' + self.summary(currents))

        if fn_out_mesh is not None:
            fn_out_mesh = os.path.abspath(fn_out_mesh)
            m = self.field_mesh(currents)
            m.write(fn_out_mesh)
            v = m.view()
            ## Configure view
            v.Mesh.SurfaceFaces = 0
            v.View[0].Visible = 1
            # Change vector type for target field
            for i, t in enumerate(self.target):
                v.View[2 + i].VectorType = 4
                v.View[2 + i].ArrowSizeMax = 60
                v.View[2 + i].Visible = 1
            # Electrode geo file
            el_geo_fn = os.path.splitext(fn_out_mesh)[0] + '_el_currents.geo'
            self.electrode_geo(el_geo_fn, currents)
            v.add_merge(el_geo_fn)
            max_c = np.max(np.abs(currents))
            v.add_view(Visible=1, RangeType=2,
                       ColorTable=gmsh_view._coolwarm_cm(),
                       CustomMax=max_c, CustomMin=-max_c)
            v.write_opt(fn_out_mesh)
            if self.open_in_gmsh:
                mesh_io.open_in_gmsh(fn_out_mesh, True)


        if fn_out_csv is not None:
            self.write_currents_csv(currents, fn_out_csv)

        return currents

    def field(self, currents):
        ''' Outputs the electric fields caused by the current combination

        f[dset].attrs['d_type'] = 'node_data'
        Parameters
        -----------
        currents: N_elec x 1 ndarray
            Currents going through each electrode, in A. Usually from the optimize
            method. The sum should be approximately zero

        Returns
        ----------
        E: simnibs.mesh.NodeData or simnibs.mesh.ElementData
            NodeData or ElementData with the field caused by the currents
        '''

        assert np.isclose(np.sum(currents), 0, atol=1e-5), 'Currents should sum to zero'
        E = np.einsum('ijk,i->jk', self.leadfield, currents[1:])

        if self.lf_type == 'node':
            E = mesh_io.NodeData(E, self.field_name, mesh=self.mesh)

        if self.lf_type == 'element':
            E = mesh_io.ElementData(E, self.field_name, mesh=self.mesh)

        return E

    def electrode_geo(self, fn_out, currents=None, mesh_elec=None, elec_tags=None):
        ''' Creates a mesh with the electrodes and their currents

        Parameters
        ------------
        currents: N_elec x 1 ndarray (optional)
            Electric current values per electrode. Default: do not print currents
        mesh_elec: simnibs.mesh.Msh (optional)
            Mesh with the electrodes. Default: look for a mesh called mesh_electrodes in
            self.leadfield_hdf
        elec_tags: N_elec x 1 ndarray of ints (optional)
            Tags of the electrodes corresponding to each leadfield column. The first is
            the reference electrode. Default: lood at the attribute electrode_tags in the
            leadfield dataset
        '''
        if mesh_elec is None:
            if self.leadfield_hdf is not None:
                try:
                    mesh_elec = mesh_io.Msh.read_hdf5(self.leadfield_hdf, 'mesh_electrodes')
                except KeyError:
                    raise IOError('Could not find mesh_electrodes in '
                                  '{0}'.format(self.leadfield_hdf))
            else:
                raise ValueError('Please define a mesh with the electrodes')

        if elec_tags is None:
            if self.leadfield_hdf is not None:
                with h5py.File(self.leadfield_hdf, 'r') as f:
                    elec_tags = f[self.leadfield_path].attrs['electrode_tags']
            else:
                raise ValueError('Please define the electrode tags')

        if currents is None:
            currents = np.ones(len(elec_tags))
        assert len(elec_tags) == len(currents), 'Define one current per electrode'

        triangles = []
        values = []
        elec_pos = []
        bar = mesh_elec.elements_baricenters()
        norms = mesh_elec.triangle_normals()
        for t, c in zip(elec_tags, currents):
            triangles.append(mesh_elec.elm[mesh_elec.elm.tag1 == t, :3])
            values.append(c * np.ones(len(triangles[-1])))
            avg_norm = np.average(norms[mesh_elec.elm.tag1 == t], axis=0)
            pos = np.average(bar[mesh_elec.elm.tag1 == t], axis=0)
            pos += avg_norm * 4
            elec_pos.append(pos)

        triangles = np.concatenate(triangles, axis=0)
        values = np.concatenate(values, axis=0)
        elec_pos = np.vstack(elec_pos)
        mesh_io.write_geo_triangles(
            triangles - 1, mesh_elec.nodes.node_coord,
            fn_out, values, 'electrode_currents')

        if self.leadfield_hdf is not None:
            with h5py.File(self.leadfield_hdf, 'r') as f:
                try:
                    elec_names = f[self.leadfield_path].attrs['electrode_names']
                    elec_names = [n.decode() for n in elec_names]
                except KeyError:
                    elec_names = None

            if elec_names is not None:
                mesh_io.write_geo_text(
                    elec_pos, elec_names,
                    fn_out, name="electrode_names", mode='ba')


    def electrode_geo_old(self, fn_out, currents=None, elec_pos=None, elec_names=None):
        ''' Creates a .geo file with electrodes currents/positions and/or names

        Parameters
        -------------
        fn_out: str
            Output name of .geo file
        ALL THE BELOW ARE AUTOMATICALLY FILLED BY THE LEADFIELD
        currents: Nx1 ndarray (optional)
            Currents through the electrodes. If set, will create spheres with the
            currents in the .geo file
        elec_pos: Nx3 ndarray (optional)
            Electrode positions in the subject space. If not set will look into the
            leadfield
        elec_names: list with names of the electrdes (optional)
            Names of the electrodes
        '''
        if self.leadfield_hdf is not None:
            if elec_pos is None:
                with h5py.File(self.leadfield_hdf, 'r') as f:
                    elec_pos = f[self.leadfield_path].attrs['electrode_pos']

            if elec_names is None:
                with h5py.File(self.leadfield_hdf, 'r') as f:
                    try:
                        elec_names = f[self.leadfield_path].attrs['electrode_names']
                        elec_names = [n.decode() for n in elec_names]
                    except KeyError:
                        pass

        if elec_pos is None:
            raise ValueError('please define either elec_pos or a valid leadfield')

        if os.path.isfile(fn_out):
            os.remove(fn_out)

        if currents is not None:
            mesh_io.write_geo_spheres(
                elec_pos, fn_out,
                values=currents, name="Electrode Currents", mode='bw')



    def field_mesh(self, currents):
        ''' Creates showing the targets and the field
        Parameters
        -------------
        currents: N_elec x 1 ndarray
            Currents going through each electrode, in A. Usually from the optimize
            method. The sum should be approximately zero

        Returns
        ---------
        results: simnibs.msh.mesh_io.Msh
            Mesh file
        ''' 
        target_fields = [t.as_field('target_{0}'.format(i+1)) for i, t in
                         enumerate(self.target)]
        weight_fields = [t.as_field('avoid_{0}'.format(i+1)) for i, t in
                         enumerate(self.avoid)]
        e_field = self.field(currents)
        e_norm_field = e_field.norm()
        m = copy.deepcopy(self.mesh)
        if self.lf_type == 'node':
            m.nodedata = [e_norm_field, e_field] + target_fields + weight_fields
        elif self.lf_type == 'element':
            m.elmdata = [e_norm_field, e_field] + target_fields + weight_fields
        return m


    def write_currents_csv(self, currents, fn_csv, electrode_names=None):
        ''' Writes the currents and the corresponding electrode names to a CSV file
        
        Parameters
        ------------
        currents: N_elec x 1 ndarray
            Array with electrode currents
        fn_csv: str
            Name of CSV file to write
        electrode_names: list of strings (optional)
            Name of electrodes. Default: will read from the electrode_names attribute in
            the leadfield dataset
        '''
        if electrode_names is None:
            if self.leadfield_hdf is not None:
                with h5py.File(self.leadfield_hdf, 'r') as f:
                    electrode_names = f[self.leadfield_path].attrs['electrode_names']
                    electrode_names = [n.decode() for n in electrode_names]
            else:
                raise ValueError('Please define the electrode names')

        assert len(electrode_names) == len(currents)
        with open(fn_csv, 'w', newline='') as f:
            writer = csv.writer(f)
            for n, c in zip(electrode_names, currents):
                writer.writerow([n, c])

    def run(self, cpus=1):
        ''' Interface to use with the run_simnibs function

        Parameters
        ---------------
        cpus: int (optional)
            Does not do anything, it is just here for the common interface with the
            simulation's run function
        '''
        if not self.name:
            if self.leadfield_hdf is not None:
                try:
                    name = re.search(r'(.+)_leadfield_', self.leadfield_hdf).group(1)
                except AttributeError:
                    name = 'optimization'
            else:
                name = 'optimization'
        else:
            name = self.name
        out_folder = os.path.dirname(name)
        os.makedirs(out_folder, exist_ok=True)

        # Set-up logger
        fh = logging.FileHandler(name + '.log', mode='w')
        formatter = logging.Formatter(
            '[ %(name)s - %(asctime)s - %(process)d ]%(levelname)s: %(message)s')
        fh.setFormatter(formatter)
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)

        fn_summary = name + '_summary.txt'
        fh_s = logging.FileHandler(fn_summary, mode='w')
        fh_s.setFormatter(logging.Formatter('%(message)s'))
        fh_s.setLevel(25)
        logger.addHandler(fh_s)

        fn_out_mesh = name + '.msh'
        fn_out_csv = name + '.csv'
        logger.info('Optimizing')
        logger.log(25, str(self))
        self.optimize(fn_out_mesh, fn_out_csv)
        logger.log(
            25,
            '\n=====================================\n'
            'SimNIBS finished running optimization\n'
            'Mesh file: {0}\n'
            'CSV file: {1}\n'
            'Summary file: {2}\n'
            '====================================='
            .format(fn_out_mesh, fn_out_csv, fn_summary))

        logger.removeHandler(fh)
        logger.removeHandler(fh_s)


    def __str__(self):
        s = 'Optimization set-up\n'
        s += '===========================\n'
        s += 'Leadfield file: {0}\n'.format(self.leadfield_hdf)
        s += 'Max. total current: {0} (A)\n'.format(self.max_total_current)
        s += 'Max. individual current: {0} (A)\n'.format(self.max_individual_current)
        s += 'Max. active electrodes: {0}\n'.format(self.max_active_electrodes)
        s += 'Name: {0}\n'.format(self.name)
        s += '----------------------\n'
        s += 'N targets: {0}\n'.format(len(self.target))
        s += '......................\n'.join(
            ['Target {0}:\n{1}'.format(i+1, str(t)) for i, t in
             enumerate(self.target)])
        s += '----------------------\n'
        s += 'N avoid: {0}\n'.format(len(self.avoid))
        s += '......................\n'.join(
            ['Avoid {0}:\n{1}'.format(i+1, str(t)) for i, t in
             enumerate(self.avoid)])
        return s


    def summary(self, currents):
        ''' Returns a string with a summary of the optimization

        Parameters
        ------------
        field: ElementData or NodeData
            Field of interest

        Returns
        ------------
        summary: str
            Summary of field
        '''
        s = 'Optimization Summary\n'
        s += '=============================\n'
        s += 'Total current: {0:.2e} (A)\n'.format(np.linalg.norm(currents, ord=1)/2)
        s += 'Maximum current: {0:.2e} (A)\n'.format(np.max(np.abs(currents)))
        s += 'Active electrodes: {0}\n'.format(int(np.linalg.norm(currents, ord=0)))
        field = self.field(currents)
        s += 'Field Summary\n'
        s += '----------------------------\n'
        s += 'Peak Value (99.9 percentile): {0:.2f} ({1})\n'.format(
            field.get_percentiles(99.9)[0], self.field_units)
        s += 'Mean field norm: {0:.2e} ({1})\n'.format(
            field.mean_field_norm(), self.field_units)
        if np.any(self.mesh.elm.elm_type==4):
            v_units = 'mm3'
        else:
            v_units = 'mm2'
        s += 'Focality: 50%: {0:.2e} 70%: {1:.2e} ({2})\n'.format(
            *field.get_focality(cuttofs=[50, 70], peak_percentile=99.9),
            v_units)
        for i, t in enumerate(self.target):
            s += 'Target {0}\n'.format(i + 1)
            s += '    Intensity specified:{0:.2f} achieved: {1:.2f} ({2})\n'.format(
                t.intensity, t.mean_intensity(field), self.field_units)
            if t.max_angle is not None:
                s += ('    Average angle across target: {0:.1f} '
                      '(max set to {1:.1f}) (degrees)\n'.format(
                      t.mean_angle(field), t.max_angle))
            else:
                s += '    Average angle across target: {0:.1f} (degrees)\n'.format(
                    t.mean_angle(field))

        for i, a in enumerate(self.avoid):
            s += 'Avoid {0}\n'.format(i + 1)
            s += '    Mean field norm in region: {0:.2e} ({1})\n'.format(
                a.mean_field_norm_in_region(field), self.field_units)

        return s

class TDCStarget:
    ''' Defines a target for TDCS optimization

    Attributes
    -------------
    positions: Nx3 ndarray
        List of target positions, in x, y, z coordinates and in the subject space. Will find the closes mesh points
    indexes: Nx1 ndarray of ints
        Indexes (1-based) of elements/nodes for optimization. Overwrites positions
    directions: Nx3 ndarray
        List of Electric field directions to be optimied for each mesh point or the
        string 'normal', Default: 'normal'
    intensity: float (optional)
        Target intensity of the electric field component in V/m. Default: 0.2
    max_angle: float (optional)
        Maximum angle between electric field and target direction, in degrees. Default:
        No maximum
    radius: float (optional)
        Radius of target. All the elements/nodes within the given radies of the indexes
        will be included.
    tissues: list or None (Optional)
        Tissues included in the target. Either a list of integer with tissue tags or None
        for all tissues. Default: None

    THE ONES BELOW SHOULD NOT BE FILLED BY THE USERS IN NORMAL CIRCUNTANCES:
    mesh: simnibs.msh.mesh_io.Msh (optional)
        Mesh where the target is defined. Set by the TDCSoptimize methods
    lf_type: 'node' or 'element'
        Where the electric field values are defined

    '''
    def __init__(self, positions=None, indexes=None, directions='normal',
                 intensity=0.2, max_angle=None, radius=2, tissues=None,
                 mesh=None, lf_type=None):
        self.lf_type = lf_type
        self.mesh = mesh
        self.radius = radius
        self.tissues = tissues
        self.positions = positions
        self.indexes = indexes
        self.directions = directions
        self.intensity = intensity
        self.max_angle = max_angle


    @classmethod
    def read_mat_struct(cls, mat):
        '''Reads a .mat structure
        
        Parameters
        -----------
        mat: dict
            Dictionary from scipy.io.loadmat

        Returns
        ----------
        t: TDCStarget
            TDCStarget structure
        '''
        t = cls()
        positions = try_to_read_matlab_field(mat, 'positions', list, t.positions)
        indexes = try_to_read_matlab_field(mat, 'indexes', list, t.indexes)
        directions = try_to_read_matlab_field(mat, 'directions', list, t.directions)
        try:
            directions[0]
        except IndexError:
            directions = 'normal'
        else:
            if isinstance(directions[0], str):
                directions = ''.join(directions)
            if isinstance(directions[0], bytes):
                directions = ''.join([d.decode() for d in directions])
        intensity = try_to_read_matlab_field(mat, 'intensity', float, t.intensity)
        max_angle = try_to_read_matlab_field(mat, 'max_angle', float, t.max_angle)
        radius = try_to_read_matlab_field(mat, 'radius', float, t.radius)
        tissues = try_to_read_matlab_field(mat, 'tissues', list, t.tissues)
        if positions is not None and len(positions) == 0:
            positions = None
        if indexes is not None and len(indexes) == 0:
            indexes = None
        if tissues is not None and len(tissues) == 0:
            tissues = None

        is_empty = True
        is_empty *= t.positions == positions
        is_empty *= t.indexes == indexes
        is_empty *= t.directions == directions
        is_empty *= t.intensity == intensity
        is_empty *= t.max_angle == max_angle
        is_empty *= t.tissues == tissues
        if is_empty:
            return None

        return cls(positions, indexes, directions, intensity, max_angle, radius, tissues)

    def calc_target_matrices(self, leadfield):
        ''' Calculates target-specific matrices

        Parameters
        -----------
        leadfield: N_elec-1 x M x 3 ndarray
            Leadfield to be used for calculations

        Returns
        ----------
        t_matrix: N_elec x 1 ndarray
            Array such that t_matrix.dot(x) is the mean electric field in the target
            region and directions
        Q_matrix: N_elec x N_elec
            Matrix that calculates the mean squared norm electric field in the target
            region
        '''
        if (self.positions is None) == (self.indexes is None): # negative XOR operation
            raise ValueError('Please set either positions or indexes')
        assert self.directions is not None, 'Please set directions'
        assert self.mesh is not None, 'Please set a mesh'
        assert self.lf_type is not None, 'Please set a lf_type'

        if self.lf_type == 'node':
            weights = self.mesh.nodes_volumes_or_areas().value
        elif self.lf_type == 'element':
            weights = self.mesh.elements_volumes_and_areas().value
        else:
            raise ValueError('Invalid lf_type: {0}, should be '
                             '"element" or "node"'.format(self.lf_type))

        # Use the radius and tissue information to find the final indexes for the
        # optimization
        indexes, mapping = _find_indexes(self.mesh, self.lf_type,
                                         positions=self.positions,
                                         indexes=self.indexes,
                                         tissues=self.tissues,
                                         radius=self.radius)

        directions = _find_directions(self.mesh, self.lf_type,
                                      self.directions, indexes,
                                      mapping)

        return optimization_methods.target_matrices(
            leadfield, indexes - 1, directions, weights)

    def as_field(self, name='target_field'):
        ''' Returns the target as an ElementData or NodeData field

        Parameters
        -----------
        name: str
            Name of the field. Default: 'target_field'
        Returns
        ---------
        target: ElementData or NodeData
            A vector field with a vector pointing in the given direction in the target
        '''
        if (self.positions is None) == (self.indexes is None): # negative XOR operation
            raise ValueError('Please set either positions or indexes')

        assert self.mesh is not None, 'Please set a mesh'

        if self.lf_type == 'node':
            field = np.zeros((self.mesh.nodes.nr, 3))
            field_type = mesh_io.NodeData
        elif self.lf_type == 'element':
            field = np.zeros((self.mesh.elm.nr, 3))
            field_type = mesh_io.ElementData
        else:
            raise ValueError("lf_type must be 'node' or 'element'."
                             " Got: {0} instead".format(self.lf_type))

        indexes, mapping = _find_indexes(self.mesh, self.lf_type,
                                         positions=self.positions,
                                         indexes=self.indexes,
                                         tissues=self.tissues,
                                         radius=self.radius)

        directions = _find_directions(self.mesh, self.lf_type,
                                      self.directions, indexes,
                                      mapping)

        field[indexes-1] = directions * self.intensity

        return field_type(field, name, mesh=self.mesh)

    def mean_intensity(self, field):
        ''' Calculates the mean intensity of the given field in this target 
        
        Parameters
        -----------
        field: Nx3 NodeData or ElementData
            Electric field

        Returns
        ------------
        intensity: float
            Mean intensity in this target and in the target direction
        '''
        if (self.positions is None) == (self.indexes is None): # negative XOR operation
            raise ValueError('Please set either positions or indexes')

        assert self.mesh is not None, 'Please set a mesh'
        assert field.nr_comp == 3, 'Field must have 3 components'

        indexes, mapping = _find_indexes(self.mesh, self.lf_type,
                                         positions=self.positions,
                                         indexes=self.indexes,
                                         tissues=self.tissues,
                                         radius=self.radius)

        directions = _find_directions(self.mesh, self.lf_type,
                                      self.directions, indexes,
                                      mapping)

        f = field[indexes]
        components = np.sum(f * directions, axis=1)

        if self.lf_type == 'node':
            weights = self.mesh.nodes_volumes_or_areas()[indexes]
        elif self.lf_type == 'element':
            weights = self.mesh.elements_volumes_and_areas()[indexes]
        else:
            raise ValueError("lf_type must be 'node' or 'element'."
                             " Got: {0} instead".format(self.lf_type))

        return np.average(components, weights=weights)

    def mean_angle(self, field):
        ''' Calculates the mean angle between the field and the target

        Parameters
        -----------
        field: Nx3 NodeData or ElementData
            Electric field

        Returns
        ------------
        angle: float
            Mean angle in this target between the field and the target direction, in
            degrees
        '''
        if (self.positions is None) == (self.indexes is None): # negative XOR operation
            raise ValueError('Please set either positions or indexes')

        assert self.mesh is not None, 'Please set a mesh'
        assert field.nr_comp == 3, 'Field must have 3 components'

        indexes, mapping = _find_indexes(self.mesh, self.lf_type,
                                         positions=self.positions,
                                         indexes=self.indexes,
                                         tissues=self.tissues,
                                         radius=self.radius)

        directions = _find_directions(self.mesh, self.lf_type,
                                      self.directions, indexes,
                                      mapping)
        if self.intensity < 0:
            directions *= -1
        f = field[indexes]
        components = np.sum(f * directions, axis=1)
        norm = np.linalg.norm(f, axis=1)
        tangent = np.sqrt(norm ** 2 - components ** 2)
        angles = np.rad2deg(np.arctan2(tangent, components))
        if self.lf_type == 'node':
            weights = self.mesh.nodes_volumes_or_areas()[indexes]
        elif self.lf_type == 'element':
            weights = self.mesh.elements_volumes_and_areas()[indexes]
        else:
            raise ValueError("lf_type must be 'node' or 'element'."
                             " Got: {0} instead".format(self.lf_type))
        weights *= norm
        return np.average(angles, weights=weights)

    def __str__(self):
        s = ('positions: {0}\n'
             'indexes: {1}\n'
             'directions: {2}\n'
             'radius: {3}\n'
             'intensity: {4}\n'
             'max_angle: {5}\n'
             'tissues: {6}\n'
             .format(
                 str(self.positions),
                 str(self.indexes),
                 str(self.directions),
                 self.radius,
                 self.intensity,
                 str(self.max_angle),
                str(self.tissues)))
        return s


class TDCSavoid:
    ''' List of positions to be avoided by optimizer

    Attributes
    -------------
    positions: Nx3 ndarray
        List of positions to be avoided, in x, y, z coordinates and in the subject space.
        Will find the closest mesh points
    indexes: Nx1 ndarray of ints
        Indexes (1-based) of elements/nodes to be avoided. Overwrites positions
    weight : float (optional)
        Weight to give to avoid region. The larger, the more we try to avoid it. Default:
        1e3
    radius: float (optional)
        Radius of region. All the elements/nodes within the given radius of the indexes
        will be included.
    tissues: list or None (Optional)
        Tissues to be included in the region. Either a list of integer with tissue tags or None
        for all tissues. Default: None


    Note
    -------
    If both positions and indexes are set to None, and a tissue is set, it will set the
    given weith to all elements/nodes in the given tissues

    THE ONES BELLOW SHOULD NOT BE FILLED BY THE USERS IN NORMAL CIRCUNTANCES:

    mesh: simnibs.msh.mesh_io.Msh (optional)
        Mesh where the target is defined. Set by the TDCSoptimize methods
    lf_type: 'node' or 'element'
        Where the electric field values are defined

    Warning
    -----------
    Changing positions constructing the class
    can cause unexpected behaviour
    '''
    def __init__(self, positions=None, indexes=None,
                 weight=1e3, radius=2, tissues=None,
                 mesh=None, lf_type=None):
        self.lf_type = lf_type
        self.mesh = mesh
        self.radius = radius
        self.tissues = tissues
        self.positions = positions
        self.indexes = indexes
        self.weight = weight


    @classmethod
    def read_mat_struct(cls, mat):
        '''Reads a .mat structure

        Parameters
        -----------
        mat: dict
            Dictionary from scipy.io.loadmat

        Returns
        ----------
        t: TDCSavoid
            TDCSavoid structure
        '''
        t = cls()
        positions = try_to_read_matlab_field(mat, 'positions', list, t.positions)
        indexes = try_to_read_matlab_field(mat, 'indexes', list, t.indexes)
        weight = try_to_read_matlab_field(mat, 'weight', float, t.weight)
        radius = try_to_read_matlab_field(mat, 'radius', float, t.radius)
        tissues = try_to_read_matlab_field(mat, 'tissues', list, t.tissues)
        if positions is not None and len(positions) == 0:
            positions = None
        if indexes is not None and len(indexes) == 0:
            indexes = None
        if tissues is not None and len(tissues) == 0:
            tissues = None

        is_empty = True
        is_empty *= t.positions == positions
        is_empty *= t.indexes == indexes
        is_empty *= t.weight == weight
        is_empty *= t.tissues == tissues
        if is_empty:
            return None

        return cls(positions, indexes, weight, radius, tissues)


    def _get_avoid_region(self):
        if (self.indexes is not None) or (self.positions is not None):
            indexes, _ = _find_indexes(self.mesh, self.lf_type,
                                       positions=self.positions,
                                       indexes=self.indexes,
                                       tissues=self.tissues,
                                       radius=self.radius)
            return indexes
        elif self.tissues is not None:
            if self.lf_type == 'element':
                return self.mesh.elm.elm_number[
                    np.isin(self.mesh.elm.tag1, self.tissues)]
            elif self.lf_type == 'node':
                return self.mesh.elm.nodes_with_tag(self.tissues)
        else:
            raise ValueError('Please define either indexes/positions or tissues')


    def avoid_field(self):
        ''' Returns a field with self.weight in the target area and
        weight=1 outside the target area

        Returns
        ------------
        w: float, >= 1
            Weight field
        '''
        assert self.mesh is not None, 'Please set a mesh'
        assert self.lf_type is not None, 'Please set a lf_type'
        assert self.weight > 0, 'Weights must be > 0'
        if self.lf_type == 'node':
            f = np.ones(self.mesh.nodes.nr)
        elif self.lf_type == 'element':
            f = np.ones(self.mesh.elm.nr)
        else:
            raise ValueError("lf_type must be 'node' or 'element'."
                             " Got: {0} instead".format(self.lf_type))

        indexes = self._get_avoid_region()
        f[indexes - 1] = self.weight

        return f

    def as_field(self, name='weights'):
        ''' Returns a NodeData or ElementData field with the weights

        Paramets
        ---------
        name: str (optional)
            Name for the field

        Returns
        --------
        f: NodeData or ElementData
            Field with weights
        '''
        w = self.avoid_field()
        if self.lf_type == 'node':
            return mesh_io.NodeData(w, name, mesh=self.mesh)
        elif self.lf_type == 'element':
            return mesh_io.ElementData(w, name, mesh=self.mesh)


    def mean_field_norm_in_region(self, field):
        ''' Calculates the mean field norm in the region defined by the avoid structure

        Parameters
        -----------
        field: ElementData or NodeData
            Field for which we calculate the mean norm
        '''
        assert self.mesh is not None, 'Please set a mesh'
        assert self.lf_type is not None, 'Please set a lf_type'
        indexes = self._get_avoid_region()
        v = np.linalg.norm(field[indexes], axis=1)
        if self.lf_type == 'node':
            weight = self.mesh.nodes_volumes_or_areas()[indexes]
        elif self.lf_type == 'element':
            weight = self.mesh.elements_volumes_and_areas()[indexes]
        else:
            raise ValueError("lf_type must be 'node' or 'element'."
                             " Got: {0} instead".format(self.lf_type))

        return np.average(v, weights=weight)

    def __str__(self):
        s = ('positions: {0}\n'
             'indexes: {1}\n'
             'radius: {2}\n'
             'weights: {3:.1e}\n'
             'tissues: {4}\n'
             .format(
                 str(self.positions),
                 str(self.indexes),
                 self.radius,
                 self.weight,
                 str(self.tissues)))
        return s


def _save_TDCStarget_mat(target):
    target_dt = np.dtype(
        [('type', 'O'),
         ('indexes', 'O'), ('directions', 'O'),
         ('positions', 'O'), ('intensity', 'O'),
         ('max_angle', 'O'), ('radius', 'O'),
         ('tissues', 'O')])

    target_mat = np.empty(len(target), dtype=target_dt)

    for i, t in enumerate(target):
        target_mat[i] = np.array([
            ('TDCStarget',
             remove_None(t.indexes),
             remove_None(t.directions),
             remove_None(t.positions),
             remove_None(t.intensity),
             remove_None(t.max_angle),
             remove_None(t.radius),
             remove_None(t.tissues))],
             dtype=target_dt)

    return target_mat



def _save_TDCSavoid_mat(avoid):
    avoid_dt = np.dtype(
        [('type', 'O'),
         ('indexes', 'O'), ('positions', 'O'),
         ('weight', 'O'),('radius', 'O'),
         ('tissues', 'O')])

    avoid_mat = np.empty(len(avoid), dtype=avoid_dt)

    for i, t in enumerate(avoid):
        avoid_mat[i] = np.array([
            ('TDCSavoid',
             remove_None(t.indexes),
             remove_None(t.positions),
             remove_None(t.weight),
             remove_None(t.radius),
             remove_None(t.tissues))],
             dtype=avoid_dt)

    return avoid_mat



def _find_indexes(mesh, lf_type, indexes=None, positions=None, tissues=None, radius=0.):
    ''' Looks into the mesh to find either
        1. nodes/elements withn a given radius of a set of points (defined as positions)
        and in the specified tissues. The fist step will be to find the closest
        node/element
        2. Specific indexes
    Returns the indices of the nodes/elements in the mesh as well as a mapping saying
    from which of the oridinal points the new points were acquired'''



    if (positions is not None) == (indexes is not None): # negative XOR operation
        raise ValueError('Please define either positions or indexes')

    if indexes is not None:
        indexes = np.atleast_1d(indexes)
        return indexes, np.arange(len(indexes))

    if lf_type == 'node':
        if tissues is not None:
            mesh_indexes = mesh.elm.nodes_with_tag(tissues)
        else:
            mesh_indexes = mesh.nodes.node_number

        mesh_pos = mesh.nodes[mesh_indexes]

    elif lf_type == 'element':
        if tissues is not None:
            mesh_indexes = mesh.elm.elm_number[np.isin(mesh.elm.tag1, tissues)]
        else:
            mesh_indexes = mesh.elm.elm_number

        mesh_pos = mesh.elements_baricenters()[mesh_indexes]

    else:
        raise ValueError('lf_type must be either "node" or "element"')

    assert radius >= 0., 'radius should be >= 0'
    assert len(mesh_pos) > 0, 'Could not find any elements or nodes with given tags'
    kdtree = scipy.spatial.cKDTree(mesh_pos)
    pos_projected, indexes = kdtree.query(positions)
    indexes = np.atleast_1d(indexes)
    if radius > 1e-9:
        in_radius = kdtree.query_ball_point(mesh_pos[indexes], radius)
        original = np.concatenate([(i,)*len(ir) for i, ir in enumerate(in_radius)])
        in_radius, uq_idx = np.unique(np.concatenate(in_radius), return_index=True)
        return mesh_indexes[in_radius], original[uq_idx]
    else:
        return mesh_indexes[indexes],  np.arange(len(indexes))

def _find_directions(mesh, lf_type, directions, indexes, mapping=None):
    if directions == 'normal':
        if 4 in np.unique(mesh.elm.elm_type):
            raise ValueError("Can't define a normal direction for volumetric data!")
        if lf_type == 'node':
            directions = -mesh.nodes_normals()[indexes]
        elif lf_type == 'element':
            directions = -mesh.triangle_normals()[indexes]
        return directions
    else:
        directions = np.atleast_2d(directions)
        if directions.shape[1] != 3:
            raise ValueError(
                "directions must be the string 'normal' or a Nx3 array"
            )
        if mapping is None:
            if len(directions) == len(indexes):
                mapping = np.arange(len(indexes))
            else:
                raise ValueError('Different number of indexes and directions and no '
                                 'mapping defined')
        elif len(directions) == 1:
            mapping = np.zeros(len(indexes), dtype=int)

        directions = directions/np.linalg.norm(directions, axis=1)[:, None]
        return directions[mapping]

