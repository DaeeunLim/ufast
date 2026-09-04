"""fast_pathfinder(scipy C 다익스트라) ↔ 순정 cost_search 동등성 검증.

무작위 penalty 상태에서 무작위 (출발, 도착셋) 쿼리를 두 구현으로 계산해
비용/거리 일치(상대오차 1e-9)를 확인한다. 동률 경로는 서로 다를 수 있으므로
경로 자체가 아니라 비용을 비교한다.

사용법: python scripts/verify_fast_route.py [rail_file] [n_queries]
        (기본: dataset/SMAT2022.rail, 300쿼리)
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
        # 무작위 혼잡 상태 (평균적으로 20% 섹션에 OHT 존재하는 수준)
        for name in rng.sample(node_names, max(1, len(node_names) // 5)):
            network.nodes[name].traffic_penalty = 1.0 + rng.random() * 6.0
        pf.notify_all_penalties_changed()

        src = rng.choice(node_names)
        dsts = rng.sample(node_names, rng.randint(1, 4))

        t0 = time.perf_counter()
        engine = pf._get_fast_engine()
        assert engine is not None, "scipy 엔진을 사용할 수 없습니다"
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
            # 동률 경로가 다르면 거리가 다를 수 있다 → 비용 일치가 판정 기준.
            # 경로까지 같은데 거리가 다르면 그건 진짜 버그다.
            if not cost_ok or (fp == rp and not dist_ok):
                mismatches += 1
                print(f"[MISMATCH] {src} → {dst}")
                print(f"  fast: cost={fc:.12g} dist={fd:.6g} hops={len(fp)}")
                print(f"  ref : cost={rc:.12g} dist={rd:.6g} hops={len(rp)}")

    n = n_queries
    print(f"\n쿼리당 평균: fast {t_fast/n*1000:.3f}ms  |  ref {t_ref/n*1000:.3f}ms"
          f"  |  {t_ref/t_fast:.1f}x 빠름")
    if mismatches:
        print(f"FAIL: {mismatches}건 불일치")
        sys.exit(1)
    print("PASS: 전 쿼리 비용 일치")


if __name__ == "__main__":
    main()
