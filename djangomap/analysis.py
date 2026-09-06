"""
Project health analysis: complexity, lint warnings, N+1 risk and circular deps.

Works on the graph the scanner already produced - no re-parsing needed.
Cyclomatic complexity is computed in the scanner and stored on node.meta.
"""
from __future__ import annotations

from .scanner import Project

# severity: error | warn | info
CHECKS = {
    "model.no_str":        ("warn", "Model has no __str__ method"),
    "model.fk_no_related": ("info", "ForeignKey without related_name"),
    "model.no_ordering":   ("warn", "Model used in a ListView but has no Meta.ordering"),
    "model.orphan":        ("info", "No view / serializer / admin references this model"),
    "model.no_indexes":    ("info", "Model has many fields but no db_index"),
    "url.no_name":         ("warn", "URL has no name= (cannot be used with reverse())"),
    "url.no_view":         ("error", "URL is not wired to any known view"),
    "view.no_perm":        ("warn", "DRF view without permission_classes"),
    "view.n_plus_one":     ("error", "N+1 risk: model has FKs but no select_related/prefetch_related"),
    "view.unauth":         ("info", "View without login_required / permission"),
    "task.no_retry":       ("info", "Task without max_retries / autoretry_for"),
    "task.orphan":         ("warn", "Task is never called (no delay() and no beat schedule)"),
    "task.no_bind_retry":  ("info", "Task uses self.retry but is not declared with bind=True"),
    "signal.no_sender":    ("warn", "receiver without sender (fires for every model)"),
    "complexity.high":     ("warn", "High cyclomatic complexity"),
    "app.cycle":           ("error", "Circular dependency between apps"),
}

COMPLEX_WARN = 10
COMPLEX_ERR = 18


