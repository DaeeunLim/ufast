# U-FAST 프로젝트 구조

> U-FAST (Unified Fab-AMHS Simulation Toolkit) — 생산(fab) 시뮬레이션과 물류(AMHS) 시뮬레이션을 통합하는 co-simulation 툴킷.
> 이 문서는 저장소의 폴더별 역할과 상호 관계를 정리한 것이다. (작성: 2026-08-01, 2026-08-05 src-layout 재편 반영)

## 한눈에 보기

표준 src-layout: `src/ufast/`가 배포 패키지이고, 데이터·산출물은 저장소 루트에 있다.

```
repo/
├── pyproject.toml       # 패키지 정의 (pip install -e .)
├── dataset/             # SMT2020 데이터셋 + 레일 레이아웃 (227MB, 패키지 외부)
├── results/             # 실험 결과 (생성물, gitignore)
├── logs/                # 실험 로그 (생성물, gitignore)
├── examples/            # 커스텀 전략 플러그인 예제 4종
├── scripts/             # 실험·운영 스크립트
├── tests/               # pytest 스위트
├── docs/                # 사용자 문서 (CLI 가이드, AMHS 모드, 구조)
├── external/            # 벤더링된 베이스라인 시뮬레이터 (PySCFabSim, LogiFabSim)
└── src/
    ├── ufast/           # ★ 배포 패키지 (아래 상세)
```

실행 모드는 두 개:

- **CLI co-sim 모드** (`python -m ufast.cosim.run`) — 논문 실험용 헤드리스 실행. `cosim → production + route(+bridge) → common/viz`
- **GUI 모드** (`python -m ufast.main_ui`) — PyQt6 통합 UI. `main_ui → gui/core/drawing/control/layout/integration → route`

## src/ufast/ 서브패키지

### 핵심 (CLI co-sim 경로 — SoftwareX 공개 대상)

| 서브패키지 | 역할 |
|------|------|
| `ufast/cosim/` | **co-simulation 실행기.** `run.py`가 메인 엔트리(`ufast-run`). 생산 DES와 AMHS를 이벤트 단위로 연동(`instance.py`), 이송 실행(`amhs.py`), 차량 운동학(`kinematics.py`), 워밍업 정책(`warmup.py`), 결과 저장(`results.py`), 문헌 기준치 비교(`analyze.py`, `reference.py`), 멀티시드 집계(`aggregate.py`) |
| `ufast/production/` | **생산 시뮬레이터** — PySCFabSim 기반 DES. 데이터셋 로딩(`read.py`), 이벤트/디스패칭(`events.py`, `dispatching/`), 머신·로트 모델 |
| `ufast/route/` | **AMHS 경로 관리** — Dijkstra 탐색(+scipy C 가속), 혼잡 penalty, 우회 제한, co-sim 연동 bridge, 배차 전략. 상세는 `src/ufast/route/ROUTE_MANAGER.md` |
| `ufast/common/` | 공용 유틸 — KPI 로거(`logger.py`, `equipment_kpi.py`), 파서(`fromto_parser.py`, `dxf_parser.py`), **SMAT2022→.rail 변환기**(`smat2022_to_rail.py`), rail 입출력(`rail_io.py`), 커스텀 전략 로더(`strategy_loader.py`) |
| `ufast/viz/` | Rerun 기반 3D 시각화 — 궤적 기록(`trajectory.py`), 재생(`rerun_replay.py`), 레이아웃(`rerun_layout.py`), 종료 후 리포트(`report.py`). `--viz`로 GUI 없이 데모 가능 |
| `ufast/paths.py` | 저장소 기준 경로 상수 (`DATASET_DIR`, `RESULTS_DIR`) |

### GUI 스택 (옵션 A 공개 시 제외 검토 대상)

| 서브패키지 | 역할 |
|------|------|
| `ufast/main_ui.py` | GUI 메인 엔트리 (PyQt6 통합 UI) |
| `ufast/gui/` | GUI 위젯 — 메인 뷰어(`viewer.py`, 라이브 KPI 패널 포함), 레이어 패널(`layer_dialog.py`) |
| `ufast/core/` | GUI용 시뮬레이션 코어 — 컴포넌트(Section·설비 등), 이벤트 큐·전역 데이터셋 |
| `ufast/drawing/` | CAD 도형 데이터 모델 (`geometry.py` — CLine·CText·CLayer 등) |
| `ufast/control/` | 물류 이벤트 컨트롤러 (`controllers.py`) — OHT 상태 전이, Lot 생성/배달 이벤트 |
| `ufast/layout/` | 레일 도면 → Section 변환 (`rail_manager.py`) |
| `ufast/integration/` | GUI에서 생산 시뮬 실행·타임라인 뷰 (`production_runner.py`, `production_view.py`, `timeline.py`) |
| `ufast/verification/` | 사후 규칙 위반 점검 (`run_verification()` — 시뮬 종료 후 CSV 리포트, main_ui가 호출) |

### 산출물·내부용 (공개 제외)

| 폴더/파일 | 역할 | 비고 |
|------|------|------|
| `results/` | 실험 결과 (타임스탬프 폴더, baseline_compare 등) | 생성물, gitignore |
| `logs/` | 실험 로그 (로컬 + 원격 회수) | 생성물, gitignore |

## scripts/ 상세

| 스크립트 | 역할 |
|------|------|
| `compare_baselines.py` | U-FAST vs 베이스라인(PySCFabSim, LogiFabSim) 동일 조건 벤치마크 — 논문 wall-time 수치 생산 |
| `verify_fast_route.py` | C 다익스트라 엔진 ↔ 순정 파이썬 비용 일치 검증 |
| `exp*_*.sh` | 실험 배치 생성/실행 (E1/E2/E3) |
| `exp1_analyze.py` | E1 결과 집계·요약 CSV |
| `remote_run.sh` | 학교 서버(samhead-5090)에 코드 동기화 후 tmux로 실험 실행 |
| `remote_status.sh` | 서버 실험 상태/로그 확인 |
| `remote_fetch.sh` | 서버 결과·로그 회수 |

## 실행 진입점 요약

모든 명령은 **저장소 루트에서** 실행한다. `pip install -e .` 했다면 `PYTHONPATH=src` 없이 동작하고, `ufast-run`/`ufast-analyze`/`ufast-aggregate` 콘솔 명령도 생긴다.

```bash
# CLI co-sim (논문 실험)
PYTHONPATH=src python -m ufast.cosim.run dataset/HVLM dataset/SMAT2022.rail --days 365 --oht 100 --static-warmup-days 50

# 베이스라인 비교
python scripts/compare_baselines.py --days 365 --simulators pysc logi --datasets HVLM LVHM LVLM

# GUI
PYTHONPATH=src python -m ufast.main_ui

# 원격 실험
scripts/remote_run.sh hvlm365 dataset/HVLM dataset/SMAT2022.rail --days 365 --oht 100 --static-warmup-days 50
```

## 서브패키지 간 의존 방향

```
cosim ──► production          (생산 DES 구동)
cosim ──► route.bridge ──► route.pathfinder(+fast_pathfinder)
cosim ──► common (logger/KPI), viz (--viz), paths
route ──► common.rail_format (.rail 공통 파서 — 순수 stdlib)
gui/control/integration ──► route, core, drawing, common
scripts/compare_baselines ──► ufast.cosim (subprocess) + external/*
```

역방향 의존은 없다 (production과 route는 cosim을 모른다). GUI 스택을 통째로 들어내도 CLI 경로는 독립적으로 동작한다 — SoftwareX 옵션 A(CLI 중심 릴리스)가 가능한 이유.
