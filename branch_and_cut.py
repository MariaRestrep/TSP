import traceback
import numpy

from grammars_2 import *
from docplex.mp.model import Model
from docplex.mp.callbacks.cb_mixin import *
from cplex.callbacks import LazyConstraintCallback
from timer import Timer

ti = Timer()

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
min_pattern_length = 32
max_pattern_length = 40

class BacCallback(ConstraintCallbackMixin, LazyConstraintCallback):
    def __init__(self, env):
        LazyConstraintCallback.__init__(self, env)
        ConstraintCallbackMixin.__init__(self)

    def __call__(self):
        ti.enter_callback()
        self.nb_callback += 1
        # print("\n Entering Callback")
        # print("Current value ", self.get_objective_value())
        # print("Best", self.get_best_objective_value())
        # print("Gap", self.get_MIP_relative_gap())

        # Get current solution
        val = self.get_values()
        x = {}
        for k in self.param.x_idx:
            var_x = self.x[k]
            id = var_x.id
            x[var_x.idx] = val[id]
        y = {}
        for k in self.param.y_idx:
            var_y = self.y[k]
            id = var_y.id
            y[var_y.idx] = val[id]
            self.y_core[k] = 0.5 * self.y_core[k] + 0.5 * val[id]
        theta = {}
        for k in self.param.theta_idx:
            var_theta = self.theta[k]
            id = var_theta.id
            theta[var_theta.idx] = val[id]

        mu = self.param.mu
        dem = self.param.d
        dual_objectives = {}
        cuts_generated = 0

        #solve subproblems
        for k in self.sub_problems:
            d, i, w, p = k
            dual_sol, dual_z = solve_sub_problem_sort_core(self.param, d, i, w, p, theta[k], y, self.y_core)
            dual_objectives[k] = dual_z
            # add the constraint if the value is above the threshold
            if dual_z > 0.0 and (dual_z - theta[k])/dual_z > self.param.cut_tolerance:
                proba = self.param.proba[(d, i, w)]
                dual_cap = sum(mu[(c, d, i, w, p)]*dual_sol[c]*self.y[(c, d, i, p)] for c in self.param.C)
                dual_dem = dem[(d, i, w, p)]*dual_sol[len(self.param.C)]
                ct = (self.theta[k] >= proba*(dual_cap+dual_dem))
                lhs, sense, rhs = self.linear_ct_to_cplex(ct)
                self.add(lhs, sense, rhs)
                cuts_generated+=1
        #print("{} cut generated".format(cuts_generated))

        rec_upper = compute_rec_upper(self.param, dual_objectives)
        cost = 0.0
        for c in self.param.C:
            for t in self.param.Tc[c]:
                cost += self.param.fixed_costs[(c, t)] * x[(c, t)]
        if cost + rec_upper < self.z:
            self.z = cost + rec_upper
        #print("Leaving callback")
        ti.leave_callback()

