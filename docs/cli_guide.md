# U-FAST (Unified Fab-AMHS Simulation Toolkit) CLI Execution Guide

이 문서는 U-FAST 반도체 co-simulation 시뮬레이터의 CLI(Command Line Interface) 모드 실행 방법과 주요 옵션들에 대한 상세한 가이드입니다. 

U-FAST는 생산 시뮬레이터(PySCFabSim)와 물리적 물류 시뮬레이터(AMHS)를 연동하는 co-simulation 엔진으로, 다음 세 가지 주요 CLI 스크립트를 제공합니다.

1. **통합 Co-Simulation 실행기 (`python -m ufast.cosim.run` / `ufast-run`):** 단일 생산 데이터셋과 레일 레이아웃을 기반으로 생산과 물류(AMHS)를 연동하여 실시간 시뮬레이션을 실행하고 분석 데이터를 출력합니다.
2. **Fromto(logistics-only) 모드 실행기 (`python -m ufast.cosim.run_fromto` / `ufast-fromto`):** 생산 데이터 없이 레일 레이아웃 + fromto.dat(반송 수요표)만으로 co-simulation 과 **같은 AMHS 층**(blocking 모델·운동학·전략·KPI)을 실행합니다.
3. **베이스라인 비교 및 캐싱 스크립트 (`scripts/compare_baselines.py`):** PySCFabSim, LogiFabSim, U-FAST 세 시뮬레이터의 생산 KPI(Cycle Time, Throughput, 적기 인도율 등)를 비교하고, 자동으로 캐싱하여 대규모 시뮬레이션의 효율을 극대화합니다.

---

## 1. Co-Simulation 실행기 (`python -m ufast.cosim.run`)

생산공정과 물리적 AMHS 물류를 통합하여 실제 가동 궤적 및 제어 전략을 시뮬레이션하는 메인 스크립트입니다.

### 실행 명령어 구조
```bash
PYTHONPATH=src python -m ufast.cosim.run [dataset_dir] [rail_file] [options]
```

### 위치 인수 (Positional Arguments)
* **`dataset_dir`**: 생산공정 데이터셋 폴더 경로 (기본값: `dataset/HVLM`)
  * 예시: `dataset/HVLM`, `dataset/LVHM`, `dataset/LVLM`
* **`rail_file`**: 물류 레일 레이아웃 파일 경로 (기본값: `dataset/SMAT2022.rail`)
  * 예시: `dataset/SMAT2022.rail`, `case1_SMT2020_106_nospur.rail`

### 상세 CLI 옵션 (Options)

