from __future__ import annotations

import importlib.util
import os
import pickle
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, Optional


class StrategyLoadError(Exception):
    """Exception raised while loading or invoking a custom strategy file."""


_KIND_CONFIG = {
    "routing": {
        "factory_names": [
            "create_routing_strategy",
            "create_strategy",
            "get_strategy",
            "load_strategy",
        ],
        "attribute_names": [
            "ROUTING_STRATEGY",
            "routing_strategy",
            "strategy",
        ],
        "class_names": [
            "RoutingStrategy",
            "Strategy",
        ],
        "function_names": [
            "get_route",
            "route",
        ],
    },
    "assignment": {
        "factory_names": [
            "create_assignment_strategy",
            "create_strategy",
            "get_strategy",
            "load_strategy",
        ],
        "attribute_names": [
            "ASSIGNMENT_STRATEGY",
            "assignment_strategy",
            "strategy",
        ],
        "class_names": [
            "AssignmentStrategy",
            "Strategy",
        ],
        "function_names": [
            "select",
            "dispatch",
        ],
    },
    "idle_positioning": {
        "factory_names": [
            "create_idle_positioning_strategy",
            "create_strategy",
            "get_strategy",
            "load_strategy",
        ],
        "attribute_names": [
            "IDLE_POSITIONING_STRATEGY",
            "idle_positioning_strategy",
            "strategy",
        ],
        "class_names": [
            "IdlePositioningStrategy",
            "Strategy",
        ],
        "function_names": [
            "plan_reposition",
            "pick_target",
        ],
    },
    "routing_cost": {
        "factory_names": [
            "create_routing_cost_function",
            "create_cost_function",
            "create_strategy",
            "get_strategy",
            "load_strategy",
        ],
        "attribute_names": [
            "ROUTING_COST_FUNCTION",
            "routing_cost_function",
            "cost_function",
            "strategy",
        ],
        "class_names": [
            "RoutingCostFunction",
            "CostFunction",
            "Strategy",
        ],
        "function_names": [
            "compute_cost",
            "calculate_cost",
            "cost",
            "__call__",
        ],
    },
}


def describe_strategy(strategy: Any) -> str:
    """Strategy name to display in log/status messages."""
    if strategy is None:
        return "Default"
    cls = getattr(strategy, "__class__", None)
    if cls is None:
        return repr(strategy)
    name = getattr(cls, "__name__", str(cls))
    module = getattr(cls, "__module__", "")
    if module in ("builtins", "__main__", ""):
        return name
    return f"{module}.{name}"


def load_strategy(path: str, kind: str) -> Any:
    """
    Load a .py / .pkl / .pickle strategy file.

    For Python files the entry point is searched in the following order.
      1) create_*_strategy(), create_strategy(), get_strategy(), load_strategy()
      2) a *_strategy or strategy variable
      3) a *Strategy class
      4) a kind-specific function (get_route/select/plan_reposition, etc.)
    """
    if kind not in _KIND_CONFIG:
        raise StrategyLoadError(f"Unsupported strategy kind: {kind}")

    normalized = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(normalized):
        # Look once more in the user strategy folder (<repo>/strategies/).
        from ufast.paths import REPO_ROOT
        candidate = os.path.join(REPO_ROOT, "strategies", path)
        if os.path.exists(candidate):
            normalized = candidate
        else:
            raise StrategyLoadError(
                f"File not found: {normalized} (also not in the strategies/ folder)")

    ext = Path(normalized).suffix.lower()
    if ext == ".py":
        module = _load_module_from_py(normalized)
        strategy = _extract_strategy_from_module(module, kind)
    elif ext in {".pkl", ".pickle"}:
        with open(normalized, "rb") as f:
            strategy = pickle.load(f)
    else:
        raise StrategyLoadError("Only .py, .pkl, and .pickle files are supported.")

    if strategy is None:
        raise StrategyLoadError(f"Failed to create the strategy object: {normalized}")
    return strategy


