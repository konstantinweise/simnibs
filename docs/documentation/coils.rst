.. _coil_fies:

Coil Models Included in SimNIBS
==================================

Default Coil Files
-------------------

.. list-table::
   :widths: 40 40
   :header-rows: 1

   * - Coil Name
     - File Names
   * - Magstim 70mm Figure-of-Eight
     - Magstim_70mm_Fig8.ccd Magstim_70mm_Fig8.nii.gz
   * - MagVenture MC B70
     - MagVenture_MC_B70.ccd MagVenture_MC_B70.nii.gz


These coil models were constructed based on X-ray images.

Reference
''''''''''

`Thielscher, Axel, and Thomas Kammer. "Electric field properties of two commercial figure-8 coils in TMS: calculation of focality and efficiency." Clinical neurophysiology 115.7 (2004): 1697-1708. <https://doi.org/10.1016/j.clinph.2004.02.019>`_


Extra Coil Files
----------------

Please see :download:`here <Deng_Brain_Stim_2013_docu.pdf>` for more information.
These coil files are optionally downloaded by SimNIBS during the installation. 

We would like to thank Zhi-De Deng for sharing his coil definition files with us and Mursel Karadas for converting them to SimNIBS format. These models were constructed from geometrical values about the coil windings taken from literature. Please note that some of the models represent non-planar coils. Setting the coil position for those in the SimNIBS GUI can easily result in the simulation of physically impossible coil placements, i.e. corresponding to parts of the coil being inside the head, and are only meant for expert use!

Reference
''''''''''


`Deng, Zhi-De, Sarah H. Lisanby, and Angel V. Peterchev. "Electric field depth–focality tradeoff in transcranial magnetic stimulation: simulation comparison of 50 coil designs." Brain stimulation 6.1 (2013): 1-13. <https://doi.org/10.1016/j.brs.2012.02.005>`_ 
