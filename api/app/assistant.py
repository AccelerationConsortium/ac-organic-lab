"""Moved to ``lab_assistant.engine`` (the ``assistant/`` workspace package).

Kept for one release as an alias: this name *is* that module, so existing
imports, ``mock.patch("app.assistant....")`` targets and monkeypatches keep
working unchanged. Import ``lab_assistant.engine`` in new code.
"""

import sys

from lab_assistant import engine as _module

sys.modules[__name__] = _module
