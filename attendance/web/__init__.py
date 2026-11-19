"""Dashboard: a hand-rolled ASGI app served by uvicorn.

No web framework on purpose - the attendance service depends only on the
standard library, Jinja2 and uvicorn, so it installs and runs anywhere.
"""
from .app import create_app

__all__ = ["create_app"]
