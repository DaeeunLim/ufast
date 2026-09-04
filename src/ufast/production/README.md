# src/ufast/production — PySCFabSim 기반 생산 DES

## 역할

SMT2020 형식의 fab 데이터셋(tool/route/order/WIP/setup/downcal/pmcal 등 탭 구분 텍스트)을 읽어 **lot–machine 스케줄링을 next-event 방식으로 시뮬레이션하는 생산 DES 코어**다(PySCFabSim 포크). 머신·스텝·로트 모델, 이벤트 큐, 디스패치 규칙(fifo/cr/random), 배치·셋업·리워크·샘플링·고장/PM을 담당하며, 그 자체로 물류를 모델링하지 않는다(스텝 간 이송은 `fromto.txt` 기반 정적 분포). U-FAST 통합 실행(생산+물류)을 위해 상류 원본 대비 **이송을 가로챌 수 있는 훅**(`Instance._lot_ready_for_step`), 장비/노드 매핑(`Machine.equipment_id`, `Machine.node_name`), 머신 예약(`reserved_lots`/`reserved_machine`)이 추가되어 있다.

## 파일별 역할

| 파일 | 핵심 클래스/함수 | 역할 |
|---|---|---|
| `instance.py` | `Instance` — `next_step()`, `free_up_lots()`, `_lot_ready_for_step()`, `dispatch()`, `next_decision_point()` | **시뮬레이션 코어.** 이벤트 소비, lot의 step 전진(리워크·샘플링 판정), 디스패치 시 처리/셋업/캐스케이딩 시간 산정, PM 카운트다운, CQT 위반 판정, 플러그인 훅 호출. `_lot_ready_for_step()`이 통합 실행이 물류를 끼워 넣는 확장점 |
| `file_instance.py` | `FileInstance(Instance)` | 파일 dict → 도메인 객체 변환. `STNQTY`만큼 `Machine` 생성(+장비 id/노드 부착), route step에 `fromto` 이송 분포 연결, order/WIP → `Lot` 생성, setup 표·downcal/pmcal → `BreakdownEvent` 구성 |
| `classes.py` | `Machine`, `Step`, `Lot`, `Route`, `FileRoute`, `Product` | 도메인 모델 — Machine(로드/언로드, family, 캐스케이딩, PM 카운터, 셋업 상태, min-run), Step(처리시간 분포, 배치 min/max, 샘플링·리워크 확률, CQT, dedication), Lot(우선순위, 마감, 누적 시간, `cr()`) |
| `events.py` | `MachineDoneEvent`, `LotDoneEvent`, `ReleaseEvent`, `BreakdownEvent` | 이벤트 타입. 고장/PM은 자기 자신을 다음 주기로 재등록 |
| `event_queue.py` | `EventQueue` | 타임스탬프 정렬 이벤트 큐 (이진 탐색 삽입) |
| `read.py` | `read_all()` | 데이터셋 디렉터리의 `*.txt`를 파싱해 `{파일명: [dict, ...]}` 반환. `NOWIP`/`NOBREAKDOWN`/`NOPM`/`NOREWORK`/`NOSAMPLING` 환경변수로 전처리기 적용 |
| `dataset_preprocess.py` | `Remove*` 전처리기 5종 | 데이터셋 단순화 — 고장/PM/WIP/리워크/샘플링 제거 |
| `tools.py` | `get_distribution()`, 분포 3종 | 단위 변환(sec/min/hr/day → 초)과 분포 객체 팩토리 |
| `randomizer.py` | `Randomizer` (Singleton) | 전역 단일 난수원. `SEED` 환경변수 또는 외부 시드 주입으로 재현성 확보 |
| `greedy.py` | `get_lots_to_dispatch_by_machine()`, `get_lots_to_dispatch_by_lot()`, `run_greedy()` | 디스패치 결정 로직 — 머신 기준(l4m)/로트 기준(m4l), 배치 구성, 셋업 일치 머신 스와핑. `run_greedy()`는 standalone CLI |
| `stats.py` | `print_statistics()` | lot별 cycle time/throughput/on-time, family별 가용률·가동률·PM·고장 통계 출력 및 JSON 저장 |
| `dispatching/` | `Dispatchers`, `dispatcher_map`, `LotForMachineDispatchManager`, `MachineForLotDispatchManager` | 우선순위 규칙(fifo/cr/random ptuple)과 l4m/m4l 매칭 관리 |
| `plugins/` | `IPlugin`, `CostPlugin` | 관측 훅 규약(`on_dispatch`, `on_lot_done` 등 11종)과 지각/미완 비용 KPI |

## 데이터 흐름

1. 로딩: `read_all()` → `FileInstance` → `Machine/Route/Step/Lot` + `BreakdownEvent` 생성.
2. 루프: `next_decision_point()` → `greedy`의 디스패치 후보 산출 → `dispatch()` → `MachineDoneEvent`/`LotDoneEvent` 등록.
3. lot 완료 시 `free_up_lots()`가 다음 step의 `_lot_ready_for_step()` 호출 — 기본 구현은 즉시 dispatchable, **통합 실행 서브클래스(`ufast.UFastInstance`)는 여기서 OHT 이송을 삽입**한다.
4. 관측은 `IPlugin` 훅으로 나가고 종료 시 `finalize()` → `on_sim_done`.

## 외부 의존과 주의사항

- `src/ufast/common.equipment_kpi` — starvation/downtime 계측 연동 (원본 대비 추가).
- 서드파티 없음 (표준 라이브러리만).
- 실사용자는 `src/ufast`(CLI)와 `src/ufast/integration/production_runner.py`(GUI) — 둘 다 이 패키지를 쓴다.
- 알려진 잔재: `greedy.run_greedy()`의 `--wandb`/`--chart` 옵션은 이 패키지에 없는 플러그인을 참조하므로 ImportError가 난다 (standalone 경로 한정, 통합 실행 경로와 무관).
