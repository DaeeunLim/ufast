"""
reference.py — reference KPI values from the LogiFabSim paper (Rank & Betker, 2025 IFAC).

ufast/cosim/analyze.py uses the dicts in this module as the baseline to compare
U-FAST results against.

Source: Sebastian Rank, Vincent Betker. "Comprehensive Simulation of Semiconductor
      Production: 'LogiFabSim' — Integrating Dynamic Transport Times in
      Production Planning." IFAC PapersOnLine 59-27 (2025) 208-213.
"""

# ── Paper Table 1 — validation experiment (SMT2020 HVLM 2 years, c=1 baseline) ──
# Comparison of Kovács et al. (2022) results | LogiFabSim results.
# Kovács transport time = uniform 300-600 s, LogiFabSim = t_ij (acceleration/deceleration model).
PAPER_TABLE_1_HVLM = {
    'days': 730,
    'dataset': 'HVLM',
    'note': 'Kovács et al. (2022) vs LogiFabSim, both 2-year HVLM',
    'by_lot_type': {
        'HotLot_3':      {'kovacs':     {'cycle_days': 33, 'throughput':   522, 'on_time_pct': 54},
                          'logifabsim': {'cycle_days': 31, 'throughput':   522, 'on_time_pct': 84}},
        'HotLot_4':      {'kovacs':     {'cycle_days': 18, 'throughput':   523, 'on_time_pct': 66},
                          'logifabsim': {'cycle_days': 18, 'throughput':   522, 'on_time_pct': 84}},
        'Lot_3':         {'kovacs':     {'cycle_days': 68, 'throughput': 19607, 'on_time_pct': 17},
                          'logifabsim': {'cycle_days': 66, 'throughput': 19671, 'on_time_pct': 18}},
        'Lot_4':         {'kovacs':     {'cycle_days': 39, 'throughput': 19961, 'on_time_pct': 13},
                          'logifabsim': {'cycle_days': 38, 'throughput': 19963, 'on_time_pct': 14}},
        'SuperHotLot_3': {'kovacs':     {'cycle_days': 33, 'throughput':    40, 'on_time_pct': 46},
                          'logifabsim': {'cycle_days': 31, 'throughput':    40, 'on_time_pct': 86}},
    },
}

# ── Paper Table 3 — correction-term sensitivity (SMT2020 HMLV=LVHM 2 years, c=1 baseline) ──
# Only the baseline (t̂_TIP = t_ij) values are used in this comparison. The fixed/linear/exp
# correction terms may later be used to compare against U-FAST's α correction.
PAPER_TABLE_3_HMLV_BASELINE = {
    'days': 730,
    'dataset': 'LVHM',
    'note': 'LogiFabSim baseline (c=1, no correction), 40 seeds avg',
    'by_category': {
        'Regular':  {'cycle_days': 54.0, 'throughput': 39522, 'on_time_pct': 14.6},
        'Hot':      {'cycle_days': 26.5, 'throughput':  1043, 'on_time_pct': 56.2},
        'SuperHot': {'cycle_days': 33.2, 'throughput':    40, 'on_time_pct': 58.8},
    },
    'wall_time_s': 5 * 60 + 43,   # 5:43 min, average over 40 seeds
}

# Deviation (%) from baseline when a correction term is applied — for later α-mapping calibration.
PAPER_TABLE_3_HMLV_DEVIATIONS = {
    'fixed_f2':      {  # c = 2 (constant, independent of TIP)
        'Regular':  {'cycle': +3.0, 'throughput': -0.4, 'on_time': -9.3},
        'Hot':      {'cycle': +7.6, 'throughput': -0.2, 'on_time': -83.3},
        'SuperHot': {'cycle': +7.6, 'throughput': +0.0, 'on_time': -86.4},
        'wall_time_dev': +0.3,
    },
    'linear_l1':     {  # c = 1 + TIP/TIPmax
        'Regular':  {'cycle': +1.8, 'throughput': -0.2, 'on_time': -5.6},
        'Hot':      {'cycle': +4.6, 'throughput': -0.1, 'on_time': -56.2},
        'SuperHot': {'cycle': +4.2, 'throughput': +0.0, 'on_time': -57.4},
        'wall_time_dev': +9.3,
    },
    'exponential_e2': {  # c = e^(TIP/TIPmax), e=2 scaling
        'Regular':  {'cycle': +0.8, 'throughput': -0.1, 'on_time': -4.7},
        'Hot':      {'cycle': +3.8, 'throughput': -0.1, 'on_time': -45.2},
        'SuperHot': {'cycle': +4.2, 'throughput': +0.0, 'on_time': -56.2},
        'wall_time_dev': +13.7,
    },
}

# ── Paper Table 2 — 1-year wall-time comparison ───────────────────────────
# The datasets in paper Table 2 are LVHM / LVHM_E (not HVLM). AutoSched AP is a
# commercial DES tool (used by Kopp). PINOKIO is a commercial simulator (Lee's group).
PAPER_TABLE_2_WALL_TIMES_1Y = {
    'Kopp_LVHM_AutoSched':     8 * 60,      # SMT2020 LVHM, no AMHS (Kopp et al. 2020)
    'Kopp_LVHM_E_AutoSched':  10 * 60,      # SMT2020 LVHM_E, no AMHS (Kopp et al. 2020)
    'Lee2022_PINOKIO_vehicle': 3750 * 60,   # SMT2020 LVHM_E + SMAT2022, vehicle-based
    'Lee2023_PINOKIO_cell':    202 * 60,    # SMT2020 LVHM_E + SMAT2022, cell-based
    'LogiFabSim':              2 * 60,      # SMT2020 LVHM + SMAT2022
}
