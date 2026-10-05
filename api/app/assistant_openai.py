"""Moved to ``lab_assistant.openai_backend`` (the ``assistant/`` workspace package).

Kept for one release as an alias: this name *is* that module, so existing
imports, ``mock.patch("app.assistant_openai....")`` targets and monkeypatches keep
working unchanged. Import ``lab_assistant.openai_backend`` in new code.
"""

import sys

from lab_assistant import openai_backend as _module

sys.modules[__name__] = _module