| 옵션명 | 타입 | 선택지 및 포맷 | 기본값 | 설명 및 영향 |
| :--- | :--- | :--- | :--- | :--- |
| `--days` | `int` | 양의 정수 | `1` | **시뮬레이션 수행 기간** (일 단위). 기간이 길어질수록 WIP(재공) 상태가 안정화되나 연산 시간이 증가합니다. |
| `--oht` | `int` | 양의 정수 | `200` | **AMHS 내 OHT 차량 대수** (Fleet Size). 차량 대수가 너무 적으면 물류 병목이 발생하고, 너무 많으면 레일 혼잡도가 급증합니다. |
| `--seed` | `int` | 정수 | `0` | **난수 시드값**. 난수 재현성을 확보하여 동일 조건 하에서 항상 같은 시뮬레이션 결과를 출력합니다. |
| `--alpha` | `float` | `0.0` ~ `1.0` | `0.05` | **혼잡도 가중치 ($\alpha$)**. 혼잡 기반 최단경로 탐색 시, 전방 차량 1대당 경로 가중치에 추가할 페널티 계수입니다. |
| `--dispatcher` | `str` | `fifo`<br>`cr`<br>`random` | `fifo` | **생산 Dispatching Rule**. 설비 대기열에서 다음 가공할 Lot을 결정하는 의사결정 규칙입니다.<br>• `fifo`: 선입선출<br>• `cr`: Critical Ratio (긴급도 우선)<br>• `edd`: Earliest Due Date (납기일 기준)<br>• `setup_avoidance`: 셋업 전환 최소화 규칙 |
| `--strategy` | `str` | `fifo`<br>`nearest`<br>`same_section`<br>`congestion` | `fifo` | **AMHS OHT 배차(Assignment) 전략**. 유휴 OHT를 로딩 요청이 발생한 로트에게 어떻게 매칭할지 결정합니다.<br>• `fifo`: 요청이 들어온 순서대로 매칭<br>• `nearest`: 최단 거리에 있는 차량 우선 매칭 |
| `--congestion` | `str` | `queue`<br>`section_local`<br>`global_tip`<br>`off` | `queue` | **물류 혼잡 모델 선택**.<br>• `queue` (**기본값**): **용량 제약 blocking 모델** — 각 섹션이 유한 FIFO 슬롯(⌊구간 길이/차량 footprint⌋)을 갖고, 가득 찬 섹션은 진입이 차단되어 대기합니다. 데드락은 wait-for 사이클 탐지 후 강제 진입으로 해소되며 `blocked_events`/`blocked_time_s`/`deadlock_forced` 지표가 결과에 추가됩니다 (α 미사용).<br>• `section_local`: delay 기반 대안 — 동일 구간 내 다수 OHT 주행 시 이동 시간이 점유 비례로 팽창합니다 (α 사용).<br>• `global_tip`: LogiFabSim 방식의 전역 TIP 비례 감속 (비교용 재현).<br>• `off`: 혼잡 미반영 (free-flow). |
| `--idle` | `str` | `off`<br>`on` | `off` | **유휴 차량(Idle OHT) 재배치 전략**.<br>• `off`: 유휴 차량이 목적지에서 대기<br>• `reposition`: 유휴 차량을 수요가 높을 것으로 예상되는 영역으로 사전에 이동시킵니다. |
| `--routing` | `str` | `off`<br>`dynamic` | `off` | **OHT 경로 탐색 모드**.<br>• `off` / `static`: 정적 최단 거리(Free-flow) 경로 기준 주행<br>• `dynamic`: 실시간 레일 혼잡(α 페널티 포함)을 반영한 동적 다익스트라 경로 재탐색 |
| `--machine-selection` | `str` | `exact`<br>`nearest` | `exact` | **다음 가공 설비(Machine) 후보 매칭 모드**.<br>• `exact` (기본): 상위 후보 설비 8개에 대해 정밀한 물리적 다익스트라 물류 경로를 연산해 최단 시간 내 도달할 수 있는 설비를 선택합니다.<br>• `nearest` (추천): 물리적 경로 탐색 대신 유클리드 최단 거리(직선 거리)를 활용해 설비를 근사 선택합니다. **(연산 성능을 약 5배 이상 단축시킵니다.)** |
| `--static-warmup-days` | `float` | 양의 실수 | `0.0` | **정적 웜업 일수** (일 단위). 시뮬레이션 초기 적체 및 웜업 기간 동안 물리적 AMHS 모델을 생략하고 정적 반송 시간 상수를 적용하여, 고부하 구간인 웜업 단계를 순식간에 지나가도록 합니다. |
| `--amhs-settling-days` | `float` | 양의 실수 | `0.0` | **AMHS 적응/안정화 일수** (일 단위). 정적 웜업이 완료된 후, 통계 집계를 시작하기 전에 실제 AMHS(OHT 주행)를 가동하여 물류 흐름이 안착되도록 하는 유예 기간입니다. |
| `--custom-assignment` / `--custom-routing` / `--custom-idle` | `path` | `.py` / `.pkl` | 없음 | **커스텀 전략 플러그인** — OHT 배차 / 라우팅 / 유휴 재배치 슬롯을 사용자 파일로 교체. `None` 반환 시 내장 규칙으로 폴백. 파일 이름만 주면 `strategies/` 폴더에서 찾음. 규약·예제: [strategies/README.md](../strategies/README.md), `examples/` |
| `--custom-routing-cost` | `path` | `.py` / `.pkl` | 없음 | **경로 탐색 엣지 비용 함수** 플러그인 (`compute_cost(...)`). 설정 시 C 가속 경로탐색이 꺼지고 순정 파이썬 경로로 폴백. 적용된 플러그인 파일명은 네 종 모두 결과 JSON `meta.custom_strategies` 에 기록 |
| `--viz` | `flag` | `--viz` 추가 시 활성화 | `False` | **Rerun 시각화 데이터 생성 플래그**. 활성화 시 Rerun 뷰어에서 재생 가능한 시뮬레이션 주행 및 머신 상태 궤적 데이터(`_trajectories.json`)를 생성하고, 시뮬레이션 종료 시 자동으로 Rerun 브라우저를 연동하여 시각화합니다. |
| `--strict` | `flag` | `--strict` 추가 시 활성화 | `False` | **입력 정합성 엄격 모드**. 데이터셋의 tool family(STNFAM)가 레일 목적지(eq_to_node 또는 Equipment.csv)로 해석되지 않으면 실행을 중단합니다. 기본 동작은 경고 출력 후 해당 family의 이송을 스킵(`skipped_transport` 집계)합니다. Delay 가상 스테이션(STNFAMLOC=Delay)은 검사에서 제외됩니다. |
| `--vehicle-spec` | `path` | `.json` 또는 SMAT2022 `VehicleType.csv` | `dataset/vehicle_spec.json` | **OHT 차량 제원 파일**. 속도·가감속·차량 길이·최소 차간·직선/곡선 제한속도. 기본 파일 값 = SMAT2022 OHTV0. 아래 플래그가 파일 값을 덮어씁니다. |
| `--oht-speed` / `--oht-accel` / `--oht-decel` | `float` | m/s, m/s², m/s² | 파일 값 (5 / 2 / 3.5) | 최고속도·가속도·감속도 (사다리꼴 운동학 자유주행 시간에 반영) |
| `--oht-length` / `--oht-headway` | `float` | mm | 파일 값 (784 / 125) | 차량 길이·최소 차간. 합(footprint, 기본 909 mm)으로 `queue` 모델의 섹션 용량 = max(1, ⌊섹션 길이 / footprint⌋) 결정 |
| `--line-speed` / `--curve-speed` | `float` | m/s | 파일 값 (5 / 1) | 직선/곡선 링크 제한속도 |

