"""djangomap - Django project structure -> beautiful interactive HTML diagram."""
from .scanner import scan_project
from .render import render_html

__version__ = "0.1.0"
__all__ = ["scan_project", "render_html"]