def _load_module_from_py(path: str) -> ModuleType:
    module_name = f"custom_strategy_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise StrategyLoadError(f"Failed to load Python module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _extract_strategy_from_module(module: ModuleType, kind: str) -> Any:
    cfg = _KIND_CONFIG[kind]

    for factory_name in cfg["factory_names"]:
        factory = getattr(module, factory_name, None)
        if callable(factory):
            created = factory()
            if created is not None:
                return created

    for attr_name in cfg["attribute_names"]:
        if hasattr(module, attr_name):
            value = getattr(module, attr_name)
            if value is not None:
                return value

    for cls_name in cfg["class_names"]:
        cls = getattr(module, cls_name, None)
        if isinstance(cls, type):
            return cls()

    for fn_name in cfg["function_names"]:
        fn = getattr(module, fn_name, None)
        if callable(fn):
            return fn

    raise StrategyLoadError(
        f"Could not find a {kind} strategy entry point in the module. "
        f"Use a factory function, a strategy variable, a Strategy class, or the designated function name."
    )


def _call_variants(func, call_specs: list[tuple[tuple[Any, ...], dict[str, Any]]]) -> Any:
    """
    Try the argument combinations a user strategy function may accept, in order.
    On TypeError, the next signature is tried.
    """
    last_error: Optional[Exception] = None
    for args, kwargs in call_specs:
        try:
            return func(*args, **kwargs)
        except TypeError as e:
            last_error = e
            continue
    if last_error is not None:
        raise last_error
    return None


def invoke_assignment_strategy(
    strategy: Any,
    target_section_id: int,
    idle_ohts: list[Any],
    route_manager: Any,
    bridge: Any,
    vehicle_controller: Any,
) -> Any:
    """Invoke the assignment strategy. The return value may be an OHT object or an OHT name."""
    if strategy is None:
        return None

    func = getattr(strategy, "select", None)
    if not callable(func):
        func = getattr(strategy, "dispatch", None)
    if not callable(func) and callable(strategy):
        func = strategy
    if not callable(func):
        raise StrategyLoadError("An assignment strategy must have a select()/dispatch() method or be callable.")

    return _call_variants(func, [
        ((), {
            "target_section_id": target_section_id,
            "idle_ohts": idle_ohts,
            "route_manager": route_manager,
            "bridge": bridge,
            "vehicle_controller": vehicle_controller,
        }),
        ((target_section_id, idle_ohts, route_manager, bridge, vehicle_controller), {}),
        ((target_section_id, idle_ohts, route_manager, bridge), {}),
        ((target_section_id, idle_ohts, vehicle_controller), {}),
        ((target_section_id, idle_ohts), {}),
    ])


def invoke_routing_strategy(
    strategy: Any,
    from_sec_id: int,
    to_sec_id: int,
    vehicle_controller: Any,
) -> Any:
    """Invoke the routing strategy. The return value may be a section id path, or a dict/tuple."""
    if strategy is None:
        return None

    func = getattr(strategy, "get_route", None)
    if not callable(func):
        func = getattr(strategy, "route", None)
    if not callable(func) and callable(strategy):
        func = strategy
    if not callable(func):
        raise StrategyLoadError("A routing strategy must have a get_route()/route() method or be callable.")

    return _call_variants(func, [
        ((), {
            "from_sec_id": from_sec_id,
            "to_sec_id": to_sec_id,
            "vehicle_controller": vehicle_controller,
        }),
        ((from_sec_id, to_sec_id, vehicle_controller), {}),
        ((from_sec_id, to_sec_id), {}),
    ])


def invoke_node_routing_strategy(
    strategy: Any,
    from_node: str,
    to_node: str,
    route_manager: Any,
    bridge: Any,
) -> Any:
    """Invoke a ufast routing strategy that returns a node path."""
    if strategy is None:
        return None

    func = getattr(strategy, "get_route", None)
    if not callable(func):
        func = getattr(strategy, "route", None)
    if not callable(func) and callable(strategy):
        func = strategy
    if not callable(func):
        raise StrategyLoadError(
            "Routing strategy must provide get_route()/route() or be callable."
        )

    return _call_variants(func, [
        ((), {
            "from_node": from_node,
            "to_node": to_node,
            "route_manager": route_manager,
            "bridge": bridge,
        }),
        ((from_node, to_node, route_manager, bridge), {}),
        ((from_node, to_node, route_manager), {}),
        ((from_node, to_node), {}),
    ])


