# U-FAST 통합 시뮬레이터

반도체 fab 의 **물류(AMHS/OHT)** 와 **생산(lot–machine 스케줄링)** 을 함께 다루는
시뮬레이터입니다. 실행 방법은 두 가지입니다.

- **CLI (기본)** — `python -m ufast.cosim.run` 으로 생산+물류 통합 시뮬레이션을 실행합니다.
  실험·논문용 주 경로이며, 옵션·결과 포맷은 `ufast/cosim/README.md` 참조.
- **GUI (보조)** — `python -m ufast.main_ui` 로 레일 도면 위 OHT 애니메이션과 생산 대시보드를
  제공합니다. 정책을 눈으로 검증하거나 데모할 때 사용합니다.

두 경로 모두 **같은 생산 엔진(`production/`, PySCFabSim 포크)** 을 사용합니다.

---

## 1. GUI 실행

```bash
pip install -r requirements.txt
pip install -e .          # 또는 PYTHONPATH=src
python3 -m ufast.main_ui   # 저장소 루트에서
```

우측 **Simulation** 패널이 컨트롤 타워입니다 — 상단 ▶Run/■Stop, 모드 선택,
그리고 모드별 **Run Options** 가 패널에 상주합니다 (Run 시점 다이얼로그 없음).
실행 중에는 옵션이 잠기고, 통계·범례는 실행 시 나타나는 **Results** 패널에
표시됩니다.

| 모드 | 입력 | 실행 방식 |
|------|------|-----------|
| **Logistics (AMHS)** | Layout(.dxf/.rail) + From-To 파일 | Live(실시간 애니메이션) 또는 Replay(기록 후 재생) |
| **Production (PySCFabSim)** | SMT2020 형식 데이터셋 폴더 | 전속력 계산 후 결과 표 표시 |

File 메뉴는 입력 종류별로 묶여 있습니다:
**Layout**(Load DXF/Rail, Save Rail) · **Flow Data**(Load FromTo) ·
**Production Data**(Add Dataset Folder…). 로딩된 입력은 패널의 **Input Files**
그룹에 항상 표시됩니다. **Reset All**(⌘N)은 확인 후 전체 초기화합니다.

### 물류 모드
1. File → Layout → Load Rail(또는 DXF) + Flow Data → Load FromTo
2. Run Options 에서 **Run mode** 선택:
   - **Live** — 시뮬레이션이 실시간으로 진행되며 OHT/EQ 가 화면에서 움직입니다.
     Speed 슬라이더(0.1–20x)로 배속 조절, 하단 Timeline 패널로 과거 스크럽.
     개체 동작(로직) 검증용.
   - **Replay** — 애니메이션 없이 전속력으로 실행하면서 집계를 기록하고, 종료
     시 **Rerun 뷰어**가 자동으로 열립니다. 레일 혼잡도 히트맵, OHT/EQ 상태,
     KPI 시계열이 `sim_time` 타임라인에 동기되어 배속·스크럽 재생됩니다.
     집계 분석용 — 장기(1년+) 실행도 프레임 상한(1만)으로 용량이 제어됩니다.
3. OHT 대수·실행 시간·KPI 저장 기준 등을 패널에서 설정하고 ▶ Run.
   커스텀 전략 4종(.py/.pkl)은 "Custom strategies…" 버튼으로 지정합니다.

### 생산 모드
1. File → Production Data → **Add Dataset Folder…** 로 데이터셋 폴더를 추가
   (필수 파일 자동 검증). 유효한 데이터셋이 없으면 Production 모드는
   선택할 수 없습니다.
2. Run Options 에서 일수/Dispatcher/Algorithm/Seed 설정 후 ▶ Run.
3. 계산 종료 즉시 대시보드에 Lot 유형별 처리량·사이클타임·정시율과 머신
   가동률이 표시되고 `logs/production/<timestamp>/` 에 저장됩니다.

