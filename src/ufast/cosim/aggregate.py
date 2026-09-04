"""
aggregate.py — KPI aggregation over seed-replication results.

Collects the result JSONs of runs that share the same configuration (config) and
differ only in seed, and computes mean / std / min / max of each KPI per
configuration.

Usage:
  python3 ufast/cosim/aggregate.py                       # collect all of results/ automatically
  python3 ufast/cosim/aggregate.py results/HVLM*         # explicit paths/globs
  python3 ufast/cosim/aggregate.py --out results/agg     # output folder
  python3 ufast/cosim/aggregate.py --metrics amhs.avg_transport_s production.by_category.Regular.avg_cycle_days

Behaviour:
  - Groups runs by a "configuration key": the meta of each result JSON minus
    volatile fields such as seed and wall time (same dataset/days/oht/dispatcher/
    strategies... = same experiment).
  - Flattens the numeric leaves of the production / amhs / equipment / transport
    trees into dotted paths and treats them as KPIs.
  - Saves aggregate.csv (metric, n, mean, std, min, max) and aggregate.json per
    group, and prints a summary of the key KPIs to the console.

Uses only the stdlib (no pandas) — minimal dependencies for the SoftwareX release.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import statistics
import sys
from typing import Any, Dict, List

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from ufast.paths import RESULTS_DIR

# meta fields excluded from the configuration key — seed and per-run result values
_VOLATILE_META = {
    "seed", "run_id", "wall_time_s", "sim_time_days", "dispatch_steps",
}

# key KPIs shown in the console summary (only those present are printed)
_SUMMARY_METRICS = [
    "production.total.throughput",
    "production.by_category.Regular.avg_cycle_days",
    "production.by_category.Regular.on_time_pct",
    "production.by_category.Hot.avg_cycle_days",
    "amhs.avg_transport_s",
    "amhs.avg_delivery_s",
    "amhs.avg_oht_wait_s",
    "amhs.avg_congestion_factor",
    "amhs.avg_utilization",
    "meta.wall_time_s",
    "transport.completion_rate",
    "transport.avg_transport_s",
]


def _flatten(prefix: str, node: Any, out: Dict[str, float]):
    """Flatten the numeric leaves of a nested dict into 'a.b.c' paths."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        out[prefix] = float(node)
        return
    if isinstance(node, dict):
        for k, v in node.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)


def _config_key(meta: Dict[str, Any]) -> str:
    cfg = {k: v for k, v in sorted(meta.items()) if k not in _VOLATILE_META}
    return json.dumps(cfg, ensure_ascii=False, sort_keys=True)


def _collect_json_paths(inputs: List[str]) -> List[str]:
    paths: List[str] = []
    for item in inputs:
        if os.path.isdir(item):
            paths.extend(glob.glob(os.path.join(item, "**", "*.json"),
                                   recursive=True))
        else:
            paths.extend(glob.glob(item))
    # exclude aggregation outputs and trajectory files
    return sorted({
        p for p in paths
        if p.endswith(".json")
        and "aggregate" not in os.path.basename(p)
        and not p.endswith("_trajectories.json")
    })


def load_runs(inputs: List[str]) -> List[Dict[str, Any]]:
    runs = []
    for p in _collect_json_paths(inputs):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:  # noqa: BLE001
            print(f"[aggregate] skip (read failed): {p} — {e}")
            continue
        if not isinstance(data, dict) or "meta" not in data:
            continue
        data["_path"] = p
        runs.append(data)
    return runs


