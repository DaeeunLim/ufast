"""
ufast/machine_activity.py — production Machine 의 가동 기간을 기록하는 plugin.

PySCFabSim IPlugin 인터페이스를 이용해 dispatch 시작 / machine free 시점을
훅으로 받아 (start, end, family) tuple 을 누적한다.
trajectory log 에 같이 저장돼 rerun_replay 에서 family 단위 활동 시각화에 쓰임.

기록되는 활동:
  - 한 머신이 dispatch 로 busy 가 된 시점부터 free_up_machines 로 free 가 되는 시점까지.
  - 시뮬레이션 종료 시점에 여전히 busy 인 머신은 미완 활동(_active) 으로 남음
    — finalize() 에서 끝점을 current_time 으로 닫는다.
"""
from __future__ import annotations
from typing import Any, Dict, List, Tuple

from ufast.production.plugins.interface import IPlugin


class MachineActivityPlugin(IPlugin):
    """머신 가동 기간 (start, end, family) 을 누적하는 plugin."""

    def __init__(self):
        self.activities: List[Dict[str, Any]] = []
        # machine.idx → (start_time, family)
        self._active: Dict[int, Tuple[float, str]] = {}

    def on_dispatch(self, instance, machine, lots,
                    machine_end_time, lot_end_time):
        # 동일 machine 이 미반환 상태라면 (안전) 그 기록을 닫고 새로 시작
        if machine.idx in self._active:
            start, family = self._active.pop(machine.idx)
            self.activities.append({
                'start': start,
                'end': instance.current_time,
                'family': family,
            })
        self._active[machine.idx] = (instance.current_time, machine.family)

    def on_machine_free(self, instance, machine):
        if machine.idx in self._active:
            start, family = self._active.pop(machine.idx)
            self.activities.append({
                'start': start,
                'end': instance.current_time,
                'family': family,
            })

    def on_sim_done(self, instance):
        # 종료 시점에 여전히 busy 인 머신들의 활동을 마무리
        end = instance.current_time
        for idx, (start, family) in list(self._active.items()):
            self.activities.append({
                'start': start,
                'end': end,
                'family': family,
            })
        self._active.clear()