def analyse(proj: Project) -> dict:
    nodes = {n.id: n for n in proj.nodes}
    issues: list[dict] = []

    def add(node_id, code, detail=""):
        sev, msg = CHECKS[code]
        issues.append({"node": node_id, "code": code, "severity": sev,
                       "message": msg, "detail": detail})

    # --- indexes for lookups -------------------------------------------- #
    out_edges: dict[str, list] = {}
    in_edges: dict[str, list] = {}
    for e in proj.edges:
        out_edges.setdefault(e.source, []).append(e)
        in_edges.setdefault(e.target, []).append(e)

    listview_models = set()
    for n in proj.nodes:
        if n.kind == "view" and "List" in " ".join(n.meta.get("bases", [])):
            for e in out_edges.get(n.id, []):
                if nodes.get(e.target, None) and nodes[e.target].kind == "model":
                    listview_models.add(e.target)

    # --- per node checks ------------------------------------------------- #
    for n in proj.nodes:
        m = n.meta or {}
        cx = m.get("complexity") or 0
        if cx >= COMPLEX_WARN:
            issues.append({
                "node": n.id, "code": "complexity.high",
                "severity": "error" if cx >= COMPLEX_ERR else "warn",
                "message": CHECKS["complexity.high"][1], "detail": f"CC = {cx}"})

        if n.kind == "model":
            methods = m.get("methods") or []
            if "__str__" not in (m.get("all_methods") or methods):
                add(n.id, "model.no_str")
            fields = m.get("fields") or []
            for f in fields:
                if f.get("to") and not f.get("related_name") and f["type"] != "ManyToManyField":
                    add(n.id, "model.fk_no_related", f["name"])
            if n.id in listview_models and not (m.get("meta_opts") or {}).get("ordering"):
                add(n.id, "model.no_ordering")
            refs = [e for e in in_edges.get(n.id, [])
                    if nodes.get(e.source) and nodes[e.source].kind in
                    ("view", "serializer", "admin", "form")]
            if fields and not refs:
                add(n.id, "model.orphan")
            if len(fields) >= 6 and not any(
                    "db_index" in (f.get("opts") or []) or f.get("to") for f in fields):
                add(n.id, "model.no_indexes")

        elif n.kind == "url":
            if not m.get("name") and not m.get("include"):
                add(n.id, "url.no_name", m.get("handler", ""))
            if not m.get("include") and not any(
                    e.kind == "routes" for e in out_edges.get(n.id, [])):
                add(n.id, "url.no_view", m.get("handler", ""))

        elif n.kind == "view":
            decs = " ".join(m.get("decorators") or [])
            is_drf = any("APIView" in b or "ViewSet" in b or "generics" in b
                         for b in (m.get("bases") or [])) or "api_view" in decs
            if is_drf and not m.get("permission_classes"):
                add(n.id, "view.no_perm")
            if not is_drf and "login_required" not in decs and not any(
                    "LoginRequired" in b or "Permission" in b for b in (m.get("bases") or [])):
                add(n.id, "view.unauth")
            # N+1 heuristic
            model_ids = [e.target for e in out_edges.get(n.id, [])
                         if nodes.get(e.target) and nodes[e.target].kind == "model"]
            has_fk = any((nodes[mid].meta.get("fields") or []) and
                         any(f.get("to") for f in nodes[mid].meta.get("fields", []))
                         for mid in model_ids)
            if has_fk and not m.get("optimised_qs"):
                add(n.id, "view.n_plus_one",
                    ", ".join(nodes[i].label for i in model_ids[:3]))

        elif n.kind == "task":
            o = m.get("task_opts") or {}
            if not o.get("max_retries") and not o.get("autoretry_for"):
                add(n.id, "task.no_retry")
            if not any(e.kind in ("calls", "schedules") for e in in_edges.get(n.id, [])):
                add(n.id, "task.orphan")
            if m.get("uses_self_retry") and not o.get("bind"):
                add(n.id, "task.no_bind_retry")

        elif n.kind == "signal":
            if not m.get("sender"):
                add(n.id, "signal.no_sender", m.get("signal", ""))

    # --- app dependency cycles ------------------------------------------- #
    cycles = _find_cycles(proj)
    for cyc in cycles:
        issues.append({"node": None, "code": "app.cycle", "severity": "error",
                       "message": CHECKS["app.cycle"][1], "detail": " → ".join(cyc + [cyc[0]]),
                       "apps": cyc})

    by_node: dict[str, list] = {}
    for i in issues:
        if i["node"]:
            by_node.setdefault(i["node"], []).append(i)

    counts = {"error": 0, "warn": 0, "info": 0}
    for i in issues:
        counts[i["severity"]] += 1

    per_app: dict[str, dict] = {}
    for i in issues:
        app = nodes[i["node"]].app if i["node"] else "(project)"
        d = per_app.setdefault(app, {"error": 0, "warn": 0, "info": 0, "total": 0})
        d[i["severity"]] += 1
        d["total"] += 1

    return {
        "issues": issues,
        "by_node": by_node,
        "counts": counts,
        "per_app": per_app,
        "cycles": cycles,
        "checks": {k: {"severity": v[0], "message": v[1]} for k, v in CHECKS.items()},
        "score": _score(counts, len(proj.nodes)),
    }


def _find_cycles(proj: Project) -> list[list[str]]:
    """Shortest dependency cycles between apps (DFS)."""
    g: dict[str, set] = {}
    for d in proj.app_deps:
        g.setdefault(d["source"], set()).add(d["target"])

    found: list[list[str]] = []
    seen_sets: set[frozenset] = set()

    def dfs(start, node, path, visited):
        for nxt in g.get(node, ()):
            if nxt == start and len(path) > 1:
                key = frozenset(path)
                if key not in seen_sets:
                    seen_sets.add(key)
                    found.append(list(path))
            elif nxt not in visited and len(path) < 6:
                dfs(start, nxt, path + [nxt], visited | {nxt})

    for a in sorted(g):
        dfs(a, a, [a], {a})
    return sorted(found, key=len)[:12]


def _score(counts: dict, n_nodes: int) -> int:
    if not n_nodes:
        return 100
    penalty = counts["error"] * 3 + counts["warn"] * 1 + counts["info"] * 0.25
    # cap at ~3 penalty points per node so the score does not saturate
    return max(0, min(100, round(100 - penalty / (max(n_nodes, 1) * 3) * 100)))
