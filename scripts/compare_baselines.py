from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT_DIR = ROOT / "results" / "baseline_compare"
# Baseline simulators are NOT part of this repository: they run as subprocesses
# inside their own checkouts. Point these at your clones (env vars override).
#   PySCFabSim : https://github.com/prosysscience/PySCFabSim-release  (MIT)
#   LogiFabSim : Rank & Betker (2025) — obtain from the authors; keep as a
#                sibling checkout named external/j3c-fork or set UFAST_LOGI_DIR.
PYSC_DIR = Path(os.environ.get("UFAST_PYSC_DIR", ROOT / "external" / "PySCFabSim-release"))
LOGI_DIR = Path(os.environ.get("UFAST_LOGI_DIR", ROOT / "external" / "j3c-fork"))


def _require_baseline(path: Path, name: str, hint: str) -> None:
    if not path.exists():
        sys.exit(f"[compare_baselines] {name} checkout not found at {path}\n"
                 f"  {hint}")


def _run_python(cwd: Path, code: str, python: str | None = None) -> dict[str, Any]:
    proc = subprocess.run(
        [python or sys.executable, "-c", code],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed in {cwd}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    marker = "JSON_RESULT="
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(marker):
            return json.loads(line[len(marker):])
    raise RuntimeError(f"No JSON_RESULT marker found.\nSTDOUT:\n{proc.stdout}")


def _interpreter_label(python: str | None) -> str:
    """Short, self-documenting label for the interpreter a run used.

    Runtime numbers are only comparable across simulators when the
    interpreter matches; recording it makes the comparison honest (CPython
    vs PyPy can differ by an order of magnitude on these baselines)."""
    return os.path.basename(python or sys.executable)


def _fmt_suffix_number(value: float) -> str:
    text = f"{float(value):g}"
    return text.replace(".", "p").replace("-", "m")


def _warmup_suffix(static_warmup_days: float = 0.0,
                   amhs_settling_days: float = 0.0) -> str:
    suffix = ""
    if static_warmup_days:
        suffix += f"_sw{_fmt_suffix_number(static_warmup_days)}"
    if amhs_settling_days:
        suffix += f"_as{_fmt_suffix_number(amhs_settling_days)}"
    return suffix


def _aggregate_lots(rows: dict[str, dict[str, Any]],
                    warmup_days: float = 0.0) -> dict[str, Any]:
    # Warm-up exclusion uses a completion window: keep lots whose done_at is at
    # or after the cutoff.  Completion-based (not release-based) because initial
    # WIP carries release_at < 0, which would distort a release-based filter.
    if warmup_days and warmup_days > 0:
        cutoff = warmup_days * 86400
        rows = {
            k: v for k, v in rows.items()
            if v.get("done_at", float("-inf")) >= cutoff
        }

    by_lot: dict[str, list[dict[str, Any]]] = {}
    by_cat: dict[str, list[dict[str, Any]]] = {
        "Regular": [],
        "Hot": [],
        "SuperHot": [],
        "Other": [],
    }

    def category(name: str) -> str:
        if name.startswith("SuperHotLot"):
            return "SuperHot"
        if name.startswith("HotLot"):
            return "Hot"
        if name.startswith("Lot"):
            return "Regular"
        return "Other"

    for row in rows.values():
        lot_type = row["lot_type"]
        by_lot.setdefault(lot_type, []).append(row)
        by_cat[category(lot_type)].append(row)

    def agg(items: list[dict[str, Any]]) -> dict[str, Any] | None:
        # Schema mirrors src/ufast/results._aggregate (U-FAST) so all three
        # simulators land in identical CSV columns. lot_rows are in days;
        # the *_s columns convert back to seconds (*86400).
        if not items:
            return None
        import statistics

        n = len(items)
        cts = [x["CT"] for x in items]
        on_time = sum(x["on_time"] for x in items)
        return {
            "throughput": n,
            "avg_cycle_days": round(statistics.mean(cts), 2),
            "median_cycle_days": round(statistics.median(cts), 2),
            "on_time_count": on_time,
            "on_time_pct": round(100 * on_time / n, 1),
            "avg_tardiness_s": round(sum(x["tardiness"] for x in items) / n * 86400, 1),
            "avg_waiting_s": round(sum(x["waiting_time"] for x in items) / n * 86400, 1),
            "avg_processing_s": round(sum(x["processing_time"] for x in items) / n * 86400, 1),
            "avg_transport_s": round(sum(x["transport_time"] for x in items) / n * 86400, 1),
        }

    return {
        "completed": len(rows),
        "by_lot_type": {k: agg(v) for k, v in sorted(by_lot.items())},
        "by_category": {k: agg(v) for k, v in by_cat.items() if v},
    }


def _fill_template(template: str, **values: Any) -> str:
    code = template
    for key, value in values.items():
        code = code.replace(f"@@{key}@@", repr(value))
    return code


PYSC_CODE = r"""
import json
import time
from collections import defaultdict

from simulation.dispatching.dispatcher import dispatcher_map
from simulation.file_instance import FileInstance
from simulation.greedy import get_lots_to_dispatch_by_machine, get_lots_to_dispatch_by_lot
from simulation.plugins.cost_plugin import CostPlugin
from simulation.randomizer import Randomizer
from simulation.read import read_all

dataset = @@dataset@@
days = @@days@@
dispatcher_name = @@dispatcher@@
seed = @@seed@@
alg = @@alg@@

files = read_all('datasets/' + dataset)
run_to = 3600 * 24 * days
Randomizer().random.seed(seed)
l4m = alg == 'l4m'
instance = FileInstance(files, run_to, l4m, [CostPlugin()])
dispatcher = dispatcher_map[dispatcher_name]

t0 = time.time()
while not instance.done:
    done = instance.next_decision_point()
    if done or instance.current_time > run_to:
        break
    if l4m:
        machine, lots = get_lots_to_dispatch_by_machine(instance, dispatcher)
        if lots is None:
            instance.usable_machines.remove(machine)
        else:
            instance.dispatch(machine, lots)
    else:
        machine, lots = get_lots_to_dispatch_by_lot(instance, instance.current_time, dispatcher)
        if lots is None:
            instance.usable_lots.clear()
            instance.lot_in_usable.clear()
            instance.next_step()
        else:
            instance.dispatch(machine, lots)
instance.finalize()

rows = {}
for lot in instance.done_lots:
    rows[f'{lot.name}_{lot.idx}'] = {
        'lot_type': lot.name,
        'CT': (lot.done_at - lot.release_at) / 86400,
        'on_time': 1 if lot.done_at <= lot.deadline_at else 0,
        'tardiness': max(0, lot.done_at - lot.deadline_at) / 86400,
        'waiting_time': lot.waiting_time / 86400,
        'processing_time': lot.processing_time / 86400,
        'transport_time': lot.transport_time / 86400,
        'waiting_time_batching': lot.waiting_time_batching / 86400,
        'release_at': lot.release_at,
        'done_at': lot.done_at,
    }

print('JSON_RESULT=' + json.dumps({
    'meta': {
        'simulator': 'PySCFabSim',
        'dataset': dataset,
        'days': days,
        'dispatcher': dispatcher_name,
        'seed': seed,
        'alg': alg,
        'wall_time_s': time.time() - t0,
        'sim_time_days': instance.current_time_days,
        'active_lots': len(instance.active_lots),
    },
    'lot_rows': rows,
}))
"""


LOGI_CODE = r"""
import json
import simulation.stats as _stats

# Non-invasive: attach release_at/done_at so the harness can apply a warm-up
# window.  greedy.py calls simulation.stats.get_lot_statistics via the module,
# so patching the module attribute is picked up inside run_greedy_core.
_orig_lot_stats = _stats.get_lot_statistics
def _lot_stats_with_times(instance):
    rows = _orig_lot_stats(instance)
    for lot in instance.done_lots:
        lot_id = f'{lot.name}_{lot.idx}'
        if lot_id in rows:
            rows[lot_id]['release_at'] = lot.release_at
            rows[lot_id]['done_at'] = lot.done_at
    return rows
_stats.get_lot_statistics = _lot_stats_with_times

from simulation.greedy import run_greedy_core

class P:
    dataset = @@dataset@@
    days = @@days@@
    dispatcher = @@dispatcher@@
    congestion_factor = @@cf@@
    max_transport_load = @@max_transport_load@@
    max_congestion_factor = @@cf_max@@
    alg = @@alg@@
    wandb = False
    chart = False

rows, duration = run_greedy_core(P(), seed=@@seed@@)
print('JSON_RESULT=' + json.dumps({
    'meta': {
        'simulator': 'LogiFabSim',
        'dataset': P.dataset,
        'days': P.days,
        'dispatcher': P.dispatcher,
        'seed': @@seed@@,
        'alg': P.alg,
        'congestion_factor': P.congestion_factor,
        'max_transport_load': P.max_transport_load,
        'max_congestion_factor': P.max_congestion_factor,
        'wall_time_s': duration.total_seconds(),
    },
    'lot_rows': rows,
}))
"""


def run_pysc(dataset: str, days: int, dispatcher: str, seed: int, alg: str,
             python: str | None = None, warmup_days: float = 0.0) -> dict[str, Any]:
    if dataset == "HVLM":
        external_dataset = "SMT2020_HVLM"
    elif dataset == "LVHM":
        external_dataset = "SMT2020_LVHM"
    elif dataset == "LVLM":
        external_dataset = "SMT2020_LVLM"
    else:
        external_dataset = dataset
    code = _fill_template(
        PYSC_CODE,
        dataset=external_dataset,
        days=days,
        dispatcher=dispatcher,
        seed=seed,
        alg=alg,
    )
    _require_baseline(PYSC_DIR, "PySCFabSim",
                      "git clone https://github.com/prosysscience/PySCFabSim-release "
                      "external/PySCFabSim-release  (or set UFAST_PYSC_DIR)")
    result = _run_python(PYSC_DIR, code, python=python)
    result["meta"]["dataset_short"] = dataset
    result["meta"]["interpreter"] = _interpreter_label(python)
    result["meta"]["warmup_days"] = warmup_days
    result["production"] = _aggregate_lots(result["lot_rows"], warmup_days)
    return result


def run_logi(
    dataset: str,
    days: int,
    dispatcher: str,
    seed: int,
    alg: str,
    cf: str | None,
    cf_max: float | None,
    max_transport_load: int,
    python: str | None = None,
    warmup_days: float = 0.0,
) -> dict[str, Any]:
    code = _fill_template(
        LOGI_CODE,
        dataset=dataset,
        days=days,
        dispatcher=dispatcher,
        seed=seed,
        alg=alg,
        cf=cf,
        cf_max=cf_max,
        max_transport_load=max_transport_load,
    )
    _require_baseline(LOGI_DIR, "LogiFabSim",
                      "place the LogiFabSim checkout at external/j3c-fork "
                      "(or set UFAST_LOGI_DIR); U-FAST itself does not need it")
    result = _run_python(LOGI_DIR, code, python=python)
    result["meta"]["dataset_short"] = dataset
    result["meta"]["interpreter"] = _interpreter_label(python)
    result["meta"]["warmup_days"] = warmup_days
    result["production"] = _aggregate_lots(result["lot_rows"], warmup_days)
    return result


def _fills_result_path(dataset: str, days: int, oht: int, seed: int,
                       machine_selection: str = "exact",
                       static_warmup_days: float = 0.0,
                       amhs_settling_days: float = 0.0,
                       congestion: str = "queue") -> Path:
    # Mirrors src/ufast/results.auto_result_path for fifo/fifo/{congestion}.
    # A non-default machine_selection is separated from exact results by the '_ms{sel}' suffix.
    ms = "" if machine_selection == "exact" else f"_ms{machine_selection}"
    warmup = _warmup_suffix(static_warmup_days, amhs_settling_days)
    cg = {"section_local": "sl", "global_tip": "gt", "off": "no"}.get(
        congestion, congestion)
    return ROOT / "results" / f"{dataset}_{days}d_{oht}oht_a0.05_fifo_fifo_{cg}{ms}{warmup}_s{seed}.json"


def _fills_lot_rows(json_path: Path) -> dict[str, dict[str, Any]] | None:
    """Build lot_rows (same schema as the baselines) from U-FAST's _lots.csv.

    Lets the warm-up window apply uniformly across all three simulators.
    Returns None when the per-lot CSV is absent (older runs)."""
    csv_path = json_path.with_name(json_path.stem + "_lots.csv")
    if not csv_path.exists():
        return None
    import csv as _csv

    rows: dict[str, dict[str, Any]] = {}
    with open(csv_path, encoding="utf-8") as f:
        for i, r in enumerate(_csv.DictReader(f)):
            rows[f"{r['lot_name']}_{i}"] = {
                "lot_type": r["lot_name"],
                "CT": float(r["cycle_time_days"]),
                "on_time": int(r["on_time"]),
                "tardiness": float(r["tardiness_s"]) / 86400,
                "waiting_time": float(r["waiting_s"]) / 86400,
                "processing_time": float(r["processing_s"]) / 86400,
                "transport_time": float(r["transport_s"]) / 86400,
                "release_at": float(r["release_at"]),
                "done_at": float(r["done_at"]),
            }
    return rows


def load_fills(dataset: str, days: int, oht: int, seed: int,
               warmup_days: float = 0.0, machine_selection: str = "exact",
               static_warmup_days: float = 0.0,
               amhs_settling_days: float = 0.0,
               congestion: str = "queue") -> dict[str, Any]:
    path = _fills_result_path(
        dataset, days, oht, seed, machine_selection,
        static_warmup_days, amhs_settling_days, congestion)
    if not path.exists():
        candidates = [p for p in (ROOT / "results").glob(f"**/{path.name}") if not p.name.startswith(".")]
        if candidates:
            path = max(candidates, key=lambda p: p.stat().st_mtime)
        else:
            raise FileNotFoundError(
                f"U-FAST result not found: {path}\n"
                f"  → use --fills-source run to run it live first"
            )
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    result["meta"]["simulator"] = "U-FAST"
    result["meta"].setdefault("interpreter", _interpreter_label(sys.executable))
    result["meta"]["warmup_days"] = warmup_days
    result["meta"].setdefault("static_warmup_days", static_warmup_days)
    result["meta"].setdefault("amhs_settling_days", amhs_settling_days)

    # Re-aggregate from per-lot CSV so warm-up applies the same way as baselines.
    lot_rows = _fills_lot_rows(path)
    if lot_rows is not None:
        result["lot_rows"] = lot_rows
        result["production"] = _aggregate_lots(lot_rows, warmup_days)
    elif warmup_days:
        print(f"[U-FAST] warning: _lots.csv missing; warm-up not applied "
              f"(using all lots): {path.name}")
    return result


def run_fills(dataset: str, days: int, oht: int, dispatcher: str, seed: int,
              warmup_days: float = 0.0, machine_selection: str = "exact",
              static_warmup_days: float = 0.0,
              amhs_settling_days: float = 0.0,
              congestion: str = "queue") -> dict[str, Any]:
    """Run the U-FAST co-sim live via `-m ufast.cosim.run`, then load its result JSON.

    Regenerating per seed means changing --seed just works without a manual
    pre-run.  Uses fifo dispatcher / fifo AMHS strategy; the congestion
    variant is pinned explicitly so the filename matches load_fills
    (default queue = the representative blocking model; for the earlier delay-based
    E1 results use --ufast-congestion section_local).
    """
    dataset_dir = f"dataset/{dataset}"
    rail_file = "dataset/SMAT2022.rail"
    cmd = [
        sys.executable, "-m", "ufast.cosim.run", dataset_dir, rail_file,
        "--days", str(days), "--oht", str(oht), "--seed", str(seed),
        "--congestion", congestion,
        "--dispatcher", dispatcher,
        "--machine-selection", machine_selection,
        "--static-warmup-days", str(static_warmup_days),
        "--amhs-settling-days", str(amhs_settling_days),
    ]
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    print(f"[U-FAST] live run: {' '.join(cmd)} (cwd={ROOT})")
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env,
                          text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"U-FAST run failed.\nSTDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-2000:]}"
        )
    return load_fills(
        dataset, days, oht, seed, warmup_days=warmup_days,
        machine_selection=machine_selection,
        static_warmup_days=static_warmup_days,
        amhs_settling_days=amhs_settling_days,
        congestion=congestion)


