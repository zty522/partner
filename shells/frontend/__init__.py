"""Production frontend shells.

Only ``qq_bot`` remains as a production frontend.  The desktop GUI
and textual TUI have been removed (2026-09-17) per
``docs/operations/gui_tui_retirement_checklist.md``.

The Web workbench is served from ``partner/web/app.py`` (Python
Flask-optional HTTP API) and ``partner/web/frontend_src/`` (React
+ TypeScript SPA).
"""
