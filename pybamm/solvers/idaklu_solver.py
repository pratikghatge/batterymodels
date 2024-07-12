#
# Solver class using sundials with the KLU sparse linear solver
#
# mypy: ignore-errors
import casadi
import scipy
import scipy.sparse
import pybamm
import numpy as np
import scipy.sparse as sparse


import importlib

idaklu_spec = importlib.util.find_spec("pybamm.solvers.idaklu")
if idaklu_spec is not None:
    try:
        idaklu = importlib.util.module_from_spec(idaklu_spec)
        if idaklu_spec.loader:
            idaklu_spec.loader.exec_module(idaklu)
    except ImportError:  # pragma: no cover
        idaklu_spec = None


def have_idaklu():
    return idaklu_spec is not None


class IDAKLUSolver(pybamm.BaseSolver):
    """
    Solve a discretised model, using sundials with the KLU sparse linear solver.

    Parameters
    ----------
    rtol : float, optional
        The relative tolerance for the solver (default is 1e-6).
    atol : float, optional
        The absolute tolerance for the solver (default is 1e-6).
    root_method : str or pybamm algebraic solver class, optional
        The method to use to find initial conditions (for DAE solvers).
        If a solver class, must be an algebraic solver class.
        If "casadi",
        the solver uses casadi's Newton rootfinding algorithm to find initial
        conditions. Otherwise, the solver uses 'scipy.optimize.root' with method
        specified by 'root_method' (e.g. "lm", "hybr", ...)
    root_tol : float, optional
        The tolerance for the initial-condition solver (default is 1e-6).
    extrap_tol : float, optional
        The tolerance to assert whether extrapolation occurs or not (default is 0).
    output_variables : list[str], optional
        List of variables to calculate and return. If none are specified then
        the complete state vector is returned (can be very large) (default is [])
    options: dict, optional
        Addititional options to pass to the solver, by default:

        .. code-block:: python

            options = {
                # print statistics of the solver after every solve
                "print_stats": False,
                # jacobian form, can be "none", "dense",
                # "banded", "sparse", "matrix-free"
                "jacobian": "sparse",
                # name of sundials linear solver to use options are: "SUNLinSol_KLU",
                # "SUNLinSol_Dense", "SUNLinSol_Band", "SUNLinSol_SPBCGS",
                # "SUNLinSol_SPFGMR", "SUNLinSol_SPGMR", "SUNLinSol_SPTFQMR",
                "linear_solver": "SUNLinSol_KLU",
                # preconditioner for iterative solvers, can be "none", "BBDP"
                "preconditioner": "BBDP",
                # for iterative linear solvers, max number of iterations
                "linsol_max_iterations": 5,
                # for iterative linear solver preconditioner, bandwidth of
                # approximate jacobian
                "precon_half_bandwidth": 5,
                # for iterative linear solver preconditioner, bandwidth of
                # approximate jacobian that is kept
                "precon_half_bandwidth_keep": 5,
                # Number of threads available for OpenMP
                "num_threads": 1,
            }

        Note: These options only have an effect if model.convert_to_format == 'casadi'


    """

    def __init__(
        self,
        rtol=1e-6,
        atol=1e-6,
        root_method="casadi",
        root_tol=1e-6,
        extrap_tol=None,
        output_variables=None,
        options=None,
    ):
        # set default options,
        # (only if user does not supply)
        default_options = {
            "print_stats": False,
            "jacobian": "sparse",
            "linear_solver": "SUNLinSol_KLU",
            "preconditioner": "BBDP",
            "linsol_max_iterations": 5,
            "precon_half_bandwidth": 5,
            "precon_half_bandwidth_keep": 5,
            "num_threads": 1,
        }
        if options is None:
            options = default_options
        else:
            for key, value in default_options.items():
                if key not in options:
                    options[key] = value
        self._options = options

        self.output_variables = [] if output_variables is None else output_variables

        if idaklu_spec is None:  # pragma: no cover
            raise ImportError("KLU is not installed")

        base_options = {
            "num_threads": options["num_threads"],
        }

        super().__init__(
            "ida",
            rtol,
            atol,
            root_method,
            root_tol,
            extrap_tol,
            output_variables,
            base_options,
        )
        self.name = "IDA KLU solver"

        pybamm.citations.register("Hindmarsh2000")
        pybamm.citations.register("Hindmarsh2005")

    def _check_atol_type(self, atol, size):
        """
        This method checks that the atol vector is of the right shape and
        type.

        Parameters
        ----------
        atol: double or np.array or list
            Absolute tolerances. If this is a vector then each entry corresponds to
            the absolute tolerance of one entry in the state vector.
        size: int
            The length of the atol vector
        """

        if isinstance(atol, float):
            atol = atol * np.ones(size)
        elif not isinstance(atol, np.ndarray):
            raise pybamm.SolverError(
                "Absolute tolerances must be a numpy array or float"
            )

        return atol

    def set_up(self, model, inputs=None, t_eval=None, ics_only=False, batch_size=1):
        base_set_up_return = super().set_up(model, inputs, t_eval, ics_only, batch_size)
        nbatches = len(inputs) // batch_size

        if isinstance(inputs, dict):
            inputs_list = [inputs]
        else:
            inputs_list = inputs or [{}]

        inputs = self._inputs_to_stacked_vect(
            inputs_list[:batch_size], model.convert_to_format
        )

        y0 = model.y0_list[0]
        nstates = y0.shape[0]

        if model.convert_to_format != "casadi" and self.output_variables:
            raise pybamm.SolverError(
                "output_variables can only be specified "
                'with convert_to_format="casadi"'
            )  # pragma: no cover

        if ics_only:
            return base_set_up_return

        if model.convert_to_format == "jax":
            mass_matrix = model.mass_matrix.entries.toarray()
        elif model.convert_to_format == "casadi":
            if self._options["jacobian"] == "dense":
                mass_matrix = casadi.DM(model.mass_matrix.entries.toarray())
            else:
                mass_matrix = casadi.DM(model.mass_matrix.entries)
        else:
            mass_matrix = model.mass_matrix.entries

        # take care of batch size
        if model.convert_to_format == "casadi":
            mass_matrix = casadi.diagcat(*([mass_matrix] * batch_size))
        elif scipy.sparse.issparse(mass_matrix):
            mass_matrix = scipy.sparse.block_diag(
                [mass_matrix] * batch_size, format="csr"
            )
        else:
            mass_matrix = scipy.linalg.block_diag(*([mass_matrix] * batch_size))

        # construct residuals function by binding inputs
        if model.convert_to_format == "casadi":
            # TODO: do we need densify here?
            rhs_algebraic = model.rhs_algebraic_eval
        else:

            def resfn(t, y, inputs, ydot):
                return (
                    model.rhs_algebraic_eval(t, y, inputs).flatten()
                    - mass_matrix @ ydot
                )

        if not model.use_jacobian:
            raise pybamm.SolverError("KLU requires the Jacobian")

        # need to provide jacobian_rhs_alg - cj * mass_matrix
        if model.convert_to_format == "casadi":
            t_casadi = casadi.MX.sym("t")
            y_casadi = casadi.MX.sym("y", nstates)
            cj_casadi = casadi.MX.sym("cj")

            p_casadi_stacked = casadi.MX.sym("p_stacked", inputs.shape[0])

            if batch_size == 1:
                jac_times_cjmass = casadi.Function(
                    "jac_times_cjmass",
                    [t_casadi, y_casadi, p_casadi_stacked, cj_casadi],
                    [
                        model.jac_rhs_algebraic_eval(
                            t_casadi, y_casadi, p_casadi_stacked
                        )
                        - cj_casadi * mass_matrix
                    ],
                )
            else:
                # create a casadi function that can be used to evaluate the jacobian
                # for multiple inputs
                jac_times_cjmass = casadi.Function(
                    "jac_times_cjmass",
                    [t_casadi, y_casadi, p_casadi_stacked, cj_casadi],
                    [
                        model.jac_rhs_algebraic_eval(
                            t_casadi, y_casadi, p_casadi_stacked
                        )
                        - cj_casadi * mass_matrix
                    ],
                )

            jac_times_cjmass_sparsity = jac_times_cjmass.sparsity_out(0)
            jac_bw_lower = jac_times_cjmass_sparsity.bw_lower()
            jac_bw_upper = jac_times_cjmass_sparsity.bw_upper()
            jac_times_cjmass_nnz = jac_times_cjmass_sparsity.nnz()
            jac_times_cjmass_colptrs = np.array(
                jac_times_cjmass_sparsity.colind(), dtype=np.int64
            )
            jac_times_cjmass_rowvals = np.array(
                jac_times_cjmass_sparsity.row(), dtype=np.int64
            )

            v_casadi = casadi.MX.sym("v", nstates)

            jac_rhs_algebraic_action = model.jac_rhs_algebraic_action_eval

            # also need the action of the mass matrix on a vector
            mass_action = casadi.Function(
                "mass_action", [v_casadi], [casadi.densify(mass_matrix @ v_casadi)]
            )

            # if output_variables specified then convert 'variable' casadi
            # function expressions to idaklu-compatible functions
            self.var_idaklu_fcns = []
            self.dvar_dy_idaklu_fcns = []
            self.dvar_dp_idaklu_fcns = []
            for key in self.output_variables:
                # ExplicitTimeIntegral's are not computed as part of the solver and
                # do not need to be converted
                if isinstance(
                    model.variables_and_events[key], pybamm.ExplicitTimeIntegral
                ):
                    continue
                self.var_idaklu_fcns.append(
                    idaklu.generate_function(self.computed_var_fcns[key].serialize())
                )
                # Convert derivative functions for sensitivities
                if (inputs.shape[0] > 0) and (model.calculate_sensitivities):
                    self.dvar_dy_idaklu_fcns.append(
                        idaklu.generate_function(
                            self.computed_dvar_dy_fcns[key].serialize()
                        )
                    )
                    self.dvar_dp_idaklu_fcns.append(
                        idaklu.generate_function(
                            self.computed_dvar_dp_fcns[key].serialize()
                        )
                    )

        else:
            t0 = 0 if t_eval is None else t_eval[0]
            jac_y0_t0 = model.jac_rhs_algebraic_eval(t0, y0, inputs)
            if sparse.issparse(jac_y0_t0):

                def jacfn(t, y, inputs, cj):
                    j = model.jac_rhs_algebraic_eval(t, y, inputs) - cj * mass_matrix
                    return j

            else:

                def jacfn(t, y, inputs, cj):
                    jac_eval = (
                        model.jac_rhs_algebraic_eval(t, y, inputs) - cj * mass_matrix
                    )
                    return sparse.csr_matrix(jac_eval)

            class SundialsJacobian:
                def __init__(self):
                    self.J = None

                    random = np.random.random(size=nstates)
                    J = jacfn(10, random, inputs, 20)
                    self.nnz = J.nnz  # hoping nnz remains constant...

                def jac_res(self, t, y, inputs, cj):
                    # must be of form j_res = (dr/dy) - (cj) (dr/dy')
                    # cj is just the input parameter
                    # see p68 of the ida_guide.pdf for more details
                    self.J = jacfn(t, y, inputs, cj)

                def get_jac_data(self):
                    return self.J.data

                def get_jac_row_vals(self):
                    return self.J.indices

                def get_jac_col_ptrs(self):
                    return self.J.indptr

            jac_class = SundialsJacobian()

        num_of_events = len(model.terminate_events_eval)

        # rootfn needs to return an array of length num_of_events
        if model.convert_to_format == "casadi":
            rootfn = casadi.Function(
                "rootfn",
                [t_casadi, y_casadi, p_casadi_stacked],
                [
                    casadi.vertcat(
                        *[
                            event(t_casadi, y_casadi, p_casadi_stacked)
                            for event in model.terminate_events_eval
                        ]
                    )
                ],
            )
        else:

            def rootfn(t, y, inputs):
                return_root = np.array(
                    [event(t, y, inputs) for event in model.terminate_events_eval]
                ).reshape(-1)

                return return_root

        # get ids of rhs and algebraic variables
        ids = np.concatenate(
            [np.ones(model.len_rhs), np.zeros(model.len_alg)] * batch_size
        )

        number_of_sensitivity_parameters = 0
        if model.jacp_rhs_algebraic_eval is not None:
            sensitivity_names = model.calculate_sensitivities
            for name in model.calculate_sensitivities:
                inp = inputs_list[0][name]
                if isinstance(inp, np.ndarray):
                    number_of_sensitivity_parameters += inp.size
                else:
                    number_of_sensitivity_parameters += 1
        else:
            sensitivity_names = []

        if model.convert_to_format == "casadi":
            # for the casadi solver we just give it dFdp_i
            if model.jacp_rhs_algebraic_eval is None:
                sensfn = casadi.Function("sensfn", [], [])
            else:
                sensfn = model.jacp_rhs_algebraic_eval

        else:
            # for the python solver we give it the full sensitivity equations
            # required by IDAS
            def sensfn(resvalS, t, y, inputs, yp, yS, ypS):
                """
                this function evaluates the sensitivity equations required by IDAS,
                returning them in resvalS, which is preallocated as a numpy array of
                size (np, n), where n is the number of states and np is the number of
                parameters

                The equations returned are:

                 dF/dy * s_i + dF/dyd * sd_i + dFdp_i for i in range(np)

                Parameters
                ----------
                resvalS: ndarray of shape (np, n)
                    returns the sensitivity equations in this preallocated array
                t: number
                    time value
                y: ndarray of shape (n)
                    current state vector
                yp: list (np) of ndarray of shape (n)
                    current time derivative of state vector
                yS: list (np) of ndarray of shape (n)
                    current state vector of sensitivity equations
                ypS: list (np) of ndarray of shape (n)
                    current time derivative of state vector of sensitivity equations

                """

                dFdy = model.jac_rhs_algebraic_eval(t, y, inputs)
                dFdyd = mass_matrix
                dFdp = model.jacp_rhs_algebraic_eval(t, y, inputs)

                for i in range(dFdp.shape[1]):
                    resvalS[i][:] = dFdy @ yS[i] - dFdyd @ ypS[i] + dFdp[:, [i]]

        try:
            atol = model.atol
        except AttributeError:
            atol = self.atol

        rtol = self.rtol
        atol = self._check_atol_type(atol, nstates)

        if model.convert_to_format == "casadi":
            rhs_algebraic = idaklu.generate_function(rhs_algebraic.serialize())
            jac_times_cjmass = idaklu.generate_function(jac_times_cjmass.serialize())
            jac_rhs_algebraic_action = idaklu.generate_function(
                jac_rhs_algebraic_action.serialize()
            )
            rootfn = idaklu.generate_function(rootfn.serialize())
            mass_action = idaklu.generate_function(mass_action.serialize())
            sensfn = idaklu.generate_function(sensfn.serialize())

            self._setup = {
                "jac_bandwidth_upper": jac_bw_upper,
                "jac_bandwidth_lower": jac_bw_lower,
                "rhs_algebraic": rhs_algebraic,
                "jac_times_cjmass": jac_times_cjmass,
                "jac_times_cjmass_colptrs": jac_times_cjmass_colptrs,
                "jac_times_cjmass_rowvals": jac_times_cjmass_rowvals,
                "jac_times_cjmass_nnz": jac_times_cjmass_nnz,
                "jac_rhs_algebraic_action": jac_rhs_algebraic_action,
                "mass_action": mass_action,
                "sensfn": sensfn,
                "rootfn": rootfn,
                "num_of_events": num_of_events,
                "ids": ids,
                "sensitivity_names": sensitivity_names,
                "number_of_sensitivity_parameters": number_of_sensitivity_parameters,
                "output_variables": self.output_variables,
                "var_casadi_fcns": self.computed_var_fcns,
                "var_idaklu_fcns": self.var_idaklu_fcns,
                "dvar_dy_idaklu_fcns": self.dvar_dy_idaklu_fcns,
                "dvar_dp_idaklu_fcns": self.dvar_dp_idaklu_fcns,
            }

            solver = idaklu.create_casadi_solver_group(
                number_of_states=nstates,
                number_of_parameters=self._setup["number_of_sensitivity_parameters"],
                rhs_alg=self._setup["rhs_algebraic"],
                jac_times_cjmass=self._setup["jac_times_cjmass"],
                jac_times_cjmass_colptrs=self._setup["jac_times_cjmass_colptrs"],
                jac_times_cjmass_rowvals=self._setup["jac_times_cjmass_rowvals"],
                jac_times_cjmass_nnz=self._setup["jac_times_cjmass_nnz"],
                jac_bandwidth_lower=jac_bw_lower,
                jac_bandwidth_upper=jac_bw_upper,
                jac_action=self._setup["jac_rhs_algebraic_action"],
                mass_action=self._setup["mass_action"],
                sens=self._setup["sensfn"],
                events=self._setup["rootfn"],
                number_of_events=self._setup["num_of_events"],
                rhs_alg_id=self._setup["ids"],
                atol=atol,
                rtol=rtol,
                inputs=inputs.shape[0],
                var_casadi_fcns=self._setup["var_idaklu_fcns"],
                dvar_dy_fcns=self._setup["dvar_dy_idaklu_fcns"],
                dvar_dp_fcns=self._setup["dvar_dp_idaklu_fcns"],
                options=self._options,
                nsolvers=nbatches,
            )

            self._setup["solver"] = solver
        else:
            self._setup = {
                "resfn": resfn,
                "jac_class": jac_class,
                "sensfn": sensfn,
                "rootfn": rootfn,
                "num_of_events": num_of_events,
                "use_jac": 1,
                "ids": ids,
                "sensitivity_names": sensitivity_names,
                "number_of_sensitivity_parameters": number_of_sensitivity_parameters,
            }

        return base_set_up_return

    def _integrate(
        self, model, t_eval, inputs_list=None, batched_inputs=None, nproc=None
    ):
        """
        Overloads the _integrate method from BaseSolver to use the IDAKLU solver
        """

        inputs_list, batched_inputs, nbatches, batch_size, y0S_list = (
            self._handle_integrate_defaults(model, inputs_list, batched_inputs)
        )

        # do this here cause y0 is set after set_up (calc consistent conditions)
        def handle_y0(y0):
            if y0 is None:
                return y0
            if isinstance(y0, casadi.DM):
                y0 = y0.full()
            y0 = y0.flatten()
            return y0

        y0_list = [handle_y0(y0) for y0 in model.y0_list]
        batched_inputs = [handle_y0(inputs) for inputs in batched_inputs]

        # only casadi solver needs sensitivity ics
        if model.convert_to_format == "casadi" and y0S_list[0] is not None:
            # concatentate the senstivity initial conditions to the state vector
            y0S_list = [handle_y0(y0S) for y0S in y0S_list]
            y0full_list = [
                np.concatenate([y0, y0S]) for y0, y0S in zip(y0_list, y0S_list)
            ]
        else:
            y0full_list = y0_list

        # solver works with ydot0 set to zero
        ydot0full_list = [np.zeros_like(y0full) for y0full in y0full_list]

        try:
            atol = model.atol
        except AttributeError:
            atol = self.atol

        # solver takes individual y0s and ydot0s as rows in a 2d array
        y0full = np.vstack(y0full_list)
        ydot0full = np.vstack(ydot0full_list)
        inputs = np.vstack(batched_inputs)

        rtol = self.rtol
        atol = self._check_atol_type(atol, y0_list[0].size)

        timer = pybamm.Timer()
        if model.convert_to_format == "casadi":
            sols = self._setup["solver"].solve(
                t_eval,
                y0full,
                ydot0full,
                inputs,
            )
        else:
            ydot0 = np.zeros_like(y0_list[0])
            sols = []
            for y0, inputs in zip(y0_list, batched_inputs):
                inputs = inputs.reshape(-1, 1)
                sols.append(
                    idaklu.solve_python(
                        t_eval,
                        y0,
                        ydot0,
                        self._setup["resfn"],
                        self._setup["jac_class"].jac_res,
                        self._setup["sensfn"],
                        self._setup["jac_class"].get_jac_data,
                        self._setup["jac_class"].get_jac_row_vals,
                        self._setup["jac_class"].get_jac_col_ptrs,
                        self._setup["jac_class"].nnz,
                        self._setup["rootfn"],
                        self._setup["num_of_events"],
                        1,
                        self._setup["ids"],
                        atol,
                        rtol,
                        inputs,
                        self._setup["number_of_sensitivity_parameters"],
                    )
                )

        integration_time = timer.time()

        for sol in sols:
            if sol.flag not in [0, 2]:
                raise pybamm.SolverError("idaklu solver failed")

        number_of_sensitivity_parameters = self._setup[
            "number_of_sensitivity_parameters"
        ]
        sensitivity_names = self._setup["sensitivity_names"]

        pybamm_sols = []

        for i, sol in enumerate(sols):
            inputs_sublist = inputs_list[i * batch_size : (i + 1) * batch_size]
            t = sol.t
            number_of_timesteps = t.size
            number_of_states = y0_list[0].shape[0]

            sol_y = sol.y
            sol_yS = sol.yS
            if self.output_variables:
                # Substitute empty vectors for state vector 'y'
                y_out = np.zeros((number_of_timesteps * number_of_states, 0))
            else:
                y_out = sol_y.reshape((number_of_timesteps, number_of_states))

            # return sensitivity solution, we need to flatten yS to
            # (#timesteps * #states (where t is changing the quickest),)
            # to match format used by Solution
            # note that yS is (n_p, n_t, n_y)
            if number_of_sensitivity_parameters != 0:
                yS_out = {
                    name: sol_yS[i].reshape(-1, 1)
                    for i, name in enumerate(sensitivity_names)
                }
                # add "all" stacked sensitivities ((#timesteps * #states,#sens_params))
                yS_out["all"] = np.hstack([yS_out[name] for name in sensitivity_names])
            else:
                yS_out = False

            # 0 = solved for all t_eval
            if sol.flag == 0:
                termination = "final time"
            # 2 = found root(s)
            elif sol.flag == 2:
                termination = "event"

            batchsols = pybamm.Solution.from_concatenated_state(
                sol.t,
                np.transpose(y_out),
                model,
                inputs_sublist,
                np.array([t[-1]]),
                np.transpose(y_out[-1])[:, np.newaxis],
                termination,
                sensitivities=yS_out,
            )
            for s in batchsols:
                s.integration_time = integration_time
                if self.output_variables:
                    # Populate variables and sensititivies dictionaries directly
                    number_of_samples = sol_y.shape[0] // number_of_timesteps
                    sol_y = sol_y.reshape((number_of_timesteps, number_of_samples))
                    startk = 0
                    for _, var in enumerate(self.output_variables):
                        # ExplicitTimeIntegral's are not computed as part of the solver and
                        # do not need to be converted
                        if isinstance(
                            model.variables_and_events[var], pybamm.ExplicitTimeIntegral
                        ):
                            continue
                        len_of_var = (
                            self._setup["var_casadi_fcns"][var](0, 0, 0)
                            .sparsity()
                            .nnz()
                        )
                        s._variables[var] = pybamm.ProcessedVariableComputed(
                            [model.variables_and_events[var]],
                            [self._setup["var_casadi_fcns"][var]],
                            [sol_y[:, startk : (startk + len_of_var)]],
                            s,
                        )
                        # Add sensitivities
                        s[var]._sensitivities = {}
                        if model.calculate_sensitivities:
                            for paramk, param in enumerate(inputs_list[0].keys()):
                                s[var].add_sensitivity(
                                    param,
                                    [sol_yS[:, startk : (startk + len_of_var), paramk]],
                                )
                        startk += len_of_var
            pybamm_sols += batchsols
        return pybamm_sols

    def jaxify(
        self,
        model,
        t_eval,
        *,
        output_variables=None,
        calculate_sensitivities=True,
    ):
        """JAXify the solver object

        Creates a JAX expression representing the IDAKLU-wrapped solver
        object.

        Parameters
        ----------
        model : :class:`pybamm.BaseModel`
            The model to be solved
        t_eval : numeric type, optional
            The times at which to compute the solution. If None, the times in the model
            are used.
        output_variables : list of str, optional
            The variables to be returned. If None, all variables in the model are used.
        calculate_sensitivities : bool, optional
            Whether to calculate sensitivities. Default is True.
        """
        obj = pybamm.IDAKLUJax(
            self,  # IDAKLU solver instance
            model,
            t_eval,
            output_variables=output_variables,
            calculate_sensitivities=calculate_sensitivities,
        )
        return obj
