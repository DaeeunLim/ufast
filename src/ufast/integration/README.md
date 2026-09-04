# src/ufast/integration — 생산 시뮬 연동 및 타임라인 재생

## 역할

물류(AMHS) 시뮬레이터와 생산 시뮬레이터를 **하나의 GUI 안에서 통합**하는 레이어. 두 모드 공통의 "비디오 녹화/재생" 엔진(`TimelineRecorder`), 생산 greedy 루프를 백그라운드 QThread로 구동하며 스냅샷을 채우는 어댑터(`ProductionRunner`), 생산 지표 전용 대시보드 위젯(`ProductionDashboard`)으로 구성된다.

## 파일별 역할

| 파일 | 핵심 클래스 | 역할 |
|---|---|---|
| `timeline.py` | `Snapshot` | 한 시점 복원용 최소 상태 — `sim_time`, `mode`, OHT/EQ 상태 dict, 공통 지표 |
| | `TimelineRecorder` | sim-time 오름차순 스냅샷 저장소 — `maybe_record()`(interval 초과 시에만 기록), `at_fraction()`(bisect 조회). PyQt 무의존 순수 파이썬 |
| `production_runner.py` | `ProductionParams` | 생산 실행 파라미터 (dataset, days, dispatcher, congestion_factor 등) |
| | `ProductionRunner(QThread)` | 생산 greedy 루프를 백그라운드에서 최대 속도로 완주하며 타임라인을 채움. `progress`/`results_ready`/`failed` 시그널, `request_stop()` |
| | `_build_result_summary()` | lot-type별 throughput/CT/on-time/tardiness, family별 가동률·starvation·breakdown·PM, WIP/downtime 요약 |
| `production_view.py` | `ProductionDashboard(QWidget)` | 생산 모드 중앙 화면 — Lot Metrics 그리드, 게이지 2종, 결과 요약(테이블 2종) |

## 데이터 흐름

- **물류 모드**: `SimulationThread`가 매 스텝 스냅샷을 `timeline_recorder.maybe_record()`로 기록 → 진행바 드래그 시 `at_fraction()` → `viewer.render_logistics_snapshot()`.
- **생산 모드**: `start_production_simulation()` → `ProductionRunner` 시작 → 완료 시 `results_ready(summary)` → 대시보드 갱신; 이후 스크럽은 `dashboard.set_snapshot()`.
- `ProductionDashboard`는 `gui.viewer`가 중앙 스택에 직접 삽입 — integration → gui 역참조 없음 (단방향).

## 외부 의존

내부: **`simulation.*`** (greedy·dispatcher·file_instance·plugins — `production`이 아니라 `simulation` 패키지를 쓴다는 점 주의), `common.equipment_kpi`. 서드파티: PyQt6 (`timeline.py`만 완전 독립).
