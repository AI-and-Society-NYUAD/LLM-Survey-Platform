# LLM Persuasion Survey Platform

A between-conditions survey platform for measuring how short LLM conversations
shift participants' political attitudes. Each participant goes through four
topics (Gun control, Immigration, Police, Taxes). Each topic is independently
assigned one of **six cells** by a server-side balanced randomizer (base advocacy
over-weighted) — the conservative and liberal advocacy arms are each crossed with a **strength**
factor (explicit vs. base), so strength is randomized *within* each participant,
topic by topic:

| Cell | Treatment |
|------|-----------|
| Conservative · explicit | Ideologically-selected model, conservative prompt that argues the pole |
| Conservative · base | Same model, but only told to stay on topic (natural behavior) |
| Liberal · explicit | Ideologically-selected model, liberal prompt that argues the pole |
| Liberal · base | Same model, but only told to stay on topic (natural behavior) |
| Neutral | Claude Sonnet + a balanced/non-partisan prompt (Appendix A); strength does not apply |
| Control | No LLM; participant answers the stance items directly |

Strength only changes the *system prompt*, never the model: an advocacy cell's
model is chosen by topic + pole alone. For chat cells a participant gives a
**pre**-stance, has a timed conversation (3-minute timer; no message minimum),
gives a **post**-stance, then answers per-conversation manipulation checks.
Control topics collect a single stance.

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
- **`CELL_WEIGHTS`** / **`CELLS`** — the six per-topic cells and their allocation
  ratios. Base advocacy is over-weighted: `cons·base` and `lib·base` = 1/4 each;
  `cons·explicit`, `lib·explicit`, `neutral`, `control` = 1/8 each (sum 1).
- **System prompts** — `NEUTRAL_PROMPTS` and `POLE_POSITIONS` in `survey.py`.
- Consent / instrument questions / debrief text in `llmSurvey.html`.

---

## Strength (explicit vs. base) is a within-subject factor

Earlier versions ran two separate backends (explicit on :5000, base on :5001) and
fixed the prompt style per participant. Strength is now randomized **per topic
inside a single backend**, so one participant sees a mix of explicit and base
conversations across their four topics. There is no `SURVEY_PROMPT_MODE`, no
`?mode=` URL param, and a single results dir.

- **explicit** advocacy cells get a gentle partisan prompt that argues the pole.
- **base** advocacy cells are only told to stay on topic (natural behavior).
- **neutral** is mode-invariant (balanced Appendix-A prompt); **control** has no chat.

Run the one service (THREADED workers — a slow `/chat` must not starve `/assign`):
```bash
gunicorn --worker-class gthread -w 4 --threads 16 --timeout 120 \
  --certfile=... --keyfile=... -b 0.0.0.0:5000 survey:app
```

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
sudo gunicorn -w 4 --timeout 120 \
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

Host on any static web server (Apache, Nginx, ...). The frontend needs **no**
config file: it derives `API_BASE` from its own page URL (see `API_PORT` near the
top of the `<script>` block) and gets the completion code from `/complete`. One
behavior toggle lives near the top of the `<script>` block:

- **`CHAT_SECONDS`** — conversation length (default 180 = 3 min; the only gate to continue).

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
