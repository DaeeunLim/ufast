# src/ufast/layout — 레이아웃 변환

## 역할

CAD/도면 세계와 시뮬레이션 세계를 잇는 **레이아웃 변환 계층**. `rail_manager.py`는 로딩된 `CLayer` 도형들 중 rail 레이어의 직선들을 그래프로 해석해 `Section` 객체망(연결 관계·길이·버퍼)으로 변환하고, 도면 텍스트를 최근접 레일 노드에 매핑해 `EQ`를 생성한다.


## 파일별 역할

| 파일 | 핵심 클래스/함수 | 역할 |
|---|---|---|
| `rail_manager.py` | `RailManager.build_layout(layers)` | 핵심 변환 파이프라인 — ① rail 레이어의 `CLine` 수집(없으면 기존 layout 유지) ② 좌표 반올림으로 node_map 구축 ③ degree≠2 분기/말단 노드에서 출발해 연속 선분을 하나의 `Section`으로 병합 ④ 끝점 거리 <1.0이면 `next_sections`/`prev_sections` 연결 ⑤ EQ 바인딩 |
| | `_bind_eq_from_text()` | 모든 레이어의 `CText`를 최근접 레일 노드에 매핑(`MAX_EQ_DISTANCE=5000.0`) → `EQ` 생성 및 Section에 등록 |

## 데이터 흐름

- `main_ui`의 `load_rail_file()`/`load_layout_file()`(DXF) → `rail_manager.build_layout(layers)` → `SimulatorDataSet.sections`/`eq_list` 채움 → `viewer.draw_layout()`. 레이어 패널의 "Convert to Rail"도 같은 경로.
- `RailManager`는 반환값 없이 싱글턴에 직접 write한다.

## 외부 의존

내부: `ufast.drawing.geometry`, `ufast.core.components`, `ufast.core.data_set`. 서드파티 없음.