### 출력 결과물

필드 단위 상세 명세는 [output_files.md](output_files.md) 참조. 실행마다 `results/<run_id>/` 디렉토리가 새로 만들어진다 (`run_id` = `YYYY-MM-DD_HH-MM-SS_마이크로초`). 이전 실행을 덮어쓰지 않으며, 한 디렉토리 안의 파일은 모두 같은 기본 이름을 공유한다.

**기본 이름 (co-simulation)**

```
<데이터셋>_<일수>d_<OHT>oht_a<알파>_<생산디스패처>_<AMHS전략>_<혼잡>[부가옵션]_s<시드>
예) HVLM_180d_15oht_a0.05_fifo_fifo_queue_sw120_as10_s0
```

혼잡 약어: `queue` 그대로, `section_local`→`sl`, `global_tip`→`gt`, `off`→`no`. 부가옵션은 기본값이 아닐 때만 붙는다: `_idleon`, `_rdyn`(dynamic routing), `_ms<machine-selection>`, `_sw<일>`(static warm-up), `_as<일>`(AMHS settling).

**기본 이름 (logistics-only)**: `fromto_<레일>_<FromTo>_<초>s_<OHT>oht_<전략>_<혼잡>_s<시드>` (예: `fromto_case1_case1_Fromto_3600s_50oht_nearest_queue_s0`).

| 파일 | 생성 조건 | 내용 |
|---|---|---|
| `<이름>.json` | 항상 | KPI 요약 + 실행 메타데이터 (아래 표) |
| `<이름>_lots.csv` | 항상 (co-simulation) | 완료 로트 1행씩: `lot_name, release_at, done_at, deadline_at, cycle_time_days, tardiness_s, waiting_s, processing_s, transport_s, on_time`. 시각은 sim 초. 측정창 필터링(예: `done_at ≥ 130 d`)·분위수 분석의 원천 |
| `<이름>_trips.csv` | `--viz` | OHT 이송 1건씩: `oht_id, request_time, assignment_time, delivery_time, empty_duration, loaded_duration, total_duration, congestion, empty_path_len, loaded_path_len` |
| `<이름>_kpi_timeseries.csv` | `--viz` | 주기 스냅샷: `sim_time, busy_oht, section_inflight_sum, pending_queue, cumulative_delivered, max_section_inflight` |
| `<이름>_machines.csv` | `--viz` (co-simulation) | 장비 가동 구간: `family, start_time, end_time, duration_s` (간트/부하 시각화용) |
| `<이름>_trajectories.json` | `--viz` | 위 trips·스냅샷·장비 구간과 OHT 초기 위치를 묶은 Rerun 재생 로그 (`ufast.viz.rerun_replay.show_run`) |
| `results/aggregate/aggregate.{json,csv}` | `ufast-aggregate results/` | 같은 설정(파일 기본 이름에서 `_s<시드>` 제외)끼리 묶은 다중 시드 `mean/std/min/max` 표 |