def write_result(result: dict[str, Any], out_dir: Path) -> Path:
    meta = result["meta"]
    sim = meta["simulator"].lower()
    dataset = meta.get("dataset_short") or meta.get("dataset")
    days = meta["days"]
    seed = meta["seed"]
    suffix = ""
    if sim == "logifabsim":
        suffix = f"_{meta.get('congestion_factor') or 'none'}"
    elif sim == "fills":
        if meta.get("machine_selection", "exact") != "exact":
            suffix += f"_{meta.get('machine_selection')}"
        suffix += _warmup_suffix(
            float(meta.get("static_warmup_days", 0.0) or 0.0),
            float(meta.get("amhs_settling_days", 0.0) or 0.0),
        )
    path = out_dir / f"{sim}_{dataset}_{days}d_s{seed}{suffix}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return path


def make_summary(results: list[dict[str, Any]], out_dir: Path) -> Path:
    rows = []
    for result in results:
        meta = result["meta"]
        prod = result["production"]
        equipment = result.get("equipment", {}) or {}
        amhs = result.get("amhs", {}) or {}
        by_cat = prod.get("by_category", {})
        days = meta["days"]
        wall_time_s = meta.get("wall_time_s", 0.0)
        completed_total = prod.get("completed", "")
        runtime_min_per_sim_year = (wall_time_s / 60.0) * (365.0 / days) if days else ""
        for category, values in by_cat.items():
            rows.append({
                "simulator": meta["simulator"],
                "dataset": meta.get("dataset_short") or meta.get("dataset"),
                "days": days,
                "seed": meta["seed"],
                "variant": meta.get("congestion_factor", meta.get("congestion_model", "")) or "none",
                "category": category,
                "completed_total": completed_total,
                **values,
                "starvation_count": equipment.get("starvation_count", ""),
                "total_starvation_s": equipment.get("total_starvation_s", ""),
                "avg_starvation_s": equipment.get("avg_starvation_s", ""),
                "p95_starvation_s": equipment.get("p95_starvation_s", ""),
                "breakdown_count": equipment.get("breakdown_count", ""),
                "breakdown_time_s": equipment.get("breakdown_time_s", ""),
                "pm_count": equipment.get("pm_count", ""),
                "pm_time_s": equipment.get("pm_time_s", ""),
                "total_downtime_s": equipment.get("total_downtime_s", ""),
                "amhs_avg_oht_wait_s": amhs.get("avg_oht_wait_s", ""),
                "amhs_avg_empty_travel_s": amhs.get("avg_empty_travel_s", ""),
                "amhs_avg_loaded_travel_s": amhs.get("avg_loaded_travel_s", ""),
                "amhs_avg_transport_s": amhs.get("avg_transport_s", ""),
                "amhs_avg_delivery_s": amhs.get("avg_delivery_s", ""),
                "amhs_avg_congestion_factor": amhs.get("avg_congestion_factor", ""),
                "amhs_max_queue": amhs.get("max_queue", ""),
                "warmup_days": meta.get("warmup_days", ""),
                "static_warmup_days": meta.get("static_warmup_days", ""),
                "amhs_settling_days": meta.get("amhs_settling_days", ""),
                "interpreter": meta.get("interpreter", ""),
                "wall_time_s": wall_time_s,
                "runtime_min_per_sim_year": runtime_min_per_sim_year,
            })
    path = out_dir / "summary_by_category.csv"
    if not rows:
        return path
    import csv

    preferred = [
        "simulator",
        "dataset",
        "days",
        "seed",
        "variant",
        "category",
        "completed_total",
        "throughput",
        "avg_cycle_days",
        "median_cycle_days",
        "on_time_count",
        "on_time_pct",
        "avg_tardiness_s",
        "avg_waiting_s",
        "avg_processing_s",
        "avg_transport_s",
        "starvation_count",
        "total_starvation_s",
        "avg_starvation_s",
        "p95_starvation_s",
        "breakdown_count",
        "breakdown_time_s",
        "pm_count",
        "pm_time_s",
        "total_downtime_s",
        "amhs_avg_oht_wait_s",
        "amhs_avg_empty_travel_s",
        "amhs_avg_loaded_travel_s",
        "amhs_avg_transport_s",
        "amhs_avg_delivery_s",
        "amhs_avg_congestion_factor",
        "amhs_max_queue",
        "warmup_days",
        "static_warmup_days",
        "amhs_settling_days",
        "interpreter",
        "wall_time_s",
        "runtime_min_per_sim_year",
    ]
    extra = sorted({key for row in rows for key in row.keys()} - set(preferred))
    fieldnames = preferred + extra
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def load_existing_results(out_dir: Path) -> list[dict[str, Any]]:
    results = []
    for path in sorted(out_dir.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            results.append(json.load(f))
    return results


def make_graphs(results: list[dict[str, Any]], out_dir: Path) -> list[Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception as exc:
        print(f"Skipping graphs: {exc}")
        return []

    graph_dir = out_dir / "graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)
    written = []

    by_dataset: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        dataset = result["meta"].get("dataset_short") or result["meta"].get("dataset")
        by_dataset.setdefault(dataset, []).append(result)

    def label_for(meta: dict[str, Any]) -> str:
        sim = meta["simulator"]
        if sim == "LogiFabSim":
            return f"LogiFabSim:{meta.get('congestion_factor') or 'none'}"
        return sim

    colors = {
        "PySCFabSim": "#6b7280",
        "LogiFabSim:none": "#2563eb",
        "LogiFabSim:linear": "#60a5fa",
        "LogiFabSim:flat": "#93c5fd",
        "LogiFabSim:exp": "#1d4ed8",
        "U-FAST": "#dc2626",
    }

    for dataset, dataset_results in by_dataset.items():
        categories = sorted({
            cat
            for result in dataset_results
            for cat in result.get("production", {}).get("by_category", {})
        })
        if not categories:
            continue

        metrics = [
            ("avg_cycle_days", "Cycle Time", "days"),
            ("throughput", "Throughput", f"lots / {dataset_results[0]['meta']['days']}d"),
            ("on_time_pct", "On-Time Rate", "%"),
        ]

        fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), constrained_layout=True)
        fig.suptitle(f"{dataset}: baseline comparison", fontsize=15, fontweight="bold")

        x = np.arange(len(categories))
        width = min(0.22, 0.75 / max(1, len(dataset_results)))
        offsets = np.linspace(
            -width * (len(dataset_results) - 1) / 2,
            width * (len(dataset_results) - 1) / 2,
            len(dataset_results),
        )

        for ax, (metric, title, ylabel) in zip(axes, metrics):
            for result, offset in zip(dataset_results, offsets):
                meta = result["meta"]
                label = label_for(meta)
                by_cat = result["production"]["by_category"]
                values = [
                    (by_cat.get(category) or {}).get(metric, 0.0)
                    for category in categories
                ]
                bars = ax.bar(
                    x + offset,
                    values,
                    width,
                    label=label,
                    color=colors.get(label),
                )
                for bar, value in zip(bars, values):
                    text = f"{value:,.0f}" if value >= 100 else f"{value:.1f}"
                    ax.annotate(
                        text,
                        (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 3),
                        textcoords="offset points",
                        ha="center",
                        va="bottom",
                        fontsize=7,
                    )
            ax.set_title(title)
            ax.set_ylabel(ylabel)
            ax.set_xticks(x)
            ax.set_xticklabels(categories, rotation=25, ha="right")
            ax.grid(axis="y", color="#e5e7eb")
            ax.spines[["top", "right"]].set_visible(False)
            if metric == "on_time_pct":
                ax.set_ylim(0, 110)
        axes[-1].legend(frameon=False, loc="best")

        path = graph_dir / f"{dataset.lower()}_baseline_comparison.png"
        fig.savefig(path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        written.append(path)

    return written


# ─────────────────────────── E2: OHT sweep ───────────────────────────
# U-FAST simulates a discrete OHT fleet, so transport KPIs respond to fleet
# size.  PySCFabSim has no transport; LogiFabSim models transport via a static
# CF(load) function with no notion of vehicles (max_transport_load fixed at
# 300) — so both are OHT-independent reference lines.  The sweep shows the
# fleet-size dependence only U-FAST captures.

def _overall_avg(result: dict[str, Any], key: str, warmup_days: float = 0.0) -> float:
    """Throughput-unweighted mean of a per-lot field (in days) from lot_rows."""
    rows = result.get("lot_rows") or {}
    if warmup_days and warmup_days > 0:
        cut = warmup_days * 86400
        rows = {k: v for k, v in rows.items() if v.get("done_at", float("-inf")) >= cut}
    vals = [v[key] for v in rows.values() if key in v]
    return sum(vals) / len(vals) if vals else 0.0


def _overall_throughput(result: dict[str, Any], warmup_days: float = 0.0) -> int:
    rows = result.get("lot_rows") or {}
    if warmup_days and warmup_days > 0:
        cut = warmup_days * 86400
        rows = {k: v for k, v in rows.items() if v.get("done_at", float("-inf")) >= cut}
    return len(rows)


def run_oht_sweep(dataset: str, days: int, oht_list: list[int], dispatcher: str,
                  seed: int, warmup_days: float, logi_cf: list[str],
                  baseline_py: str | None, out_dir: Path,
                  machine_selection: str = "exact",
                  static_warmup_days: float = 0.0,
                  amhs_settling_days: float = 0.0) -> dict[str, Any]:
    """Sweep OHT count for U-FAST; run PySC/Logi once as OHT-independent refs."""
    cf_options = {"none": (None, None), "flat": ("flat", 2),
                  "linear": ("linear", 1), "exp": ("exp", 2)}

    # U-FAST — one live run per fleet size.
    fills_points = []
    for oht in oht_list:
        r = run_fills(dataset, days, oht, dispatcher, seed, warmup_days=warmup_days,
                      machine_selection=machine_selection,
                      static_warmup_days=static_warmup_days,
                      amhs_settling_days=amhs_settling_days,
                      congestion=congestion)
        write_result(r, out_dir)
        amhs = r.get("amhs", {})
        equipment = r.get("equipment", {})
        fills_points.append({
            "oht": oht,
            "lot_transport_s": _overall_avg(r, "transport_time", warmup_days) * 86400,
            "amhs_congestion_factor": amhs.get("avg_congestion_factor", 1.0),
            "amhs_transport_s": amhs.get("avg_transport_s", 0.0),
            "amhs_free_flow_s": amhs.get("avg_free_flow_s", 0.0),
            "amhs_oht_wait_s": amhs.get("avg_oht_wait_s", 0.0),
            "amhs_empty_travel_s": amhs.get("avg_empty_travel_s", 0.0),
            "amhs_loaded_travel_s": amhs.get("avg_loaded_travel_s", 0.0),
            "amhs_delivery_s": amhs.get("avg_delivery_s", 0.0),
            "max_queue": amhs.get("max_queue", 0),
            "avg_starvation_s": equipment.get("avg_starvation_s", 0.0),
            "p95_starvation_s": equipment.get("p95_starvation_s", 0.0),
            "breakdown_count": equipment.get("breakdown_count", 0),
            "breakdown_time_s": equipment.get("breakdown_time_s", 0.0),
            "pm_count": equipment.get("pm_count", 0),
            "pm_time_s": equipment.get("pm_time_s", 0.0),
            "throughput": _overall_throughput(r, warmup_days),
            "avg_cycle_days": _overall_avg(r, "CT", warmup_days),
            "wall_time_s": r["meta"].get("wall_time_s", 0.0),
        })

    # LogiFabSim — once per CF variant (OHT-independent).  Effective CF level
    # = variant transport / none transport.
    logi_points = {}
    logi_none_transport = None
    for cf_name in logi_cf:
        cf, cf_max = cf_options[cf_name]
        r = run_logi(dataset, days, dispatcher, seed, "l4m", cf, cf_max,
                     max_transport_load=300, python=baseline_py, warmup_days=warmup_days)
        write_result(r, out_dir)
        t = _overall_avg(r, "transport_time", warmup_days) * 86400
        logi_points[cf_name] = {
            "lot_transport_s": t,
            "throughput": _overall_throughput(r, warmup_days),
            "avg_cycle_days": _overall_avg(r, "CT", warmup_days),
        }
        if cf_name == "none":
            logi_none_transport = t
    for cf_name, p in logi_points.items():
        p["effective_cf"] = (p["lot_transport_s"] / logi_none_transport
                             if logi_none_transport else None)

    # PySCFabSim — once (static transport, no congestion).
    pysc_point = None
    r = run_pysc(dataset, days, dispatcher, seed, "l4m",
                 python=baseline_py, warmup_days=warmup_days)
    write_result(r, out_dir)
    pysc_point = {
        "lot_transport_s": _overall_avg(r, "transport_time", warmup_days) * 86400,
        "throughput": _overall_throughput(r, warmup_days),
        "avg_cycle_days": _overall_avg(r, "CT", warmup_days),
    }

    sweep = {
        "meta": {"dataset": dataset, "days": days, "seed": seed,
                 "warmup_days": warmup_days, "oht_list": oht_list,
                 "static_warmup_days": static_warmup_days,
                 "amhs_settling_days": amhs_settling_days},
        "fills": fills_points,
        "logifabsim": logi_points,
        "pyscfabsim": pysc_point,
    }
    with open(out_dir / f"sweep_{dataset}_{days}d_s{seed}.json", "w", encoding="utf-8") as f:
        json.dump(sweep, f, indent=2, ensure_ascii=False)
    _write_sweep_csv(sweep, out_dir)
    make_sweep_graphs(sweep, out_dir)
    return sweep


def _write_sweep_csv(sweep: dict[str, Any], out_dir: Path) -> Path:
    import csv as _csv
    path = out_dir / f"sweep_{sweep['meta']['dataset']}_{sweep['meta']['days']}d.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["simulator", "variant", "oht", "lot_transport_s",
                    "congestion_factor", "throughput", "avg_cycle_days", "wall_time_s"])
        for p in sweep["fills"]:
            w.writerow(["U-FAST", "", p["oht"], round(p["lot_transport_s"], 1),
                        round(p["amhs_congestion_factor"], 4), p["throughput"],
                        round(p["avg_cycle_days"], 2), round(p["wall_time_s"], 1)])
        for cf_name, p in sweep["logifabsim"].items():
            w.writerow(["LogiFabSim", cf_name, "", round(p["lot_transport_s"], 1),
                        round(p["effective_cf"], 4) if p.get("effective_cf") else "",
                        p["throughput"], round(p["avg_cycle_days"], 2), ""])
        pp = sweep["pyscfabsim"]
        if pp:
            w.writerow(["PySCFabSim", "", "", round(pp["lot_transport_s"], 1), "",
                        pp["throughput"], round(pp["avg_cycle_days"], 2), ""])
    return path


