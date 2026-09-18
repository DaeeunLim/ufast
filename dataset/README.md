# dataset/ — Datasets and rail layouts

## Role

Input data store for the simulation. On the production side there are three SMT2020-family fab
datasets (HVLM/LVHM/LVLM); on the logistics side, the SMAT2022 AMHS layout CSVs and the `.rail` file
converted from them. The From-To demand table (`.dat`) used by the logistics-only runner
(`ufast-fromto`), the default OHT vehicle specification and the conversion outputs are also kept here.

## Production datasets (SMT2020 family)

| Folder | Characteristics |
|---|---|
| `HVLM/` | High-Volume/Low-Mix — few products, high volume. Default smoke-test and benchmark target (2 routes) |
| `LVLM/` | Low-Volume/Low-Mix (4 routes) |
| `LVHM/` | Low-Volume/High-Mix — the most complex product mix (10 routes) |

Common file layout (tab-separated text):

| File | Content |
|---|---|
| `tool.txt.1l` | Tool (STNFAM/STN) definitions — dispatch rule, load/unload, capacity, setup group |
| `route_*.txt` | Process routes — tool family per step, processing-time distribution, batching, rework, CQT |
| `part.txt` / `order.txt` / `WIP.txt` | Product→route mapping / release plan / initial WIP |
| `downcal.txt` / `pmcal.txt` / `attach.txt` | Breakdown and PM calendars and their attachment to tool groups |
| `setup.txt` / `setupgrp.txt` | Setup change matrix / group definitions |
| `fromto.txt` | Static transport-time distribution bundled with the dataset — used during the static warm-up period and by the production-only baselines |

## Rail layouts (.rail)

| File | Role |
|---|---|
| `SMAT2022.rail` | **Primary layout** — generated from the SMAT2022 CSVs by `smat2022_to_rail.convert()` (2,858 nodes / 3,424 links / 1,698 sections) |
| `case1.rail` | Small demonstration layout — default input of `ufast-fromto` and the CI logistics smoke run |
| `case1_SMT2020_106_nospur.rail` | case1-based mapping of the 106 SMT2020 tools with spurs removed |

## SMAT2022/ original sources

`Adress.csv` (2,857 nodes) · `Rail.csv` (3,423 links) · `Equipment.csv` (1,115 tools) ·
`Rail_ext.csv` (links with lengths, from `add_lengths.py`) · `Port.csv` (equipment ports) ·
`VehicleType.csv` (OHT specification: Vmax 5000 mm/s, accel 2000 / decel 3500 mm/s²) ·
`transport_times_between_tool_groups.csv` (precomputed transport times between tool groups) ·
three computation scripts (`add_lengths.py`, `calc_transport_times.py`,
`calc_mean_transport_times_between_families.py`)

## Other

- `case1_Fromto.dat` (1.27M rows) — From-To hourly rate (rate [events/hour]) time series for the
  logistics-only mode (read by `common/fromto_parser`; default input of `ufast-fromto`)
- `SMAT2022_family_node_map.csv` — conversion output: representative rail node for each of the 108
  tool groups

## Vehicle specification (`vehicle_spec.json`)

Default OHT specification read by both runners (`ufast-run`, `ufast-fromto`). Units are mm / mm/s /
mm/s². The values are OHTV0 from `SMAT2022/VehicleType.csv` (length 784, minimum headway 125 →
footprint 909 mm; Vmax 5000, accel 2000, decel 3500) plus the straight/curve link speed limits
(5000 / 1000 mm/s). At run time a different file can be given with `--vehicle-spec PATH`, or
individual values can be overridden with flags such as `--oht-speed` (flags take precedence); the
values actually used are recorded in the result JSON under `meta.vehicle`.

## Usage context

```bash
ufast-run dataset/HVLM dataset/SMAT2022.rail --days 365 --oht 100
```

The default dataset list of `scripts/compare_baselines.py` is the three sets HVLM/LVHM/LVLM, and
`tests/test_ufast_smoke.py`, `tests/test_fromto.py` and `scripts/verify_fast_route.py` also use this
folder as input.

## Provenance

- SMT2020 datasets: D. Kopp, M. Hassoun, A. Kalir, L. Mönch, *SMT2020 — A Semiconductor
  Manufacturing Testbed*, IEEE Trans. Semiconductor Manufacturing, 2020.
- SMAT2022 layout (rail network, equipment coordinates, vehicle type): as distributed with
  LogiFabSim (S. Rank, V. Betker, IFAC-PapersOnLine, 2025).

See [NOTICE.txt](../NOTICE.txt) for the attributions. `SMAT2022.rail` and
`SMAT2022_family_node_map.csv` can be regenerated from the CSVs:

```bash
python -c "from ufast.common.smat2022_to_rail import convert; convert('dataset/SMAT2022', 'dataset/SMAT2022.rail', 'dataset/SMAT2022_family_node_map.csv')"
```
