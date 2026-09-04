# strategies/ — 사용자 전략 파일 보관 폴더

`ufast-run` / `ufast-fromto` 의 `--custom-assignment`, `--custom-routing`,
`--custom-idle`, `--custom-routing-cost` 에 넘길 사용자 전략 파일을 두는 곳이다.
플래그에 **파일 이름만** 주면 이 폴더에서 찾는다 (절대·상대 경로를 주면 그 경로를 그대로 쓴다).

```bash
ufast-run dataset/HVLM dataset/SMAT2022.rail --days 7 --custom-assignment my_assign.py
# == --custom-assignment strategies/my_assign.py
```

## 파일 형식

| 형식 | 용도 |
|---|---|
| `.py` | 규칙을 코드로 작성. `examples/` 의 예제 4종을 복사해 고치는 것이 표준 워크플로. 로더가 `create_*_strategy()` / `*Strategy` 클래스 / kind 별 함수(`select`, `get_route`, `plan_reposition`, `compute_cost`) 순으로 진입점을 찾는다 |
| `.pkl` / `.pickle` | 학습된 정책 등 **피클된 전략 객체**. 같은 메서드 규약을 가진 객체를 `pickle.dump` 한 파일이면 된다. 오프라인에서 학습한 정책(예: RL)을 내장 규칙과 같은 시드로 비교할 때 쓴다 |

| 슬롯 | 플래그 | 규약 (호출되는 메서드) | 예제 |
|---|---|---|---|
| OHT 배차 | `--custom-assignment` | `select(target_section_id, idle_ohts, route_manager, bridge, vehicle_controller)` → OHT 또는 `None` | `examples/custom_assignment_example.py` |
| 라우팅 | `--custom-routing` | `get_route(from_sec_id, to_sec_id, vehicle_controller)` → 섹션 id 리스트 또는 `None` | `examples/custom_routing_example.py` |
| 유휴 재배치 | `--custom-idle` | `plan_reposition(oht, current_time, current_node, ...)` → 목적 섹션 또는 `None` | `examples/custom_idle_positioning_example.py` |
| 경로 탐색 엣지 비용 | `--custom-routing-cost` | `compute_cost(move_time, raw_penalty, effective_penalty, section, from_node, to_node, default_cost, context)` (키워드 인자) → 비용(float) | `examples/custom_routing_cost_example.py` |

`None`(또는 빈 리스트/음수/예외)을 반환하면 내장 규칙으로 되돌아간다. `--custom-routing-cost`
를 쓰면 C 가속 경로탐색이 꺼지고 순정 파이썬 경로로 폴백되어 실행이 느려지는 것이 정상이다.
실제 적용된 전략 파일 이름은 결과 JSON `meta.custom_strategies` 에 기록된다.

이 폴더의 `README.md` 외 파일은 git 이 추적하지 않는다(개인 실험용).
