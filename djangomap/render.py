from __future__ import annotations

import json
import os

HERE = os.path.dirname(__file__)
TEMPLATE = os.path.join(HERE, "template.html")
TAILWIND = os.path.join(HERE, "tailwind.css")

CDN = ('/* Tailwind build missing - falling back to CDN (needs internet) */\n'
       '@import url("https://cdn.jsdelivr.net/npm/tailwindcss@3.4.17/dist/tailwind.min.css");')


def render_html(project, out_path: str, title: str | None = None) -> str:
    data = project.to_dict() if hasattr(project, "to_dict") else project
    with open(TEMPLATE, encoding="utf-8") as fh:
        html = fh.read()

    css = CDN
    if os.path.exists(TAILWIND):
        with open(TAILWIND, encoding="utf-8") as fh:
            css = fh.read()

    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    html = (html
            .replace("__TAILWIND__", css)
            .replace("__TITLE__", title or data["name"])
            .replace("__ROOT__", data["root"])
            .replace("__DATA__", payload))

    d = os.path.dirname(os.path.abspath(out_path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path