**JSON 블록** (co-simulation; logistics-only 는 `production`/`equipment` 대신 `transport` 블록)

| 블록 | 주요 키 |
|---|---|
| `meta` | `mode`(production/fromto), `run_id`, 입력 경로(`dataset_dir`, `rail_file`), `days`/`sim_duration_s`, `num_oht`, `seed`, 정책 슬롯 전부(`dispatcher`, `amhs_strategy`, `congestion_model`, `alpha`, `idle_positioning`, `routing_model`, `machine_selection`), warm-up(`static_warmup_days`, `amhs_settling_days`, `measurement_start_days`), **`vehicle`**(길이·차간·footprint·속도·가감속·직선/곡선 제한속도·출처), `wall_time_s`, `dispatch_steps`. 이 블록만으로 같은 실행을 재현할 수 있다 |
| `production` | `completed`(**전 구간 누적** 완성 로트 — 측정창 기준 집계는 `_lots.csv` 의 `done_at` 으로), `active`, 이송 횟수(`transport_count`, `static_transport_count`, `skipped_transport`, `same_node_transport`), `by_lot_type`·`by_category`(Regular/Hot/SuperHot 별 throughput·평균/중앙 cycle time·on-time·대기/가공/이송 시간) |
| `equipment` | 측정창(`measurement_start_s`~`_end_s`), 장비 수, `busy_time_s`, `setup_time_s`, `utilization_pct`, starvation 총량·횟수·분위수, **`transport_blocked_starvation_s/_pct`**(즉시 이송이었다면 없었을 기아) vs `upstream_starvation_s`, 고장/PM 시간, `by_family` 별 내역 |
| `amhs` | `total_requested_jobs`, `total_jobs`, `avg_free_flow_s`, `avg_transport_s`, `avg_empty_travel_s`, `avg_loaded_travel_s`, `avg_delivery_s`(요청→배달), `avg_oht_wait_s`, `delivery_/transport_{p50,p95,p99,max}_s`, `avg_congestion_factor`, `avg_utilization`, `max_queue`, `max_node_inflight`, queue 모델 전용 `blocked_events`, `blocked_time_s`, `deadlock_forced`, 섹션별 `blocked_by_section`·`blocked_time_by_section`(차단 히트맵 데이터), `reposition_count` |
| `transport` (logistics-only) | `lots_generated`(요청 수), `lots_completed`, `completion_rate`, `oht_count`, `oht_final_status` |

`ufast-analyze [json]` 은 이 JSON 을 읽어 콘솔 리포트(문헌 참조값 대비 포함)를 출력한다. 섹션별 **점유** 히트맵 원시값(`*_sections.csv`)은 실행기 출력이 아니라 `scripts/make_queue_occupancy_heatmap.py` 가 계측 런을 돌려 만든다.

---

## 2. Fromto(logistics-only) 모드 실행기 (`python -m ufast.cosim.run_fromto` / `ufast-fromto`)

생산 데이터(SMT2020 route/order/tool)가 없는 입력 시나리오에서, 레일 레이아웃과 fromto.dat(반송 수요표)만으로 AMHS 물류 시뮬레이션을 실행합니다. co-simulation 실행기와 **같은 `AMHSExecutor`** 를 쓰므로 용량 제약 blocking(queue)·데드락 해소·운동학 자유주행·배차/라우팅/idle 전략·커스텀 플러그인·KPI JSON/CSV·Rerun 재생이 동일합니다. 이벤트 큐만 생산층 대신 `HeapInstance` 가 맡습니다.

