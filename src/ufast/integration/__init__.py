"""
Integration package: 물류(main_ui) + 생산(PySCFabSim) 통합 레이어.

구성 요소
----------
- timeline.py        : 두 모드 공통 스냅샷/타임라인 기록 + 재생(스크럽) 엔진
- production_runner.py: PySCFabSim greedy 루프를 GUI 스레드에서 구동하는 어댑터
- production_view.py  : 생산 모드 전용 대시보드 위젯
"""

from .timeline import TimelineRecorder, Snapshot

__all__ = ["TimelineRecorder", "Snapshot"]
