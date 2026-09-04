# src/ufast/verification — 사후 검증

## 역할

시뮬레이션 동작에는 개입하지 않고, 종료 시점의 상태와 logger 기록을 기반으로
규칙 위반(rule violation) 리포트를 생성한다. 공용 유틸(`src/ufast/common/`)과 성격이
달라 별도 폴더로 분리했다 (2026-08-01, 구 `my_utils/verification.py`).

## 파일별 역할

| 파일 | 핵심 함수 | 역할 |
|---|---|---|
| `verification.py` | `run_verification()` | 종료 후 규칙 위반 점검 — OHT 상태/섹션(V0xx), 버퍼(V1xx), Lot 타임스탬프(V2xx), rundown 순서(V3xx), 트래커 등록(V4xx) CSV 리포트 |

## 사용 맥락

- `main_ui.py`가 시뮬레이션 종료 후 로그 저장 시 `run_verification()`을 호출한다.
