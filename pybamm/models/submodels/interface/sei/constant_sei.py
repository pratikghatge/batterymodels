#
# Class for constant SEI thickness
#
import pybamm
from .base_sei import BaseModel


class ConstantSEI(BaseModel):
    """
    Class for SEI with constant thickness.

    Note that there is no SEI current, so we don't need to update the "sum of
    interfacial current densities" variables from
    :class:`pybamm.interface.BaseInterface`

    Parameters
    ----------
    param : parameter class
        The parameters to use for this submodel
    options : dict
        A dictionary of options to be passed to the model.
    phase : str, optional
        Phase of the particle (default is "primary")

    **Extends:** :class:`pybamm.sei.BaseModel`
    """

    def __init__(self, param, options, phase="primary"):
        super().__init__(param, options=options, phase=phase)
        if self.options.electrode_types["negative"] == "planar":
            self.reaction_loc = "interface"
        else:
            self.reaction_loc = "full electrode"

    def get_fundamental_variables(self):
        # Constant thicknesses
        L_inner = self.phase_param.L_inner_0
        L_outer = self.phase_param.L_outer_0
        L_sei = self.phase_param.L_sei_0
        variables = self._get_standard_thickness_variables(L_inner, L_outer, L_sei)

        # Concentrations (derived from thicknesses)
        variables.update(self._get_standard_concentration_variables(variables))

        # Reactions
        if self.reaction_loc == "interface":
            zero = pybamm.PrimaryBroadcast(pybamm.Scalar(0), "current collector")
        else:
            zero = pybamm.FullBroadcast(
                pybamm.Scalar(0), "negative electrode", "current collector"
            )
        variables.update(self._get_standard_reaction_variables(zero, zero))

        return variables
