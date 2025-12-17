from collections import defaultdict, deque
from typing import Any, Dict, List, Set, Tuple


class DagExecutor:
    """
    Minimal DAG resolver/executor for structured plan steps.
    Steps are dicts with keys: id (string), requires (list of ids), tool/target/mode.
    Execution here is simulated; returns ordered batches respecting dependencies.
    """

    def __init__(self, steps: List[Dict[str, Any]]) -> None:
        self.steps = steps or []

    def resolve(self) -> Tuple[List[List[Dict[str, Any]]], List[str]]:
        """Return batches of steps in dependency order and list of unresolved ids."""
        graph: Dict[str, Set[str]] = defaultdict(set)
        indegree: Dict[str, int] = {}
        step_map: Dict[str, Dict[str, Any]] = {}

        for step in self.steps:
            sid = step.get("id") or f"step_{len(step_map)}"
            step_map[sid] = step
            requires = step.get("requires") or []
            indegree.setdefault(sid, 0)
            for r in requires:
                graph[r].add(sid)
                indegree[sid] = indegree.get(sid, 0) + 1

        queue = deque([sid for sid, deg in indegree.items() if deg == 0])
        batches: List[List[Dict[str, Any]]] = []
        visited = set()

        while queue:
            batch: List[Dict[str, Any]] = []
            for _ in range(len(queue)):
                sid = queue.popleft()
                visited.add(sid)
                batch.append(step_map[sid])
                for nei in graph.get(sid, []):
                    indegree[nei] -= 1
                    if indegree[nei] == 0:
                        queue.append(nei)
            batches.append(batch)

        unresolved = [sid for sid in step_map.keys() if sid not in visited]
        return batches, unresolved
