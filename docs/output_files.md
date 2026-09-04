# U-FAST 결과물 명세 (results/)

`ufast-run`(co-simulation)과 `ufast-fromto`(logistics-only)가 생성하는 파일과 그 안의
필드를 정리한다. 사후 분석(pandas / R)과 재현에 필요한 모든 정보는 이 파일들에 있다.

## 1. 폴더 및 파일 명명 규칙

- **폴더**: `results/<YYYY-MM-DD_HH-MM-SS_마이크로초>/` — 실행마다 새로 생성되며 이전
  실행을 덮어쓰지 않는다. 한 폴더 안의 파일은 모두 같은 기본 이름을 공유한다.
- **기본 이름 (co-simulation)**

  ```
  <데이터셋>_<기간>d_<OHT대수>oht_a<알파>_<생산디스패처>_<AMHS전략>_<혼잡모델>[부가옵션]_s<시드>
  예) HVLM_180d_15oht_a0.05_fifo_fifo_queue_sw120_as10_s0
  ```

  혼잡 모델 약어: `queue`(그대로), `section_local`→`sl`, `global_tip`→`gt`, `off`→`no`.
  부가옵션은 기본값이 아닐 때만 붙는다: `_idleon`(유휴 재배치), `_rdyn`(동적 라우팅),
  `_ms<machine-selection>`(예: `_msnearest`), `_sw<일>`(정적 웜업), `_as<일>`(AMHS 안정화).
  소수 일수는 `0p2`처럼 표기한다.
- **기본 이름 (logistics-only)**

  ```
  fromto_<레일>_<FromTo>_<초>s_<OHT대수>oht_<전략>_<혼잡모델>_s<시드>
  예) fromto_case1_case1_Fromto_3600s_50oht_nearest_queue_s0
  ```

## 2. 생성되는 결과물

| 파일 | 생성 조건 | 내용 |
|---|---|---|
| `<이름>.json` | 항상 | 통합 KPI + 실행 메타데이터 (§3) |
| `<이름>_lots.csv` | 항상 (co-simulation) | 완료 로트 이력 (§4) |
| `<이름>_trips.csv` | `--viz` | OHT 이송(trip) 트레이스 (§4) |
| `<이름>_kpi_timeseries.csv` | `--viz` | 물류 스냅샷 시계열 (§4) |
| `<이름>_machines.csv` | `--viz` (co-simulation) | 장비 가동 구간 (§4) |
| `<이름>_trajectories.json` | `--viz` | Rerun 재생용 통합 궤적 로그 (§5) |
| `results/aggregate/aggregate.{json,csv}` | `ufast-aggregate results/` | 다중 시드 집계표 (§6) |

## 3. 통합 KPI JSON (`<이름>.json`)

실행 재현에 필요한 메타데이터와 팹의 세 관점(생산·장비·물류) KPI를 계층적으로 담는다.

| 최상위 키 | 포함 항목 |
|---|---|
| `meta` | • 실행 모드(`mode`: `production` / `fromto`), `run_id`, (fromto) `engine`<br>• 입력 경로: `dataset_dir`, `rail_file`, (fromto) `fromto_file` 와 짧은 이름<br>• 실험 조건: `days` 또는 `sim_duration_s`, `num_oht`, `seed`, `alpha`<br>• 정책 슬롯 전부: `dispatcher`, `amhs_strategy`(fromto 는 `strategy`), `congestion_model`, `idle_positioning`, `routing_model`, `machine_selection`<br>• 웜업: `static_warmup_days`, `amhs_settling_days`, `measurement_start_days`<br>• **`vehicle`**: 차량 제원 전부 — `length_mm`, `headway_mm`, `footprint_mm`, `max_speed_mm_s`, `accel_mm_s2`, `decel_mm_s2`, `line_speed_mm_s`, `curve_speed_mm_s`, `source`(파일/플래그 출처)<br>• `wall_time_s`, `sim_time_days`(또는 `sim_time_s`), `dispatch_steps`(또는 `events_processed`)<br>이 블록만으로 같은 실행을 재현할 수 있다. |
| `production` (co-simulation) | • `completed`: **전 구간 누적** 완성 로트 수 (측정창 기준 집계는 `_lots.csv` 의 `done_at` 으로 직접 센다), `active`: 진행 중 로트<br>• 이송 횟수: `transport_count`(AMHS 실제 이송), `static_transport_count` / `static_transport_time_s`(정적 웜업 이송), `skipped_transport`(rail 미매핑 family), `same_node_transport`, `reserved_transport`, `preassigned_machine`<br>• `by_lot_type`: 로트 타입별(`Lot_3`, `HotLot_3` …) throughput, 평균/중앙값 cycle time, on-time 달성률, 대기/가공/이송 시간<br>• `by_category`: 우선순위 카테고리별(Regular / Hot / SuperHot) 같은 집계 |
| `equipment` (co-simulation) | • 측정창: `measurement_start_s` ~ `measurement_end_s`, 장비 수<br>• `busy_time_s`, `setup_time_s`, `utilization_pct`<br>• 기아(starvation) 분해: 총 시간·횟수, **`transport_blocked_starvation_s` / `_pct`**(즉시 이송이었다면 없었을 기아), `upstream_starvation_s`(상류 생산 귀속), 평균·p95·최대<br>• 고장/PM: `breakdown_time_s`, `pm_time_s`<br>• `by_family`: 장비 패밀리별 가동/기아/고장 내역 |
| `amhs` | • 작업 수: `total_requested_jobs`, `total_jobs`(완료)<br>• 시간: `avg_free_flow_s`, `avg_transport_s`, `avg_empty_travel_s`, `avg_loaded_travel_s`, `avg_delivery_s`(요청→배달), `avg_oht_wait_s`(배차 대기)<br>• 분위수: `delivery_{p50,p95,p99,max}_s`, `transport_{p50,p95,p99,max}_s`<br>• `avg_congestion_factor`(이송/자유주행), `avg_utilization`(함대 가동률), `max_queue`, `max_node_inflight`(섹션 동시 최대 점유)<br>• queue(blocking) 모델 전용: `blocked_events`, `blocked_time_s`, `deadlock_forced`; 섹션별 `blocked_by_section`, `blocked_time_by_section`(차단 히트맵 데이터, 그 외 모델에서는 빈 dict)<br>• `reposition_count`, `oht_count`, `congestion_alpha` |
| `transport` (logistics-only) | `lots_generated`(요청 수), `lots_completed`, `completion_rate`, `oht_count`, `oht_final_status`. `production`/`equipment` 대신 들어가며 `amhs` 블록은 co-simulation 과 같다. |