### 실행 명령어 구조
```bash
PYTHONPATH=src python -m ufast.cosim.run_fromto [rail_file] [fromto_file] [options]
```

### 위치 인수 (Positional Arguments)
* **`rail_file`**: `.rail` 레이아웃 파일 (기본값: `dataset/case1.rail`)
* **`fromto_file`**: `fromto.dat` — 탭 구분 텍스트, 각 행 `from_eq \t to_eq \t rate_per_hour` (3열은 시간당 발생율[건/시간], 행 순서가 시간대 순서; `3600/rate` 초 고정 간격으로 이벤트 생성) (기본값: `dataset/case1_Fromto.dat`)

### 상세 CLI 옵션 (Options)

| 옵션명 | 타입 | 선택지 및 포맷 | 기본값 | 설명 및 영향 |
| :--- | :--- | :--- | :--- | :--- |
| `--oht` | `int` | 양의 정수 | `50` | OHT 차량 대수 (Fleet Size) |
| `--duration` | `float` | 양의 실수 | `3600.0` | 시뮬레이션 종료 시각 (sim 초) |
| `--seed` | `int` | 정수 | `0` | 난수 시드값 |
| `--strategy` | `str` | `fifo`<br>`nearest`<br>`same_section`<br>`congestion` | `nearest` | OHT 배차(Assignment) 전략 (co-simulation 과 동일) |
| `--congestion` | `str` | `queue`<br>`section_local`<br>`global_tip`<br>`off` | `queue` | 혼잡 모델 — §1 과 동일 |
| `--alpha` | `float` | 실수 | `0.05` | delay 모델의 혼잡 계수 α |
| `--idle` / `--routing` | `str` | `off`/`on`, `off`/`dynamic` | `off` | idle 재배치, 혼잡 반응 재라우팅 — §1 과 동일 |
| `--custom-routing` / `--custom-assignment` / `--custom-idle` / `--custom-routing-cost` | `path` | `.py` / `.pkl` | 없음 | 커스텀 전략 플러그인 — §1 과 동일. 파일 이름만 주면 `strategies/` 에서 찾음 |
| `--vehicle-spec` | `path` | `.json` 또는 SMAT2022 `VehicleType.csv` | `dataset/vehicle_spec.json` | **OHT 차량 제원 파일**. 속도·가감속·차량 길이·최소 차간·직선/곡선 제한속도. 기본 파일 값 = SMAT2022 OHTV0. 아래 플래그가 파일 값을 덮어씁니다. |
| `--oht-speed` / `--oht-accel` / `--oht-decel` | `float` | m/s, m/s², m/s² | 파일 값 (5 / 2 / 3.5) | 최고속도·가속도·감속도 (사다리꼴 운동학 자유주행 시간에 반영) |
| `--oht-length` / `--oht-headway` | `float` | mm | 파일 값 (784 / 125) | 차량 길이·최소 차간. 합(footprint, 기본 909 mm)으로 `queue` 모델의 섹션 용량 = max(1, ⌊섹션 길이 / footprint⌋) 결정 |
| `--line-speed` / `--curve-speed` | `float` | m/s | 파일 값 (5 / 1) | 직선/곡선 링크 제한속도 |
| `--strict` | `flag` | `--strict` 추가 시 활성화 | `False` | **입력 정합성 엄격 모드**. fromto.dat 이 참조하는 설비가 레일 레이아웃의 EQ 목록에 없으면 실행을 중단합니다. 기본 동작은 미매칭 요약을 경고로 출력하고 해당 레코드를 이벤트 생성에서 제외합니다. |
| `--viz` | `flag` | `--viz` 추가 시 활성화 | `False` | Rerun 시각화 데이터 생성 및 재생 |
| `--engine` | `str` | `amhs`<br>`legacy` | `amhs` | `legacy` 는 이전 GUI 컨트롤러 기반 실행기(`run_legacy.py`: 상수 속도 1 m/s, blocking 없음) — 비교·회귀 용도 |

