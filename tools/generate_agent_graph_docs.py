#!/usr/bin/env python3
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPHS_DIR = ROOT / "src" / "postbridge" / "agent" / "graphs"
DOCS_DIR = ROOT / "docs" / "agent-graphs"


@dataclass(frozen=True)
class GraphDocMeta:
    title: str
    kind: str
    purpose: str
    inputs: list[str]
    outputs: list[str]
    node_descriptions: dict[str, str]
    notes: list[str]


@dataclass(frozen=True)
class GraphSpec:
    graph_id: str
    builder_name: str
    file_path: Path
    nodes: list[str]
    child_builders: list[str]
    kind: str


GRAPH_META: dict[str, GraphDocMeta] = {
    "post_copilot": GraphDocMeta(
        title="# `post_copilot` Graph",
        kind="graph",
        purpose="Editor flow for draft creation and draft revision.",
        inputs=[
            "tenant_id",
            "channel_id",
            "user_request",
            "content_item_id",
            "image_request",
            "seed_urls",
            "approved_image_urls",
            "workspace_policy",
        ],
        outputs=[
            "selected_candidates",
            "source_package",
            "source_package_summary",
            "tool_trace",
        ],
        node_descriptions={
            "load_context": "Reads channel context, style profile, recent publications, and current draft state.",
            "build_source_package": "Invokes the compiled `source_package` subgraph and merges its outputs back into parent state.",
            "draft_candidate": "Builds the editor prompt, injects workspace policy and image constraints, and normalizes the final candidate.",
        },
        notes=[
            "This flow embeds `source_package` as a real subgraph.",
            "It is used by the orchestrator when `mode == \"post_copilot\"`.",
        ],
    ),
    "source_package": GraphDocMeta(
        title="# `source_package` Subgraph",
        kind="subgraph",
        purpose="Reusable source-discovery flow that collects, shortlists, and packages sources plus image candidates.",
        inputs=[
            "topic_definition",
            "user_request",
            "seed_urls",
            "image_request",
            "approved_image_urls",
            "workspace_policy",
        ],
        outputs=[
            "source_bundle",
            "shortlisted_source_bundle",
            "source_shortlist_summary",
            "source_package",
            "source_package_summary",
            "tool_trace",
        ],
        node_descriptions={
            "collect_sources": "Collects explicit `seed_urls` first, otherwise runs topic discovery with workspace-level source filters.",
            "shortlist_sources": "Uses `shortlist_topic_evidence(...)` to build a smaller editorial pack.",
            "prepare_source_package": "Builds the final source package, extracts image candidates, and computes summary metrics.",
        },
        notes=[
            "This flow is compiled independently and then invoked from `post_copilot`.",
            "It is also the source-review artifact shown to users before draft generation.",
        ],
    ),
    "topic_scout": GraphDocMeta(
        title="# `topic_scout` Graph",
        kind="graph",
        purpose="Discovery flow for topic ideation, evidence gathering, angle selection, and reranked candidate generation.",
        inputs=[
            "tenant_id",
            "channel_id",
            "topic_definition",
            "user_request",
            "seed_urls",
            "workspace_policy",
        ],
        outputs=[
            "selected_candidates",
            "tool_trace",
        ],
        node_descriptions={
            "load_context": "Loads channel context and source evidence through `collect_topic_evidence(...)` with workspace policy applied.",
            "shortlist_sources": "Builds a tighter shortlist of evidence sources.",
            "shortlist_angles": "Generates a more diverse angle pack from shortlisted sources.",
            "generate_candidates": "Builds the ideation prompt, injects workspace policy, and normalizes structured topic candidates.",
            "rerank_candidates": "Runs reranking and applies angle-diversity penalties to avoid repetitive results.",
        },
        notes=[
            "This flow stays in ideation mode and does not materialize a draft.",
            "It is used by the orchestrator when `mode == \"topic_scout\"`.",
        ],
    ),
}


def main() -> None:
    specs = load_graph_specs()
    builder_to_graph_id = {spec.builder_name: spec.graph_id for spec in specs}
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    write_overview(specs, builder_to_graph_id)
    for spec in specs:
        write_graph_doc(spec, builder_to_graph_id)


def load_graph_specs() -> list[GraphSpec]:
    specs: list[GraphSpec] = []
    for file_path in sorted(GRAPHS_DIR.glob("*.py")):
        if file_path.name == "__init__.py":
            continue
        module = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in module.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("build_"):
                continue
            ordered_nodes = extract_ordered_nodes(node)
            if not ordered_nodes:
                continue
            graph_id = node.name.removeprefix("build_").removesuffix("_graph").removesuffix("_subgraph")
            kind = "subgraph" if node.name.endswith("_subgraph") else "graph"
            child_builders = sorted(set(find_child_builder_calls(node)))
            specs.append(
                GraphSpec(
                    graph_id=graph_id,
                    builder_name=node.name,
                    file_path=file_path.relative_to(ROOT),
                    nodes=ordered_nodes,
                    child_builders=child_builders,
                    kind=kind,
                )
            )
    return specs


