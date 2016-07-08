"""
This module saves cosmosis output to a 2-pt file after interpolating it 
to specified ell values. It is useful for making simulations, but
is not yet fully supported so please use it with care and check 
the results carefully.

"""


from cosmosis.datablock import option_section, names
import numpy as np
from scipy.interpolate import interp1d
import twopoint
from twopoint_cosmosis import type_table
import gaussian_covariance




def setup(options):
    # ell range of output - all assumed the same, with log-spacing and 
    # no window functions
    ell_min = options.get_double(option_section, "ell_min")
    ell_max = options.get_double(option_section, "ell_max")
    n_ell = options.get_int(option_section, "n_ell")
    ell = np.logspace(np.log10(ell_min), np.log10(ell_max), n_ell)


    def get_arr(x):
        a = options[option_section, x]
        if not isinstance(a, np.ndarray):
            a = np.array([a])
        return a

    number_density_shear_bin = get_arr("number_density_shear_bin")
    number_density_lss_bin = get_arr("number_density_lss_bin")
    sigma_e_bin = get_arr("sigma_e_bin")
    survey_area = options[option_section, "survey_area"] * (np.pi*np.pi)/(180*180)

    #names n(z) sections in the datablock, to be saved with the same name
    #to the FITS file output
    shear_nz = options.get_string(option_section, "shear_nz_name").upper()
    position_nz = options.get_string(option_section, "position_nz_name").upper()

    #name of the output file and whether to overwrite it.
    filename = options.get_string(option_section, "filename")
    clobber = options.get_bool(option_section, "clobber", False)

    return [ell, filename, shear_nz, position_nz, clobber, number_density_shear_bin, number_density_lss_bin, sigma_e_bin, survey_area]

def spectrum_measurement_from_block(block, section_name, output_name, types, kernels, ell_sample):

    #The dictionary type_table stores the codes used in the FITS files for
    #the types of spectrum
    type_codes = (types[0].name, types[1].name)
    _, _, bin_format = type_table[type_codes]

    # for cross correlations we must save bin_ji as well as bin_ij.
    # but not for auto-correlations. Also the numbers of bins can be different
    is_auto = (types[0] == types[1])
    if is_auto:
        nbin_a = block[section_name, "nbin"]
    else:
        nbin_a = block[section_name, "nbin_a"]
        nbin_b = block[section_name, "nbin_b"]

    #This is the ell values that have been calculated by cosmosis, not to
    #be confused with the ell values at which we want to save the results
    #(which is ell_sample)
    ell = block[section_name, "ell"]

    #This is the length of the sample values
    n_ell_sample = len(ell_sample)


    #The fits format stores all the measurements
    #as one long vector.  So we build that up here from the various
    #bins that we will load in.  These are the different columns
    bin1 = []
    bin2 = []
    value = []
    angular_bin = []
    angle = []

    #Bin pairs. Varies depending on auto-correlation
    for i in xrange(nbin_a):
        if is_auto:
            jmax = i+1
        else:
            jmax = nbin_b
        for j in xrange(jmax):

            #Load and interpolate from the block
            cl = block[section_name, bin_format.format(i+1,j+1)]
            cl_sample = interp1d(ell, cl)(ell_sample)
            #Build up on the various vectors that we need
            bin1.append(np.repeat(i+1, n_ell_sample))
            bin2.append(np.repeat(j+1, n_ell_sample))
            value.append(cl_sample)
            angular_bin.append(np.arange(n_ell_sample))
            angle.append(ell_sample)

    #Convert all the lists of vectors into long single vectors
    bin1 = np.concatenate(bin1)
    bin2 = np.concatenate(bin2)
    angular_bin = np.concatenate(angular_bin)
    value = np.concatenate(value)
    angle = np.concatenate(angle)
    bins = (bin1, bin2)

    #At the moment we only support this window function
    windows = "SAMPLE"

    #Build the output object type reqired.
    s = twopoint.SpectrumMeasurement(output_name, bins, types, kernels, windows, angular_bin, value, angle=angle)
    return s




def nz_from_block(block, nz_name):
    print
    print
    print "Saving n(z) from the block to file."
    print "A quick warning - we are assuming things about the n(z) that may not be quite right."
    print "Converting from splines to histograms."
    print "To properly fix this I will have to do a bit more work."
    print
    print
    z = block[nz_name, "z"]
    zlow = z
    dz = z[1]-z[0]
    zhigh = z+dz
    z = zlow+0.5*dz
    nbin = block[nz_name, "nbin"]
    nzs = []
    for i in xrange(nbin):
        nz = block[nz_name, "bin_{}".format(i+1)]
        nzs.append(nz)

    return twopoint.NumberDensity(nz_name, zlow, z, zhigh, nzs)

def convert_nz_steradian(n):
    return n * (41253.0*60.*60.) / (4*np.pi)

