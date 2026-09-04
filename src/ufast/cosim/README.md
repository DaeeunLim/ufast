# src/ufast/cosim — U-FAST Co-simulation 실행기

## 역할

`ufast.cosim`은 생산 시뮬레이터(PySCFabSim 기반 `src/ufast/production`)와 물류 시뮬레이터(section 단위 AMHS)를 **하나의 이벤트 큐·하나의 클럭 위에서 함께 돌리는 통합 시뮬레이션 실행기**다(2026-08-05 src-layout 재편으로 구 `src/ufast` 최상위에서 `ufast/cosim` 서브패키지로 이동). 생산 레이어가 공정 step 사이마다 이송 작업을 발주하면, 물류 레이어가 유한한 OHT 풀로 그 이송을 section transition 이벤트 단위로 물리 수행하고 완료 콜백으로 생산 레이어를 깨운다. 생산 데이터가 없는 `fromto.dat` 단독 시나리오용 헤드리스 실행 경로(legacy/fromto 모드)와, 결과 JSON/CSV 저장·KPI 분석·Rerun 시각화 연동까지 같은 패키지 안에 포함한다.

## 파일별 역할

| 파일 | 핵심 클래스/함수 | 역할 |
|---|---|---|
| `run.py` | `run_ufast(...)` | **production 모드 진입점.** 레일/데이터셋 로드 → RouteManager·SectionNodeBridge·AMHSExecutor·UFastInstance 조립 → 디스패치 루프 실행 → 결과 저장/분석/시각화. CLI 는 argparse 기반(`--help` 참조): `--days --oht --seed --alpha --strategy --dispatcher --congestion --idle --routing --machine-selection --static-warmup-days --amhs-settling-days --viz` + 커스텀 전략 파일 `--custom-routing/--custom-assignment/--custom-idle` (.py/.pkl, GUI Custom strategies 와 동일 로더) |
| `amhs.py` | `AMHSExecutor`, `OHT`, `TransportJob`, `SectionEnterEvent`, `ArriveEvent`, `RepositionTickEvent` | **물류 코어.** OHT 배차, section 시퀀스 경로 산출, 각 section 진입마다 이벤트 발사, 진입 시점 점유(`section_inflight`) 기반 통과시간 보정(밀림), idle reposition, KPI 스냅샷/trip 로그 수집 |
| `instance.py` | `UFastInstance(FileInstance)`, `StaticTransportDoneEvent` | **생산↔물류 seam.** `_lot_ready_for_step()`을 오버라이드해 step 사이 이송을 `amhs.request_transport()`로 위임하고, 배달 콜백(`_on_delivered`)에서 lot을 다시 dispatchable로 만든다. 목적지 머신 선택(`machine_selection='exact'|'nearest'`), route의 정적 `transport_time`을 0으로 치환 |
| `vehicle.py` | `VehicleSpec`, `load_vehicle_spec()`, `add_vehicle_args()`, `spec_from_args()` | **OHT 차량 제원** (길이·최소 차간·속도·가감속·직선/곡선 제한속도). 우선순위 플래그 > `--vehicle-spec` 파일 > `dataset/vehicle_spec.json` > 내장값. 두 실행기가 공유하며 값은 결과 JSON `meta['vehicle']` 에 기록 |
| `kinematics.py` | `VehicleKinematics.edge_time()`, `.path_time()` | OHT 가감속(트래피저이드) 운동학 free-flow 이송시간. LogiFabSim 의 이송시간 모델 재구현(reimplemented following Rank & Betker, 2025) — baseline을 논문과 동일하게 맞추기 위한 모듈 |
| `warmup.py` | `WarmupPolicy` (frozen dataclass) | static warm-up / AMHS settling 구간 정의. warm-up 동안은 AMHS 대신 정적 transport 분포를 쓰고 KPI 측정 시작 시각을 정한다 |
| `equipment_mapping.py` | `load_machine_equipment()`, `infer_layout_dir()` | `Equipment.csv`의 장비 좌표를 파싱해 tool group(STNFAM)별 장비 리스트를 만들고, 각 장비를 가장 가까운 rail 노드에 붙인다 → 머신 단위 이송 목적지 확보 |
| `results.py` | `collect_results()`, `save_results()`, `save_csv_exports()`, `auto_result_path()` | 실행 결과를 KPI dict로 집계해 JSON 저장 + CSV 4종(`trips`/`kpi_timeseries`/`lots`/`machines`) export. 집계 단위는 lot type별·category(Regular/Hot/SuperHot)별 |
| `analyze.py` | `analyze_one()`, `main()` | 결과 JSON을 읽어 콘솔 KPI 리포트 출력 + LogiFabSim 논문 Table 1/2/3 대비 비교. 인자 없이 실행하면 `results/` 최신 JSON 자동 로드 |
| `reference.py` | `PAPER_TABLE_1_HVLM`, `PAPER_TABLE_2_WALL_TIMES_1Y`, `PAPER_TABLE_3_HMLV_BASELINE` | LogiFabSim(Rank & Betker, IFAC 2025) 논문의 참조 KPI 값 — `analyze.py`의 비교 baseline |
| `machine_activity.py` | `MachineActivityPlugin(IPlugin)` | 머신 busy 구간 `(start, end, family)` 기록 플러그인 — Rerun 재생의 family 부하 시각화용 |
| `heap_instance.py` | `HeapInstance` | 생산 레이어 없이 AMHSExecutor만 돌릴 때 이벤트 큐를 대신하는 경량 shim |
| `fromto_amhs.py` | `setup_fromto_amhs()` | `fromto.dat` 수요를 AMHSExecutor + HeapInstance 조합으로 구동. 시간당 발생율 기반 고정 간격(3600/rate 초) 스케줄 |
| `run_fromto.py` | `run_fromto()`, `main()` | **fromto(logistics-only) 모드 진입점 (`ufast-fromto`).** `.rail` + FromTo 수요표를 `setup_fromto_amhs()` 로 AMHSExecutor(blocking 엔진)에 연결해 co-simulation 과 같은 옵션·KPI·CSV·Rerun 으로 실행 |
| `run_legacy.py` | `run_legacy_fromto()` | 이전 fromto 실행기 (`ufast-fromto --engine legacy`). legacy 컴포넌트(`common/rail_io`, `core/data_set`, `control/controllers`: 상수 속도 1 m/s, blocking 없음)를 PyQt 타이머 없이 순수 next-event 루프로 구동 — 비교·회귀 용도 |
| `legacy_trajectory.py` | `LegacyTrajectoryRecorder` | legacy `VehicleController`의 OHT 상태를 관찰해 production 모드와 동일한 `TrajectoryLog` 포맷으로 변환 (Rerun 재생 공통화) |

