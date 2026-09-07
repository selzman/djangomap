from __future__ import annotations

import argparse
import json
import sys

from .scanner import scan_project
from .analysis import analyse
from .render import render_html
from .export import EXPORTERS, to_markdown


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="djangomap",
        description="Scan a Django project and render an interactive HTML diagram "
                    "of models, views, urls, celery tasks and signals.")
    ap.add_argument("path", nargs="?", default=".", help="project root (default: .)")
    ap.add_argument("-o", "--out", default="djangomap.html", help="output HTML file")
    ap.add_argument("--json", dest="json_out", help="also dump raw graph JSON here")
    ap.add_argument("--title", help="diagram title")
    ap.add_argument("--include-tests", action="store_true",
                    help="also scan tests/, simulation/ and test_*.py files")
    ap.add_argument("--apps-only", action="store_true",
                    help="only scan detected Django apps (skip root scripts, config)")
    ap.add_argument("--group-by", choices=["none", "prefix"], default="prefix",
                    help="group app boards by their path prefix (default: prefix)")

    g = ap.add_argument_group("source links")
    g.add_argument("--editor", default="vscode",
                   choices=["vscode", "vscode-insiders", "pycharm", "none"],
                   help="scheme used by 'open in editor' links (default: vscode)")
    g.add_argument("--repo-url", help="e.g. https://github.com/me/proj — enables "
                                      "'view on remote' links")
    g.add_argument("--branch", default="main", help="branch for --repo-url (default: main)")

    g2 = ap.add_argument_group("export")
    g2.add_argument("--format", choices=sorted(EXPORTERS), help="print a text diagram to stdout")
    g2.add_argument("--markdown", help="write a Markdown report (with mermaid ERD) here")
    g2.add_argument("--fail-on", choices=["error", "warn", "info"],
                    help="exit 1 if issues of this severity or worse exist (for CI)")
    g2.add_argument("--no-health", action="store_true", help="skip the health analysis")
    a = ap.parse_args(argv)

    proj = scan_project(a.path, name=a.title,
                        include_tests=a.include_tests, apps_only=a.apps_only)
    data = proj.to_dict()
    health = None if a.no_health else analyse(proj)
    if health:
        data["health"] = health
    data["links"] = {"editor": a.editor, "repo_url": (a.repo_url or "").rstrip("/"),
                     "branch": a.branch}
    data["group_by"] = a.group_by

    if a.format:
        print(EXPORTERS[a.format](data))
        return 0

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
    if a.markdown:
        with open(a.markdown, "w", encoding="utf-8") as fh:
            fh.write(to_markdown(data, health))
        print(f"✓ {a.markdown}")

    out = render_html(data, a.out, a.title)
    s = proj.stats()
    print(f"✓ {out}")
    print(f"  apps detected: {len(proj.apps)}")
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(s.items())))
    if health:
        c = health["counts"]
        print(f"  health={health['score']}/100  "
              f"errors={c['error']} warnings={c['warn']} info={c['info']}")
        if health["cycles"]:
            for cyc in health["cycles"]:
                print("  ⚠ cycle: " + " → ".join(cyc + [cyc[0]]))

    if a.fail_on and health:
        rank = {"error": 3, "warn": 2, "info": 1}
        worst = max((rank[i["severity"]] for i in health["issues"]), default=0)
        if worst >= rank[a.fail_on]:
            print(f"✗ failing: issues at or above '{a.fail_on}'", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
