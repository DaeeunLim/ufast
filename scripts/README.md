# scripts — 벤치마크 및 원격 실험 스크립트

## 역할

논문용 실험을 실행·수집하는 자동화 스크립트 모음. Python 스크립트는 3-way 시뮬레이터 비교(E1)·OHT sweep(E2) 벤치마크와 경로탐색 엔진 검증을, 셸 스크립트는 원격 서버에서 장기 실험을 tmux로 돌리고 결과를 회수하는 원격 워크플로를 담당한다.

원격 스크립트를 쓰려면 서버 접속 정보를 먼저 설정한다 (개인 설정이라 git에 올라가지 않는다):

```bash
```

## 파일별 역할

| 파일 | 역할 |
|---|---|
| `compare_baselines.py` | **핵심 벤치마크 러너.** PySCFabSim(`external/PySCFabSim-release`, 저장소에 미포함 — `git clone https://github.com/prosysscience/PySCFabSim-release external/PySCFabSim-release`), LogiFabSim(`external/j3c-fork`, 미포함 — 저자에게서 입수; 경로는 `UFAST_PYSC_DIR`/`UFAST_LOGI_DIR` 환경변수로 변경 가능. U-FAST 자체 실행에는 둘 다 필요 없음), U-FAST를 서브프로세스로 실행하고 결과를 공통 KPI로 정규화·요약·그래프화. `--mode compare`(3-way 비교, E1) / `--mode sweep`(OHT 대수 sweep, E2). warm-up 제외는 완료 시각(`done_at`) 기준. `--baseline-python`으로 베이스라인 인터프리터(PyPy 등) 분리 지정 가능. baseline 결과는 `results/baseline/`에 캐시 |
| `verify_fast_route.py` | scipy C 다익스트라(`route/fast_pathfinder`) ↔ 순정 `cost_search` 동등성 검증 — 무작위 혼잡 상태 쿼리의 비용 상대오차 1e-9 일치 확인(동률 경로는 다를 수 있어 비용 기준). 불일치 시 exit 1 |

## 사용 예

```bash
# 3-way 비교 (E1)
python scripts/compare_baselines.py --days 365 --simulators pysc logi --datasets HVLM LVHM LVLM


# C 엔진 회귀 검증
python scripts/verify_fast_route.py
```
