"""
Static (AST-based) scanner for Django projects.

Nothing from the target project is imported - it is parsed as an AST only,
so the project's own dependencies do not need to be installed.
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field, asdict
from typing import Any

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "env", ".env", ".tox", ".mypy_cache", ".pytest_cache", "migrations",
    "static", "media", "dist", "build", ".idea", ".vscode",
}

MODEL_BASES = {"Model", "models.Model", "AbstractUser", "AbstractBaseUser",
               "TimeStampedModel", "PolymorphicModel"}
VIEW_HINTS = ("View", "ViewSet", "APIView")
FIELD_HINT = ("Field", "ForeignKey", "OneToOneField", "ManyToManyField",
              "CharField", "TextField", "IntegerField", "DateTimeField")
REL_FIELDS = {"ForeignKey", "OneToOneField", "ManyToManyField"}
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
REL_KIND = {"ForeignKey": "fk", "ManyToManyField": "m2m", "OneToOneField": "o2o"}

# kinds that participate in the high level flow chart
FLOW_LAYERS = ["url", "middleware", "view", "serializer", "form",
               "model", "signal", "task", "beat"]


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    id: str
    label: str
    kind: str
    app: str = ""
    file: str = ""
    line: int = 0
    endline: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    target: str
    kind: str = "ref"
    label: str = ""


@dataclass
class Project:
    name: str
    root: str
    apps: list = field(default_factory=list)
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    app_deps: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "root": self.root,
            "apps": self.apps,
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
            "settings": self.settings,
            "app_deps": self.app_deps,
            "app_stats": self.app_stats(),
            "stats": self.stats(),
        }

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.nodes:
            out[n.kind] = out.get(n.kind, 0) + 1
        out["apps"] = len(self.apps)
        out["edges"] = len(self.edges)
        return out

    def app_stats(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for n in self.nodes:
            d = out.setdefault(n.app, {"total": 0, "loc": 0})
            d[n.kind] = d.get(n.kind, 0) + 1
            d["total"] += 1
            if n.endline and n.line:
                d["loc"] += max(0, n.endline - n.line + 1)
        return out


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _src(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:                                    # pragma: no cover
        return "?"


def _const(node: ast.AST):
    return node.value if isinstance(node, ast.Constant) else None


def _decorators(fn: ast.AST) -> list[str]:
    return [_src(d) for d in getattr(fn, "decorator_list", [])]


def _is_task(decorators: list[str]) -> bool:
    return any(
        ("task" in d and ("shared_task" in d or "app.task" in d
                          or d.endswith(".task") or "celery" in d.lower()
                          or d == "task"))
        for d in decorators
    )


def _docstring(node) -> str:
    d = ast.get_docstring(node) or ""
    return " ".join(d.strip().split())[:300]


def _end(node) -> int:
    return getattr(node, "end_lineno", node.lineno) or node.lineno


def _kwargs(call: ast.Call) -> dict:
    out = {}
    for kw in call.keywords:
        if kw.arg:
            v = _const(kw.value)
            out[kw.arg] = v if v is not None else _src(kw.value)
    return out


def _list_of_str(node) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [str(_const(e) if _const(e) is not None else _src(e)) for e in node.elts]
    return []


def _class_attr(cls: ast.ClassDef, name: str):
    for st in cls.body:
        if isinstance(st, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in st.targets):
            v = _const(st.value)
            return v if v is not None else _src(st.value)
    return None


def _complexity(node) -> int:
    """Approximate cyclomatic complexity."""
    c = 1
    for sub in ast.walk(node):
        if isinstance(sub, (ast.If, ast.For, ast.AsyncFor, ast.While,
                            ast.ExceptHandler, ast.With, ast.AsyncWith, ast.Assert)):
            c += 1
        elif isinstance(sub, ast.BoolOp):
            c += len(sub.values) - 1
        elif isinstance(sub, (ast.IfExp,)):
            c += 1
        elif isinstance(sub, ast.comprehension):
            c += 1 + len(sub.ifs)
        elif isinstance(sub, getattr(ast, "Match", ())):
            c += 1
    return c


def _uses(node, *names) -> bool:
    src = _src(node)
    return any(n in src for n in names)


def _url_params(route: str) -> list[str]:
    out, i = [], 0
    while True:
        a = route.find("<", i)
        if a < 0:
            break
        b = route.find(">", a)
        if b < 0:
            break
        out.append(route[a + 1:b])
        i = b + 1
    return out


# --------------------------------------------------------------------------- #
# per-file visitor
# --------------------------------------------------------------------------- #
class FileScanner:
    def __init__(self, project: Project, path: str, app: str, rel: str):
        self.p = project
        self.path = path
        self.app = app
        self.rel = rel
        self.fname = os.path.basename(path)
        self.imports: dict[str, str] = {}     # local name -> module

    def run(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            tree = ast.parse(text, filename=self.path)
        except (SyntaxError, ValueError):
            return
        self.lines = text.count("\n") + 1

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    self.imports[a.asname or a.name] = node.module
                self._app_dep(node.module)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    self.imports[a.asname or a.name] = a.name
                    self._app_dep(a.name)

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._class(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._func(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                self._assign(node)

        if self.fname == "urls.py":
            self._urls(tree)
        if self.fname == "admin.py":
            self._admin_register(tree)
        if "management" in self.rel.split(os.sep) and self.fname not in ("__init__.py",):
            self._command(tree)

    # ------------------------------------------------------------------ #
    def _app_dep(self, module: str) -> None:
        top = module.split(".")[0]
        if top and top != self.app:
            self.p.app_deps.append({"source": self.app, "target": top})

    def _add(self, nid, label, kind, node, meta=None) -> str:
        self.p.nodes.append(Node(id=nid, label=label, kind=kind, app=self.app,
                                 file=self.rel, line=getattr(node, "lineno", 0),
                                 endline=_end(node) if hasattr(node, "lineno") else 0,
                                 meta=meta or {}))
        return nid

    # ------------------------------------------------------------------ #
    def _class(self, cls: ast.ClassDef) -> None:
        bases = [_src(b) for b in cls.bases]
        nid = f"{self.app}.{cls.name}"
        blob = " ".join(bases)

        kind = None
        if self.fname.startswith("models") or any(b.split(".")[-1] in MODEL_BASES for b in bases):
            kind = "model"
        elif "Serializer" in blob or self.fname.startswith("serializers"):
            kind = "serializer"
        elif "Middleware" in cls.name or self.fname.startswith("middleware"):
            kind = "middleware"
        elif any(h in blob for h in VIEW_HINTS) or self.fname.startswith("views"):
            kind = "view"
        elif "Form" in blob or self.fname.startswith("forms"):
            kind = "form"
        elif "ModelAdmin" in blob or self.fname.startswith("admin"):
            kind = "admin"
        if kind is None:
            return

        methods = [f for f in cls.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
        meta: dict[str, Any] = {
            "bases": bases,
            "doc": _docstring(cls),
            "decorators": _decorators(cls),
            "n_methods": len(methods),
            "complexity": max([_complexity(m) for m in methods], default=1),
            "all_methods": [m.name for m in methods],
        }

        if kind == "model":
            meta["fields"] = self._model_fields(cls, nid)
            meta["meta_opts"] = self._model_meta(cls)
            meta["methods"] = [m.name for m in methods if not m.name.startswith("__")][:20]
            meta["properties"] = [m.name for m in methods
                                  if any("property" in d for d in _decorators(m))]
            meta["managers"] = [t.id for st in cls.body
                                if isinstance(st, ast.Assign)
                                and isinstance(st.value, ast.Call)
                                and "Manager" in _src(st.value.func)
                                for t in st.targets if isinstance(t, ast.Name)]
        else:
            meta["methods"] = [m.name for m in methods if not m.name.startswith("_")][:20]

        if kind == "view":
            meta["http"] = [m.name for m in methods if m.name in HTTP_METHODS]
            meta["fbv"] = False
            meta["optimised_qs"] = _uses(cls, "select_related", "prefetch_related", "only(", "defer(")
            for attr in ("model", "queryset", "serializer_class", "template_name",
                         "form_class", "permission_classes", "authentication_classes",
                         "paginate_by", "lookup_field", "filter_backends"):
                v = _class_attr(cls, attr)
                if v is not None:
                    meta[attr] = v
            for attr in ("model", "queryset", "serializer_class", "form_class"):
                v = meta.get(attr)
                if isinstance(v, str) and v:
                    tgt = v.split(".")[0].replace("'", "").replace('"', "")
                    self.p.edges.append(Edge(nid, f"?{tgt}", "uses", attr))
        if kind == "serializer":
            for attr in ("model", "fields", "read_only_fields"):
                v = _class_attr(cls, attr)
                if v is None:
                    for st in cls.body:                          # class Meta:
                        if isinstance(st, ast.ClassDef) and st.name == "Meta":
                            v = _class_attr(st, attr)
                            break
                if v is not None:
                    meta[attr] = v
            if isinstance(meta.get("model"), str):
                self.p.edges.append(Edge(nid, f"?{meta['model'].split('.')[-1]}", "uses", "model"))
        if kind == "admin":
            for attr in ("list_display", "list_filter", "search_fields",
                         "readonly_fields", "raw_id_fields", "inlines"):
                v = _class_attr(cls, attr)
                if v is not None:
                    meta[attr] = v

        self._add(nid, cls.name, kind, cls, meta)

        for m in methods:                      # tasks defined as methods
            if _is_task(_decorators(m)):
                self._add(f"{nid}.{m.name}", f"{cls.name}.{m.name}", "task", m,
                          {"doc": _docstring(m)})
        for b in bases:
            self.p.edges.append(Edge(nid, "?" + b.split(".")[-1], "inherits"))
        self._scan_calls(cls, nid)

    def _model_meta(self, cls: ast.ClassDef) -> dict:
        for st in cls.body:
            if isinstance(st, ast.ClassDef) and st.name == "Meta":
                out = {}
                for a in st.body:
                    if isinstance(a, ast.Assign) and isinstance(a.targets[0], ast.Name):
                        v = _const(a.value)
                        out[a.targets[0].id] = v if v is not None else _src(a.value)
                return out
        return {}

    def _model_fields(self, cls: ast.ClassDef, nid: str) -> list[dict]:
        fields: list[dict] = []
        for stmt in cls.body:
            if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                continue
            target = stmt.targets[0]
            if not isinstance(target, ast.Name):
                continue
            ftype = _src(stmt.value.func).split(".")[-1]
            if not any(h in ftype for h in FIELD_HINT) and ftype not in REL_FIELDS:
                continue
            kw = _kwargs(stmt.value)
            rel = ""
            if ftype in REL_FIELDS and stmt.value.args:
                a0 = stmt.value.args[0]
                rel = (_const(a0) or _src(a0).replace("'", "")).split(".")[-1]
                self.p.edges.append(Edge(nid, f"?{rel}", REL_KIND[ftype], target.id))
            opts = []
            for k in ("null", "blank", "unique", "db_index", "primary_key",
                      "auto_now", "auto_now_add", "editable"):
                if kw.get(k) is True:
                    opts.append(k)
            fields.append({
                "name": target.id, "type": ftype, "to": rel,
                "max_length": kw.get("max_length"),
                "default": None if kw.get("default") is None else str(kw.get("default")),
                "on_delete": (kw.get("on_delete") or "").split(".")[-1] if kw.get("on_delete") else "",
                "related_name": kw.get("related_name"),
                "choices": bool(kw.get("choices")),
                "help_text": kw.get("help_text"),
                "opts": opts,
            })
        return fields

    # ------------------------------------------------------------------ #
    def _func(self, fn) -> None:
        decs = _decorators(fn)
        nid = f"{self.app}.{fn.name}"
        args = [a.arg for a in fn.args.args]
        base = {"doc": _docstring(fn), "decorators": decs, "args": args,
                "is_async": isinstance(fn, ast.AsyncFunctionDef),
                "complexity": _complexity(fn),
                "uses_self_retry": _uses(fn, "self.retry")}

        if _is_task(decs):
            opts = {}
            for d in fn.decorator_list:
                if isinstance(d, ast.Call):
                    opts.update(_kwargs(d))
            base["task_opts"] = {k: v for k, v in opts.items()
                                 if k in ("bind", "max_retries", "queue", "rate_limit",
                                          "autoretry_for", "retry_backoff", "acks_late",
                                          "time_limit", "soft_time_limit", "name")}
            self._add(nid, fn.name, "task", fn, base)
        elif "receiver" in " ".join(decs):
            sig = ""
            for d in fn.decorator_list:
                if isinstance(d, ast.Call) and d.args:
                    sig = _src(d.args[0]).split(".")[-1]
                    kw = _kwargs(d)
                    base["sender"] = kw.get("sender")
                    if base.get("sender"):
                        self.p.edges.append(
                            Edge(nid, "?" + str(base["sender"]).split(".")[-1], "listens", sig))
            base["signal"] = sig
            self._add(nid, fn.name, "signal", fn, base)
        elif self.fname.startswith("views") or any(
                "require_" in d or "login_required" in d or "api_view" in d for d in decs):
            base["fbv"] = True
            base["optimised_qs"] = _uses(fn, "select_related", "prefetch_related", "only(", "defer(")
            for d in fn.decorator_list:
                if isinstance(d, ast.Call) and "api_view" in _src(d.func) and d.args:
                    base["http"] = [m.lower() for m in _list_of_str(d.args[0])]
            self._add(nid, fn.name, "view", fn, base)
        elif self.fname.startswith("tasks"):
            self._add(nid, fn.name, "task", fn, base)
        else:
            return
        self._scan_calls(fn, nid)

    def _scan_calls(self, scope, nid: str) -> None:
        """delay/apply_async + ORM usage inside a scope."""
        for sub in ast.walk(scope):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            if isinstance(f, ast.Attribute):
                if f.attr in ("delay", "apply_async"):
                    self.p.edges.append(
                        Edge(nid, "?" + _src(f.value).split(".")[-1], "calls", f.attr))
                elif f.attr in ("objects", "filter", "get", "create", "all",
                                "get_object_or_404", "select_related"):
                    tgt = _src(f.value).split(".")[0]
                    if tgt and tgt[:1].isupper():
                        self.p.edges.append(Edge(nid, f"?{tgt}", "queries", f.attr))
            elif isinstance(f, ast.Name) and f.id == "get_object_or_404" and sub.args:
                self.p.edges.append(
                    Edge(nid, "?" + _src(sub.args[0]).split(".")[-1], "queries", "get_or_404"))

    # ------------------------------------------------------------------ #
    def _assign(self, node) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if not isinstance(t, ast.Name):
                continue
            name = t.id
            if name == "CELERY_BEAT_SCHEDULE" and isinstance(node.value, ast.Dict):
                self._beat(node)
            elif name in ("INSTALLED_APPS", "MIDDLEWARE", "AUTHENTICATION_BACKENDS"):
                self.p.settings[name] = _list_of_str(node.value)
            elif name in ("CELERY_BROKER_URL", "BROKER_URL", "CELERY_RESULT_BACKEND",
                          "AUTH_USER_MODEL", "ROOT_URLCONF", "WSGI_APPLICATION",
                          "ASGI_APPLICATION", "TIME_ZONE", "LANGUAGE_CODE", "DEBUG"):
                v = _const(node.value)
                self.p.settings[name] = v if v is not None else _src(node.value)
            elif name == "DATABASES" and isinstance(node.value, ast.Dict):
                self.p.settings["DATABASES"] = _src(node.value)[:200]

    def _beat(self, node) -> None:
        for k, v in zip(node.value.keys, node.value.values):
            name = _const(k) or _src(k)
            info = {"task": "", "schedule": "", "args": "", "kwargs": "", "queue": ""}
            if isinstance(v, ast.Dict):
                for kk, vv in zip(v.keys, v.values):
                    key = _const(kk)
                    if key in info:
                        c = _const(vv)
                        info[key] = c if c is not None else _src(vv)
                    elif key == "options":
                        info["queue"] = _src(vv)
            nid = f"beat.{name}"
            self._add(nid, str(name), "beat", node, info)
            if info["task"]:
                self.p.edges.append(
                    Edge(nid, "?" + str(info["task"]).split(".")[-1], "schedules", "beat"))

    # ------------------------------------------------------------------ #
    def _urls(self, tree: ast.Module) -> None:
        prefixes: dict[int, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = _src(node.func).split(".")[-1]

            # DRF router registrations
            if fname == "register" and node.args:
                route = _const(node.args[0]) or ""
                handler = _src(node.args[1]) if len(node.args) > 1 else ""
                kw = _kwargs(node)
                nid = f"url:{self.app}:router:{route}:{node.lineno}"
                self._add(nid, "/" + str(route), "url", node,
                          {"handler": handler, "name": kw.get("basename", ""),
                           "router": True, "methods": ["GET", "POST", "PUT", "PATCH", "DELETE"]})
                b = handler.split("(")[0].split(".")[-1]
                if b:
                    self.p.edges.append(Edge(nid, f"?{b}", "routes", "router"))
                continue

            if fname not in ("path", "re_path", "url") or not node.args:
                continue
            route = _const(node.args[0])
            if route is None:
                continue
            handler = _src(node.args[1]) if len(node.args) > 1 else ""
            kw = _kwargs(node)
            include = "include" in handler
            nid = f"url:{self.app}:{route or '/'}:{node.lineno}"
            self._add(nid, "/" + str(route), "url", node, {
                "handler": handler,
                "name": kw.get("name", ""),
                "params": _url_params(str(route)),
                "include": include,
                "extra_kwargs": {k: v for k, v in kw.items() if k != "name"},
            })
            if include:
                continue
            b = handler.split("(")[0].replace(".as_view", "").split(".")[-1]
            if b:
                self.p.edges.append(Edge(nid, f"?{b}", "routes", kw.get("name", "")))

    def _admin_register(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _src(node.func).endswith("site.register") and node.args:
                m = _src(node.args[0]).split(".")[-1]
                a = _src(node.args[1]).split(".")[-1] if len(node.args) > 1 else ""
                if a:
                    self.p.edges.append(Edge(f"{self.app}.{a}", f"?{m}", "manages", "register"))
            if isinstance(node, ast.ClassDef):
                for d in node.decorator_list:
                    if isinstance(d, ast.Call) and "register" in _src(d.func) and d.args:
                        self.p.edges.append(Edge(f"{self.app}.{node.name}",
                                                 "?" + _src(d.args[0]).split(".")[-1],
                                                 "manages", "register"))

    def _command(self, tree: ast.Module) -> None:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "Command":
                nid = f"cmd:{self.app}:{self.fname[:-3]}"
                self._add(nid, self.fname[:-3], "command", node,
                          {"doc": _docstring(node) or str(_class_attr(node, "help") or ""),
                           "methods": [m.name for m in node.body
                                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]})
                self._scan_calls(node, nid)


# --------------------------------------------------------------------------- #
# project walk
# --------------------------------------------------------------------------- #
def _app_of(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    return parts[0] if len(parts) > 1 else "(root)"


def scan_project(root: str, name: str | None = None) -> Project:
    root = os.path.abspath(root)
    proj = Project(name=name or os.path.basename(root), root=root)
    apps: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            app = _app_of(root, full)
            apps.add(app)
            FileScanner(proj, full, app, os.path.relpath(full, root)).run()

    _resolve(proj)
    proj.apps = sorted(a for a in apps if any(n.app == a for n in proj.nodes))
    _clean_deps(proj)
    return proj


def _resolve(proj: Project) -> None:
    """Resolve temporary ?Name references to real ids; drop external ones."""
    by_label: dict[str, list[str]] = {}
    ids = {n.id for n in proj.nodes}
    for n in proj.nodes:
        by_label.setdefault(n.label, []).append(n.id)

    resolved: list[Edge] = []
    seen = set()
    for e in proj.edges:
        tgt = e.target
        if tgt.startswith("?"):
            cand = by_label.get(tgt[1:])
            if not cand:
                continue
            tgt = cand[0]
        if tgt not in ids or e.source not in ids or tgt == e.source:
            continue
        key = (e.source, tgt, e.kind, e.label)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(Edge(e.source, tgt, e.kind, e.label))
    proj.edges = resolved


def _clean_deps(proj: Project) -> None:
    apps = set(proj.apps)
    seen, out = set(), []
    for d in proj.app_deps:
        k = (d["source"], d["target"])
        if d["target"] in apps and d["source"] in apps and k not in seen and k[0] != k[1]:
            seen.add(k)
            out.append(d)
    proj.app_deps = out
