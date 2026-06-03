# LLM Persuasion Survey Platform

A between-conditions survey platform for measuring how short LLM conversations
shift participants' political attitudes. Each participant goes through four
topics (Gun control, Immigration, Police, Taxes). Each topic is independently
assigned one of four conditions by a server-side balanced randomizer:

| Condition | Treatment |
|-----------|-----------|
| Conservative | An ideologically-selected model + a conservative system prompt for that topic |
| Liberal | An ideologically-selected model + a liberal system prompt for that topic |
| Neutral | Claude Sonnet + a balanced/non-partisan system prompt (protocol Appendix A) |
| Control | No LLM; participant answers the stance items directly |

For chat conditions a participant gives a **pre**-stance, has a timed
conversation (min turns enforced), gives a **post**-stance, then answers
per-conversation manipulation checks. Control topics collect a single stance.

This repository has two components:

1. **`survey.py`** — Flask backend. Serves all models through one **OpenRouter**
   (OpenAI-compatible) gateway and stores one JSON file per participant under `results/`.
2. **`llmSurvey.html`** — single-file frontend, served from any static web server.

---

## Configuration — what you need to fill in

There are exactly **two** things to set for a deployment:

1. **`config.json`** (non-secret) — read by *both* `survey.py` (from disk) and
   `llmSurvey.html` (fetched by the browser at load). Fill in:

   | Key | What it is |
   |-----|------------|
   | `openrouter_api_key` | Your OpenRouter key (secret; or set the `OPENROUTER_API_KEY` env var instead) |
   | `completion_code` | Prolific completion code, returned by `/complete` at the end |
   | `domain_name` | Your domain (used to derive cert paths if not given explicitly) |
   | `port` | Port for a direct `python survey.py` run (Gunicorn uses `-b` instead) |
   | `ssl_certfile` / `ssl_keyfile` | Absolute paths to your Let's Encrypt cert + key |

   (The frontend gets `api_base` from its own page URL — see `API_PORT` in
   `llmSurvey.html` — so there's no `api_base` in `config.json`.)

2. **`OPENROUTER_API_KEY`** (secret) — put it in `config.json` as `openrouter_api_key`,
   **or** set it as an environment variable (the env var overrides the file):
   ```bash
   export OPENROUTER_API_KEY="sk-or-..."   # optional; overrides config.json
   ```

> `config.json` is **backend-only**: it lives next to `survey.py`, is never fetched
> by the browser, and now holds the API key. **Never** place it in the web root and
> **never** commit a real key to git. The frontend gets `api_base` from its own URL
> and the completion code from `/complete`, so it needs no config file.

### Other things you may want to edit (in the source, not placeholders)
- **`MODELS`** in `survey.py` — the per-topic, per-pole model roster, selected from
  the ideology scores and pinned to OpenRouter slugs. **Re-verify the slugs** against
  the live catalog before launch.
- **`CONDITION_WEIGHTS`** — arm allocation ratios (default 1/3 · 1/3 · 1/6 · 1/6).
- **System prompts** — `NEUTRAL_PROMPTS` and `POLE_POSITIONS` in `survey.py`.
- Consent / instrument questions / debrief text in `llmSurvey.html`.

---

## Two versions: explicit vs. base

The platform ships two prompt variants that share all code (`survey.py` is the
core; the entry points just set the mode and write to separate results dirs):

| Entry point | Conservative/liberal arms | Neutral arm | Data dir |
|-------------|---------------------------|-------------|----------|
| `survey_explicit:app` | explicitly prompted to argue the pole | balanced (Appendix A) | `results_explicit/` |
| `survey_base:app` | only told to stay on topic (natural behavior) | balanced (Appendix A) | `results_base/` |

Both use the identical model roster and arm assignment; only the conservative/
liberal system prompt differs. Run each as its own service (different port), e.g.:
```bash
gunicorn -w 4 --certfile=... --keyfile=... -b 0.0.0.0:5000 survey_explicit:app
gunicorn -w 4 --certfile=... --keyfile=... -b 0.0.0.0:5001 survey_base:app
```
(`SURVEY_PROMPT_MODE=explicit|base` is set by the entry points; `survey:app`
directly defaults to explicit.)

## Backend: `survey.py`

### Endpoints
`/assign` (balanced per-topic assignment, single-blind view to the client),
`/chat`, `/start`, `/end` (timing), `/stance` (post-treatment stance),
`/checks` (per-conversation §7.6 checks, chat arms only),
`/survey` (pre-treatment instrument), `/complete`.

### Data output
One file per participant: `results/<prolificPID>.json`, containing the full
instrument, per-topic assignment (condition / lean / model — kept server-side),
pre/post stances, manipulation checks, timing, and the full transcript.
Balancing state lives in `results/_assignments.json`.

### Dependencies
```bash
pip install flask flask_cors openai gunicorn
```

### Running with Gunicorn + HTTPS
```bash
sudo gunicorn -w 4 \
  --certfile=/etc/letsencrypt/live/mydomain.com/fullchain.pem \
  --keyfile=/etc/letsencrypt/live/mydomain.com/privkey.pem \
  -b 0.0.0.0:443 survey:app
```

> Notes: binding port 443 needs elevated privileges (hence `sudo`, or grant the
> capability another way). `survey.py` uses file locks (`flock`) for the shared
> balancing state, so the multi-worker (`-w 4`) Gunicorn setup is safe.

### Certificates (Let's Encrypt)
```bash
sudo apt install certbot
sudo certbot certonly --standalone -d mydomain.com -d www.mydomain.com
```
Ensure your DNS A record points `mydomain.com` to your server's IP.

---

## Frontend: `llmSurvey.html`

Host on any static web server (Apache, Nginx, ...), with `config.json` in the same
directory. `API_BASE` and the completion code come from `config.json` at load time.
Two behavior toggles remain near the top of the `<script>` block:

- **`CHAT_SECONDS`** — conversation length (default 300 = 5 min).
- **`MIN_TURNS`** — minimum participant messages before "Continue" unlocks (default 4).

---

## Quick start

1. Fill in `config.json` (OpenRouter key, domain, port, cert paths, completion code).
2. (Optional) `export OPENROUTER_API_KEY="sk-or-..."` to override the key in `config.json`.
3. Verify the `MODELS` slugs in `survey.py` against the live OpenRouter catalog.
4. `pip install flask flask_cors openai gunicorn`
5. Obtain certificates with Certbot.
6. Run the backend with Gunicorn (command above).
7. Host `llmSurvey.html` **and** `config.json` together, and point your DNS at the server.
8. Visit `https://yourdomain.com/llmSurvey.html?PROLIFIC_PID=test` and walk the flow.

---

## Citation
```
@misc{aldahoul_2025_LLM,
  title={Large Language Models are often politically extreme, usually ideologically inconsistent, and persuasive even in informational contexts},
  author={Nouar Aldahoul and Hazem Ibrahim and Matteo Varvello and Aaron Kaufman and Talal Rahwan and Yasir Zaki},
  year={2025},
  eprint={2505.04171},
  archivePrefix={arXiv},
  primaryClass={cs.CY},
  url={https://arxiv.org/abs/2505.04171}
}
```
