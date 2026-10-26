import heapq
from dataclasses import dataclass

MAX_STEPS = 3


@dataclass(frozen=True)
class Step:
    engine: object
    source: str
    target: str


@dataclass(frozen=True)
class Route:
    steps: tuple
    cost: int


class RoutePlanner:
    def __init__(self, engines, priority=None):
        priority = priority or []
        self.edges_from = {}
        for engine in engines:
            penalty = priority.index(engine.id) if engine.id in priority else len(priority)
            for edge in engine.edges:
                self.edges_from.setdefault(edge.source, []).append((engine, edge, edge.cost + min(penalty, 9)))
        self._cache = {}

    def plan(self, source, target):
        key = (source, target)
        if key not in self._cache:
            self._cache[key] = self._plan(source, target)
        return self._cache[key]

    def _plan(self, source, target):
        if source == target:
            best = min(
                ((engine, cost) for engine, edge, cost in self.edges_from.get(source, []) if edge.target == target),
                key=lambda item: item[1],
                default=None,
            )
            return Route((Step(best[0], source, target),), best[1]) if best else None

        dist = {source: 0}
        hops = {source: 0}
        prev = {}
        counter = 0
        queue = [(0, counter, source)]
        while queue:
            cost, _, node = heapq.heappop(queue)
            if cost > dist[node]:
                continue
            if node == target:
                break
            if hops[node] >= MAX_STEPS:
                continue
            for engine, edge, edge_cost in self.edges_from.get(node, []):
                if edge.target == edge.source or edge.target == source:
                    continue
                if (edge.start_only and node != source) or (edge.final_only and edge.target != target):
                    continue
                total = cost + edge_cost
                if edge.target in dist and dist[edge.target] <= total:
                    continue
                dist[edge.target] = total
                hops[edge.target] = hops[node] + 1
                prev[edge.target] = (node, Step(engine, node, edge.target))
                counter += 1
                heapq.heappush(queue, (total, counter, edge.target))

        if target not in prev:
            return None
        steps = []
        node = target
        while node != source:
            node, step = prev[node]
            steps.append(step)
        return Route(tuple(reversed(steps)), dist[target])

    def can_convert(self, source, target):
        return self.plan(source, target) is not None

    def targets_from(self, source):
        reachable = set()
        frontier = {source}
        for hop in range(MAX_STEPS):
            following = set()
            for node in frontier:
                for _, edge, _ in self.edges_from.get(node, []):
                    if edge.target == source or (edge.start_only and hop > 0):
                        continue
                    reachable.add(edge.target)
                    if not edge.final_only and edge.target not in following:
                        following.add(edge.target)
            frontier = following
        return reachable
