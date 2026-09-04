#!/usr/bin/env python3
"""queue 혼잡 모델의 섹션별 시간평균 점유 heatmap (논문 Figure (a) 의 queue 판).

1일 HVLM co-simulation 을 --congestion queue 로 계측 실행해 섹션별
시간평균 점유 ∫n(s,t)dt / T 를 수집하고, 레일 레이아웃 위에
Figure (a) 와 동일한 스타일(RdYlGn_r + PowerNorm)로 그린다.
blocking 으로 정지해 있는 차량도 점유에 포함되므로, delay 모델의
Figure (a) 와 나란히 두면 두 충실도의 혼잡 표현 차이가 드러난다.

사용:
  PYTHONPATH=src python scripts/make_queue_occupancy_heatmap.py [out.png]
  옵션 환경변수: UFAST_HEATMAP_DAYS(기본 1), UFAST_HEATMAP_OHT(기본 100),
                UFAST_HEATMAP_CONGESTION(기본 queue)
"""
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'src'))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import PowerNorm
import numpy as np

from ufast.cosim.amhs import AMHSExecutor
from ufast.cosim.run import run_ufast


def main():
    out_path = (sys.argv[1] if len(sys.argv) > 1
                else os.path.join(_ROOT, 'results',
                                  'queue_occupancy_heatmap.png'))
    days = int(os.environ.get('UFAST_HEATMAP_DAYS', '1'))
    num_oht = int(os.environ.get('UFAST_HEATMAP_OHT', '100'))
    congestion = os.environ.get('UFAST_HEATMAP_CONGESTION', 'queue')

    # ── 계측: _add/_remove_inflight 를 감싸 시간 적분 ∫n dt 수집 ──
    occ_integral = defaultdict(float)   # sec -> ∫ n dt
    occ_last_t = defaultdict(float)     # sec -> 마지막 변경 시각
    _orig_add = AMHSExecutor._add_inflight
    _orig_remove = AMHSExecutor._remove_inflight

    def _accumulate(self, sec):
        t = self.last_event_time
        n = self.section_inflight.get(sec, 0)
        occ_integral[sec] += n * max(0.0, t - occ_last_t[sec])
        occ_last_t[sec] = t

    def _add(self, sec, *args, **kwargs):
        _accumulate(self, sec)
        return _orig_add(self, sec, *args, **kwargs)

    def _remove(self, sec, *args, **kwargs):
        _accumulate(self, sec)
        return _orig_remove(self, sec, *args, **kwargs)

    AMHSExecutor._add_inflight = _add
    AMHSExecutor._remove_inflight = _remove
    try:
        _instance, amhs, _ = run_ufast(
            os.path.join(_ROOT, 'dataset', 'HVLM'),
            os.path.join(_ROOT, 'dataset', 'SMAT2022.rail'),
            days=days, num_oht=num_oht, seed=0,
            congestion_model=congestion,
        )
    finally:
        AMHSExecutor._add_inflight = _orig_add
        AMHSExecutor._remove_inflight = _orig_remove

    T = max(amhs.last_event_time, 1.0)
    mean_occ = {sec: v / T for sec, v in occ_integral.items()}
    print(f"[queue_occ] sections with traffic: {len(mean_occ)}, "
          f"max mean occ: {max(mean_occ.values()):.3f}")

    nodes = amhs.rm.network.nodes
    segments, values = [], []
    for sec, names in amhs.bridge.section_to_nodes.items():
        pts = [(nodes[n].x, nodes[n].y) for n in names if n in nodes]
        if len(pts) < 2:
            continue
        for i in range(len(pts) - 1):
            segments.append([pts[i], pts[i + 1]])
            values.append(mean_occ.get(sec, 0.0))

    values = np.array(values)
    fig, ax = plt.subplots(figsize=(7.0, 4.2), dpi=300)
    base = LineCollection(segments, colors='#d9d9d9', linewidths=0.8, zorder=1)
    ax.add_collection(base)
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
    cbar.set_label('Time-averaged OHTs per section', fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, bbox_inches='tight')
    fig.savefig(out_path.replace('.png', '.pdf'), bbox_inches='tight')
    print(f"[queue_occ] 저장: {out_path} (+.pdf)")


if __name__ == '__main__':
    main()