def extract_ordered_nodes(builder_node: ast.FunctionDef) -> list[str]:
    for child in ast.walk(builder_node):
        if not isinstance(child, ast.Call):
            continue
        if not isinstance(child.func, ast.Name) or child.func.id != "compile_linear_graph":
            continue
        if len(child.args) < 2 or not isinstance(child.args[1], ast.List):
            continue
        ordered: list[str] = []
        for item in child.args[1].elts:
            if (
                isinstance(item, ast.Tuple)
                and len(item.elts) >= 1
                and isinstance(item.elts[0], ast.Constant)
                and isinstance(item.elts[0].value, str)
            ):
                ordered.append(item.elts[0].value)
        return ordered
    return []


def find_child_builder_calls(builder_node: ast.FunctionDef) -> list[str]:
    out: list[str] = []
    for child in ast.walk(builder_node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name) and child.func.id.startswith("build_"):
            if child.func.id != builder_node.name:
                out.append(child.func.id)
    return out


def write_overview(specs: list[GraphSpec], builder_to_graph_id: dict[str, str]) -> None:
    inventory_lines = "\n".join(
        f"{idx}. [`{spec.graph_id}.md`](./{spec.graph_id}.md)"
        for idx, spec in enumerate(specs, start=1)
    )
    topology_lines = [
        "flowchart TD",
        "    ORCH[AgentOrchestrator.run_once]",
    ]
    for spec in specs:
        label = f"{spec.graph_id} {spec.kind}"
        topology_lines.append(f"    {node_id(spec.graph_id)}[{label}]")
    topology_lines.extend(
        [
            "    ORCH -->|mode=post_copilot| POST_COPILOT",
            "    ORCH -->|mode=topic_scout| TOPIC_SCOUT",
        ]
    )
    for spec in specs:
        for child in spec.child_builders:
            child_graph_id = builder_to_graph_id.get(child)
            if child_graph_id:
                topology_lines.append(
                    f"    {node_id(spec.graph_id)} --> {node_id(child_graph_id)}"
                )
    content = "\n".join(
        [
            "# Agent Graphs",
            "",
            "This folder documents every graph and subgraph currently used by the agent runtime in `postbridge-core`.",
            "",
            "Current graph inventory:",
            "",
            inventory_lines,
            "",
            "## Topology overview",
            "",
            "```mermaid",
            *topology_lines,
            "```",
            "",
            "## Notes",
            "",
            "- All documented flows are currently compiled through `compile_linear_graph(...)` in [`src/postbridge/agent/runtime.py`](../../src/postbridge/agent/runtime.py).",
            "- `source_package` is the only embedded subgraph today.",
            "- `AgentOrchestrator` is the runtime entrypoint, but it is not itself a LangGraph graph.",
        ]
    )
    (DOCS_DIR / "README.md").write_text(content + "\n", encoding="utf-8")


def write_graph_doc(spec: GraphSpec, builder_to_graph_id: dict[str, str]) -> None:
    meta = GRAPH_META.get(spec.graph_id)
    if meta is None:
        raise KeyError(f"Missing GRAPH_META entry for {spec.graph_id}")
    mermaid_lines = [
        "flowchart TD",
        "    GRAPH_START([START])",
    ]
    for name in spec.nodes:
        mermaid_lines.append(f"    {node_id(name)}[{name}]")
    mermaid_lines.append("    GRAPH_END([END])")
    mermaid_lines.append(f"    GRAPH_START --> {node_id(spec.nodes[0])}")
    for idx, name in enumerate(spec.nodes):
        target = "GRAPH_END" if idx == len(spec.nodes) - 1 else node_id(spec.nodes[idx + 1])
        mermaid_lines.append(f"    {node_id(name)} --> {target}")

    node_sections: list[str] = []
    for node_name in spec.nodes:
        node_sections.extend(
            [
                f"### `{node_name}`",
                "",
                meta.node_descriptions.get(node_name, "Node description not yet curated."),
                "",
            ]
        )

    child_lines: list[str] = []
    resolved_children = [
        builder_to_graph_id[child]
        for child in spec.child_builders
        if child in builder_to_graph_id
    ]
    if resolved_children:
        child_lines.extend(
            [
                "## Embedded subgraphs",
                "",
                *[f"- [`{child}.md`](./{child}.md)" for child in resolved_children],
                "",
            ]
        )

    notes_lines = [f"- {item}" for item in meta.notes]
    content = "\n".join(
        [
            meta.title,
            "",
            f"Source: [`{spec.file_path.as_posix()}`](../../{spec.file_path.as_posix()})",
            "",
            "## Purpose",
            "",
            meta.purpose,
            "",
            "## Diagram",
            "",
            "```mermaid",
            *mermaid_lines,
            "```",
            "",
            "## Nodes",
            "",
            *node_sections,
            "## Inputs",
            "",
            *[f"- `{item}`" for item in meta.inputs],
            "",
            "## Outputs",
            "",
            *[f"- `{item}`" for item in meta.outputs],
            "",
            *child_lines,
            "## Notes",
            "",
            *notes_lines,
        ]
    )
    (DOCS_DIR / f"{spec.graph_id}.md").write_text(content + "\n", encoding="utf-8")


def node_id(value: str) -> str:
    return value.upper().replace("-", "_")


if __name__ == "__main__":
    main()
