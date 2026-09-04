"""
Example custom routing cost function (edge cost for route search).

Use:
  ufast-run ... --custom-routing-cost examples/custom_routing_cost_example.py
  (or put your own file in strategies/ and pass its bare name; GUI: Simulation
  settings > Routing Cost Function)

The pathfinder calls compute_cost(...) once per edge with keyword arguments:
  move_time         free-flow traversal time of the edge [s]
  raw_penalty       current traffic penalty on the edge (1.0 = free)
  effective_penalty penalty after the pathfinder's own shaping
  section           NetworkSection the edge belongs to (has .section_type)
  from_node, to_node   Node objects
  default_cost      the built-in cost (move_time * effective_penalty)
  context           search context tag (e.g. 'DISPATCH_PROBE')
Return the edge cost as a float. None / negative / an exception fall back to
default_cost. Setting a custom cost function disables the C-accelerated
pathfinder (pure-Python search), so runs get slower — that is expected.
"""


def compute_cost(move_time, raw_penalty, effective_penalty=None, section=None,
                 from_node=None, to_node=None, default_cost=None, context="",
                 **_ignored):
    if effective_penalty is None:
        effective_penalty = raw_penalty
    cost = move_time * effective_penalty

    # Example 1: treat curved sections a little more conservatively.
    if getattr(section, "section_type", "") == "CURVE":
        cost *= 1.15

    # Example 2: extra penalty on heavily congested edges.
    if raw_penalty > 2.0:
        cost += move_time * 0.25 * (raw_penalty - 2.0)

    return cost
