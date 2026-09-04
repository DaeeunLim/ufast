# tests — pytest 스위트

## 역할

핵심 KPI 계산, 결과 저장 규약, 입력 파서, 큐(blocking) 모델의 동작을 보호하는
경량 회귀 테스트 모음 (48 케이스, 2초 안팎). 무거운 시뮬레이션 대신
`SimpleNamespace` 더미 객체와 0일 스모크 실행으로 빠르게 돌도록 설계됐다.
GitHub Actions(`.github/workflows/tests.yml`)가 push/PR 마다 ubuntu·macOS ×
Python 3.10·3.13 조합으로 실행한다.

## 파일별 역할

| 파일 | 검증 대상 |
|---|---|
| `test_equipment_kpi.py` | `common/equipment_kpi`의 측정 윈도우 의미 — starvation의 measurement_start 클리핑, breakdown+PM union 합산, 미종료 starvation의 censored 분리, `run_to` 상한 |
| `test_logistics_kpi.py` | `SimulationLogger`의 OHT 시간 배분 — 상태 전이 후 `finalize_oht_times` 중복 호출 멱등성, idle/assigned/loaded 배분과 utilization 계산 |
| `test_result_paths.py` | `cosim/results`의 파일명·디렉터리 규약 — `results/<run_id>/` 그룹화, `HVLM_60d_100oht_a0.05_fifo_fifo_sl_s0.json` 형식, `meta.run_id` 보존 |
| `test_ufast_smoke.py` | (1) `requirements.txt` UTF-8 및 numpy/scipy 포함 (2) `run_ufast` 0일 실행 스모크 — 머신 로드, 비교표 출력 (3) `strategy_loader` 반환값 정규화 |
| `test_queue_model.py` | capacity-constrained queue 모델 — 섹션 용량 계산, FIFO 진입 차단/깨움, wait-for 사이클 데드락 탐지와 강제 진입 집계 |
| `test_fromto.py` | `common/fromto_parser` — 3열 시간당 발생율(rate) 해석, `3600/rate` 고정 간격 이벤트 생성, 시간대별 rate 순환; `run_fromto` 1시간 스모크(case1, blocking 엔진, 결과 JSON 의 mode/engine/vehicle/blocking 키) |
| `test_vehicle_spec.py` | `cosim/vehicle` — 기본 파일 = SMAT2022 OHTV0, CSV↔JSON 일치, 플래그 덮어쓰기·단위 변환(m/s→mm/s), 사용자 JSON 부분 지정, meta·kinematics 변환 |
| `test_consistency.py` | `common/consistency` — FromTo 설비명 ↔ `.rail` EQ 목록, 데이터셋 tool family ↔ rail 목적지 정합성 검사와 `--strict` 동작 |

## 실행

```bash
python -m pytest tests/ -q
```

`test_ufast_smoke.py`는 `dataset/HVLM` + `dataset/SMAT2022.rail` 실물 데이터를
쓰므로 데이터셋/.rail 포맷이 깨지면 여기서 먼저 잡힌다. 라우팅/AMHS 로직
자체는 `scripts/verify_fast_route.py`가 별도 검증한다.
