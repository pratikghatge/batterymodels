#
# Class for constant SEI thickness
#
import pybamm
from .base_sei import BaseModel


class ConstantSEI(BaseModel):
    """
    Class for SEI with constant concentration.

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
    """

    def __init__(self, param, options, phase="primary"):
        super().__init__(param, options=options, phase=phase)
        if self.options.electrode_types["negative"] == "planar":
            self.reaction_loc = "interface"
        else:
            self.reaction_loc = "full electrode"

    def get_fundamental_variables(self):
        # Constant concentrations
        if self.reaction_loc == "interface":
            c_inner = self.phase_param.L_inner_0 / self.phase_param.V_bar_inner
            c_outer = self.phase_param.L_outer_0 / self.phase_param.V_bar_outer
        else:
            c_inner = self.phase_param.a_typ * (
                self.phase_param.L_inner_0 / self.phase_param.V_bar_inner
            )
            c_outer = self.phase_param.a_typ * (
                self.phase_param.L_outer_0 / self.phase_param.V_bar_outer
            )
        variables = self._get_standard_concentration_variables(c_inner, c_outer)

        # Reactions
        if self.reaction_loc == "interface":
            zero = pybamm.PrimaryBroadcast(pybamm.Scalar(0), "current collector")
        else:
            zero = pybamm.FullBroadcast(
                pybamm.Scalar(0), "negative electrode", "current collector"
            )
        variables.update(self._get_standard_reaction_variables(zero, zero))

        return variables