def invoke_idle_positioning_strategy(
    strategy: Any,
    oht: Any,
    current_time: float,
    current_node: str,
    route_manager: Any,
    bridge: Any,
    event_handler: Any,
    vehicle_controller: Any,
) -> Any:
    """Invoke the idle positioning strategy. The return value may be a target section/node or a section path."""
    if strategy is None:
        return None

    func = getattr(strategy, "plan_reposition", None)
    if not callable(func):
        func = getattr(strategy, "pick_target", None)
    if not callable(func) and callable(strategy):
        func = strategy
    if not callable(func):
        raise StrategyLoadError(
            "An idle positioning strategy must have a plan_reposition()/pick_target() method or be callable."
        )

    return _call_variants(func, [
        ((), {
            "oht": oht,
            "current_time": current_time,
            "current_node": current_node,
            "route_manager": route_manager,
            "bridge": bridge,
            "event_handler": event_handler,
            "vehicle_controller": vehicle_controller,
        }),
        ((oht, current_time, current_node, route_manager, bridge, event_handler, vehicle_controller), {}),
        ((oht, current_time, current_node, route_manager, bridge), {}),
        ((oht, current_node, route_manager, bridge), {}),
        ((oht,), {}),
    ])


def normalize_oht_selection(selection: Any, oht_dict: dict[str, Any]) -> Optional[str]:
    """Normalize a strategy return value to an OHT name."""
    if selection is None:
        return None
    if isinstance(selection, str):
        return selection if selection in oht_dict else None
    name = getattr(selection, "name", None)
    if isinstance(name, str) and name in oht_dict:
        return name
    return None


def normalize_section_path(result: Any, from_sec_id: Optional[int] = None) -> Optional[list[int]]:
    """
    Normalize a user routing/idle strategy return value to a list of section ids.

    Accepted examples:
      - [12, 13, 14]
      - {"path": [12, 13, 14]}
      - {"section_path": [12, 13, 14]}
      - ([12, 13, 14], cost)

    The returned path excludes the current section, following the existing controller convention.
    """
    if result is None:
        return None

    candidate = result
    if isinstance(candidate, dict):
        for key in ("path", "section_path", "sections", "route"):
            if key in candidate:
                candidate = candidate[key]
                break
        else:
            return None

    if isinstance(candidate, tuple) and candidate:
        first = candidate[0]
        if isinstance(first, (list, tuple)):
            candidate = first

    if not isinstance(candidate, (list, tuple)):
        return None

    path: list[int] = []
    for item in candidate:
        if item is None:
            continue
        if isinstance(item, int):
            sid = item
        elif isinstance(item, str):
            text = item.strip()
            if text.lower().startswith("sec"):
                text = text[3:]
            try:
                sid = int(text)
            except ValueError:
                return None
        else:
            return None
        if not path or path[-1] != sid:
            path.append(sid)

    if from_sec_id is not None and path and path[0] == from_sec_id:
        path = path[1:]
    return path


def normalize_node_path(result: Any, route_manager: Any) -> Optional[list[str]]:
    """Normalize a custom routing result to a node-name path."""
    if result is None:
        return None

    candidate = result
    if isinstance(candidate, dict):
        for key in ("node_path", "nodes", "path", "route"):
            if key in candidate:
                candidate = candidate[key]
                break
        else:
            return None

    if isinstance(candidate, tuple) and candidate:
        first = candidate[0]
        if isinstance(first, (list, tuple)):
            candidate = first

    if not isinstance(candidate, (list, tuple)):
        return None

    nodes = getattr(getattr(route_manager, "network", None), "nodes", {})
    path: list[str] = []
    for item in candidate:
        if item is None:
            continue
        node_name = str(item)
        if nodes and node_name not in nodes:
            return None
        if not path or path[-1] != node_name:
            path.append(node_name)
    return path if len(path) >= 2 else None
