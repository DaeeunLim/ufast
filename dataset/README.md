# src/Dataset — 데이터셋 및 레일 레이아웃

## 역할

시뮬레이션 입력 데이터 저장소. 생산 측은 SMT2020 계열 팹 데이터셋 3종(HVLM/LVHM/LVLM), 물류 측은 SMAT2022 AMHS 레이아웃 CSV와 이를 변환한 `.rail` 파일이 담당한다. legacy 물류 전용 실행에 쓰는 대용량 From-To 트레이스(`.dat`)와 변환 산출물도 함께 있다.

## 생산 데이터셋 (SMT2020 계열)

| 폴더 | 특징 |
|---|---|
| `HVLM/` | High-Volume/Low-Mix — 제품 수 적고 물량 많음. 기본 스모크·벤치마크 대상 (route 2종) |
| `LVLM/` | Low-Volume/Low-Mix (route 4종) |
| `LVHM/` | Low-Volume/High-Mix — 제품 믹스가 가장 복잡 (route 10종) |

공통 파일 구성 (탭 구분 텍스트):

| 파일 | 내용 |
|---|---|
| `tool.txt.1l` | 설비(STNFAM/STN) 정의 — 디스패치 rule, load/unload, capacity, setup group |
| `route_*.txt` | 공정 route — 스텝별 설비 family, 처리시간 분포, 배치, rework, CQT |
| `part.txt` / `order.txt` / `WIP.txt` | 제품→route 매핑 / 릴리즈 계획 / 초기 WIP |
| `downcal.txt` / `pmcal.txt` / `attach.txt` | 고장·PM 캘린더와 설비 그룹 부착 |
| `setup.txt` / `setupgrp.txt` | setup 전환 행렬 / 그룹 정의 |
| `fromto.txt` | 데이터셋 내장 정적 이송시간 분포 — AMHS 미사용 시 baseline |

## 레일 레이아웃 (.rail)

| 파일 | 역할 |
|---|---|
| `SMAT2022.rail` | **주력 레이아웃** — `smat2022_to_rail.convert()`가 SMAT2022 CSV에서 생성 (노드 2,858 / 링크 3,424 / 섹션 1,698) |
| `case1.rail` | 원본 case1 소규모 레이아웃 (legacy 포맷) |
| `case1_SMT2020_106_nospur.rail` | case1 기반 SMT2020 106설비 매핑 + spur 제거 |

## SMAT2022/ 원본 소스

`Adress.csv`(노드 2,857) · `Rail.csv`(링크 3,423) · `Equipment.csv`(장비 1,115대) · `VehicleType.csv`(OHT 사양: Vmax 5000, accel 2000, decel 3500 mm/s) · `transport_times_between_tool_groups.csv`(사전 계산 tool-group 간 이송시간) · 계산 스크립트 3종(`add_lengths.py`, `calc_transport_times.py`, `calc_mean_transport_times_between_families.py`)

## 기타

- `case1_Fromto.dat` (127만 행) — logistics-only 모드용 From-To 시간당 발생율(rate [건/시간]) 시계열 (`common/fromto_parser`가 읽음). 대형 팹 확장 예제(LVHM_E)는 용량 문제로 공개 저장소에 포함하지 않는다.
- `SMAT2022_family_node_map.csv` — 변환 산출물: tool group 108개별 대표 레일 노드 매핑

## 차량 제원 (`vehicle_spec.json`)

두 실행기(`ufast-run`, `ufast-fromto`)가 읽는 OHT 기본 제원. 단위 mm / mm/s / mm/s². 값은 `SMAT2022/VehicleType.csv` 의 OHTV0(길이 784, 최소 차간 125 → footprint 909 mm; Vmax 5000, accel 2000, decel 3500)에 직선/곡선 링크 제한속도(5000 / 1000 mm/s)를 더한 것이다. 실행 시 `--vehicle-spec PATH` 로 다른 파일을 주거나 `--oht-speed` 등 플래그로 개별 값을 덮어쓸 수 있고(플래그 우선), 실제 사용된 값은 결과 JSON `meta.vehicle` 에 남는다.

## 사용 맥락

```bash
cd src && python ufast/run.py Dataset/HVLM Dataset/SMAT2022.rail --days 365 --oht 100
```

`scripts/compare_baselines.py`의 기본 데이터셋 목록이 HVLM/LVHM/LVLM 3종이며, `tests/test_cosim_smoke.py`와 `scripts/verify_fast_route.py`도 이 폴더를 입력으로 쓴다. SMT2020/SMAT2022는 외부 공개 데이터셋이므로, 코드 공개 시 원본 재배포 대신 "원본 다운로드 → 변환기 실행" 안내가 안전하다.
