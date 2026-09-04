"""
reference.py — LogiFabSim 논문(Rank & Betker, 2025 IFAC) 의 참조 KPI 값.

ufast/cosim/analyze.py 가 본 모듈의 dict 를 baseline 으로 사용해 U-FAST 결과와 대조한다.

원전: Sebastian Rank, Vincent Betker. "Comprehensive Simulation of Semiconductor
      Production: 'LogiFabSim' — Integrating Dynamic Transport Times in
      Production Planning." IFAC PapersOnLine 59-27 (2025) 208-213.
"""

# ── 논문 Table 1 — 검증 실험 (SMT2020 HVLM 2년, c=1 baseline) ───────────────
# Kovács et al. (2022) 결과 | LogiFabSim 결과 의 비교.
# Kovács 의 transport time = uniform 300~600s, LogiFabSim = t_ij (가감속 모델).
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

# ── 논문 Table 3 — 보정항 민감도 (SMT2020 HMLV=LVHM 2년, c=1 baseline) ──────
# 본 비교에서는 baseline (t̂_TIP = t_ij) 값만 사용. fixed/linear/exp 보정항은
# 추후 U-FAST 의 α 보정 비교에 활용 가능.
PAPER_TABLE_3_HMLV_BASELINE = {
    'days': 730,
    'dataset': 'LVHM',
    'note': 'LogiFabSim baseline (c=1, no correction), 40 seeds avg',
    'by_category': {
        'Regular':  {'cycle_days': 54.0, 'throughput': 39522, 'on_time_pct': 14.6},
        'Hot':      {'cycle_days': 26.5, 'throughput':  1043, 'on_time_pct': 56.2},
        'SuperHot': {'cycle_days': 33.2, 'throughput':    40, 'on_time_pct': 58.8},
    },
    'wall_time_s': 5 * 60 + 43,   # 5:43 min, 40 seeds 평균
}

# 보정항 적용 시 baseline 대비 편차 (%) — 추후 α 매핑 보정에 사용.
PAPER_TABLE_3_HMLV_DEVIATIONS = {
    'fixed_f2':      {  # c = 2 (TIP 무관 상수)
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

# ── 논문 Table 2 — 1년 실행시간 비교 ─────────────────────────────────────
# 논문 Table 2 의 데이터셋은 LVHM / LVHM_E (HVLM 아님). AutoSched AP 는
# 상용 DES 도구(Kopp 본인이 사용). PINOKIO 는 상용 simulator(Lee 연구진).
PAPER_TABLE_2_WALL_TIMES_1Y = {
    'Kopp_LVHM_AutoSched':     8 * 60,      # SMT2020 LVHM, AMHS 없음 (Kopp et al. 2020)
    'Kopp_LVHM_E_AutoSched':  10 * 60,      # SMT2020 LVHM_E, AMHS 없음 (Kopp et al. 2020)
    'Lee2022_PINOKIO_vehicle': 3750 * 60,   # SMT2020 LVHM_E + SMAT2022, vehicle-based
    'Lee2023_PINOKIO_cell':    202 * 60,    # SMT2020 LVHM_E + SMAT2022, cell-based
    'LogiFabSim':              2 * 60,      # SMT2020 LVHM + SMAT2022
}