# define the master problem wrt to the parameters given
def define_master(param, grammar_graph, grammar):

    #print("Defining the master problem")
    ti.start_defining()

    model = Model("Master problem")

    model.iter = 0

    relax_x_variables = False

    # sets
    model.days = set(range(len(DAYS)))
    model.employees = param.C
    model.shifts = set(range(len(param.data.shifts)))

    # print(model.shifts, model.days, model.employees)


    # define the variables
    if(grammar == False):
        param.x_idx = [(c, t) for c in param.C for t in param.Tc[c]]
    else:
        param.x_idx = [(c, d, s) for c in param.C for d in model.days for s in model.shifts]

    param.y_idx = [(c, d, i, p) for c in param.C for d in param.D for i in param.I for p in param.P]
    param.theta_idx = [(d, i, w, p) for d in param.D for i in param.I for w in param.Omega_i_d[d][i] for p in param.P]


    v_idx = [(c, d, i, w, p) for c in param.C for d in param.D for i in param.I for w in param.Omega_i_d[d][i] for p in
             param.P]
    e_idx = [(d, i, w, p) for d in param.D for i in param.I for w in param.Omega_i_d[d][i] for p in param.P]

    model.v = model.integer_var_dict(v_idx, name="V")
    model.e = model.continuous_var_dict(e_idx, name="E")

    v = model.v
    e = model.e

    for var in model.iter_variables():
        var_type = var.name[0]
        if var_type == "V": var.type = "V"
        if var_type == "E": var.type = "E"


    #print("Defining Master variables")
    if relax_x_variables:
        #model.x_var = model.continuous_var_cube(keys1=model.employees, keys2=model.days, keys3=model.shifts,name="x_var")
        model.x = model.continuous_var_dict(param.x_idx, lb=0.0, ub=1.0, name="X")

    else:
        model.x = model.binary_var_dict(param.x_idx, name="X")
        #model.x_var = model.binary_var_cube(keys1=model.employees, keys2=model.days, keys3=model.shifts, name="x_var")

    model.y = model.continuous_var_dict(param.y_idx, lb=0.0, ub=1.0, name="Y")
    model.theta = model.continuous_var_dict(param.theta_idx, lb=0.0, name="Theta")

    id = 0
    if grammar:
        for c, d, s in param.x_idx:
            var = model.x[(c, d, s)]
            var.idx = (c, d, s)
            var.type = "X"
            var.id = id
            id+=1
    else:
        for c, t in param.x_idx:
            var = model.x[(c, t)]
            var.idx = (c, t)
            var.type = "X"
            var.id = id
            id+=1

    for c, d, i, p in param.y_idx:
        var = model.y[(c, d, i, p)]
        var.idx = (c, d, i, p)
        var.type = "Y"
        var.id = id
        id+=1

    for d, i, w, p in param.theta_idx:
        var = model.theta[(d, i, w, p)]
        var.idx = (d, i, w, p)
        var.type = "Theta"
        var.id = id
        id+=1

    # x = model.x
    y = model.y
    theta = model.theta

    ### CONSTRAINTS ###

    if grammar:

        for c in model.employees:
            build_mip_component_from_dag(model, grammar_graph[c], param.data, c, 1, True)

        # print("afer building mip component from DAG")

        # min and max working time per employee per week
        for c in model.employees:
            model.add_constraint(model.sum(
                param.data.shifts[s].workTime * model.x[c, d, s] for d in model.days for s in model.shifts) <= max_pattern_length)
            model.add_constraint(model.sum(
                param.data.shifts[s].workTime * model.x[c, d, s] for d in model.days for s in model.shifts) >= min_pattern_length)

        # one o-d pair per time period (if working)
        for c in param.C:
            for d in param.D:
                for t in param.I:
                    model.add_constraint(model.sum(y[c, d, t, p] for p in param.P) == model.sum(
                        param.data.shifts[s].shift[t] * model.x[c, d, s] for s in model.shifts))


    else:

        # constraint (2) each courier is affected to exactly one shift
        print("Defining Master constraint 2")
        for c in param.C:
            model.add_constraint(model.sum(x[c, t] for t in param.Tc[c]) == 1,
                                 "constraint_2_{}".format(c))

        # constraint (3) the courier is affected in an od-pair (two districts) during a working period
        print("Defining Master constraint 3")
        for c in param.C:
            for d in param.D:
                for i in param.I:
                    #model.add_constraint(
                    #    (model.sum(y[(c, d, i, p)] for p in param.P) - (model.sum(param.delta_d[(c, d, i, t)] * x[(c, t)] for t in param.Tc[c]))) == 0,
                    #    "constraint_3_{}_{}_{}".format(c, d, i))

                    #TODO: check this comment
                    # if param.full_flexibility:  # if full flexibility, delta does not depend on c
                    #     delta=lambda t: param.delta_d[(d, i, t)]
                    # else:

                    delta=lambda t: param.delta_d[(c, d, i, t)]
                    model.add_constraint((model.sum(y[(c, d, i, p)] for p in param.P) -
                        (model.sum(delta(t) * x[(c, t)] for t in param.Tc[c]))) == 0,
                        "constraint_3_{}_{}_{}".format(c, d, i))

    # print("Model defined")
    #
    #
    # # constraint (5) constraint limiting the number of packages that an internal courier can deliver
    # for c in param.C:
    #     for d in param.D:
    #         for i in param.I:
    #             for w in param.Omega_i_d[d][i]:
    #                 for p in param.P:
    #                     model.add_constraint(
    #                         (v[(c, d, i, w, p)] - (param.mu[(c, d, i, w, p)] * y[(c, d, i, p)])) <= 0,
    #                         "constraint_5_{}_{}_{}_{}_{}".format(c, d, i, w, p))
    #
    # total_demand=0
    # # constraint (6) constraint for the demand satisfaction
    # for d in param.D:
    #     for i in param.I:
    #         for w in param.Omega_i_d[d][i]:
    #             for p in param.P:
    #                 model.add_constraint(
    #                     (model.sum(v[(c, d, i, w, p)] for c in param.C) + e[(d, i, w, p)]) == param.d[(d, i, w, p)],
    #                     "constraint_6_{}_{}_{}_{}".format(d, i, w, p))
    #                 #print(d,i,p,w,param.d[(d, i, w, p)])
    #                 total_demand+=param.d[(d, i, w, p)]
    #
    # print("total demand", total_demand)
    # ti.end_defining()

    # exit(1)

    ### OBJECTIVE ###
    if grammar:
        model.fixed_cost = model.sum(param.data.shifts[s].cost * model.x[c, d, s] for c in model.employees for d in model.days
                                     for s in model.shifts)
        # print("enter here")
    else:
        model.fixed_cost = model.sum(
            model.sum(param.fixed_costs[(c, t)] * model.x[(c, t)] for t in param.Tc[c]) for c in param.C)

    model.theta_cost = model.sum(theta[k] for k in param.theta_idx)
    model.minimize(model.fixed_cost + model.theta_cost)

    # print("start solving")


    # ### OBJECTIVE ###
    #
    # model.scenario_cost = model.sum(model.sum(model.sum(param.proba[(d, i, w)] * (
    #         model.sum(model.sum(param.l[(c, i, p)] * v[(c, d, i, w, p)] for p in param.P) for c in param.C)
    #         + model.sum(param.c[(i, p)] * e[(d, i, w, p)] for p in param.P)
    # ) for w in param.Omega_i_d[d][i]) for i in param.I) for d in param.D)

    # model.minimize(model.fixed_cost + model.scenario_cost)
    #
    # model.solve(log_output=True)
    #
    # cost_shifts = 0
    # lenght_tour = 0
    # enter = False
    #
    # print("*************************** Solution ***************************")
    # if grammar:
    #     print("Shift allocation:")
    #     for c in param.C:
    #         lenght_tour = 0
    #         print("Employee: ", c, end='[ ')
    #         for d in param.D:
    #             enter = False
    #             for s in model.shifts:
    #                 if (model.x[c, d, s].solution_value > 0):
    #                     enter = True
    #                     # print("employee ", c, " lenght of shift ", data.shifts[s].workTime, " shift: ", s, " day: ", d)
    #                     print(s, end=',')
    #                     lenght_tour += param.data.shifts[s].workTime
    #                     cost_shifts += param.data.shifts[s].cost
    #             if enter == False:
    #                 print("r", end=',')
    #         print("] lenght tour: ", lenght_tour)
    # else:
    #     for c in param.C:
    #         for t in param.Tc[c]:
    #             if (model.x[c, t].solution_value > 0):
    #                 cost_shifts += param.fixed_costs[(c, t)]
    #
    # print("shift allocation cost: ", cost_shifts)
    #
    # cost_package_allocation = 0
    # print("Package allocation:")
    # for c in param.C:
    #     for d in param.D:
    #         for t in param.I:
    #             for p in param.P:
    #                 # if (y[c, d, t, p].solution_value > 0):
    #                 #     print("value of y: ",c, d, t, " p ", p, y[c, d, t, p].solution_value)
    #
    #                 for w in param.Omega_i_d[d][t]:
    #                     if (v[c, d, t, w, p].solution_value > 0):
    #                         # if(d==0 and t==0):
    #                         #     print("package allocation", c, d, t, " p ", p, w, v[c, d, t, w, p].solution_value)
    #
    #                         cost_package_allocation += v[c, d, t, w, p].solution_value * \
    #                                                    param.l[(c, t, p)]* \
    #                                                    param.proba[(d, t, w)]
    #
    # print("full time package allocation cost: ", cost_package_allocation)
    #
    # #exit(1)
    #
    # cost_external_allocation = 0
    #
    # print("Package allocation external:")
    # for d in param.D:
    #     for t in param.I:
    #         for p in param.P:
    #             for w in param.Omega_i_d[d][t]:
    #                 # if d==0:
    #                 #     print(d, t, p, w, e[d, t, w, p].solution_value, param.proba[(d, t, w)])
    #                 if (e[d, t, w, p].solution_value > 0):
    #                     # print(d, t, p, w, model.e_var[d, t, p, w].solution_value)
    #
    #                     cost_external_allocation += e[d, t, w, p].solution_value * param.c[(t, p)] * \
    #                                                 param.proba[(d, t, w)]
    #                     # if d==0:
    #                     #     cost_external_allocation_1 += e[d, t, w, p].solution_value * param.c[(t, p)] *param.proba[(d, t, w)]
    #
    # print("external cost: ", cost_external_allocation)
    #
    # exit(1)

    return model

