"""
Integration package: layer that integrates AMHS (main_ui) + production (PySCFabSim).

Components
----------
- timeline.py        : snapshot/timeline recording + replay (scrubbing) engine shared by both modes
- production_runner.py: adapter that drives the PySCFabSim greedy loop from a GUI thread
- production_view.py  : dashboard widget dedicated to production mode
"""

from .timeline import TimelineRecorder, Snapshot

__all__ = ["TimelineRecorder", "Snapshot"]
