"""
aggregate.py — seed 반복실험 결과의 KPI 집계.

같은 구성(config)으로 seed 만 바꿔 여러 번 실행한 결과 JSON 들을 모아,
구성별로 KPI 의 mean / std / min / max 를 계산한다.

사용:
  python3 ufast/cosim/aggregate.py                       # results/ 전체 자동 수집
  python3 ufast/cosim/aggregate.py results/HVLM*         # 경로/글롭 지정
  python3 ufast/cosim/aggregate.py --out results/agg     # 저장 폴더 지정
  python3 ufast/cosim/aggregate.py --metrics amhs.avg_transport_s production.by_category.Regular.avg_cycle_days

동작:
  - 각 결과 JSON 의 meta 에서 seed·실행시각 등 휘발 필드를 제외한 나머지를
    "구성 키"로 삼아 그룹핑한다 (dataset/days/oht/dispatcher/전략... 이 같으면
    같은 실험).
  - production / amhs / equipment / transport 트리의 숫자 leaf 를 점(.) 경로로
    평탄화해 KPI 로 삼는다.
  - 그룹별로 aggregate.csv (metric, n, mean, std, min, max) 와
    aggregate.json 을 저장하고, 핵심 KPI 요약을 콘솔에 출력한다.

stdlib 만 사용한다 (pandas 불필요) — SoftwareX 공개 시 의존성 최소화.
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

# 구성 키에서 제외할 meta 필드 — seed 와 실행마다 달라지는 결과성 값들
_VOLATILE_META = {
    "seed", "run_id", "wall_time_s", "sim_time_days", "dispatch_steps",
}

# 콘솔 요약에 표시할 핵심 KPI (존재하는 것만 출력)
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
    """중첩 dict 의 숫자 leaf 를 'a.b.c' 경로로 평탄화."""
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
    # 집계 산출물·궤적 파일은 제외
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
            print(f"[aggregate] skip (읽기 실패): {p} — {e}")
            continue
        if not isinstance(data, dict) or "meta" not in data:
            continue
        data["_path"] = p
        runs.append(data)
    return runs


def aggregate_runs(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """구성별 그룹 → KPI 통계. 반환: [{config, seeds, n, metrics:{path:{...}}}]"""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in runs:
        groups.setdefault(_config_key(r["meta"]), []).append(r)

    out = []
    for cfg_json, members in sorted(groups.items()):
        # run 별 KPI 평탄화 (meta 의 결과성 값도 metric 으로 포함)
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
            print(f"[aggregate] ⚠️ 같은 구성에 중복 seed 존재: {seeds} "
                  "(같은 실험을 두 번 저장했을 수 있음)")
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
            print("  (요약 대상 KPI 없음 — --metrics 로 경로를 지정하세요)")


def main():
    p = argparse.ArgumentParser(
        description="seed 반복실험 결과 JSON 을 구성별로 묶어 KPI mean/std 집계")
    p.add_argument("inputs", nargs="*",
                   default=[RESULTS_DIR],
                   help="결과 JSON 파일/폴더/글롭 (기본: results/)")
    p.add_argument("--out", default=None,
                   help="집계 저장 폴더 (기본: <첫 입력 폴더>/aggregate)")
    p.add_argument("--metrics", nargs="*", default=None,
                   help="콘솔 요약에 표시할 KPI 점 경로 목록")
    a = p.parse_args()

    runs = load_runs(a.inputs)
    if not runs:
        print("[aggregate] 결과 JSON 을 찾지 못했습니다. "
              "ufast/cosim/run.py 를 먼저 실행하세요.")
        sys.exit(1)
    print(f"[aggregate] 결과 {len(runs)}개 로드")

    groups = aggregate_runs(runs)
    print(f"[aggregate] 구성 그룹 {len(groups)}개")

    out_dir = a.out or os.path.join(
        a.inputs[0] if os.path.isdir(a.inputs[0]) else os.path.dirname(a.inputs[0]),
        "aggregate")
    saved = save_aggregates(groups, out_dir)
    print_summary(groups, a.metrics)
    print()
    for s in saved:
        print(f"[aggregate] 저장: {s}")


if __name__ == "__main__":
    main()
