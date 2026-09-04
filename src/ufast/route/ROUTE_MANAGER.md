# route 패키지 기술 문서

## 개요

`src/ufast/route/` 패키지는 반도체 Fab AMHS(Automated Material Handling System)의 OHT(Overhead Hoist Transport) 레일 네트워크에서 **노드 단위 경로 탐색 및 관리**를 담당한다.

상용/사내 OHT 제어 시스템에서 쓰이는 노드 단위 경로 관리 개념을 시뮬레이션 목적에 맞게 재구현한 것으로, 실시간 하드웨어 제어 기능은 다루지 않으며 데드락 탐지, 우회 제한, C 가속 비용 탐색 등 시뮬레이션에 필요한 기능을 갖추고 있다.

---

## 패키지 구조

```
src/ufast/route/
├── __init__.py          # 패키지 export
├── graph.py             # 네트워크 데이터 구조 (Node, Link, NetworkSection, Network)
├── rail_parser.py       # RailData(공통 파서 common/rail_format.py 결과) → Network 변환
├── pathfinder.py        # Dijkstra 경로 탐색 + 우회 제한
├── fast_pathfinder.py   # scipy C 다익스트라 가속 엔진 (cost_search 전용)
├── route_manager.py     # 통합 API
├── bridge.py            # co-sim 연동 (섹션 경로 비용, 혼잡 penalty 갱신)
├── dispatcher.py        # OHT 배차 전략
├── vehicle_tracker.py   # 노드 점유 추적 + 데드락 탐지 (GUI 모드 전용)
├── logistics_logger.py  # 물류 이벤트 로거 (GUI/legacy 전용)
└── ROUTE_MANAGER.md     # 이 문서
```

---

## 주요 알고리즘

### 1. Dijkstra 최단 경로 (PathFinder)

`heapq` 기반 우선순위 큐로 `O(log n)` 삽입/추출을 달성한다.

```python
# heapq - O(log n) 삽입
heapq.heappush(heap, (new_time, node_name))
# O(log n) 추출
curr_time, curr_name = heapq.heappop(heap)
```

**탐색 흐름**:

```
1. 출발 노드를 arrived_time=0으로 설정, 힙에 추가
2. 힙에서 가장 작은 비용의 노드를 꺼냄
3. 해당 노드가 속한 모든 섹션에서 인접 노드를 탐색
4. 각 인접 노드에 대해:
   - new_cost = current_cost + move_in_time × effective_penalty
   - new_cost < 기존 arrived_time이면 갱신하고 힙에 추가
5. 도착 노드에 도달하면 prev_node 체인을 역추적하여 경로 구성
```

**섹션 기반 탐색**: 일반적인 인접 리스트 기반 Dijkstra와 달리, 노드가 속한 "섹션(Section)" 단위로 탐색한다. 섹션은 연속된 노드의 배열이며, 한 방향으로 순차 탐색한 후 섹션 끝 노드만 큐에 추가한다. 레일 네트워크가 "합류/분기점 사이의 긴 직선 구간"으로 이루어진 특성을 활용한 구조다.

```
섹션 S1: [A, B, C, D]
현재 노드 B (pos=1)에서:
  정방향: C → D (D가 섹션 끝이므로 큐에 추가)
  역방향: A (양방향일 때만, A가 섹션 끝이므로 큐에 추가)
```

### 2. CostSearch (1:N Dijkstra)

하나의 출발 노드에서 여러 도착 노드까지의 비용을 한 번에 계산한다. 설비 간 비용 테이블(`build_eq_cost_table`)과 co-sim의 섹션 경로 비용 추정(`bridge.estimate_section_route_cost`)에 사용한다.

PathSearch와 동일한 Dijkstra지만, 모든 도착 노드를 찾을 때까지 탐색을 계속하며 경로와 함께 거리(length)도 추적한다.

### 3. C 가속 엔진 (fast_pathfinder.FastCostEngine)

co-sim 실행 시간의 대부분을 차지하던 `cost_search`를 `scipy.sparse.csgraph.dijkstra`(C 구현)로 가속한다. 1일 HVLM co-sim 기준 전체 실행 97초 → 15.5초.

