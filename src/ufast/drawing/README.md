# src/ufast/drawing — CAD 도형 데이터 모델

## 역할

도면(DXF/.rail) 세계의 **CAD 도형 dataclass**만 담는 최하위 계층. 직선·원·텍스트·2차 베지어와 레이어를 표현하며, 파서(`common/dxf_parser`·`common/rail_io`)가 이 도형들을 만들고 레이아웃 변환(`layout/rail_manager`)과 GUI 렌더링(`gui/viewer`)이 소비한다. 원래 `src/ufast/core/geometry.py`였으나 시뮬레이션 엔티티(`core`)와 도면 표현을 분리하기 위해 독립 패키지로 이동했다 (2026-08-05).

> `__init__.py` 없음 (암묵적 네임스페이스 패키지).

## 파일별 역할

| 파일 | 핵심 클래스 | 역할 |
|---|---|---|
| `geometry.py` | `Figure`, `CLine`, `CCircle`, `CText`, `CQuadCurve`, `CLayer`, `FigureBlock` | CAD 도형 dataclass — 직선, 원, 텍스트(EQ 이름 추출용), 2차 베지어, 레이어(`is_rail_layer` 플래그) |

## 사용처

- `common/dxf_parser.py`·`common/rail_io.py` — DXF/.rail 파일을 파싱해 도형 생성
- `layout/rail_manager.py` — 도형 → `Section` 그래프 변환
- `core/components.py` — `Section.figures` 시각화 도형
- `gui/viewer.py`·`gui/layer_dialog.py`, `viz/replay_recorder.py` — 렌더링·기록

## 외부 의존

표준 라이브러리만 사용 (`dataclasses`). 서드파티·PyQt 의존 없음.
