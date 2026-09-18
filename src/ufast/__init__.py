"""
U-FAST (Unified Fab-AMHS Simulation Toolkit)
— an integrated co-simulation toolkit for production (fab) simulation and AMHS simulation.

Subpackages:
  cosim        Integrated runner (run/analyze/aggregate, AMHS core)
  production   Production DES (PySCFabSim fork)
  route        AMHS route management (Dijkstra + congestion penalty)
  common       Shared utilities: parsers, loggers, strategy loader
  drawing      CAD figure data model
  core         Simulation entities for the GUI (OHT, EQ, Section)
  control      AMHS event controllers (GUI mode)
  layout       Rail drawing ↔ Section conversion
  gui          PyQt6 widgets
  viz          Rerun 3D visualization
  integration  GUI hooks for the production simulation
  verification Post-run rule-violation checks
"""

__version__ = "1.0.0"
