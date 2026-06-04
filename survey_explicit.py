"""Entry point for the EXPLICIT (partisan-prompt) version of the study.

Conservative/liberal arms are explicitly prompted to argue their pole.
Data is written to results_explicit/. Run with:

    gunicorn -w 4 --timeout 120 --certfile=... --keyfile=... -b 0.0.0.0:5000 survey_explicit:app
"""

import os

os.environ["SURVEY_PROMPT_MODE"] = "explicit"

from survey import app  # noqa: E402,F401  (import after setting the mode)