# return the master solution: x, y, theta variables
def master_solution(master):
    x = {}
    y = {}
    theta = {}
    for var in master.iter_variables():
        try:
            var_type = var.type
        except:
            var_type = ""
        k = var.get_key()
        if var_type == "X":
            x[k] = var.solution_value
        if var_type == "Y":
            y[k] = var.solution_value
        if var_type == "Theta":
            theta[k] = var.solution_value
    return x, y, theta

# return the costs decomposed into fixed cost and theta costs
def decompose_costs(param, problem):
    fixed_cost=theta_cost=0
    problem.x_sol={}
    problem.y_sol={}
    for var in problem.iter_variables():
        if var.type=="X":
            c, t = var.get_key()
            problem.x[(c, t)]=var.solution_value
            fixed_cost+=var.solution_value*param.fixed_costs[(c, t)]
        if var.type=="Y":
            c, d, i, p = var.get_key()
            problem.y[(c, d, i, p)]=var.solution_value
        if var.type=="Theta":
            theta_cost+=var.solution_value
    return fixed_cost, theta_cost

# solve sub-problem primal to get the second-stage solution of sub-problem (d, i, w, p)
def solve_subproblem_primal(param, d, i, w, p, y):
    ext=param.c[(i, p)]
    var=[]
    cap=[]
    for c in param.C:
        var.append(param.l[(c, i, p)])
        cap.append(param.mu[(c, d, i, w, p)]*y[(c, d, i, p)])
    inc_cost_couriers=numpy.argsort(var)
    remaining_dem=param.d[(d, i, w, p)]
    v_sol=[0]*len(param.C)
    obj=0
    for c in inc_cost_couriers:
        v_sol[c]=min(remaining_dem, cap[c])
        remaining_dem-=v_sol[c]
        obj+=v_sol[c]*var[c]
    e_sol=remaining_dem
    obj+=e_sol*ext
    return v_sol, e_sol, obj

