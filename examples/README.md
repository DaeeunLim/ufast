# src/examples — 커스텀 전략 플러그인 예제

## 역할

사용자가 직접 작성하는 물류 전략 플러그인의 **참조 구현 4종**. 각 파일은 `common/strategy_loader.py`가 인식하는 kind별 최소 규약(메서드 이름, 인자, 반환 형식, fallback 조건)을 docstring으로 명시하고 동작하는 예제를 제공한다. 모든 예제는 `None`(또는 빈 리스트/음수/예외) 반환 시 기본 전략으로 fallback된다. 새 배차/경로 알고리즘을 시험할 때 이 파일들을 복사해 수정하는 것이 표준 워크플로다.

## 파일별 역할

| 파일 | 규약 (kind) | 예제 내용 |
|---|---|---|
| `custom_assignment_example.py` | `AssignmentStrategy.select(target_section_id, idle_ohts, route_manager, bridge, vehicle_controller)` — 배차 | 같은 섹션 OHT 최우선 → `bridge.estimate_section_route_cost()` 최소 비용 OHT → section id 차이 최소 순 |
| `custom_routing_example.py` | `RoutingStrategy.get_route(from_sec_id, to_sec_id, vehicle_controller)` — 라우팅 | `next_sections` 기반 BFS 섹션 경로 탐색 |
| `custom_idle_positioning_example.py` | `IdlePositioningStrategy.plan_reposition(oht, current_time, current_node, ...)` — 유휴 재배치 | 빈 버퍼가 있는 인접 섹션 중 혼잡도 최소인 곳으로 1-hop 이동 |
| `custom_routing_cost_example.py` | `compute_cost(move_time, raw_penalty, effective_penalty, section, from_node, to_node, default_cost, context)` — 엣지 비용 (키워드 인자로 호출됨) | 기본식에 CURVE 1.15배 가중 + 고혼잡 노드 추가 페널티 |

## 사용 맥락

- 자기 전략은 `strategies/` 폴더에 두고 파일 이름만 넘기면 된다 (`strategies/README.md`). GUI 설정 또는 CLI(`--custom-assignment` / `--custom-routing` / `--custom-idle` / `--custom-routing-cost`)에서 전략 파일을 지정하면 `strategy_loader.load_strategy()`가 로드하고, `control/controllers.py`·`ufast/amhs.py`가 실행 중 호출한다.
- 참고: 커스텀 routing_cost 함수가 설정되면 경로탐색 C 가속 엔진(`route/fast_pathfinder`)이 비활성화되고 순정 파이썬 경로로 폴백된다 — 전략 실험 시 실행 속도가 느려지는 것이 정상이다.
