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
   | `api_base` | Full backend URL the frontend calls, e.g. `https://yourdomain.com:5000` |
   | `completion_code` | Prolific completion code shown at the end |
   | `domain_name` | Your domain (used to derive cert paths if not given explicitly) |
   | `port` | Backend port (default 5000) |
   | `ssl_certfile` / `ssl_keyfile` | Absolute paths to your Let's Encrypt cert + key |

2. **`OPENROUTER_API_KEY`** (secret) — set as an **environment variable**, never in
   `config.json` (the browser can read that file). Export it before launching:
   ```bash
   export OPENROUTER_API_KEY="sk-or-..."
   ```

> Deploy `config.json` alongside `survey.py` on the backend host **and** alongside
> `llmSurvey.html` on the static host (same values; each side reads what it needs).

### Other things you may want to edit (in the source, not placeholders)
- **`MODELS`** in `survey.py` — the per-topic, per-pole model roster, selected from
  the ideology scores and pinned to OpenRouter slugs. **Re-verify the slugs** against
  the live catalog before launch.
- **`CONDITION_WEIGHTS`** — arm allocation ratios (default 1/3 · 1/3 · 1/6 · 1/6).
- **System prompts** — `NEUTRAL_PROMPTS` and `POLE_POSITIONS` in `survey.py`.
- Consent / instrument questions / debrief text in `llmSurvey.html`.

---

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
gunicorn -w 4 \
  --certfile=/etc/letsencrypt/live/mydomain.com/fullchain.pem \
  --keyfile=/etc/letsencrypt/live/mydomain.com/privkey.pem \
  -b 0.0.0.0:5000 survey:app
```

> Note: `survey.py` uses file locks (`flock`) for the shared balancing state, so
> the multi-worker (`-w 4`) Gunicorn setup is safe.

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

1. Fill in `config.json` (domain, `api_base`, ports, cert paths, completion code).
2. `export OPENROUTER_API_KEY="sk-or-..."` on the backend host.
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