# decompose the second stage costs for all sub-problems
def decompose_subproblems_costs(param, problem, subproblems):
    x, y, theta = master_solution(problem)
    v_dict={}
    e_dict={}
    v_cost=e_cost=0
    for k in subproblems:
        d, i, w, p = k
        v, e, obj = solve_subproblem_primal(param, d, i, w, p, y)
        v_dict[k]=v
        e_dict[k]=e
        proba=param.proba[(d, i, w)]
        for c in param.C:
            v_cost+=proba*v[c]*param.l[(c, i, p)]
        e_cost+=proba*e*param.c[(i, p)]
    problem.v_sol=v_dict
    problem.e_sol=e_dict
    return v_cost, e_cost

# add optimality cut to master problem for sub-problem k=(d, i, w, p)
def add_optimality_cut(param, problem, k, dual_x, dual_z, theta):
    d, i, w, p = k
    mu = param.mu; dem = param.d[(d, i, w, p)]
    proba = param.proba[(d, i, w)]
    a = sum(mu[(c, d, i, w, p)]*dual_x[c]*problem.y[(c, d, i, p)] for c in param.C)
    b = dem*dual_x[len(param.C)]
    rhs = proba*(a+b)
    res=False
    if dual_z >0.0 and (dual_z - theta.solution_value)/dual_z > param.cut_tolerance:
        ct = (theta >= rhs)
        #print("Add cut:", ct)
        problem.add_constraint(ct, "cut_{}_{}_{}_{}_{}".format(problem.iter, d, i, w, p))
        res=True
    return res

def update_y_core(y_core, y):
    new={}
    for k in y_core.keys():
        new[k] = 0.5*y[k] + 0.5*y_core[k]
    return new

def compute_rec_upper(param, dual_objectives):
    total = 0.0
    for k in dual_objectives.keys():
        d, i, w, p = k
        proba = param.proba[(d, i, w)]
        obj = dual_objectives[k]
        total += proba*obj
    return total

def compute_rec_lower(problem):
    total = 0.0
    theta = problem.theta
    for k in theta.keys():
        total += theta[k]\
            .solution_value
    return total


# function to solve the branch and cut
# parameters: param object from class model_parameters_weekly, timeout, tolerance, absolute tolerance, epsilon tolerance,
# fix first stage is used to solve from a first stage solution (ater the mean value resolution for example),
# decomp_costs enable the decomposition of theta costs into variable and external costs