class ObservedClGetter(object):
    def __init__(self, number_density_shear_bin, number_density_lss_bin, sigma_e_bin):
        self.number_density_shear_bin=convert_nz_steradian(number_density_shear_bin)
        self.number_density_lss_bin=convert_nz_steradian(number_density_lss_bin)
        self.sigma_e_bin=sigma_e_bin
        self.splines = {}

    def lookup(self, block, A, B, i, j, ell):
        """
        This is a helper function for the compute_gaussian_covariance code.
        It looks up the theory value of C^{ij}_{AB}(ell) in the block and adds noise
        """
        # We have already saved splines into the theory space earlier
        # when constructing the theory vector.
        # So now we just need to look those up again, using the same
        # code we use in the twopoint library.
        section, ell_name, value_name = type_table[A, B]
        assert ell_name=="ell", "Gaussian covariances are currently only written for C_ell, not other 2pt functions"

        #We extract relevant bits from the block and spline them
        #for output
        name_ij = value_name.format(i,j)

        if name_ij in self.splines:
            spline = self.splines[name_ij]
        else:
            spline = self.make_spline(block, A, B, i, j, ell)
            self.splines[name_ij] = spline

        obs_c_ell = spline(ell)

        #For shear-shear the noise component is sigma^2 / number_density_bin
        #and for position-position it is just 1/number_density_bin
        if (A==B) and (A==twopoint.Types.galaxy_shear_emode_fourier.name) and (i==j):
            noise = self.sigma_e_bin[i-1]**2 / self.number_density_shear_bin[i-1]
            obs_c_ell += noise
        if (A==B) and (A==twopoint.Types.galaxy_position_fourier.name) and (i==j):
            noise = 1.0 / self.number_density_lss_bin[i-1]
            obs_c_ell += noise


        return obs_c_ell


    def make_spline(self, block, A, B, i, j, ell):
        section, ell_name, value_name = type_table[A, B]
        assert ell_name=="ell", "Gaussian covariances are currently only written for C_ell, not other 2pt functions"

        #We extract relevant bits from the block and spline them
        #for output
        name_ij = value_name.format(i,j)
        name_ji = value_name.format(j,i)

        angle = block[section, ell_name]

        if block.has_value(section, name_ij):
            theory = block[section, name_ij]
        elif block.has_value(section, name_ji) and A==B:
            theory = block[section, name_ji]
        else:
            raise ValueError("Could not find theory prediction {} in section {}".format(value_name.format(i,j), section))

        spline = interp1d(angle, theory)
        return spline

def covmat_from_block(block, spectra, sky_area, number_density_shear_bin, number_density_lss_bin, sigma_e_bin):
    getter = ObservedClGetter(number_density_shear_bin, number_density_lss_bin, sigma_e_bin)
    C = []
    names = []
    starts = []
    lengths = []

    # s and t index the spectra that we have. e.g. s or t=1 might be the full set of 
    #shear-shear measuremnts
    x = 0
    for s,AB in enumerate(spectra[:]):
        M = []
        starts.append(x)
        L = len(AB)
        lengths.append(L)
        x+=L

        names.append(AB.name)
        for t,CD in enumerate(spectra[:]):
            print "Looking at covariance between {} and {} (s={}, t={})".format(AB.name, CD.name, s, t)
            #We only calculate the upper triangular.
            #Get the lower triangular here. We have to 
            #transpose it compared to the upper one.
            if s>t:
                MI = C[t][s].T
            else:
                MI = gaussian_covariance.compute_gaussian_covariance(sky_area, 
                    getter.lookup, block, AB, CD)
            M.append(MI)
        C.append(M)
    C = np.vstack([np.hstack(CI) for CI in C])
    info = twopoint.CovarianceMatrixInfo("COVMAT", names, lengths, C)
    return info




def execute(block, config):
    ell_sample, filename, shear_nz, position_nz, clobber, number_density_shear_bin, number_density_lss_bin, sigma_e_bin, survey_area = config
    print "Saving two-point data to {}".format(filename)

    spectra = []

    if block.has_section(names.shear_cl):
        name = "shear_cl"
        types = (twopoint.Types.galaxy_shear_emode_fourier, twopoint.Types.galaxy_shear_emode_fourier)
        kernels = (shear_nz, shear_nz)
        s = spectrum_measurement_from_block(block, name, name, types, kernels, ell_sample)
        print " - saving shear_cl"
        spectra.append(s)

    if block.has_section("galaxy_shear_cl"):
        name = "galaxy_shear_cl"
        types = (twopoint.Types.galaxy_position_fourier, twopoint.Types.galaxy_shear_emode_fourier)
        kernels = (position_nz, shear_nz)        
        s = spectrum_measurement_from_block(block, name, name, types, kernels, ell_sample)
        print " - saving galaxy_shear_cl"
        spectra.append(s)

    if block.has_section("galaxy_cl"):
        name = "galaxy_cl"
        types = (twopoint.Types.galaxy_position_fourier, twopoint.Types.galaxy_position_fourier)
        kernels = (position_nz, position_nz)
        s = spectrum_measurement_from_block(block, name, name, types, kernels, ell_sample)
        print " - saving galaxy_cl"
        spectra.append(s)

    covmat_info = covmat_from_block(block, spectra, survey_area, number_density_shear_bin, number_density_lss_bin, sigma_e_bin)

    if not spectra:
        raise ValueError("Sorry - I couldn't find any shear_cl, shear_galaxy_cl, or galaxy_cl to save.")


    kernels = []
    if block.has_section(names.shear_cl) or block.has_section("galaxy_shear_cl"):
        kernels.append(nz_from_block(block, shear_nz))
    if (block.has_section("galaxy_cl") or block.has_section("galaxy_shear_cl")) and (shear_nz!=position_nz):
        kernels.append(nz_from_block(block, position_nz))
    
    windows = []

    data = twopoint.TwoPointFile(spectra, kernels, windows, covmat_info)
    data.to_fits(filename, clobber=clobber)

    return 0