def make_sweep_graphs(sweep: dict[str, Any], out_dir: Path) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        print(f"Skipping sweep graphs: {exc}")
        return None

    graph_dir = out_dir / "graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)
    dataset = sweep["meta"]["dataset"]
    fp = sorted(sweep["fills"], key=lambda p: p["oht"])
    ohts = [p["oht"] for p in fp]
    logi = sweep["logifabsim"]
    pysc = sweep["pyscfabsim"]
    cf_colors = {"none": "#94a3b8", "linear": "#60a5fa",
                 "flat": "#fbbf24", "exp": "#1d4ed8"}

    fig, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    fig.suptitle(f"{dataset}: transport/KPI vs OHT fleet size "
                 f"({sweep['meta']['days']}d, warmup {sweep['meta']['warmup_days']}d)",
                 fontsize=14, fontweight="bold")

    def hlines(ax, value_fn):
        for cf_name, p in logi.items():
            v = value_fn(p)
            if v is None:
                continue
            ax.axhline(v, ls="--", lw=1.3, color=cf_colors.get(cf_name, "#64748b"),
                       label=f"LogiFabSim:{cf_name}")
        if pysc and value_fn(pysc) is not None:
            ax.axhline(value_fn(pysc), ls=":", lw=1.3, color="#6b7280",
                       label="PySCFabSim")

    # (0,0) lot transport time vs OHT
    ax = axes[0][0]
    ax.plot(ohts, [p["lot_transport_s"] for p in fp], "o-", color="#dc2626",
            lw=2, label="U-FAST")
    hlines(ax, lambda p: p.get("lot_transport_s"))
    ax.set_title("Avg transport time per lot")
    ax.set_xlabel("OHT count"); ax.set_ylabel("seconds")

    # (0,1) congestion factor vs OHT
    ax = axes[0][1]
    ax.plot(ohts, [p["amhs_congestion_factor"] for p in fp], "o-", color="#dc2626",
            lw=2, label="U-FAST (emergent)")
    hlines(ax, lambda p: p.get("effective_cf"))
    ax.set_title("Congestion factor (transport / free-flow)")
    ax.set_xlabel("OHT count"); ax.set_ylabel("CF")

    # (1,0) throughput vs OHT
    ax = axes[1][0]
    ax.plot(ohts, [p["throughput"] for p in fp], "o-", color="#dc2626", lw=2, label="U-FAST")
    hlines(ax, lambda p: p.get("throughput"))
    ax.set_title("Throughput (post-warmup window)")
    ax.set_xlabel("OHT count"); ax.set_ylabel("lots")

    # (1,1) cycle time vs OHT
    ax = axes[1][1]
    ax.plot(ohts, [p["avg_cycle_days"] for p in fp], "o-", color="#dc2626", lw=2, label="U-FAST")
    hlines(ax, lambda p: p.get("avg_cycle_days"))
    ax.set_title("Avg cycle time")
    ax.set_xlabel("OHT count"); ax.set_ylabel("days")

    for row in axes:
        for ax in row:
            ax.grid(color="#e5e7eb")
            ax.spines[["top", "right"]].set_visible(False)
    axes[0][1].legend(frameon=False, fontsize=8, loc="best")

    path = graph_dir / f"{dataset.lower()}_oht_sweep.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--datasets", nargs="+", default=["HVLM", "LVHM", "LVLM"])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dispatcher", default="fifo")
    parser.add_argument("--alg", default="l4m", choices=["l4m", "m4l"])
    parser.add_argument("--oht", type=int, default=100)
    parser.add_argument("--simulators", nargs="+", default=["pysc", "logi", "fills"])
    parser.add_argument("--logi-cf", nargs="+", default=["none"])
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--summarize-only", action="store_true")
    parser.add_argument(
        "--baseline-python", default=None,
        help="Interpreter for running PySCFabSim/LogiFabSim (e.g. path to pypy3). "
             "Default = current python (same as U-FAST). Recommended to set explicitly "
             "for a fair runtime comparison.",
    )
    parser.add_argument(
        "--ufast-congestion", default="queue",
        choices=["queue", "section_local", "global_tip", "off"],
        help="U-FAST congestion model (default queue = representative blocking model; "
             "use section_local for the delay-based comparison).",
    )
    parser.add_argument(
        "--fills-source", default="run", choices=["run", "load"],
        help="run = live run via -m ufast.cosim.run (regenerated per seed), load = load an existing result JSON.",
    )
    parser.add_argument(
        "--warmup-days", type=float, default=0.0,
        help="Warm-up period before steady state (days). Only lots whose done_at is after this time are aggregated.",
    )
    parser.add_argument(
        "--static-warmup-days", type=float, default=0.0,
        help="U-FAST only: use static transport during this initial period.",
    )
    parser.add_argument(
        "--amhs-settling-days", type=float, default=0.0,
        help="U-FAST only: run AMHS after static warm-up before KPI aggregation.",
    )
    parser.add_argument(
        "--machine-selection", default="exact", choices=["exact", "nearest"],
        help="U-FAST machine selection: exact (exact route, default) / nearest (rough, fast, approximate transport).",
    )
    parser.add_argument(
        "--mode", default="compare", choices=["compare", "sweep"],
        help="compare = 3-way KPI comparison (E1), sweep = OHT sweep + CF overlay (E2).",
    )
    parser.add_argument(
        "--oht-list", nargs="+", type=int, default=[20, 30, 50, 100, 200],
        help="List of U-FAST OHT fleet sizes in sweep mode (E2).",
    )
    args = parser.parse_args()
    effective_warmup_days = max(
        args.warmup_days,
        args.static_warmup_days + args.amhs_settling_days,
    )

    baseline_py = args.baseline_python
    fills_py = sys.executable
    if baseline_py and os.path.basename(baseline_py) != os.path.basename(fills_py):
        print(
            f"[fairness] baseline={_interpreter_label(baseline_py)} vs "
            f"U-FAST={_interpreter_label(fills_py)} — interpreters differ. "
            f"State this difference in the paper when comparing runtimes."
        )

    if args.mode == "sweep":
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        logi_cf = args.logi_cf if args.logi_cf != ["none"] else ["none", "linear", "flat", "exp"]
        for dataset in args.datasets:
            sweep = run_oht_sweep(
                dataset, args.days, args.oht_list, args.dispatcher, args.seed,
                effective_warmup_days, logi_cf, baseline_py, out_dir,
                machine_selection=args.machine_selection,
                static_warmup_days=args.static_warmup_days,
                amhs_settling_days=args.amhs_settling_days,
            )
            print(f"[sweep] {dataset}: U-FAST {len(sweep['fills'])} fleet sizes, "
                  f"Logi {len(sweep['logifabsim'])} CF variants → {out_dir}")
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    baseline_cache_dir = ROOT / "results" / "baseline"
    baseline_cache_dir.mkdir(parents=True, exist_ok=True)
    results = []
    written = []

    if args.summarize_only:
        results = load_existing_results(out_dir)
    else:
        for dataset in args.datasets:
            if "pysc" in args.simulators:
                pysc_filename = f"pyscfabsim_{dataset}_{args.days}d_s{args.seed}.json"
                pysc_cache_path = baseline_cache_dir / pysc_filename
                if pysc_cache_path.exists():
                    print(f"[baseline] Loading cached PySCFabSim for {dataset} ({args.days}d) from {pysc_cache_path.name}")
                    with open(pysc_cache_path, encoding="utf-8") as f:
                        result = json.load(f)
                    # Re-aggregate warmup if necessary
                    result["production"] = _aggregate_lots(result["lot_rows"], effective_warmup_days)
                    result["meta"]["warmup_days"] = effective_warmup_days
                else:
                    print(f"[baseline] Running PySCFabSim for {dataset} ({args.days}d)")
                    result = run_pysc(dataset, args.days, args.dispatcher, args.seed,
                                      args.alg, python=baseline_py,
                                      warmup_days=effective_warmup_days)
                    write_result(result, baseline_cache_dir)
                results.append(result)
                written.append(write_result(result, out_dir))

            if "logi" in args.simulators:
                cf_options = {
                    "none": (None, None),
                    "flat": ("flat", 2),
                    "linear": ("linear", 1),
                    "exp": ("exp", 2),
                }
                for cf_name in args.logi_cf:
                    cf, cf_max = cf_options[cf_name]
                    logi_filename = f"logifabsim_{dataset}_{args.days}d_s{args.seed}_{cf_name}.json"
                    logi_cache_path = baseline_cache_dir / logi_filename
                    if logi_cache_path.exists():
                        print(f"[baseline] Loading cached LogiFabSim ({cf_name}) for {dataset} ({args.days}d) from {logi_cache_path.name}")
                        with open(logi_cache_path, encoding="utf-8") as f:
                            result = json.load(f)
                        # Re-aggregate warmup if necessary
                        result["production"] = _aggregate_lots(result["lot_rows"], effective_warmup_days)
                        result["meta"]["warmup_days"] = effective_warmup_days
                    else:
                        print(f"[baseline] Running LogiFabSim ({cf_name}) for {dataset} ({args.days}d)")
                        result = run_logi(
                            dataset, args.days, args.dispatcher, args.seed, args.alg,
                            cf, cf_max, max_transport_load=300, python=baseline_py,
                            warmup_days=effective_warmup_days,
                        )
                        write_result(result, baseline_cache_dir)
                    results.append(result)
                    written.append(write_result(result, out_dir))

            if "fills" in args.simulators:
                if args.fills_source == "run":
                    result = run_fills(dataset, args.days, args.oht,
                                       args.dispatcher, args.seed,
                                       warmup_days=effective_warmup_days,
                                       machine_selection=args.machine_selection,
                                       static_warmup_days=args.static_warmup_days,
                                       amhs_settling_days=args.amhs_settling_days,
                                       congestion=args.ufast_congestion)
                else:
                    result = load_fills(dataset, args.days, args.oht, args.seed,
                                        warmup_days=effective_warmup_days,
                                        machine_selection=args.machine_selection,
                                        static_warmup_days=args.static_warmup_days,
                                        amhs_settling_days=args.amhs_settling_days,
                                        congestion=args.ufast_congestion)
                results.append(result)
                written.append(write_result(result, out_dir))

    summary = make_summary(results, out_dir)
    graphs = make_graphs(results, out_dir)
    print("Wrote:")
    for path in written:
        print(f"  {path}")
    print(f"  {summary}")
    for path in graphs:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
