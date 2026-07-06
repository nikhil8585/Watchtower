"""
app/__init__.py
===============
Watchtower application package.

Exposes the application version at the package level for convenience.
No circular imports — this file does not import from any sibling modules.
"""

from app.constants import APP_NAME, APP_VERSION

__version__: str = APP_VERSION
__all__ = ["APP_NAME", "APP_VERSION", "__version__"]
