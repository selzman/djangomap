"""Text exports: Mermaid and Graphviz DOT, for pasting into a README / PR."""
from __future__ import annotations

REL = {"fk": "||--o{", "o2o": "||--||", "m2m": "}o--o{"}


def _safe(s: str) -> str:
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in str(s))


def to_mermaid_erd(data: dict) -> str:
    """Mermaid ER diagram of the models."""
    nodes = {n["id"]: n for n in data["nodes"]}
    out = ["erDiagram"]
    for n in data["nodes"]:
        if n["kind"] != "model":
            continue
        fields = (n["meta"].get("fields") or [])[:24]
        out.append(f"    {_safe(n['label'])} {{")
        for f in fields:
            t = _safe(f["type"].replace("Field", "") or "Field")
            name = _safe(f["name"])
            note = "PK" if f.get("opts") and "primary_key" in f["opts"] else (
                "FK" if f.get("to") else "")
            out.append(f"        {t} {name}{(' ' + note) if note else ''}")
        out.append("    }")
    for e in data["edges"]:
        if e["kind"] not in REL:
            continue
        s, t = nodes.get(e["source"]), nodes.get(e["target"])
        if not s or not t:
            continue
        out.append(f"    {_safe(s['label'])} {REL[e['kind']]} {_safe(t['label'])} : "
                   f'"{e.get("label") or e["kind"]}"')
    return "\n".join(out)


def to_mermaid_flow(data: dict) -> str:
    """Mermaid flowchart: URL -> View -> Model plus tasks, grouped by app."""
    nodes = {n["id"]: n for n in data["nodes"]}
    keep = {"url", "view", "model", "task", "beat", "signal", "serializer"}
    shape = {"url": ("([", "])"), "view": ("[", "]"), "model": ("[(", ")]"),
             "task": ("{{", "}}"), "beat": ("((", "))"), "signal": (">", "]"),
             "serializer": ("[/", "/]")}
    out = ["flowchart LR"]
    for app in data["apps"]:
        items = [n for n in data["nodes"] if n["app"] == app and n["kind"] in keep]
        if not items:
            continue
        out.append(f"    subgraph {_safe(app)}[{app}]")
        for n in items:
            a, b = shape.get(n["kind"], ("[", "]"))
            label = n["label"].replace('"', "'")
            out.append(f'        {_safe(n["id"])}{a}"{label}"{b}')
        out.append("    end")
    style = {"routes": "-->", "calls": "-.->", "schedules": "-.->",
             "fk": "-->", "m2m": "---", "o2o": "-->", "uses": "-.->",
             "queries": "-->", "listens": "-.->"}
    for e in data["edges"]:
        s, t = nodes.get(e["source"]), nodes.get(e["target"])
        if not s or not t or s["kind"] not in keep or t["kind"] not in keep:
            continue
        arrow = style.get(e["kind"])
        if not arrow:
            continue
        lbl = f'|{e["label"]}|' if e.get("label") else ""
        out.append(f'    {_safe(e["source"])} {arrow}{lbl} {_safe(e["target"])}')
    return "\n".join(out)


def to_dot(data: dict) -> str:
    """Graphviz DOT, clustered by app."""
    colors = {"model": "#7ee787", "view": "#79c0ff", "url": "#ffa657",
              "task": "#d2a8ff", "beat": "#ff9ecd", "signal": "#ff7b72",
              "serializer": "#a5d6ff", "form": "#f0c674", "admin": "#56d4dd",
              "middleware": "#c9a2ff", "command": "#9ae6b4"}
    out = ["digraph djangomap {",
           '  graph [rankdir=LR, bgcolor="#0b0f14", fontname="Helvetica", pad=0.4];',
           '  node [shape=box, style="rounded,filled", fontname="Helvetica", '
           'fontsize=10, color="#232c38", fontcolor="#0b0f14"];',
           '  edge [color="#4d5b6b", fontname="Helvetica", fontsize=8, fontcolor="#7d8b99"];']
    for i, app in enumerate(data["apps"]):
        items = [n for n in data["nodes"] if n["app"] == app]
        if not items:
            continue
        out.append(f'  subgraph cluster_{i} {{')
        out.append(f'    label="{app}"; fontcolor="#e6edf3"; color="#232c38";')
        for n in items:
            out.append(f'    "{n["id"]}" [label="{n["label"]}", '
                       f'fillcolor="{colors.get(n["kind"], "#8b98a5")}"];')
        out.append("  }")
    for e in data["edges"]:
        lbl = f', label="{e["label"]}"' if e.get("label") else ""
        style = ", style=dashed" if e["kind"] in ("calls", "schedules", "m2m",
                                                  "uses", "inherits") else ""
        out.append(f'  "{e["source"]}" -> "{e["target"]}" [tooltip="{e["kind"]}"{lbl}{style}];')
    out.append("}")
    return "\n".join(out)


def to_markdown(data: dict, health: dict | None = None) -> str:
    """Summary Markdown report + Mermaid ERD, for a README or PR comment."""
    st = data["stats"]
    out = [f"# {data['name']}", "",
           "| kind | count |", "|---|---|"]
    for k, v in sorted(st.items()):
        out.append(f"| {k} | {v} |")
    if health:
        c = health["counts"]
        out += ["", f"**Health score: {health['score']}/100** — "
                    f"{c['error']} errors · {c['warn']} warnings · {c['info']} info", ""]
        top: dict[str, int] = {}
        for i in health["issues"]:
            top[i["code"]] = top.get(i["code"], 0) + 1
        out += ["| check | count |", "|---|---|"]
        for k, v in sorted(top.items(), key=lambda x: -x[1]):
            out.append(f"| `{k}` | {v} |")
    out += ["", "## ERD", "", "```mermaid", to_mermaid_erd(data), "```"]
    return "\n".join(out)


EXPORTERS = {
    "mermaid": to_mermaid_erd,
    "mermaid-erd": to_mermaid_erd,
    "mermaid-flow": to_mermaid_flow,
    "dot": to_dot,
}
