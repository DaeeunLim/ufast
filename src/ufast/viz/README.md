# src/ufast/viz — Rerun 3D 시각화

## 역할

[Rerun](https://rerun.io) 뷰어로 fab 레이아웃과 OHT 이동을 시각화하는 모듈. Phase 1은 정적 레이아웃(레일 노드/링크/tool family 마커) 로깅, Phase 2는 시뮬레이션 중 기록한 OHT trip 궤적의 시간축 재생(replay)이다. 좌표 단위는 mm, SMAT2022 fab(약 300m × 153m) 기준으로 색상·크기가 튜닝돼 있다. GUI 없이 CLI에서 데모 가능한 시각화 경로다.

## 파일별 역할

| 파일 | 핵심 클래스/함수 | 역할 |
|---|---|---|
| `rerun_layout.py` | `show_layout(rail_file, ...)`, `log_layout_entities` | `.rail`을 `RouteManager`로 파싱해 노드 점·링크 선분·tool family 마커를 Rerun 엔티티로 로그. `python3 -m ufast.ufast.viz.rerun_layout [--rail ...]`로 단독 실행 가능 |
| `trajectory.py` | `Trip`, `TrajectoryLog`, `interpolate_trip_position` | OHT 1회 이송을 empty leg(→픽업)/loaded leg(→배달)로 나눠 기록. `results/<run_id>/*_trajectories.json` 저장/로드, 시각 t에서 누적 거리 비율 선형 보간으로 (x, y, state) 계산 |
| `rerun_replay.py` | `show_run(rail_file, trajectory_path, time_step_s=60.0, ...)` | 정적 레이아웃 위에 `sim_time` 타임라인으로 OHT 위치를 프레임별 로그 → 뷰어에서 스크럽·재생. 상태별 색상(IDLE 회색/EMPTY 파랑/LOADED 주황/AT_DEST 녹색) |
| `replay_recorder.py` | `ReplayRecorder` — `on_step()`, `finalize()` | GUI 물류 **Replay 모드** 기록기. 시뮬레이션 스레드에서 섹션 혼잡도·OHT/EQ 상태·KPI를 프레임 예산(기본 1만) 상한으로 다운샘플 기록 → 종료 시 Parquet(`logs/replay/<ts>/`) 저장 + Rerun 뷰어(레일 히트맵·KPI 동기 타임라인) 실행 |

## 사용 맥락

- `ufast/cosim/run.py --viz`: trip 기록 활성화 → 종료 시 `TrajectoryLog` JSON 저장 → `show_run()`으로 뷰어 실행. `run_legacy.py`도 동일.
- `python3 -m ufast.ufast.viz.rerun_replay <trajectory.json>`으로 저장된 결과를 나중에 재생 가능.
- `rerun` 패키지는 선택적 의존성 — 없으면 `_ensure_rerun()`이 명시적으로 실패하며, 시뮬레이션 실행 자체에는 영향 없다.
