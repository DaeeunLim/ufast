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
    """커스텀 전략 파일 로딩/호출 중 발생하는 예외."""


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
    """로그/상태 메시지에 표시할 전략 이름."""
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
    .py / .pkl / .pickle 전략 파일을 로드한다.

    Python 파일은 아래 순서로 엔트리포인트를 찾는다.
      1) create_*_strategy(), create_strategy(), get_strategy(), load_strategy()
      2) *_strategy 또는 strategy 변수
      3) *Strategy 클래스
      4) kind별 함수(get_route/select/plan_reposition 등)
    """
    if kind not in _KIND_CONFIG:
        raise StrategyLoadError(f"지원하지 않는 전략 종류입니다: {kind}")

    normalized = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(normalized):
        # 사용자 전략 보관 폴더(<repo>/strategies/)에서 한 번 더 찾는다.
        from ufast.paths import REPO_ROOT
        candidate = os.path.join(REPO_ROOT, "strategies", path)
        if os.path.exists(candidate):
            normalized = candidate
        else:
            raise StrategyLoadError(
                f"파일을 찾을 수 없습니다: {normalized} (strategies/ 폴더에도 없음)")

    ext = Path(normalized).suffix.lower()
    if ext == ".py":
        module = _load_module_from_py(normalized)
        strategy = _extract_strategy_from_module(module, kind)
    elif ext in {".pkl", ".pickle"}:
        with open(normalized, "rb") as f:
            strategy = pickle.load(f)
    else:
        raise StrategyLoadError(".py, .pkl, .pickle 파일만 지원합니다.")

    if strategy is None:
        raise StrategyLoadError(f"전략 객체를 만들지 못했습니다: {normalized}")
    return strategy


def _load_module_from_py(path: str) -> ModuleType:
    module_name = f"custom_strategy_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise StrategyLoadError(f"파이썬 모듈 로딩 실패: {path}")
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
        f"모듈에서 {kind} 전략 엔트리포인트를 찾지 못했습니다. "
        f"factory 함수, strategy 변수, Strategy 클래스, 또는 지정 함수명을 사용하세요."
    )


def _call_variants(func, call_specs: list[tuple[tuple[Any, ...], dict[str, Any]]]) -> Any:
    """
    사용자 전략 함수가 받을 수 있는 인자 조합을 순차 시도한다.
    TypeError가 발생하면 다음 시그니처를 시도한다.
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
    """Assignment 전략 호출. 반환값은 OHT 객체 또는 OHT 이름이면 된다."""
    if strategy is None:
        return None

    func = getattr(strategy, "select", None)
    if not callable(func):
        func = getattr(strategy, "dispatch", None)
    if not callable(func) and callable(strategy):
        func = strategy
    if not callable(func):
        raise StrategyLoadError("Assignment 전략은 select()/dispatch() 메서드 또는 callable 이어야 합니다.")

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
    """Routing 전략 호출. 반환값은 section id path 또는 dict/tuple 형태도 허용한다."""
    if strategy is None:
        return None

    func = getattr(strategy, "get_route", None)
    if not callable(func):
        func = getattr(strategy, "route", None)
    if not callable(func) and callable(strategy):
        func = strategy
    if not callable(func):
        raise StrategyLoadError("Routing 전략은 get_route()/route() 메서드 또는 callable 이어야 합니다.")

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
    """Idle Positioning 전략 호출. 반환값은 target section/node 또는 section path면 된다."""
    if strategy is None:
        return None

    func = getattr(strategy, "plan_reposition", None)
    if not callable(func):
        func = getattr(strategy, "pick_target", None)
    if not callable(func) and callable(strategy):
        func = strategy
    if not callable(func):
        raise StrategyLoadError(
            "Idle Positioning 전략은 plan_reposition()/pick_target() 메서드 또는 callable 이어야 합니다."
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
    """전략 반환값을 OHT 이름으로 정규화한다."""
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
    사용자 routing/idle 전략 반환값을 section id 리스트로 정규화한다.

    허용 예:
      - [12, 13, 14]
      - {"path": [12, 13, 14]}
      - {"section_path": [12, 13, 14]}
      - ([12, 13, 14], cost)

    반환 path는 기존 컨트롤러 규칙에 맞게 현재 섹션을 제외한다.
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