> 데이터셋 폴더에는 `tool.txt.1l`, `fromto.txt`, `order.txt`, `WIP.txt`,
> `part.txt`, `setup.txt`, `setupgrp.txt`, `downcal.txt`, `pmcal.txt`,
> `attach.txt` 와 1개 이상의 `route_*.txt` 가 있어야 합니다.

---

## 2. 결과 저장

```
logs/
├── logistics/<timestamp>/    # 물류 Live 실행 — KPI/경로/검증 리포트
├── replay/<timestamp>/       # 물류 Replay 실행
│   ├── kpi_timeseries.parquet      # KPI 시계열
│   ├── section_congestion.parquet  # 프레임×섹션 혼잡도 (히트맵 원본)
│   └── replay.rrd                  # Rerun 기록 (rerun replay.rrd 로 재열람)
└── production/<timestamp>/   # 생산 실행 — summary.json + CSV 4종
```

seed 를 바꿔 반복한 CLI 실험은 `python3 -m ufast.cosim.aggregate` 로
구성별 KPI mean/std 를 집계할 수 있습니다.

---

## 3. 타임라인 (Live 모드)

- **실시간 진행 중**: 진행바가 현재를 추종하고, 드래그하면 시뮬레이션을 멈추지
  않은 채 과거 장면을 봅니다. "● Go LIVE"로 현재 복귀.
- **정지/완료 후**: ▶Play/진행바로 기록을 비디오처럼 재생.
- Replay 모드에서는 이 패널 대신 Rerun 뷰어가 재생·배속을 담당합니다.

---

## 4. 디렉터리 구조

```
repo/
├── pyproject.toml           # pip install -e . (콘솔 명령: ufast-run/analyze/aggregate)
├── dataset/                 # 레일(.rail)·From-To·SMT2020 데이터셋
├── results/                 # CLI 실험 결과 (생성물)
├── examples/                # 커스텀 전략 예제 (.py)
└── src/
    └── ufast/               # ★ 배포 패키지
        ├── main_ui.py       # GUI 진입점 (물류 앱 로직 + 모드 통합)
        ├── cosim/           # ★ CLI 통합 실행기 + 분석 (run/analyze/aggregate)
        ├── production/      # ★ 생산 DES 코어 (PySCFabSim 포크) — CLI·GUI 공용
        ├── route/           # 섹션 기반 라우팅/디스패치 (U-FAST AMHS)
        ├── control/ core/ layout/  # 물류 시뮬레이터 (From-To 구동)
        ├── drawing/         # CAD 도형 모델 (geometry.py)
        ├── common/          # 파서·로거·설정 등 공용 유틸
        ├── integration/     # GUI 통합 레이어 (timeline / production_runner / view)
        ├── gui/             # PyQt6 위젯 (viewer / layer panel)
        ├── viz/             # Rerun 시각화 (레이아웃·궤적 재생·GUI Replay 기록)
        └── verification/    # 결과 검증 리포트
```

> 2026-08-05: 표준 src-layout 으로 재편 — 모든 서브패키지가 `src/ufast/` 아래로
> 들어가고 `dataset`·`results` 는 저장소 루트로 이동. 구 `src/ufast`(실행기)는
> `ufast/cosim/` 이 되었습니다.

---

## Acknowledgements

- **생산 엔진**: `production/` 은 **PySCFabSim**(B. Kovács, P. Tassel) 의 코드
  포크입니다. U-FAST 확장: 이송 가로채기 훅(`_lot_ready_for_step`), 장비/노드
  매핑, 머신 예약.
- **이송시간 모델**: `cosim/kinematics.py` 는 **LogiFabSim**(Rank & Betker,
  2025 IFAC) 의 가감속 이송시간 모델을 따라 재구현한 것이며(reimplemented
  following Rank & Betker, 2025), `cosim/amhs.py` 의
  global TIP 혼잡계수 형태도 같은 논문을 따릅니다. `cosim/reference.py` 의
  비교 기준값 출처도 동일합니다.
- **데이터셋**: SMT2020(생산), SMAT2022(레이아웃/운송).
- **시각화**: Rerun (rerun.io), PyQt6.