### 입력 정합성 (Data Consistency)
두 실행기 모두 시뮬레이션 시작 전에 입력 간 이름 매칭을 검사해 `consistency[...]` 요약 한 줄을 출력합니다.
* **fromto 모드**: fromto.dat 의 from/to 설비명 ↔ `.rail` 의 EQ 목록 (정확 일치)
* **production 모드**: tool.txt 의 STNFAM ↔ `.rail` 의 eq_to_node(대소문자 무시) 또는 `Equipment.csv` 매핑
* 미매칭 항목은 기본적으로 **경고 후 스킵**되며(결과 왜곡 가능), `--strict` 를 주면 즉시 종료 코드 1로 중단합니다.

---

## 3. 베이스라인 비교 및 캐싱 스크립트 (`scripts/compare_baselines.py`)

PySCFabSim(생산 전용), LogiFabSim(생산 + 단순 대기행렬 물류), U-FAST(생산 + 물리적 AMHS)의 결과를 비교 분석하고, 3-way KPI 그래프 및 성능 요약 보고서를 일괄 집계하는 실험 자동화 스크립트입니다.

### 실행 명령어 구조
```bash
python scripts/compare_baselines.py [options]
```

### 상세 CLI 옵션 (Options)

| 옵션명 | 타입 | 선택지 및 포맷 | 기본값 | 설명 및 영향 |
| :--- | :--- | :--- | :--- | :--- |
| `--days` | `int` | 양의 정수 | `30` | **시뮬레이션 기간**. 베이스라인 생성 및 U-FAST 실행의 기준 기간입니다. |
| `--datasets` | `list` | `HVLM` / `LVHM` / `LVLM`<br>(여러 개 입력 가능) | `["HVLM", "LVHM", "LVLM"]` | **실험할 생산 데이터셋 목록**. 공백으로 구분하여 다중 지정이 가능합니다. (예: `--datasets HVLM LVLM`) |
| `--seed` | `int` | 정수 | `0` | 난수 생성을 위한 시드값. |
| `--dispatcher` | `str` | `fifo` / `random` / `cr` 등 | `fifo` | 시뮬레이터들에 적용할 생산 Dispatching 룰. |
| `--alg` | `str` | `l4m`<br>`m4l` | `l4m` | **Lot allocation 알고리즘**.<br>• `l4m` (기본): 설비 기준 매칭 (Lot-for-Machine)<br>• `m4l`: 랏 기준 매칭 (Machine-for-Lot) |
| `--oht` | `int` | 양의 정수 | `100` | U-FAST Co-Sim을 가동할 때 배치할 OHT 대수. |
| `--simulators` | `list` | `pysc` / `logi` / `fills`<br>(여러 개 입력 가능) | `["pysc", "logi", "fills"]` | **평가 및 비교할 시뮬레이터 목록**. 캐싱된 데이터를 분석할 때 특정 시뮬레이터만 선택적으로 지정하여 동작 시간을 조율할 수 있습니다. |
| `--logi-cf` | `list` | `none` / `flat` / `linear` / `exp` | `["none"]` | LogiFabSim의 물류 혼잡 가중치(Congestion Factor) 옵션 리스트. |
| `--out-dir` | `str` | 폴더 경로 | `results/baseline_compare` | 그래프 및 집계표 결과물(`.csv`, `.png`)이 저장될 디렉토리. |
| `--summarize-only` | `flag` | `--summarize-only` 추가 시 활성화 | `False` | **리포트 재생성 모드**. 시뮬레이션을 다시 수행하지 않고, 이미 `--out-dir`에 저장된 시뮬레이터별 결과 JSON 파일들만 모아 그래프와 요약 리포트만 새로 빌드하고자 할 때 사용합니다. |
| `--baseline-python` | `str` | 실행 파일 경로 | `None` | **베이스라인 시뮬레이터 전용 Python 실행 경로**. 베이스라인 시뮬레이터의 성능 비교나 고속 측정을 위해 별도의 인터프리터(예: `PyPy3` 바이너리 경로)를 지정할 수 있습니다. 미지정 시 U-FAST와 동일한 파이썬으로 가동됩니다. |
| `--fills-source` | `str` | `run`<br>`load` | `run` | **U-FAST 결과 로드 방식**.<br>• `run`: U-FAST를 실시간으로 새로 시뮬레이션하여 비교합니다.<br>• `load`: 기존에 수행되어 캐시나 출력 경로에 존재하는 U-FAST 결과 파일을 불러옵니다. |
| `--warmup-days` | `float` | 양의 실수 | `0.0` | **통계 집계 제외 기간** (일 단위). 초기 웜업 구간의 불안정한 Lot 완료 통계를 필터링하여 정상 상태(Steady-State)의 데이터만 KPI에 집계하고자 할 때 적용합니다. |
| `--static-warmup-days` | `float` | 양의 실수 | `0.0` | U-FAST Co-Sim 실행 시 전달할 정적 웜업 일수. |
| `--amhs-settling-days` | `float` | 양의 실수 | `0.0` | U-FAST Co-Sim 실행 시 전달할 AMHS 안정화 일수. |
| `--machine-selection` | `str` | `exact`<br>`nearest` | `exact` | U-FAST Co-Sim 실행 시 전달할 설비 선택 모드. |
| `--mode` | `str` | `compare`<br>`sweep` | `compare` | **작동 방식 모드**.<br>• `compare`: 3-way KPI 비교를 수행하고 성능 표와 그래프를 그립니다.<br>• `sweep`: OHT 대수 변화(fleet size)에 따른 Sweep 실험과 Logi CF 오버레이 플롯을 생성합니다. |
| `--oht-list` | `list` | 정수 리스트 | `[20, 30, 50, 100, 200]` | `sweep` 모드일 때 U-FAST에서 테스트할 OHT 대수 범위 목록. |

