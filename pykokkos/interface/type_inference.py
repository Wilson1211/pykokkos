import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import pykokkos.kokkos_manager as km
from .execution_policy import MDRangePolicy, TeamPolicy, TeamThreadRange, RangePolicy, ExecutionPolicy
from .views import View, ViewType

@dataclass
class HandledArgs:
    """
    Class for holding the arguments passed to parallel_* functions
    """

    name: Optional[str]
    policy: ExecutionPolicy
    workunit: Callable
    view: Optional[ViewType]
    initial_value: Union[int, float]


@dataclass
class UpdatedTypes:
    """
    Class for storing inferred type annotation information 
    (Making Pykokkos more pythonic by automatically inferring types)
    """

    workunit: Callable
    inferred_types: Dict[str, str] # type information stored as string: identifier -> type
    is_arg: set[str]


def handle_args(is_for: bool, *args) -> HandledArgs:
    """
    Handle the *args passed to parallel_* functions

    :param is_for: whether the arguments belong to a parallel_for call
    :param *args: the list of arguments being checked
    :returns: a HandledArgs object containing the passed arguments
    """

    unpacked: Tuple = tuple(*args)

    name: Optional[str] = None
    policy: Union[ExecutionPolicy, int]
    workunit: Callable
    view: Optional[ViewType] = None
    initial_value: Union[int, float] = 0


    if len(unpacked) == 2:
        policy = unpacked[0]
        workunit = unpacked[1]

    elif len(unpacked) == 3:
        if isinstance(unpacked[0], str):
            name = unpacked[0]
            policy = unpacked[1]
            workunit = unpacked[2]
        elif is_for and isinstance(unpacked[2], ViewType):
            policy = unpacked[0]
            workunit = unpacked[1]
            view = unpacked[2]
        elif isinstance(unpacked[2], (int, float)):
            policy = unpacked[0]
            workunit = unpacked[1]
            initial_value = unpacked[2]
        else:
            raise TypeError(f"ERROR: wrong arguments {unpacked}")

    elif len(unpacked) == 4:
        if isinstance(unpacked[0], str):
            name = unpacked[0]
            policy = unpacked[1]
            workunit = unpacked[2]

            if is_for and isinstance(unpacked[3], ViewType):
                view = unpacked[3]
            elif isinstance(unpacked[3], (int, float)):
                initial_value = unpacked[3]
            else:
                raise TypeError(f"ERROR: wrong arguments {unpacked}")
        else:
            raise TypeError(f"ERROR: wrong arguments {unpacked}")

    else:
        raise ValueError(f"ERROR: incorrect number of arguments {len(unpacked)}")

    if isinstance(policy, int):
        policy = RangePolicy(km.get_default_space(), 0, policy)

    return HandledArgs(name, policy, workunit, view, initial_value)



def get_annotations(parallel_type: str, handled_args: HandledArgs, *args, passed_kwargs) -> UpdatedTypes:
    
    # parallel_type: A string identifying the type of parallel dispatch ("parallel_for", "parallel_reduce" ...)
    # handled_args: Processed arguments passed to the dispatch
    # args: raw arguments passed to the dispatch
    # passed_kwargs: raw keyword arguments passed to the dispatch


    param_list = list(inspect.signature(handled_args.workunit).parameters.values())
    args_list = list(*args)

    # print("\t[get_annotations] PARAM VALUES:", param_list)
    # print("\t[get_annotations] handled_args view:", handled_args.view)

    #! Should you be always setting this?
    updated_types = UpdatedTypes(workunit=handled_args.workunit, inferred_types={}, is_arg=set())
    
    policy_params: int = len(handled_args.policy.begin) if isinstance(handled_args.policy, MDRangePolicy) else 1
    # print("\t[get_annotations] POLICY PARAMS:", policy_params)
    
    # accumulator 
    if parallel_type == "parallel_reduce":
        policy_params += 1

    for i in range(policy_params):
        # Check policy type
        param = param_list[i]
        if param.annotation is inspect._empty:
            # print("\t\t[!!!] ANNOTATION IS NOT PROVIDED for policy param: ", param)

            # Check policy and apply annotation(s)
            
            if isinstance(handled_args.policy, RangePolicy) or isinstance(handled_args.policy, TeamThreadRange):
                # only expects one param
                if i == 0:
                    updated_types.inferred_types[param.name] = "int"
                    updated_types.is_arg.add(param.name)
            
            elif isinstance(handled_args.policy, TeamPolicy):
                if i == 0:
                    updated_types.inferred_types[param.name] = 'pk.TeamMember'
                    updated_types.is_arg.add(param.name)
            
            elif isinstance(handled_args.policy, MDRangePolicy):
                total_dims = len(handled_args.policy.begin) 
                if i < total_dims:
                    updated_types.inferred_types[param.name] = "int"
                    updated_types.is_arg.add(param.name)
            else:
                raise ValueError("Automatic annotations not supported for this policy")
            
            # last policy param for parallel reduce is always the accumulator; the default type is float
            if i == policy_params - 1 and parallel_type == "parallel_reduce":
                updated_types.inferred_types[param.name] = "Acc:float"
                updated_types.is_arg.add(param.name)


    if len(param_list) == policy_params:
        if not len(updated_types.inferred_types): return None
        return updated_types

    # Handle Keyword args, make sure they are treated by queing them in args
    if len(passed_kwargs.keys()):
        # add value to arguments so the value can be assessed
        for param in param_list[policy_params:]:
            if param.name in passed_kwargs:
                args_list.append(passed_kwargs[param.name])
    

    # Handling arguments other than policy args, they begin at value_idx in args list
    value_idx: int = 3 if handled_args.name != None else 2 

    assert (len(param_list) - policy_params) == len(args_list) - value_idx, f"Unannotated arguments mismatch {len(param_list) - policy_params} != {len(args_list) - value_idx}"
    
    # At this point there must more arguments to the workunit that may not have their types annotated
    # These parameters may also not have raw values associated in the stand alone format -> infer types from the parameter list


    for i in range(policy_params , len(param_list)):
        # Check policy type
        param = param_list[i]
        if param.annotation is inspect._empty:
            # print("\t\t[!!!] ANNOTATION IS NOT PROVIDED PARAM", param)

            value = args_list[value_idx+i-policy_params]

            param_type = type(value).__name__

            if isinstance(value, View):
                param_type = "View"+str(len(value.shape))+"D:"+str(value.dtype.name)

            updated_types.inferred_types[param.name] = param_type
            updated_types.is_arg.add(param.name)

    if not len(updated_types.inferred_types): return None
    # print("RETURNING UPDATED TYPES", updated_types)

    return updated_types