#TODO: verify with a simple instance
def solve_bac(param, grammar_graph, grammar, log_output=False, timeout=1800, obj_tolerance=1e-03, abs_obj_tolerance=1e-05, epsilon_tolerance=0.05,
              fix_first_stage=False, first_stage_x=None, first_stage_y=None, decomp_costs=False):

    # create timer object
    ti=Timer()
    ti.start_resolution()

    # define the master problem
    ti.start_defining()

    problem = define_master(param, grammar_graph, grammar)
    ti.end_defining()

    # fix the values (bounds) of the first stage variables (used to compute the VSS from a mean value solution)
    if fix_first_stage:
        if first_stage_x is None or first_stage_y is None:
            #print("First stage values are not valid, fix first stage ignored")
            raise Exception("Invalid First stage values ")
        else:
            for var in problem.iter_variables():
                if var.type == "X":
                    c,t=var.get_key()
                    var.lb=var.ub = first_stage_x[(c, t)]
                if var.type == "Y":
                    c, d, i, p= var.get_key()
                    var.lb=var.ub = first_stage_y[(c, d, i, p)]
            #print("First stage variables have been fixed")

    problem.x_sol={}
    problem.iter = 0

    # sub-problem indexes (for each day, each period, each scenario, each od-pair)
    sub_problems = [(d, i, w, p) for d in param.D for i in param.I for w in param.Omega_i_d[d][i] for p in param.P
                    if param.d[(d, i, w, p)] > 0.0]
    #print("Nb sub problems:", len(sub_problems))

    rec_lower = -1
    rec_upper = pow(2.0, 31)

    param.cut_tolerance = 0.001
    tolerance = epsilon_tolerance # theta variables approximation tolerance, for the mcDaniel and Devine method

    #print("Start approximating Theta variables")
    ti.start_first_phase()

    init_y_core = True

    # solve the master problem
    problem.solve(log_output=False)

    #print("Relaxation solved with solution value {}".format(problem.objective_value))

    #Add optimality cuts to the linear relaxation of the master problem
    #TODO: Check the convergence of the method (how does thelower bound increases)

    while (rec_upper - rec_lower)/rec_upper > tolerance:
        #print("Gap {}".format((rec_upper - rec_lower)/rec_upper))

        # get the solution of the master
        x, y, theta = master_solution(problem)

        if init_y_core:
            init_y_core = False
            y_core = y
        else:
            y_core = update_y_core(y_core, y)

        dual_objectives = {}

        #print("Start generating cuts")
        nb_cuts = 0
        for k in sub_problems:
            # solve the problem
            d, i, w, p = k
            if param.d[(d, i, w, p)] > 0.0:
                dual_x, dual_z = solve_sub_problem_sort_core(param, d, i, w, p, theta[k], y, y_core)
                dual_objectives[k] = dual_z
                added = add_optimality_cut(param, problem, k, dual_x, dual_z, problem.theta[k])
                if added: nb_cuts += 1
            else:
                dual_objectives[k] = 0.0
        #print("Stop generating cuts ({} cuts generated)".format(nb_cuts))

        problem.solve(log_output=False)
        #print("Relaxation solved with solution value", problem.objective_value)

        problem.iter += 1

        rec_upper = compute_rec_upper(param, dual_objectives)
        #print("rec upper", rec_upper)
        rec_lower = compute_rec_lower(problem)
        #print("rec lower", rec_lower)

    #print("First phase stopped with relaxed value", problem.objective_value); ti.end_first_phase()

    # add integrality constraints to y variables
    x_var = []
    for var in problem.iter_variables():
        try:
            var_type = var.type
        except:
            var_type = ""
        if var_type == "Y":
            var.set_vartype("B")
        if var_type == "X":
            if var.solution_value >= 0.99:
                x_var.append(var.get_key())

    # define callback
    cb = problem.register_callback(BacCallback)
    cb.y_core = y_core
    cb.nb_callback = -1
    cb.last_solution = 0.0
    cb.x = problem.x
    cb.y = problem.y
    cb.theta = problem.theta
    cb.param = param
    cb.problem = problem
    cb.sub_problems = sub_problems
    cb.z = pow(2, 31)

    #print("Starting the branch and bound resolution")

    # set Tolerance
    problem.parameters.mip.tolerances.mipgap.set(obj_tolerance)
    problem.parameters.mip.tolerances.absmipgap.set(abs_obj_tolerance)

    use_param=False # use selected parameters

    # parameters
    #TODO: Check this
    if use_param:
        problem.parameters.mip.cuts.gomory=2
        integrality_tolerance = 1e-04
        problem.parameters.mip.tolerances.integrality = integrality_tolerance
        problem.parameters.emphasis.mip = 4 # generate gomory cuts aggressively
        problem.parameters.mip.cuts.mircut = 2 # generate rounding cuts aggressively
        problem.parameters.mip.strategy.nodeselect = 1 # node select after backtrack (0:DFS, 1:BoundS, 2:EstimateS, 3:AEst)
        problem.parameters.mip.strategy.probe = 1 # from 0 to 3
        problem.parameters.mip.strategy.rinsheur = 10
        problem.parameters.preprocessing.symmetry = -1 # from -1 to 5
        problem.parameters.mip.strategy.search = 1 # only classical B&C / no dynamic search because of the use of control callbacks

    problem.set_time_limit(timeout)
    ti.start_branch_and_cut()
    problem.solve(log_output=False)
    ti.end_branch_and_cut()
    ti.end_resolution()

    problem.nb_callback=cb.nb_callback
    try:
        problem.fix, problem.the = decompose_costs(param, problem)
        #print("Fix:", problem.fix)
        #print("Theta:", problem.the)
    except Exception as e:
        #print("Error while decomposing costs")
        #print(e)
        problem.fix=problem.the=-1
    if decomp_costs:
        try:
            var, ext = decompose_subproblems_costs(param, problem, sub_problems)
            problem.v_cost=var
            problem.e_cost=ext
            #print("VAR:", var)
            #print("EXT:", ext)
        except Exception as e:
            #print("Error while decomposing theta costs")
            #print(e)
            print(traceback.format_exc())
    try:
        print("Objective:", problem.objective_value)
    except:
        print("Problem did not solved successfully")
    try:
        #print("computing nb working couriers")
        nb_working_couriers=0
        problem.nb_working_couriers = nb_working_couriers
        # for var in problem.iter_variables():
        #     if var.name[0] == "X" and var.solution_value>=0.999:
        #         nb_working_couriers+=1
        # print(nb_working_couriers, "Working couriers")
        # problem.nb_working_couriers=nb_working_couriers
        #
        # print(nb_working_couriers)
        # exit(1)
    except:
        pass
    #ti.print_ti()
    problem.timer=ti
    return problem