---

### 💡 베이스라인 캐시 (Baseline Cache) 작동 원리

`PySCFabSim`과 `LogiFabSim`은 매 실행마다 물류 흐름이 정적이거나 모사 방식이 고정되어 있어 난수 시드와 기간이 동일하다면 완벽히 일관된(Deterministic) 결과를 보장합니다.

따라서 불필요한 시뮬레이션 연산 낭비를 막기 위해 다음과 같은 캐싱 정책이 작동합니다.
1. `compare_baselines.py`는 시뮬레이션을 돌리기 전 `results/baseline/` 디렉토리를 탐색합니다.
2. `[simulator]_[dataset]_[days]d_s[seed]_[cf].json` 형태의 캐시 파일이 존재하면, **시뮬레이터를 직접 가동하지 않고 캐시된 데이터를 즉시 복원**합니다.
3. 복원 시, 사용자가 CLI 파라미터로 넘긴 `--warmup-days` 등의 통계 제외 기간을 즉시 재계산(Re-aggregation)하여 필터링하므로, 캐시가 있더라도 유연하게 집계 구간을 조율할 수 있습니다.
4. 만약 캐시가 존재하지 않는다면 최초 1회 전체 시뮬레이션을 가동한 뒤, 캐시 디렉토리에 자동으로 기록하여 다음 실행부터 재사용합니다.

> [!TIP]
> **대용량 베이스라인 캐시 활용하기:**
> 장기간(예: 365일) 베이스라인 데이터를 미리 한 번 구축해두면, U-FAST의 제어 전략(`--machine-selection nearest` 등)을 고속 튜닝하면서 매번 1초 만에 베이스라인 비교 그래프를 도출할 수 있습니다.

