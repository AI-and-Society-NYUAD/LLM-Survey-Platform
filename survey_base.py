"""Entry point for the BASE (natural-behavior) version of the study.

Conservative/liberal arms are NOT told to argue a side — only to stay on topic —
so this measures the models' natural behavior. (The neutral arm still uses the
balanced 'present both sides' prompt.) Data is written to results_base/. Run with:

    gunicorn -w 4 --certfile=... --keyfile=... -b 0.0.0.0:5001 survey_base:app
"""

import os

os.environ["SURVEY_PROMPT_MODE"] = "base"

from survey import app  # noqa: E402,F401  (import after setting the mode)
