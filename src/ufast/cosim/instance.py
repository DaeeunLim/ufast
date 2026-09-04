from __future__ import annotations

import heapq

from ufast.production.events import MachineDoneEvent
from ufast.production.file_instance import FileInstance
from ufast.production.tools import ConstantDistribution
from ufast.cosim.warmup import WarmupPolicy


class StaticTransportDoneEvent:
    """Completes a static warm-up transport leg without involving AMHS."""

    def __init__(self, timestamp, lot, dest, transport_dur):
        self.timestamp = timestamp
        self.lot = lot
        self.dest = dest
        self.transport_dur = transport_dur
        self.machines = []
        self.lots = [lot]

    def handle(self, instance):
        instance._on_static_delivered(
            self.lot, self.dest, self.timestamp, self.transport_dur)


class UFastInstance(FileInstance):
    """Production instance whose inter-step transport is handled by AMHS."""

    def __init__(self, files, run_to, lot_for_machine, plugins, route_manager, amhs,
                 machine_equipment=None, machine_selection='exact',
                 warmup_policy=None):
        # FileInstance.__init__ calls next_step(), which can call
        # _lot_ready_for_step().  Prepare the AMHS seam before super().
        self.rm = route_manager
        self.amhs = amhs
        amhs.instance = self
        self.warmup_policy = warmup_policy or WarmupPolicy()
        self.measurement_start_time = self.warmup_policy.measurement_start_s

        self._fam_node = dict(route_manager.network.eq_to_node)
        self._fam_node_ci = {k.lower(): v for k, v in self._fam_node.items()}

        self.transport_count = 0
        self.static_transport_count = 0
        self.static_transport_time = 0.0
        self.skipped_transport = 0
        self.same_node_transport = 0
        self.reserved_transport = 0
        self.preassigned_machine = 0
        self._transport_estimate_cache = {}
        self._static_transport_dist = {}
        self._machine_xy_cache = {}
        self._preselect_exact_candidates = 8
        # Machine selection mode: 'exact' = exact route (Dijkstra) per candidate, 'nearest' =
        # rough Euclidean distance only (for destination choice, skips Dijkstra → perf #3).
        self.machine_selection = machine_selection

        super().__init__(files, run_to, lot_for_machine, plugins,
                         machine_equipment=machine_equipment)

        # AMHS owns inter-step movement in co-sim mode.
        for route in self.routes.values():
            for step in route.steps:
                self._static_transport_dist[step] = step.transport_time
                step.transport_time = ConstantDistribution(0)

    def family_node(self, family):
        """Representative rail node for an STNFAM/tool group."""
        node = self._fam_node.get(family)
        if node is None:
            node = self._fam_node_ci.get(str(family).lower())
        return node

    def machine_node(self, machine):
        if machine is None:
            return None
        return getattr(machine, "node_name", None) or self.family_node(machine.family)

    def _setup_penalty(self, lot, machine):
        new_setup = lot.actual_step.setup_needed
        if not new_setup or machine.current_setup == new_setup:
            return 0.0
        if lot.actual_step.setup_time is not None:
            return float(lot.actual_step.setup_time)
        if (machine.current_setup, new_setup) in self.setups:
            return float(self.setups[(machine.current_setup, new_setup)])
        if ("", new_setup) in self.setups:
            return float(self.setups[("", new_setup)])
        return 0.0

    def _machine_available_at(self, machine):
        if machine.idx < len(self.free_machines) and self.free_machines[machine.idx]:
            return self.current_time
        events = getattr(machine, "events", [])
        for event in events:
            if type(event) is MachineDoneEvent:
                return max(self.current_time, event.timestamp)
        times = [event.timestamp for event in events]
        return max(self.current_time, min(times)) if times else self.current_time

    def _machine_xy(self, machine):
        """Cache of machine position coordinates (the node mapping is immutable during a run)."""
        idx = machine.idx
        if idx in self._machine_xy_cache:
            return self._machine_xy_cache[idx]
        node = self.machine_node(machine)
        n = self.rm.network.nodes.get(node) if node else None
        xy = (n.x, n.y) if n is not None else None
        self._machine_xy_cache[idx] = xy
        return xy

    def _transport_estimate(self, from_node, to_node):
        if not from_node or not to_node or from_node == to_node:
            return 0.0
        key = (from_node, to_node)
        cached = self._transport_estimate_cache.get(key)
        if cached is not None:
            return cached
        try:
            _nodes, _secs, durs = self.amhs._route(from_node, to_node)
            estimate = float(sum(durs))
        except Exception:
            estimate = 0.0
        self._transport_estimate_cache[key] = estimate
        return estimate

    def _rough_transport_estimate(self, from_node, to_node):
        if not from_node or not to_node or from_node == to_node:
            return 0.0
        a = self.rm.network.nodes.get(from_node)
        b = self.rm.network.nodes.get(to_node)
        if a is None or b is None:
            return 0.0
        return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5

    def _best_exact(self, candidates, rough_key, exact_key):
        short = heapq.nsmallest(
            self._preselect_exact_candidates, candidates, key=rough_key)
        return min(short, key=exact_key)

    def _candidate_machines(self, lot):
        machines = []
        di = lot.actual_step.order
        for machine in self.family_machines[lot.actual_step.family]:
            if di in lot.dedications and machine.idx != lot.dedications[di]:
                continue
            machines.append(machine)
        return machines

    def _select_machine_for_lot(self, lot, current_node):
        candidates = self._candidate_machines(lot)
        if not candidates:
            return None

        # Single-pass precomputation: setup penalty, rough (Euclidean) distance and
        # availability time are computed once per machine. The key functions build the
        # same tuples as before, so the selection (including ties) is unchanged.
        step = lot.actual_step
        new_setup = step.setup_needed
        cur = self.rm.network.nodes.get(current_node) if current_node else None
        free_flags = self.free_machines
        n_flags = len(free_flags)

        setups = {}
        dists = {}
        free = []
        for m in candidates:
            if not new_setup or m.current_setup == new_setup:
                setup = 0.0
            elif step.setup_time is not None:
                setup = float(step.setup_time)
            else:
                v = self.setups.get((m.current_setup, new_setup))
                if v is None:
                    v = self.setups.get(("", new_setup))
                setup = float(v) if v is not None else 0.0
            setups[m.idx] = setup

            node = self.machine_node(m)
            if cur is None or node is None or node == current_node:
                dist = 0.0
            else:
                xy = self._machine_xy(m)
                dist = (((cur.x - xy[0]) ** 2 + (cur.y - xy[1]) ** 2) ** 0.5
                        if xy is not None else 0.0)
            dists[m.idx] = dist

            if m.idx < n_flags and free_flags[m.idx] \
                    and not getattr(m, "reserved_lots", None):
                free.append(m)

        def rough_free(machine):
            setup = setups[machine.idx]
            return (1 if setup else 0, dists[machine.idx], setup, machine.idx)

        def score_free(machine):
            setup = setups[machine.idx]
            transport = self._transport_estimate(current_node, self.machine_node(machine))
            return (1 if setup else 0, transport, setup, machine.idx)

        if free:
            setup_matched = [m for m in free if setups[m.idx] == 0.0]
            pool = setup_matched or free
            if self.machine_selection == 'nearest':
                return min(pool, key=rough_free)
            return self._best_exact(pool, rough_free, score_free)

        avails = {}

        def _avail(machine):
            a = avails.get(machine.idx)
            if a is None:
                a = self._machine_available_at(machine)
                avails[machine.idx] = a
            return a

        def rough_busy(machine):
            setup = setups[machine.idx]
            transport = dists[machine.idx]
            wait = _avail(machine) - self.current_time
            return (wait + setup + transport, setup, transport, machine.idx)

        def score_busy(machine):
            setup = setups[machine.idx]
            transport = self._transport_estimate(current_node, self.machine_node(machine))
            wait = _avail(machine) - self.current_time
            return (wait + setup + transport, setup, transport, machine.idx)

        if self.machine_selection == 'nearest':
            return min(candidates, key=rough_busy)
        return self._best_exact(candidates, rough_busy, score_busy)

    def _reserve_for_transport(self, lot, machine):
        if machine is None:
            return
        lot.reserved_machine = machine
        if lot not in machine.reserved_lots:
            machine.reserved_lots.append(lot)
        self.preassigned_machine += 1
        if machine in self.usable_machines:
            self.usable_machines.remove(machine)

    def _static_transport_sample(self, lot):
        dist = self._static_transport_dist.get(lot.actual_step)
        if dist is None:
            return 0.0
        return float(dist.sample())

    def _lot_ready_for_step(self, lot, old_step):
        cur = getattr(lot, "current_node", None)
        # Transit window (for attributing starvation to transport) — set below only when
        # a real transport happens in this step. Clear first so the previous step's
        # window does not linger.
        lot.last_transit_start = None
        lot.last_transit_end = None
        # Select an individual machine only as the destination (transport arrival node =
        # that machine's position). Production dispatching is not bound by a reservation
        # but left to the family-wide queue — reservations exhausted usable_machines and
        # caused production gridlock under high WIP (stall at day 41). An arriving lot is
        # processed by whichever machine of the family frees up first.
        machine = self._select_machine_for_lot(lot, cur)
        dest = self.machine_node(machine) if machine is not None else self.family_node(lot.actual_step.family)

        if old_step is None or dest is None or cur is None or cur == dest:
            if dest is not None:
                lot.current_node = dest
            if old_step is not None and dest is None:
                self.skipped_transport += 1
            if old_step is not None and dest is not None and cur == dest:
                self.same_node_transport += 1
            self.dm.free_up_lots(self, lot)
            return

        if self.warmup_policy.use_static_transport(self.current_time):
            dur = self._static_transport_sample(lot)
            self.static_transport_count += 1
            self.static_transport_time += dur
            lot.last_transit_start = self.current_time
            if dur <= 0:
                self._on_static_delivered(lot, dest, self.current_time, dur)
            else:
                self.add_event(StaticTransportDoneEvent(
                    self.current_time + dur, lot, dest, dur))
            return

        self.transport_count += 1
        self.reserved_transport += 1
        lot.last_transit_start = self.current_time
        self.amhs.request_transport(
            lot, cur, dest, self.current_time,
            lambda dt, dur, l=lot, d=dest: self._on_delivered(l, d, dt, dur),
        )

    def _on_static_delivered(self, lot, dest, deliver_time, transport_dur):
        lot.current_node = dest
        lot.transport_time += transport_dur
        lot.last_transit_end = deliver_time
        lot.free_since = deliver_time
        self.dm.free_up_lots(self, lot)

    def _on_delivered(self, lot, dest, deliver_time, transport_dur):
        lot.current_node = dest
        lot.transport_time += transport_dur
        lot.last_transit_end = deliver_time
        lot.free_since = deliver_time
        self.dm.free_up_lots(self, lot)