def aggregate_runs(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group by configuration → KPI statistics. Returns: [{config, seeds, n, metrics:{path:{...}}}]"""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in runs:
        groups.setdefault(_config_key(r["meta"]), []).append(r)

    out = []
    for cfg_json, members in sorted(groups.items()):
        # flatten KPIs per run (result-type meta values are included as metrics too)
        per_run: List[Dict[str, float]] = []
        for r in members:
            flat: Dict[str, float] = {}
            for section in ("production", "amhs", "equipment", "transport"):
                if section in r:
                    _flatten(section, r[section], flat)
            for k in ("wall_time_s", "sim_time_days", "dispatch_steps"):
                if isinstance(r["meta"].get(k), (int, float)):
                    flat[f"meta.{k}"] = float(r["meta"][k])
            per_run.append(flat)

        all_keys = sorted({k for f in per_run for k in f})
        metrics: Dict[str, Dict[str, float]] = {}
        for key in all_keys:
            vals = [f[key] for f in per_run if key in f]
            if not vals:
                continue
            metrics[key] = {
                "n": len(vals),
                "mean": statistics.mean(vals),
                "std": statistics.stdev(vals) if len(vals) > 1 else 0.0,
                "min": min(vals),
                "max": max(vals),
            }

        seeds = sorted(r["meta"].get("seed", -1) for r in members)
        if len(set(seeds)) != len(seeds):
            print(f"[aggregate] ⚠️ duplicate seeds in the same configuration: {seeds} "
                  "(the same experiment may have been saved twice)")
        out.append({
            "config": json.loads(cfg_json),
            "seeds": seeds,
            "n_runs": len(members),
            "paths": [r["_path"] for r in members],
            "metrics": metrics,
        })
    return out


def save_aggregates(groups: List[Dict[str, Any]], out_dir: str) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    saved = []

    json_path = os.path.join(out_dir, "aggregate.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, indent=2, ensure_ascii=False)
    saved.append(json_path)

    csv_path = os.path.join(out_dir, "aggregate.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "dataset", "days", "num_oht", "dispatcher",
                    "n_runs", "seeds", "metric", "mean", "std", "min", "max"])
        for gi, g in enumerate(groups):
            cfg = g["config"]
            for metric, s in g["metrics"].items():
                w.writerow([
                    gi,
                    cfg.get("dataset_short", cfg.get("dataset", "")),
                    cfg.get("days", ""), cfg.get("num_oht", ""),
                    cfg.get("dispatcher", ""),
                    g["n_runs"], " ".join(map(str, g["seeds"])),
                    metric,
                    round(s["mean"], 4), round(s["std"], 4),
                    round(s["min"], 4), round(s["max"], 4),
                ])
    saved.append(csv_path)
    return saved


def print_summary(groups: List[Dict[str, Any]], metrics_filter: List[str]):
    wanted = metrics_filter or _SUMMARY_METRICS
    for gi, g in enumerate(groups):
        cfg = g["config"]
        label = (f"{cfg.get('dataset_short', cfg.get('dataset', '?'))} | "
                 f"days={cfg.get('days', '?')} | OHT={cfg.get('num_oht', '?')} | "
                 f"dispatcher={cfg.get('dispatcher', '?')} | "
                 f"strategy={cfg.get('amhs_strategy', '-')}")
        print(f"\n[group {gi}] {label}")
        print(f"  seeds={g['seeds']} (n={g['n_runs']})")
        shown = 0
        for m in wanted:
            s = g["metrics"].get(m)
            if s is None:
                continue
            print(f"  {m:<52} {s['mean']:>10.3f} ± {s['std']:<8.3f} "
                  f"[{s['min']:.3f}, {s['max']:.3f}]")
            shown += 1
        if shown == 0:
            print("  (no KPI to summarise — specify paths with --metrics)")


def main():
    p = argparse.ArgumentParser(
        description="Group seed-replication result JSONs by configuration and aggregate KPI mean/std")
    p.add_argument("inputs", nargs="*",
                   default=[RESULTS_DIR],
                   help="result JSON files/folders/globs (default: results/)")
    p.add_argument("--out", default=None,
                   help="output folder for aggregates (default: <first input folder>/aggregate)")
    p.add_argument("--metrics", nargs="*", default=None,
                   help="dotted KPI paths to show in the console summary")
    a = p.parse_args()

    runs = load_runs(a.inputs)
    if not runs:
        print("[aggregate] no result JSON found. "
              "Run ufast/cosim/run.py first.")
        sys.exit(1)
    print(f"[aggregate] loaded {len(runs)} results")

    groups = aggregate_runs(runs)
    print(f"[aggregate] {len(groups)} configuration groups")

    out_dir = a.out or os.path.join(
        a.inputs[0] if os.path.isdir(a.inputs[0]) else os.path.dirname(a.inputs[0]),
        "aggregate")
    saved = save_aggregates(groups, out_dir)
    print_summary(groups, a.metrics)
    print()
    for s in saved:
        print(f"[aggregate] saved: {s}")


if __name__ == "__main__":
    main()