`ufast-analyze [json]` 은 이 JSON 을 읽어 콘솔 리포트를 출력한다 (인자 없이 실행하면
`results/` 의 최신 JSON).

## 4. 원시 데이터 CSV

JSON 과 같은 기본 이름 뒤에 접미사가 붙는다. 시각은 모두 시뮬레이션 초(sim s).

| 파일 | 컬럼 | 용도 |
|---|---|---|
| `_lots.csv` (항상) | `lot_name, release_at, done_at, deadline_at, cycle_time_days, tardiness_s, waiting_s, processing_s, transport_s, on_time` | 측정창 필터링(예: `done_at ≥ 130 d`), 분위수/분포 분석. 초기 WIP 로트는 `release_at < 0` |
| `_trips.csv` (`--viz`) | `oht_id, request_time, assignment_time, delivery_time, empty_duration, loaded_duration, total_duration, congestion, empty_path_len, loaded_path_len` | trip 단위 이송시간·공차/실차 비율 분석 |
| `_kpi_timeseries.csv` (`--viz`) | `sim_time, busy_oht, section_inflight_sum, pending_queue, cumulative_delivered, max_section_inflight` | 시간에 따른 함대 부하·대기열 추이 |
| `_machines.csv` (`--viz`, co-simulation) | `family, start_time, end_time, duration_s` | 장비 가동 간트차트, family 부하 시각화 |

## 5. 궤적 파일 (`_trajectories.json`, `--viz`)

trip 별 구간 진입 시각과 좌표, OHT 초기 위치, 장비 가동 구간, KPI 스냅샷을 묶은 로그.
`ufast.viz.rerun_replay.show_run(rail, trajectories)` 가 Rerun 뷰어에서 차량 이동과
KPI 시계열을 재생한다.

## 6. 다중 시드 집계 (`ufast-aggregate`)

`ufast-aggregate results/` 는 파일 기본 이름에서 `_s<시드>` 를 뺀 설정별로 JSON 을 묶어
`results/aggregate/aggregate.json` 과 `aggregate.csv`(metric, n, mean, std, min, max)를
쓴다.

## 7. 모드·옵션별 생성물 요약

| 실행 방식 | 생성 파일 |
|---|---|
| `ufast-run` | `<run>.json`, `<run>_lots.csv` |
| `ufast-run --viz` | 위 + `_trips.csv`, `_kpi_timeseries.csv`, `_machines.csv`, `_trajectories.json` |
| `ufast-fromto` | `fromto_<…>.json` |
| `ufast-fromto --viz` | 위 + `_trips.csv`, `_kpi_timeseries.csv`, `_trajectories.json` |
| `ufast-aggregate results/` | `results/aggregate/aggregate.json`, `aggregate.csv` |

참고: 섹션별 **점유** 히트맵의 원시값(`*_sections.csv`: `section_id, mean_occupancy_ohts,
peak_occupancy_ohts, occupancy_integral_oht_s, section_capacity_ohts, blocked_time_s,
blocked_events`)은 실행기 출력이 아니라 `scripts/make_queue_occupancy_heatmap.py` 가 계측
런을 돌려 만든다.
