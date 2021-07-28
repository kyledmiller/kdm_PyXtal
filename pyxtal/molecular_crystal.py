"""
Module for generating molecular crystals
"""

# Standard Libraries
import random
from copy import deepcopy
import numpy as np

# PyXtal imports
from pyxtal.msg import printx
from pyxtal.tolerance import Tol_matrix
from pyxtal.lattice import Lattice, cellsize
from pyxtal.wyckoff_site import mol_site, WP_merge
from pyxtal.molecule import pyxtal_molecule, orientation_in_wyckoff_position
from pyxtal.symmetry import Group, jk_from_i
from pyxtal.symmetry import choose_wyckoff_molecular as wyc_mol
from pyxtal.msg import CompatibilityError

# Define functions
# ------------------------------
class molecular_crystal:
    """
    Class for storing and generating molecular crystals based on symmetry
    constraints. Based on the crystal.random_crystal class for atomic crystals.
    Given a spacegroup, list of molecule objects, molecular stoichiometry, and
    a volume factor, generates a molecular crystal consistent with the given
    constraints. 

    Args:
        group: the spacegroup number (1-230)
        molecules: a list of pymatgen.core.structure.Molecule objects for
            each type of molecule. Alternatively, you may supply a file path,
            or the name of molecules from the built_in 
            `database <pyxtal.database.collection.html>`_
        numMols: A list of the number of each type of molecule within the
            primitive cell (NOT the conventioal cell)
        volume_factor: A volume factor used to generate a larger or smaller
            unit cell. Increasing this gives extra space between molecules
        block: a file storing the xyz coordinates of building block
        allow_inversion: Whether or not to allow chiral molecules to be
            inverted. If True, the final crystal may contain mirror images of
            the original molecule. Unless the chemical properties of the mirror
            image are known, it is highly recommended to keep this value False
        orientations: Once a crystal with the same spacegroup and molecular
            stoichiometry has been generated, you may pass its
            valid_orientations attribute here to avoid repeating the
            calculation, but this is not required
        lattice (optional): the `pyxtal.lattice.Lattice <pyxtal.lattice.Lattice.html>`_ 
            object to define the unit cell
        conventional (optional): count the number of atoms in the conventional cell
        tm (optional): the `pyxtal.tolerance.Tol_matrix <pyxtal.tolerance.tolerance.html>`_ 
            object to define the distances
        sites (optional): pre-assigned wyckoff sites (e.g., `[["4a"], ["2b"]]`)
        diag (optional): if use the nonstandart setting (P21/n, Pn, C2/n)?
    """

    def __init__(
        self,
        group,
        molecules,
        numMols,
        volume_factor = 1.1,
        block = None,
        select_high = True,
        allow_inversion = True,
        orientations = None,
        lattice = None,
        torsions = None,
        tm = Tol_matrix(prototype="molecular"),
        sites = None,
        conventional = True,
        diag = False,
    ):

        self.dim = 3 # The number of periodic dimensions (1,2,3)
        self.PBC = [1, 1, 1]
        self.thickness = None
        self.area = None
        self.diag = diag
        self.selec_high = select_high
        self.lattice_attempts = 0
        self.coord_attempts = 0

        self.init_common(
            molecules,
            numMols,
            volume_factor,
            block,
            select_high,
            allow_inversion,
            orientations,
            group,
            lattice,
            torsions,
            sites,
            conventional,
            tm,
        )

    def init_common(
        self,
        molecules,
        numMols,
        volume_factor,
        block,
        select_high,
        allow_inversion,
        orientations,
        group,
        lattice,
        torsions,
        sites,
        conventional,
        tm,
    ):
        # init functionality which is shared by 3D, 2D, and 1D crystals
        self.valid = False
        self.numattempts = 0 # number of attempts to generate the crystal.
        if type(group) == Group:
            self.group = group
        else:
            self.group = Group(group, dim=self.dim)
        if self.diag and self.group.number not in [5, 7, 8, 9, 12, 13, 14, 15]:
            self.diag = False
        self.number = self.group.number
        self.factor = volume_factor  # volume factor for the unit cell.
        numMols = np.array(numMols)  # must convert it to np.array
        if not conventional:
            mul = cellsize(self.group)
        else:
            mul = 1
        self.numMols = numMols * mul

        # boolean numbers
        self.allow_inversion = allow_inversion
        self.select_high = select_high

        # Tolerance matrix
        if type(tm) == Tol_matrix:
            self.tol_matrix = tm
        else:
            self.tol_matrix = Tol_matrix(prototype=tm)

        self.set_molecules(molecules, torsions)
        self.set_sites(sites)
        self.set_orientations(orientations)

        # Check the minimum dof within the Wyckoff positions
        compat, self.degrees = self._check_compatible()
        if not compat:
            self.valid = False
            msg = "Compoisition " + str(self.numMols) 
            msg += " not compatible with symmetry "
            msg += str(self.group.number) 
            raise CompatibilityError(msg)
        else:
            self.set_volume()
            self.set_lattice(lattice)
            self.set_crystal()

    def __str__(self):
        s = "------Random Molecular Crystal------"
        s += "\nDimension: " + str(self.dim)
        if self.group.number in [7, 14, 15] and self.diag:
            symbol = self.group.alias
        else:
            symbol = self.group.symbol
        s += "\nGroup: " + symbol
        s += "\nVolume factor: " + str(self.factor)
        s += "\n" + str(self.lattice)
        if self.valid:
            s += "\nWyckoff sites:"
            for wyc in self.mol_sites:
                s += "\n\t{}".format(wyc)
        else:
            s += "\nStructure not generated."
        return s

    def __repr__(self):
        return str(self)

    def set_sites(self, sites):
        """
        initialize Wyckoff sites

        Args:
            sites: list
        """
        # Symmetry sites
        self.sites = {}
        for i, mol in enumerate(self.molecules):
            if sites is not None and sites[i] is not None:
                self._check_consistency(sites[i], self.numMols[i])
                self.sites[i] = sites[i]
            else:
                self.sites[i] = None
 
    def set_molecules(self, molecules, torsions):
        """
        Get molecular information

        Args:
            molecules: list of molecules
            torsions: list of torsions
        """
        if torsions is None:
            torsions = [None]*len(molecules)

        self.molecules = []  
        for i, mol in enumerate(molecules):
            p_mol = pyxtal_molecule(mol, torsions=torsions[i], tm=self.tol_matrix)
            self.molecules.append(p_mol)
 
    def set_orientations(self, orientations):
        """
        Calculates the valid orientations for each Molecule and Wyckoff
        position. Returns a list with 4 indices:
            - index 1: the molecular prototype's index within self.molecules
            - index 2: the Wyckoff position's 1st index (based on multiplicity)
            - index 3: the WP's 2nd index (within the group of equal multiplicity)
            - index 4: the index of the valid orientation for the molecule/WP pair

        For example, self.valid_orientations[i][j][k] would be a list of valid
        orientations for self.molecules[i], in the Wyckoff position
        self.group.wyckoffs_organized[j][k]
        """
        if orientations is None:
            self.valid_orientations = []
            for pyxtal_mol in self.molecules:
                self.valid_orientations.append([])
                wp_index = -1
                for i, x in enumerate(self.group.wyckoffs_organized):
                    self.valid_orientations[-1].append([])
                    for j, wp in enumerate(x):
                        wp_index += 1
                        allowed = orientation_in_wyckoff_position(
                            pyxtal_mol.mol,
                            wp,
                            already_oriented=True,
                            allow_inversion=self.allow_inversion,
                        )

                        if allowed:
                            self.valid_orientations[-1][-1].append(allowed)
                        else:
                            self.valid_orientations[-1][-1].append([])
        else:
            self.valid_orientations = orientations

    def set_volume(self):
        """
        Given the molecular stoichiometry, estimate the volume needed for a unit cell.

        Returns:
            the estimated volume (in cubic Angstroms) needed for the unit cell
        """
        volume = 0
        for numMol, mol in zip(self.numMols, self.molecules):
            volume += numMol * mol.volume
        self.volume = abs(self.factor * volume)


    def set_lattice(self, lattice):
        """
        Generate the initial lattice
        """
        if lattice is not None:
            # Use the provided lattice
            self.lattice = lattice
            self.volume = lattice.volume
            # Make sure the custom lattice PBC axes are correct.
            if lattice.PBC != self.PBC:
                self.lattice.PBC = self.PBC
                printx("\n  Warning: converting custom lattice PBC to " + str(self.PBC))
        else:
            # Determine the unique axis
            if self.dim == 2:
                if self.number in range(3, 8):
                    unique_axis = "c"
                else:
                    unique_axis = "a"
            elif self.dim == 1:
                if self.number in range(3, 8):
                    unique_axis = "a"
                else:
                    unique_axis = "c"
            else:
                unique_axis = "c"

            # Generate a Lattice instance
            self.lattice = Lattice(
                self.group.lattice_type,
                self.volume,
                PBC=self.PBC,
                unique_axis=unique_axis,
                thickness=self.thickness,
                area=self.area,
                )

    def set_crystal(self):
        """
        The main code to generate a random molecular crystal. If successful,
        `self.valid` is True (False otherwise) 
        """

        if not self.degrees:
            self.lattice_attempts = 20
            self.coord_attempts = 3
            self.ori_attempts = 1
        else:
            self.lattice_attempts = 40
            self.coord_attempts = 30
            self.ori_attempts = 5

        if not self.lattice.allow_volume_reset:
            self.lattice_attempts = 1

        for cycle1 in range(self.lattice_attempts):
            self.cycle1 = cycle1
            for cycle2 in range(self.coord_attempts):
                self.cycle2 = cycle2
                output = self._set_coords()

                if output:
                    self.mol_sites = output
                    break
            if self.valid:
                return
            else:
                self.lattice.reset_matrix()

        printx("Couldn't generate crystal after max attempts.", priority=1)

    def _set_coords(self):
        """
        generate coordinates for random crystal
        """

        mol_sites_total = []
        # Add molecules 
        for i, numMol in enumerate(self.numMols):
            pyxtal_mol = self.molecules[i]
            valid_ori = self.valid_orientations[i]
            output = self._set_mol_wyckoffs(
                i, numMol, pyxtal_mol, valid_ori, mol_sites_total
            )
            if output is not None:
                mol_sites_total.extend(output)
            else:
                # correct multiplicity not achieved exit and start over
                return None

        self.valid = True
        return mol_sites_total

    def _set_mol_wyckoffs(self, id, numMol, pyxtal_mol, valid_ori, mol_wyks): 
        """
        generates a set of wyckoff positions to accomodate a given number
        of molecules

        Args:
            numMol: Number of ions to accomodate
            pyxtal_mol: Type of species being placed on wyckoff site
            mol_wyks: current wyckoff sites

        Returns:
            if sucess, wyckoff_sites_tmp: list of wyckoff sites for valid sites
            otherwise, None

        """
        numMol_added = 0
        mol_sites_tmp = []

        # Now we start to add the specie to the wyckoff position
        sites_list = deepcopy(self.sites[id]) # the list of Wyckoff site
        if sites_list is not None:
            self.wyckoff_attempts = max(len(sites_list)*2, 10)
        else:
            # the minimum numattempts is to put all atoms to the general WPs
            min_wyckoffs = int(numMol/len(self.group.wyckoffs_organized[0][0]))
            self.wyckoff_attempts = max(2*min_wyckoffs, 10)

        for cycle in range(self.wyckoff_attempts):

            # Choose a random WP for given multiplicity: 2a, 2b, 2c
            if sites_list is not None:
                site = sites_list[0]
            else: # Selecting the merging 
                site = None

            # NOTE: The molecular version return wyckoff indices, not ops
            diff = numMol - numMol_added
            wp = wyc_mol(self.group, diff, site, valid_ori, self.select_high, self.dim)

            if wp is not False:
                # Generate a list of coords from the wyckoff position
                mult = wp.multiplicity # remember the original multiplicity
                pt = self.lattice.generate_point()

                # merge coordinates if the atoms are close
                mtol = pyxtal_mol.radius * 0.5
                pt, wp, oris = WP_merge(pt, self.lattice.matrix, wp, mtol, valid_ori)

                if wp is not False:
                    if site is not None and mult != wp.multiplicity:
                        continue
                    if self.dim == 2 and self.thickness is not None and self.thickness < 0.1:
                        pt[-1] = 0.5 

                    ms0 = self._set_orientation(pyxtal_mol, pt, oris, wp)
                    if ms0 is not None:
                        # Check current WP against existing WP's  
                        passed_wp_check = True
                        for ms1 in mol_sites_tmp + mol_wyks:
                            if not ms0.check_with_ms2(ms1, tm=self.tol_matrix):
                                passed_wp_check = False
                        
                        if passed_wp_check:
                            if sites_list is not None:
                                sites_list.pop(0)

                            mol_sites_tmp.append(ms0)
                            numMol_added += len(ms0.wp)

                            # We have enough molecules of the current type
                            if numMol_added == numMol:
                                return mol_sites_tmp
        return None


    def _set_orientation(self, pyxtal_mol, pt, oris, wp): 
        """
        Generate good orientations
        """
        # Use a Wyckoff_site object for the current site
        self.numattempts += 1
        ori = random.choice(oris).copy()
        ori.change_orientation(flip=True)
        ms0 = mol_site(pyxtal_mol, pt, ori, wp, self.lattice, self.diag)
        # Check distances within the WP
        if ms0.check_distances():
            return ms0
        else:
            # Maximize the smallest distance for the general
            # positions if needed
            if len(pyxtal_mol.mol) > 1 and ori.degrees > 0:
                # bisection method
                def fun_dist(angle, ori, mo, pt):
                    # ori0 = ori.copy()
                    ori.change_orientation(angle)
                    ms0 = mol_site(
                        mo,
                        pt,
                        ori,
                        wp,
                        self.lattice,
                        self.diag,
                    )
                    d = ms0.compute_distances()
                    return d

                angle_lo = ori.angle
                angle_hi = angle_lo + np.pi
                fun_lo = fun_dist(angle_lo, ori, pyxtal_mol, pt)
                fun_hi = fun_dist(angle_hi, ori, pyxtal_mol, pt)
                fun = fun_hi
                for it in range(self.ori_attempts):
                    self.numattempts += 1
                    if (fun > 0.8) & (ms0.check_distances()):
                        return ms0
                    angle = (angle_lo + angle_hi) / 2
                    fun = fun_dist(angle, ori, pyxtal_mol, pt)
                    #print('Bisection: ', it, fun)
                    if fun_lo > fun_hi:
                        angle_hi, fun_hi = angle, fun
                    else:
                        angle_lo, fun_lo = angle, fun

        return None

    def _check_compatible(self):
        """
        Checks if the number of molecules is compatible with the Wyckoff
        positions. Considers the number of degrees of freedom for each Wyckoff
        position, and makes sure at least one valid combination of WP's exists.
        """
        # Store whether or not at least one degree of freedom exists
        has_freedom = False
        # Store the wp's already used that don't have any freedom
        used_indices = []
        # Loop over species
        for i_mol, numIon in enumerate(self.numMols):
            # Get lists of multiplicity, maxn and freedom
            l_mult0 = []
            l_maxn0 = []
            l_free0 = []
            indices0 = []
            for i_wp, wp in enumerate(self.group):
                # Check that at least one valid orientation exists
                j, k = jk_from_i(i_wp, self.group.wyckoffs_organized)
                if len(self.valid_orientations[i_mol][j][k]) > 0:
                    indices0.append(i_wp)
                    l_mult0.append(len(wp))
                    l_maxn0.append(numIon // len(wp))
                    if np.allclose(wp[0].rotation_matrix, np.zeros([3, 3])):
                        l_free0.append(False)
                    else:
                        l_free0.append(True)
            # Remove redundant multiplicities:
            l_mult = []
            l_maxn = []
            l_free = []
            indices = []
            for mult, maxn, free, i_wp in zip(l_mult0, l_maxn0, l_free0, indices0):
                if free:
                    if mult not in l_mult:
                        l_mult.append(mult)
                        l_maxn.append(maxn)
                        l_free.append(True)
                        indices.append(i_wp)
                #elif not free and i_wp not in used_indices:
                elif i_wp not in used_indices:
                    l_mult.append(mult)
                    l_maxn.append(1)
                    l_free.append(False)
                    indices.append(i_wp)
            # Loop over possible combinations
            # Create pointer variable to move through lists
            p = 0
            # Store the number of each WP, used across possible WP combinations
            n0 = [0] * len(l_mult)
            n = deepcopy(n0)
            for i, mult in enumerate(l_mult):
                if l_maxn[i] != 0:
                    p = i
                    n[i] = l_maxn[i]
                    break
            p2 = p

            if n == n0:
                #print("n == n0", n, n0)
                return False, False
            while True:
                num = np.dot(n, l_mult)
                dobackwards = False
                # The combination works: move to next species
                if num == numIon:
                    # Check if at least one degree of freedom exists
                    for val, free, i_wp in zip(n, l_free, indices):
                        if val > 0:
                            if free is True:
                                has_freedom = True
                            elif free is False:
                                indices.append(i_wp)
                    break
                # All combinations failed: return False
                if n == n0 and p >= len(l_mult) - 1:
                    #print("All combinations failed: return False")
                    return False, False
                # Too few atoms
                if num < numIon:
                    # Forwards routine
                    # Move p to the right and max out
                    if p < len(l_mult) - 1:
                        p += 1
                        n[p] = min((numIon - num) // l_mult[p], l_maxn[p])
                    else:
                        # p is already at last position: trigger backwards routine
                        dobackwards = True
                # Too many atoms
                if num > numIon or dobackwards is True:
                    # Backwards routine
                    # Set n[p] to 0, move p backwards to non-zero, and decrease by 1
                    n[p] = 0
                    while p > 0 and p > p2:
                        p -= 1
                        if n[p] != 0:
                            n[p] -= 1
                            if n[p] == 0 and p == p2:
                                p2 = p + 1
                            break
        # All species passed: return True
        if has_freedom:
            return True, True
        # All species passed, but no degrees of freedom: return 0
        else:
            return True, False

    def _check_consistency(self, site, numMol):
        """
        Check if the composition is consistent with symmetry
        """
        num = 0
        for s in site:
            num += int(s[:-1])
        if numMol == num:
            return True
        else:
            msg = "\nThe requested number of molecules is inconsistent: " + str(site)
            msg += "\nfrom numMols: {:d}".format(numMol)
            msg += "\nfrom Wyckoff list: {:d}".format(num)
            raise ValueError(msg)


class molecular_crystal_2D(molecular_crystal):
    """
    A 2d counterpart to molecular_crystal. Given a layer group, list of
    molecule objects, molecular stoichiometry, and
    a volume factor, generates a molecular crystal consistent with the given
    constraints. This crystal is stored as a pymatgen struct via self.struct

    Args:
        group: the layer group number between 1 and 80.
        molecules: a list of pymatgen.core.structure.Molecule objects for
            each type of molecule. Alternatively, you may supply a file path,
            or the name of molecules from the built_in
            `database <pyxtal.database.collection.html>`_
        numMols: A list of the number of each type of molecule within the
            primitive cell (NOT the conventioal cell)
        thickness: the thickness, in Angstroms, of the unit cell in the 3rd
            dimension (the direction which is not repeated periodically). A
            value of None causes a thickness to be chosen automatically. Note
            that this constraint applies only to the molecular centers; some
            atomic coordinates may lie outside of this range
        volume_factor: A volume factor used to generate a larger or smaller
            unit cell. Increasing this gives extra space between molecules
        allow_inversion: Whether or not to allow chiral molecules to be
            inverted. If True, the final crystal may contain mirror images of
            the original molecule. Unless the chemical properties of the mirror
            image are known, it is highly recommended to keep this value False
        orientations: Once a crystal with the same spacegroup and molecular
            stoichiometry has been generated, you may pass its
            valid_orientations attribute here to avoid repeating the
            calculation, but this is not required
        lattice (optional): `pyxtal.lattice.Lattice <pyxtal.lattice.Lattice.html>`_
            object to define the unit cell
        tm (optional): `pyxtal.tolerance.Tol_matrix <pyxtal.tolerance.tolerance.html>`_
            object to define the distances
    """

    def __init__(
        self,
        group,
        molecules,
        numMols,
        volume_factor = 1.1,
        block = None,
        select_high = True,
        allow_inversion = True,
        orientations = None,
        thickness = None,
        lattice = None,
        sites = None,
        conventional = True,
        tm=Tol_matrix(prototype="molecular"),
    ):

        self.dim = 2
        self.numattempts = 0
        self.diag = False
        self.thickness = thickness  # the thickness in Angstroms
        self.area = None
        self.PBC = [1, 1, 0]

        self.init_common(
            molecules,
            numMols,
            volume_factor,
            block,
            select_high,
            allow_inversion,
            orientations,
            group,
            lattice,
            None,
            sites,
            conventional,
            tm,
        )


class molecular_crystal_1D(molecular_crystal):
    """
    A 1d counterpart to molecular_crystal. Given a Rod group, list of
    molecule objects, molecular stoichiometry, volume factor, and area,
    generates a molecular crystal consistent with the given constraints.
    The crystal is stored as a pymatgen struct via self.struct

    Args:
        group: the Rod group number between 1 and 75. OR
            `pyxtal.symmetry.Group <pyxtal.symmetry.Group.html>`_ object
        molecules: a list of pymatgen.core.structure.Molecule objects for
            each type of molecule. Alternatively, you may supply a file path,
            or the name of molecules from the built_in
            `database <pyxtal.database.collection.html>`_
        numMols: A list of the number of each type of molecule within the
            primitive cell (NOT the conventioal cell)
        area: cross-sectional area of the unit cell in Angstroms squared. A
            value of None causes an area to be chosen automatically. Note that
            this constraint applies only to the molecular centers; some atomic
            coordinates may lie outside of this range
        volume_factor: A volume factor used to generate a larger or smaller
            unit cell. Increasing this gives extra space between molecules
        allow_inversion: Whether or not to allow chiral molecules to be
            inverted. If True, the final crystal may contain mirror images of
            the original molecule. Unless the chemical properties of the mirror
            image are known, it is highly recommended to keep this value False
        orientations: Once a crystal with the same spacegroup and molecular
            stoichiometry has been generated, you may pass its
            valid_orientations attribute here to avoid repeating the
            calculation, but this is not required
        lattice (optional): the `pyxtal.lattice.Lattice <pyxtal.lattice.Lattice.html>`_
            object to define the unit cell
        tm (optional): the `pyxtal.tolerance.Tol_matrix <pyxtal.tolerance.tolerance.html>`_
            object to define the distances
    """

    def __init__(
        self,
        group,
        molecules,
        numMols,
        volume_factor = 1.1,
        block = None,
        select_high = True,
        allow_inversion = False,
        orientations = None,
        area = None,
        lattice = None,
        sites = None,
        conventional = True,
        tm=Tol_matrix(prototype="molecular"),
    ):
        self.dim = 1
        self.area = area  # the effective cross-sectional area in A^2
        self.thickness = None
        self.diag = False
        self.PBC = [0, 0, 1]  # The periodic axes of the crystal (1,2,3)->(x,y,z)

        self.init_common(
            molecules,
            numMols,
            volume_factor,
            block,
            select_high,
            allow_inversion,
            orientations,
            group,
            lattice,
            None,
            sites,
            conventional,
            tm,
        )
