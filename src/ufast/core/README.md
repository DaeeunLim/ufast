# src/ufast/core — GUI용 시뮬레이션 코어 데이터 모델

## 역할

물류 시뮬레이터(GUI 모드)가 공유하는 **도메인 데이터 모델**만 모아둔 최하위 계층. 시뮬레이션 엔티티(OHT·EQ·Section·OHTBuffer)와 이벤트 큐를 담는 전역 싱글턴 저장소를 제공한다. PyQt 의존이 없어 headless 실행 경로(`ufast/cosim/run_legacy.py`)에서도 그대로 재사용된다.

여기의 `Section`/`OHTBuffer`는 GUI 모드의 **용량 제약 queue 기반 물류 모델**(유한 슬롯 버퍼, 진입 차단, FIFO)의 기반이다. co-sim CLI가 쓰는 지연 기반 모델(`ufast/cosim/amhs.py`)과의 차이는 참조.

> CAD 도형 dataclass(`CLine`·`CCircle`·`CText` 등)는 `src/ufast/drawing/geometry.py`로 분리됐다 (2026-08-05). `core`에는 시뮬레이션 엔티티만 남는다.
>
> `__init__.py` 없음 (암묵적 네임스페이스 패키지).

## 파일별 역할

| 파일 | 핵심 클래스 | 역할 |
|---|---|---|
| `data_set.py` | `Event` | 시뮬레이션 이벤트 — `time_scheduled`만 비교 키(heapq 정렬), `clone()` 제공 |
| | `SimulatorDataSet` (싱글턴) | 전역 저장소 — `sections`/`layers`/`eq_list`/`oht_list`, 노드·섹션 매핑, `event_queue`(heap), `main_clock`, 처리 카운터, 완료 로그. `get_instance()`, `clear()`, `add_event()`, `remove_event_from_queue()` |
| `components.py` | `OHT` | 차량 엔티티 — status(IDLE/ASSIGNED/LOADED/RUN/UNLOADING/REPOSITIONING), 현재 섹션·버퍼, 목적지, 적재 lot |
| | `EQ` | 설비 엔티티 — port/internal 버퍼, 5가지 status, `message_receiver()`가 LOT_CREATED/OHT_ASSIGNED/LOT_TRANSPORTED/LOT_INBOUND/LOT_DELIVERED 메시지로 상태 전이 |
| | `Section` | 레일 구간 — 길이·속도, `next_sections`/`prev_sections`, `oht_buffers`(길이/1.0 = 용량), `merge_figures()`, `message_receiver()`로 선두 차량만 이동 허용 |
| | `OHTBuffer` | 섹션 내 차량 슬롯 배열 — 용량 체크, 선두 판정, compaction |

## 데이터 흐름

- `SimulatorDataSet.get_instance()` 싱글턴을 `main_ui`·`gui.viewer`·`control.controllers`·`layout.rail_manager`가 모두 공유한다.
- 쓰기: `layout.rail_manager`가 sections/eq_list를 채움 → `control.controllers`가 oht_list·event_queue·main_clock 갱신 → `gui.viewer`가 매 프레임 읽어 렌더링.
- `data_set.py`는 다른 core 모듈을 import하지 않는다 (순환 방지).

## 외부 의존

- `src/ufast/drawing` — `components.py`가 `Figure`/`CLine`/`CQuadCurve`를 사용 (Section 시각화 도형)
- 그 외 표준 라이브러리만 사용 (`dataclasses`, `heapq` 등). 서드파티·PyQt 의존 없음.