- 레일 토폴로지는 불변이므로 섹션 체인을 CSR 희소행렬로 **1회만 전개**한다.
- 엣지 가중치는 `move_time × effective_penalty(도착노드 penalty)`로 기존 비용식과 동일하며, OHT 진입/이탈로 penalty가 바뀌면 해당 노드로 들어오는 엣지만 제자리 갱신한다(bridge 훅 → `notify_penalty_changed`).
- 다음 경우 기존 파이썬 경로로 자동 폴백한다: 커스텀 비용 함수 설정 시(엣지별 파이썬 콜백은 C 경로로 표현 불가), scipy 미설치 시, 환경변수 `UFAST_FAST_ROUTE=0` 설정 시.
- 동률(같은 비용) 경로가 여럿일 때 선택이 파이썬 구현과 다를 수 있다. 비용 동일성은 `scripts/verify_fast_route.py`로 검증한다.

### 4. 우회 제한 (A+B 방식)

혼잡 penalty만으로 경로를 정하면 **과도한 우회가 반복되는 문제**가 생긴다. 이를 두 가지 방식을 조합해 해결한다.

#### 방안 A: Max Detour Ratio (경로 길이 상한)

동적 경로(penalty 반영)의 노드 수가 정적 경로(penalty 무시)의 `max_detour_ratio`배를 넘으면, 정적 경로로 폴백한다.

```
정적 경로: 1→2→3→4 (4노드)
동적 경로: 1→5→6→7→8→9→4 (7노드)
ratio = 7/4 = 1.75

max_detour_ratio = 1.5 → 1.75 > 1.5 → 정적 경로 사용
```

정적 경로는 캐시(`_static_cache`)하여 동일 출발-도착 쌍에 대해 재계산하지 않는다.

#### 방안 B: Bounded Penalty (감쇠된 penalty)

`traffic_penalty`의 영향을 `penalty_weight`와 `penalty_cap`으로 제한한다.

```
effective_penalty = 1 + weight × min(raw_penalty - 1, cap)
```

파라미터별 효과:

| 파라미터 | 의미 | 예시값 |
|----------|------|--------|
| `penalty_weight` | penalty 영향 비율 (0~1) | 0.5 = 50% 감쇠 |
| `penalty_cap` | penalty 최대 초과분 | 3.0 |

계산 예시 (`weight=0.5, cap=3.0`):

| raw_penalty | excess (raw-1) | bounded | effective |
|-------------|----------------|---------|-----------|
| 1.0 | 0 | 0 | 1.0 |
| 2.0 | 1.0 | 1.0 | 1.5 |
| 4.0 | 3.0 | 3.0 | 2.5 |
| 10.0 | 9.0 | 3.0 (capped) | 2.5 |

#### A+B 조합 동작 흐름

```
path_search(from, to):
  1. bounded penalty로 동적 경로 탐색 (_dijkstra)
  2. max_detour_ratio > 0 이면:
     a. 정적 경로 탐색 (캐시 활용)
     b. 동적 노드 수 / 정적 노드 수 > ratio 이면 → 정적 경로 반환
  3. 동적 경로 반환
```

#### 설정 방법

```python
rm = RouteManager()
rm.load_from_rail("layout.rail")
rm.initialize()

# 우회 제한 설정
rm.configure_detour_limit(
    max_detour_ratio=1.5,   # 정적 경로의 1.5배까지 허용
    penalty_weight=0.5,     # penalty 영향 50%
    penalty_cap=3.0,        # penalty 상한 3.0
)

# 시뮬레이션 후 통계 확인
stats = rm.get_detour_stats()
# {'total_searches': 1000, 'fallback_count': 23, 'fallback_ratio': 0.023, ...}
```

### 5. 데드락 탐지 (VehicleTracker)

**Wait-for 그래프**를 구축하여 사이클을 탐지한다. (GUI 모드 전용 — co-sim CLI는 delay 기반 혼잡 모델을 쓰므로 노드 점유 추적이 필요 없다.)

