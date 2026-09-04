#!/usr/bin/env python3
"""queue 혼잡 모델의 섹션별 차단(blocking) heatmap.

결과 JSON 의 amhs.blocked_time_by_section 을 레일 레이아웃 위에 그린다.
스타일은 논문 Figure (a) (make_fig_a_heatmap.py) 와 동일 — RdYlGn_r
sequential 컬러맵 + PowerNorm, 무차단 섹션은 옅은 회색.

사용:
  python scripts/make_queue_blocking_heatmap.py <result.json> [rail_file] [out.png]
  (rail_file 생략 시 meta.rail_file, out 생략 시 <json_base>_blocking_heatmap.png)
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import PowerNorm
import numpy as np

from ufast.route import RouteManager, SectionNodeBridge


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    json_path = sys.argv[1]
    with open(json_path) as f:
        data = json.load(f)
    meta, amhs = data['meta'], data['amhs']
    rail_file = sys.argv[2] if len(sys.argv) > 2 else meta['rail_file']
    out_path = (sys.argv[3] if len(sys.argv) > 3
                else json_path.replace('.json', '_blocking_heatmap.png'))

    blocked_time = {int(k): v
                    for k, v in amhs.get('blocked_time_by_section', {}).items()}
    if not blocked_time:
        sys.exit("결과에 blocked_time_by_section 이 없습니다 "
                 "(--congestion queue 로 실행했는지 확인).")

    rm = RouteManager()
    rm.load_from_rail(rail_file)
    rm.initialize()
    bridge = SectionNodeBridge(rm)
    bridge.build_mapping()

    nodes = rm.network.nodes
    segments, values = [], []
    for sec_id, sec_nodes in bridge.section_to_nodes.items():
        pts = [(nodes[n].x, nodes[n].y) for n in sec_nodes if n in nodes]
        if len(pts) < 2:
            continue
        bt = blocked_time.get(sec_id, 0.0)
        for i in range(len(pts) - 1):
            segments.append([pts[i], pts[i + 1]])
            values.append(bt)

    values = np.array(values)
    fig, ax = plt.subplots(figsize=(7.0, 4.2), dpi=300)

    # 배경: 전체 레일을 옅은 회색으로 (Figure (a) 와 동일 스타일)
    base = LineCollection(segments, colors='#d9d9d9', linewidths=0.8, zorder=1)
    ax.add_collection(base)

    # 차단 발생 섹션: 초록(경미) → 노랑 → 빨강(심함). 무차단 섹션은 회색 유지.
    mask = values > 0
    hot = LineCollection(
        [s for s, m in zip(segments, mask) if m],
        array=values[mask], cmap='RdYlGn_r',
        norm=PowerNorm(gamma=0.45, vmin=0, vmax=values.max()),
        linewidths=2.0, zorder=2, capstyle='round')
    ax.add_collection(hot)

    ax.autoscale()
    ax.set_aspect('equal')
    ax.axis('off')
    cbar = fig.colorbar(hot, ax=ax, fraction=0.03, pad=0.01)
    cbar.set_label('Blocked waiting time per section [s]', fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, bbox_inches='tight')
    fig.savefig(out_path.replace('.png', '.pdf'), bbox_inches='tight')
    print(f"저장: {out_path} (+.pdf) — 차단 섹션 {len(blocked_time)}개, "
          f"이벤트 {amhs.get('blocked_events', 0):,}회")


if __name__ == '__main__':
    main()