## 데이터 흐름

1. **production 모드**: `run_ufast()` → 데이터셋/레일 로드 → `AMHSExecutor` + `UFastInstance` 조립.
2. 메인 루프: `instance.next_decision_point()` → `production.greedy.get_lots_to_dispatch_by_machine()` → `instance.dispatch()` 반복 — **생산 이벤트와 AMHS 이벤트가 같은 큐**를 소비한다.
3. Seam: lot이 다음 step 준비되면 `_lot_ready_for_step()` → `request_transport()` → `SectionEnterEvent` 체인 → `ArriveEvent` → `_on_delivered()` → 생산 재개.
4. warm-up 구간에서는 AMHS를 우회하고 `StaticTransportDoneEvent`로 정적 이송시간 사용.
5. 종료 후 `results` 저장 → `analyze` 비교 리포트, `--viz`면 Rerun 재생.

## 외부 의존

- `src/ufast/production` — DES 코어, 디스패처, 난수원
- `src/ufast/route` — `RouteManager`, `SectionNodeBridge`, 배차 전략
- `src/ufast/common` — 커스텀 전략 로더, 장비 KPI, rail/fromto 파서
- `src/ufast/core`, `src/ufast/control` — legacy 모드 전용
- `src/ufast/viz` — 선택적 (Rerun lazy import)
- 서드파티는 표준 라이브러리만 사용. 호출자는 CLI와 `gui/cosim_dialog.py`
