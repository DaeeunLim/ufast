# src/ufast/control — 물류 이벤트 컨트롤러

## 역할

AMHS 물류 시뮬레이션(GUI/legacy 모드)의 **두뇌**에 해당하는 단일 모듈 패키지 (`controllers.py`, 약 1,480줄). OHT 생성·초기 분산 배치·배차·경로 탐색을 담당하는 `VehicleController`와, 이산사건(LOT/TRANSFER_EXT/PROCESS_END)을 처리해 차량을 섹션 단위로 전진시키고 EQ 상태를 갱신하는 `EventHandler` 두 클래스로 구성된다. 커스텀 전략(.py 플러그인) 주입 → `route` 패키지 위임 → 자체 Dijkstra fallback의 3단 구조를 갖는다.

## 주요 구성

### VehicleController — 배차·라우팅 총괄

| 메서드 | 역할 |
|---|---|
| `init()` / `_distribute_ohts()` | `OHT_000...` 생성 후 discrete bin 순서로 섹션에 분산 배치 (한 영역 몰림 방지) |
| `assign_oht(target_section_id)` | 대상 섹션 인근 IDLE OHT 선택 — 커스텀 assignment 전략 → `Dispatcher` → 기본 로직 순 |
| `_select_repositioning_candidate_for_dispatch()` | REPOSITIONING 차량도 후보로 삼아 필요 시 선점 회수 |
| `get_route(from, to)` | 경로 탐색 3단 — ① 커스텀 routing 전략 ② `bridge.find_section_route()`(penalty 반영) ③ 자체 섹션 Dijkstra |
| `_log_*` | `route.logistics_logger`로 배차·이동·상태전이 CSV 로깅 |

### EventHandler — 이벤트 처리기

| 메서드 | 역할 |
|---|---|
| `process_event(event)` | LOT / TRANSFER_EXT / PROCESS_END 분기 |
| `init_lot_events()` | From-To .dat의 시간당 발생율(rate)을 기반으로 고정 간격(3600/rate 초) LOT 이벤트 등록 |
| `_handle_lot()` | 출발 EQ에서 배차 → 실패 시 5초 후 재시도, 성공 시 경로 계산 + `OHT_ASSIGNED` 통지 |
| `_handle_transfer_ext()` / `_handle_arrival()` | 섹션→섹션 전진(버퍼 여유 확인), 도착 시 픽업(LOADED 전환)·배달 완료 처리 |
| `configure_simulation_mode()` | `from_to_only`(물류 전용) vs `production_logistics`(rundown/PROCESS_END 포함) 전환 |
| `reposition_idle_ohts()` | 유휴 재배치 — 라운드로빈 + 호출당 10대 제한, 최근 방문 deque 기반 ping-pong 페널티 |

## 데이터 흐름

- `main_ui.SimulationApp.start_simulation()`이 `VehicleController` → `EventHandler` 순으로 생성, route 패키지가 있으면 `Dispatcher(NearestIdleStrategy)` 주입.
- 루프는 `main_ui.SimulationThread._step()`이 돌린다: `ds.event_queue` heappop → `process_event()` → 상태 갱신 → 주기적 `reposition_idle_ohts()`.
- 상태는 모두 `SimulatorDataSet` 싱글턴에 반영 — `gui.viewer`는 컨트롤러를 직접 참조하지 않는다.
- EQ 상태 변경은 `EQ.message_receiver()` 메시지 패턴으로만 수행.

## 외부 의존

필수: `core.data_set`, `core.components`, `common.logger`. 옵션(try/except): `common.strategy_loader`, `route.RouteManager`, `route.logistics_logger`. 서드파티 없음 — headless 실행 가능.