> [!TIP]
> **현업 및 연구용 추천 최적화 조합 (속도 + 물리 정밀성):**
> 현업 및 연구용으로 **"속도와 현실적인 물리성"**을 모두 잡기 위해 가장 추천하는 실질적인 Baseline 조합은 바로 `--machine-selection exact` + `--routing off` 입니다. 이 조합 하에서 최적화 성능 튜닝을 하시면 훌륭한 신뢰성과 실행 속도를 동시에 얻으실 수 있습니다.
>
> **💡 라우팅 옵션과 실시간 혼잡 반영 메커니즘 안내:**
> U-FAST 시뮬레이터에서 경로 선택과 물리적 주행 시뮬레이션은 독립적으로 동작합니다. 따라서 `--routing off` 상태에서도 실시간 혼잡으로 인한 감속 페널티가 정상적으로 반영됩니다.
> * **1. 실시간으로 반영되는 것 (물리적 주행 시간 지연):**
>   `--congestion section_local` (기본값) 모델에 의해 OHT 차량이 레일 구간(Section)을 하나씩 통과하는 매 순간마다, 해당 구간을 달리고 있는 다른 OHT의 실시간 대수를 확인합니다. 만약 앞차들이 밀려 있어서 해당 구간에 OHT가 4대 있다면, 통과 시간 계산식인 `Base시간 * (1 + alpha * 4)`에 따라 속도가 줄어들어 **실시간 지연(Congestion Delay)이 물리적으로 정상 발생**합니다.
>   따라서 설비를 `exact`로 고를 때도 "해당 설비까지 정적 최단 거리로 달릴 때의 가감속 소요 시간"을 기준으로 판단하되, 실제 출발한 후에는 레일 위 상황에 따라 딜레이가 실시간으로 쌓이게 됩니다.
> * **2. 반영하지 않는 것 (실시간 혼잡 우회 경로 재탐색):**
>   * `--routing off` (추천): 차량이 출발지부터 목적지까지 갈 때 무조건 고정된 정적 최단 거리 경로로만 달립니다. 가령 앞에 차량 10대가 밀려 있어도 "우회하지 않고 묵묵히 그 경로로 가겠다"는 뜻입니다. (다만 1번의 룰에 의해 엄청난 감속 딜레이를 겪게 됩니다.) ➡️ *실제 Fab 물류 제어에 가장 가깝고, 연산이 매우 빠름.*
>   * `--routing dynamic`: 차량이 길을 찾을 때 "지금 레일 상황을 보니 저 구간이 꽉 막혔으니, 조금 멀더라도 뻥 뚫린 우회로로 돌아가야지" 하고 혼잡도에 따라 매번 새로운 다익스트라 경로를 탐색하는 모드입니다. 이 우회 경로 탐색은 경로 탐색 연산량이 엄청나기 때문에 실행 시간이 기하급수적으로 느려집니다.

---

## 4. 자주 사용하는 실행 시나리오 (Recipes)

### 🚀 시나리오 A: Co-Simulation 1일 시연 및 Rerun 시각화
가장 빠르게 co-simulation의 가동 상태를 눈으로 확인하고 OHT 이동 선로를 3D/2D로 재생하고자 할 때 사용합니다.
```bash
PYTHONPATH=src python -m ufast.cosim.run dataset/HVLM dataset/SMAT2022.rail --days 1 --oht 150 --viz
```

### ⚡ 시나리오 B: U-FAST 초고속 시뮬레이션 (Warm-up 최적화 및 nearest 적용)
60일 장기 시뮬레이션을 수행하되, 웜업 단계를 정적 시간 상수로 가속하고 최단 거리 설비 선택을 결합하여 가동 시간을 약 80% 이상 단축합니다.
```bash
PYTHONPATH=src python -m ufast.cosim.run dataset/HVLM dataset/SMAT2022.rail --days 60 --static-warmup-days 50 --amhs-settling-days 5 --machine-selection nearest
```

### 📊 시나리오 C: 베이스라인 1년(365일)치 대규모 캐시 빌드
U-FAST를 돌리지 않고, 생산 전용 시뮬레이터 PySC와 Logi의 1년치 기준 성능 지표를 모두 계산하여 `results/baseline/` 캐시 폴더에 영구적으로 보존합니다.
```bash
python scripts/compare_baselines.py --days 365 --simulators pysc logi --datasets HVLM LVHM LVLM
```

### 📈 시나리오 D: 캐시 데이터 기반 U-FAST 성능 비교 분석
이미 구축된 365일 베이스라인 캐시 데이터를 복원하여 로드하고, U-FAST만 빠르게 60일(웜업 50일 적용) 시뮬레이션하여 3개 엔진의 성능을 3-way 비교 플롯 및 CSV로 집계합니다.
```bash
python scripts/compare_baselines.py --days 60 --static-warmup-days 50 --warmup-days 50 --machine-selection nearest --simulators pysc logi fills
```

### 🔍 시나리오 E: OHT 대수 변화(Fleet Size)에 따른 성능 Sweep 분석
OHT 차량 대수를 20대부터 200대까지 점진적으로 증가시키며 U-FAST 성능 추이를 수집하고, Logi CF(Congestion Factor) 옵션들과 오버레이하여 물류 병목 현상을 비교하는 시각화 플롯을 도출합니다.
```bash
python scripts/compare_baselines.py --days 30 --mode sweep --oht-list 20 50 100 150 200 --datasets HVLM
```
