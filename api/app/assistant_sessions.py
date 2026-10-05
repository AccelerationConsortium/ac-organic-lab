"""Moved to ``lab_assistant.sessions`` (the ``assistant/`` workspace package).

Kept for one release as an alias: this name *is* that module, so existing
imports and monkeypatches keep working unchanged. It also registers the
dashboard's ``lab.db`` resolver, which the package needs to place the
sessions store beside it without importing the dashboard.
"""

import sys

from lab_assistant import sessions as _module

from .db import resolve_db_path

_module.set_lab_db_path_resolver(resolve_db_path)
sys.modules[__name__] = _module
