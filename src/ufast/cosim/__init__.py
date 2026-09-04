"""
ufast — integrated production (PySCFabSim) + logistics (U-FAST AMHS) simulation.

The production layer issues transport jobs between process steps, and the AMHS
layer physically executes those jobs with a finite OHT pool. Both layers share a
single next-event queue (the production Instance.events) and a single clock.
"""