def solve_sub_problem_sort_core(param, d, i, w, p, theta, y, y_core):

    TOLERANCE = 0.001
    dem_accum = 0
    mu=param.mu; dem=param.d[(d, i, w, p)]; l_cost=param.l; c_cost=param.c[(i, p)]; proba=param.proba[(d, i, w)]
    dual_d = 0; lamb=0; slack=False
    obj = 0
    couriers = [j for j in range(len(param.C))]
    courier_cost = lambda c: param.l[(c, i, p)]
    couriers.sort(key=courier_cost)
    dual_cap = [0]*len(couriers)

    for c in couriers:
        min_dem = min(dem-dem_accum, mu[(c, d, i, w, p)]*y[(c, d, i, p)])
        dem_accum+= min_dem
        obj+= min_dem*l_cost[(c, i, p)]

    if dem_accum < dem:
        obj += (dem-dem_accum)*c_cost

    dual_obj = obj
    if (dual_obj*proba) != 0:
        test = (dual_obj*proba - theta)/(dual_obj*proba)
    else:
        test = 0
    if test > TOLERANCE:
        dem_accum=0
        for c in couriers:
            min_dem = min(dem - dem_accum, mu[(c, d, i, w, p)] * y_core[(c, d, i, p)])
            dem_accum+=min_dem
            obj+=min_dem*l_cost[(c, i, p)] # why obj is not set to 0 before

            if mu[(c, d, i, w, p)]*y_core[(c, d, i, p)] - min_dem > TOLERANCE: # why not y_core
                slack=True
                lamb=l_cost[(c, i, p)]
                dual_cap[c]=0.0
                break
            if not slack:
                dual_cap[c] = -(c_cost - l_cost[(c, i, p)])

        if dem_accum < dem:
            obj+= (dem-dem_accum)*c_cost

        if not slack:
            lamb=c_cost
        else:
            for c in couriers:
                if l_cost[(c, i, p)] < lamb:
                    dual_cap[c]=l_cost[(c, i, p)]-lamb
                else:
                    dual_cap[c]=0

    u=lamb
    dual_cap.append(u)
    return dual_cap, dual_obj