```
wait-for 관계:
  OHT_A → 노드 X로 이동하려 하지만, OHT_B가 점유 중
  OHT_B → 노드 Y로 이동하려 하지만, OHT_A가 점유 중
  → 사이클: [OHT_A, OHT_B] = 데드락
```

DFS로 사이클을 탐지하며, 여러 독립적 데드락을 모두 반환한다.

```python
deadlocks = rm.detect_deadlock()
# [['OHT_1', 'OHT_2'], ['OHT_5', 'OHT_6', 'OHT_7']]
```

---

## 데이터 구조

### Node

경로 탐색에 필요한 최소 필드만 갖는다: 위치(`name, x, y`), 소속(`hid, area, zone, virtual`), 혼잡(`traffic_penalty`), 토폴로지(`section_list, move_in_times`), Dijkstra 탐색 상태(`arrived_time, prev_node`).

### NetworkSection

연속된 노드의 배열로 이루어진 레일 구간. 이름이 `Section`이 아닌 `NetworkSection`인 이유는 `core.components.Section`(시뮬레이션 섹션)과 구분하기 위함이다.

### Network

모든 노드, 링크, 섹션, 설비 매핑을 관리하는 컨테이너.

---

## .rail 파일 포맷

탭 구분 텍스트 파일. 첫 줄은 `RAILDATA` 헤더.

| 레코드 타입 | 용도 | 형식 |
|-------------|------|------|
| NODE | 노드 정의 | `NODE {name} {x} {y}` |
| LINK | 링크 정의 | `LINK {name} {LINE/CURVE} {node1} {node2} [{penalty1} {penalty2}]` |
| EQTONODEMAP | 설비→노드 매핑 | `EQTONODEMAP {eq_name} {node_name}` |
| RAILLIST | 레일 기하 정보 | `RAILLIST {name} {type} {node1} {node2} {x1} {y1} {x2} {y2} {angle} [{length}]` |
| LINE | 시각화 라인 | (파싱만 하고 스킵) |
| CURVE | 시각화 커브 | (파싱만 하고 스킵) |
| SCALE | 도면 영역 | `SCALE {minX} {maxX} {minY} {maxY}` |

---

## 사용 예시

### 기본 사용

```python
from route import RouteManager

rm = RouteManager()
rm.load_from_rail("layout.rail")
rm.initialize(line_speed=2.6667, curve_speed=0.8)

# 노드 간 경로
path = rm.get_route("101", "250")

# 설비 간 경로
path = rm.get_route_by_eq("EQ_A", "EQ_B")

# 비용 테이블 (모든 설비 쌍)
cost_table = rm.build_eq_cost_table()
```

### 시뮬레이션 연동 (GUI 모드)

```python
# 차량 등록
rm.register_vehicle("OHT_1", initial_node="101")

# 경로 할당
path = rm.assign_route("OHT_1", to_node="250")

# 이벤트 루프에서
if rm.can_move_vehicle("OHT_1"):
    rm.move_vehicle("OHT_1")

# 데드락 확인
deadlocks = rm.detect_deadlock()
if deadlocks:
    handle_deadlock(deadlocks)
```

### 우회 제한 적용

```python
rm.configure_detour_limit(
    max_detour_ratio=1.5,
    penalty_weight=0.5,
    penalty_cap=3.0,
)

# 시뮬레이션 실행 후
stats = rm.get_detour_stats()
print(f"폴백 비율: {stats['fallback_ratio']:.1%}")
```

---

## 설계 특징 요약

| 항목 | 구현 |
|------|------|
| 우선순위 큐 | heapq O(log n) |
| 비용 탐색 가속 | scipy C 다익스트라 (CSR 행렬, penalty 부분 갱신) |
| 데드락 탐지 | wait-for 그래프 사이클 탐지 (GUI 모드) |
| 우회 제한 | A+B 방식 (detour ratio + bounded penalty) |
| 코딩 스타일 | dataclass, type hints, snake_case |
| 모듈 구성 | 탐색/파싱/추적/연동을 별도 모듈로 분리 |
