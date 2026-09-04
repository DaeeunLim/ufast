"""Equivalence check: fast_pathfinder (scipy C Dijkstra) vs. pure-Python cost_search.

Evaluates random (source, destination set) queries under random penalty states
with both implementations and checks that costs/distances agree (relative
error 1e-9).  Tied routes may differ between implementations, so costs are
compared rather than the routes themselves.

Usage: python scripts/verify_fast_route.py [rail_file] [n_queries]
       (default: dataset/SMAT2022.rail, 300 queries)
"""
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ufast.route.rail_parser import RailParser
from ufast.route.pathfinder import PathFinder


def main():
    rail_file = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "dataset/SMAT2022.rail")
    n_queries = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    rng = random.Random(42)

    network = RailParser().parse(rail_file)
    pf = PathFinder(network)
    pf.initialize()

    node_names = list(network.nodes.keys())
    print(f"rail={rail_file}  nodes={len(node_names)}  queries={n_queries}")

    mismatches = 0
    t_fast = t_ref = 0.0
    for q in range(n_queries):
        # Random congestion state (on average OHTs present in 20% of the sections)
        for name in rng.sample(node_names, max(1, len(node_names) // 5)):
            network.nodes[name].traffic_penalty = 1.0 + rng.random() * 6.0
        pf.notify_all_penalties_changed()

        src = rng.choice(node_names)
        dsts = rng.sample(node_names, rng.randint(1, 4))

        t0 = time.perf_counter()
        engine = pf._get_fast_engine()
        assert engine is not None, "scipy engine is not available"
        fast = engine.cost_search(src, dsts)
        t_fast += time.perf_counter() - t0

        t0 = time.perf_counter()
        pf._fast_engine_disabled = True
        ref = pf.cost_search(src, dsts)
        pf._fast_engine_disabled = False
        t_ref += time.perf_counter() - t0

        for dst in dsts:
            fc, fd, fp = fast[dst]
            rc, rd, rp = ref[dst]
            cost_ok = abs(fc - rc) <= 1e-9 * max(1.0, abs(rc))
            dist_ok = abs(fd - rd) <= 1e-6 * max(1.0, abs(rd))
            # Different tied routes may have different distances -> cost agreement is the criterion.
            # If the routes are identical but the distances differ, that is a real bug.
            if not cost_ok or (fp == rp and not dist_ok):
                mismatches += 1
                print(f"[MISMATCH] {src} → {dst}")
                print(f"  fast: cost={fc:.12g} dist={fd:.6g} hops={len(fp)}")
                print(f"  ref : cost={rc:.12g} dist={rd:.6g} hops={len(rp)}")

    n = n_queries
    print(f"\nMean per query: fast {t_fast/n*1000:.3f}ms  |  ref {t_ref/n*1000:.3f}ms"
          f"  |  {t_ref/t_fast:.1f}x faster")
    if mismatches:
        print(f"FAIL: {mismatches} mismatches")
        sys.exit(1)
    print("PASS: costs agree for all queries")


if __name__ == "__main__":
    main()
