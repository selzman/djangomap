# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-09-06

First public release.

### Added

**Scanner (static AST analysis)**
- Detects models, views, URLs, Celery tasks, Beat schedules, signals, middleware,
  management commands, forms, admin classes and serializers
- Extracts model fields with `on_delete`, `related_name`, `max_length`,
  `null`/`blank`/`unique`/`db_index`, `Meta` options, properties and managers
- Extracts view configuration: `model`, `queryset`, `serializer_class`,
  `template_name`, `permission_classes`, `paginate_by`, HTTP methods
- Parses `path()`/`re_path()`/`include()`, path converters and DRF router registrations
- Reads Celery task options (`bind`, `max_retries`, `queue`, `rate_limit`, …) and
  follows the `delay()` / `apply_async()` call chain
- Reads `INSTALLED_APPS`, `MIDDLEWARE`, `AUTH_USER_MODEL`, broker and result backend
- Computes cyclomatic complexity per class and function

**HTML output**
- **Apps tab** — one board per app with cards grouped by kind; relation wires routed
  through the gutters between boards so they never cross cards
- **Flow tab** — four architecture views: Request Lifecycle, App Dependencies,
  Celery Pipeline and a full ERD
- **Health tab** — 0–100 score with 16 checks, grouped issue list and per-app breakdown
- Detail panel with fields, methods, complexity, issues and clickable relations
- Source links: `open in editor` (VS Code / PyCharm) and `view on remote` (GitHub/GitLab)
- Permalinks — tab, flow view, selection and filters encoded in the URL hash
- Keyboard shortcuts: `1`/`2`/`3` switch tabs, `F` fits, `Esc` clears
- Fully responsive down to 320px, with an off-canvas drawer, bottom-sheet detail
  panel, vertically stacked flow layers and pinch-to-zoom
- Single self-contained file — Tailwind pre-compiled and inlined, zero network requests

**Exports and CI**
- `--format mermaid-erd` / `mermaid-flow` / `dot`
- `--markdown` report with an embedded Mermaid ERD
- `--fail-on error|warn|info` for CI pipelines
- `--json` raw graph dump

### Notes
- Zero runtime dependencies — Python standard library only
- Your code is never imported or executed, only parsed as an AST

[Unreleased]: https://github.com/acme/djangomap/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/acme/djangomap/releases/tag/v0.1.0
