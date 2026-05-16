from __future__ import annotations

from collections.abc import Callable
from typing import Any


try:
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when dependency missing locally
    END = "__end__"
    START = "__start__"
    StateGraph = None
    LANGGRAPH_AVAILABLE = False


class SimpleCompiledGraph:
    def __init__(self, nodes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]], edges: dict[str, str], entry: str):
        self._nodes = nodes
        self._edges = edges
        self._entry = entry

    def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        current = self._entry
        out = dict(state)
        while current != END:
            fn = self._nodes[current]
            delta = fn(out) or {}
            out.update(delta)
            current = self._edges[current]
        return out


def compile_linear_graph(
    state_type: type,
    ordered_nodes: list[tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]],
) -> Any:
    if LANGGRAPH_AVAILABLE:
        graph = StateGraph(state_type)
        for name, fn in ordered_nodes:
            graph.add_node(name, fn)
        graph.add_edge(START, ordered_nodes[0][0])
        for idx, (name, _) in enumerate(ordered_nodes):
            if idx == len(ordered_nodes) - 1:
                graph.add_edge(name, END)
            else:
                graph.add_edge(name, ordered_nodes[idx + 1][0])
        return graph.compile()
    nodes = {name: fn for name, fn in ordered_nodes}
    edges: dict[str, str] = {}
    for idx, (name, _) in enumerate(ordered_nodes):
        edges[name] = END if idx == len(ordered_nodes) - 1 else ordered_nodes[idx + 1][0]
    return SimpleCompiledGraph(nodes, edges, ordered_nodes[0][0])
