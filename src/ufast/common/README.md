# src/ufast/common — 공용 유틸리티

## 역할

U-FAST 전반에서 재사용되는 유틸리티 모음. 레이아웃/물류 입력 파일 파서(.dxf, .rail, FromTo), 시뮬레이션 KPI 수집·CSV 로깅, 커스텀 전략 플러그인 동적 로딩, 사후 검증 리포트 생성을 담당한다. 시뮬레이션 코어(`production`, `ufast`, `control`)와 GUI가 모두 이 폴더에 의존한다.

## 파일별 역할

| 파일 | 핵심 클래스/함수 | 역할 |
|---|---|---|
| `logger.py` | `SimulationLogger`, `get_logger()` | Lot·OHT·EQ 이벤트를 콜백으로 받아 누적하고, `save()`로 summary/lot/oht/eq/supply-delay CSV를 `kpi_mode`(logistics·production·both)와 `simulator_mode`에 따라 선택 저장 |
| `equipment_kpi.py` | `open_starvation`, `close_starvation`, `record_equipment_downtime`, `collect_equipment_kpis` | 설비 starvation·breakdown·PM 구간 기록, 측정 윈도우 클리핑 후 family별/전체 KPI(평균·p95·max·censored·union downtime) 집계 |
| `strategy_loader.py` | `load_strategy`, `invoke_*_strategy`, `normalize_*` | 사용자 `.py` 전략 파일을 4종 kind(`routing`/`assignment`/`idle_positioning`/`routing_cost`)로 동적 로드, 다양한 시그니처를 시도 호출 후 반환값을 표준 형식으로 정규화 |
| `rail_format.py` | `parse_rail_file`, `RailData` | **`.rail` 공통 파서** (순수 stdlib) — 파일을 중립 레코드 구조로 파싱. `rail_io.py`와 `route/rail_parser.py`가 모두 이 결과를 소비 |
| `rail_io.py` | `load_rail_file`, `save_rail_file` | `RailData` → 시각화용 `CLayer` + `SimulatorDataSet` Section/EQ 구축 (legacy 경로) |
| `smat2022_to_rail.py` | `convert(smat_dir, out_rail, out_map)` | SMAT2022 CSV(`Adress/Rail/Equipment.csv`) → U-FAST `.rail` 변환기. 노드명 정수 ID 재매핑, junction-to-junction 섹션 병합, tool group당 대표 노드 선정 + 매핑 리포트 |
| `dxf_parser.py` | `DXFParser.parse` | AutoCAD DXF → LINE/ARC/CIRCLE/TEXT/BLOCK을 `CLayer` 지오메트리로 변환 |
| `fromto_parser.py` | `load_fromto(filepath)`, `generate_fixed_interval_events()` | 탭 구분 FromTo 파일 → `(from_eq, to_eq, rate)` 리스트 및 고정 간격(3600/rate 초) 이벤트 생성 |
| `config_loader.py` | `ConfigLoader` (싱글턴) | `config/settings.json`에서 뷰어 외관 설정(색상·선 두께) 로드, 없으면 내장 기본값 |

## 사용 맥락

- `main_ui.py`: 파서 전체 + logger + strategy_loader 사용.
- 생산 코어(`production/instance.py`, `events.py`)와 `integration/production_runner.py`: `equipment_kpi` 호출.
- `control/controllers.py`, `ufast/amhs.py`: `strategy_loader`로 커스텀 전략 주입.
- `tests/`의 KPI 테스트들이 이 모듈들을 직접 단위 테스트한다.
