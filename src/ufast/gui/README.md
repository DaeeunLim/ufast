# src/ufast/gui — PyQt6 위젯/다이얼로그 레이어

## 역할

U-FAST 데스크톱 GUI의 **화면 구성 요소(위젯·도크·다이얼로그)** 를 담는 패키지. 메인 윈도우(`SimulationViewer`)가 레일 도면을 QGraphicsScene으로 그리고 OHT/EQ를 실시간 애니메이션하며, ①메뉴(File은 Layout/Flow Data/Production Data 그룹) ②중앙 스택(레일 뷰/생산 대시보드) ③Layer 패널(기본 숨김, DXF 로드 시 표시) ④Simulation 패널(▶Run/■Stop + 모드 + Input Files + 모드별 Run Options 상주 + Speed) ⑤Timeline 도크, 그리고 실행 시에만 나타나는 Results 도크(통계·범례)로 구성된다. 시뮬레이션 로직은 갖지 않고 시그널(`pyqtSignal`)로 `main_ui.SimulationApp`에 위임하는 순수 View 계층이다.

> `__init__.py` 없음 (암묵적 네임스페이스 패키지) — `src/`를 sys.path 루트로 두고 `from gui.viewer import ...` 형태로 import된다.

## 파일별 역할

| 파일 | 핵심 클래스 | 역할 |
|---|---|---|
| `viewer.py` | `SimulationViewer(QMainWindow)` | 메인 윈도우 — 그룹핑된 메뉴바, 중앙 `QStackedWidget`(0=레일 뷰, 1=생산 대시보드), Simulation 패널(Run Options 상주 — 물류: Run mode Live/Replay·OHT 수·시간·KPI 기준·전략 버튼 / 생산: 데이터셋·일수·seed 등), Results 도크, Timeline 도크. `get_logistics_options()`/`get_production_options()`로 옵션을 노출하고 시그널로 앱 로직에 위임. EQ 상태색은 `EQ_STATUS_STYLES` 단일 소스 |
| | `CADGraphicsView` | 휠 줌/미들버튼 팬/마우스 좌표 추적 CAD 뷰포트 |
| | `OHTItem` | OHT 원형 아이템 — 섹션 figure 위 선형/베지어 보간 위치 계산, 스냅샷 복원(`apply_snapshot`) |
| | `update_statistics()` | Lot/OHT 카운트 + `common.logger`의 라이브 KPI(이송·배송·호출대기·rundown·WIP) 표시 |
| `layer_dialog.py` | `LayerPanel(QDockWidget)` | CAD 레이어 테이블(Name/On/Rail) 도크. "RAIL" 포함 레이어명 자동 체크 |


## 데이터 흐름

- 진입점은 `main_ui.SimulationApp`이 생성하는 `SimulationViewer` 하나. 렌더링 데이터는 위젯이 소유하지 않고 `SimulatorDataSet.get_instance()` 싱글턴을 매 프레임 읽는 pull 방식.
- 과거 재생 시 `render_logistics_snapshot()` 호출 → `_playback_mode=True`로 라이브 애니메이션과 충돌 방지.

## 외부 의존

내부: `core.data_set`, `core.geometry`, `core.components`, `integration.production_view`(try/except), `common.config_loader`, `common.logger`. 서드파티: PyQt6.
