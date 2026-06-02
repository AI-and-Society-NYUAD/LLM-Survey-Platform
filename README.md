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

## Backend: `survey.py`

### Configuration (top of the file)
- **`OPENROUTER_API_KEY`** — read from the environment variable of the same name
  (falls back to a placeholder). Export it before launching:
  ```bash
  export OPENROUTER_API_KEY="sk-or-..."
  ```
- **`DOMAIN_NAME`** — your domain, used for the Let's Encrypt cert paths.
- **`MODELS`** — the per-topic, per-pole model roster (protocol §6). The protocol
  model names are kept as comments; **verify and edit the OpenRouter slugs** on the
  right-hand side against the live OpenRouter catalog before launch — several
  models are uncommon and may use different slugs or be unavailable.
- **`CONDITION_WEIGHTS`** — allocation ratios (default 1/3 · 1/3 · 1/6 · 1/6).
- **System prompts** — `NEUTRAL_PROMPTS` (Appendix A, verbatim) and
  `POLE_POSITIONS` (one-line stance per topic used to build the persuasive prompts).

### Endpoints
`/assign` (balanced per-topic assignment, single-blind view to the client),
`/chat`, `/start`, `/end` (timing), `/stance` (pre/post), `/checks` (§7.6),
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

Host on any static web server (Apache, Nginx, ...). Things to configure in the
`<script>` block near the bottom:

- **`API_BASE`** — replace `https://XXXXXX.com:5000` with your backend URL.
- **`CHAT_SECONDS`** — conversation length (default 420 = 7 min).
- **`MIN_TURNS`** — minimum participant messages before "Continue" unlocks (default 4).
- **`POST_ONLY`** — set `true` to skip the pre-stance for chat arms (post-only mode).

Other editable content: the consent form, the pre-treatment instrument
questions, the completion code, and the debrief text on the completion screen.

---

## Quick start

1. `export OPENROUTER_API_KEY=...` and edit `DOMAIN_NAME` + verify `MODELS` slugs in `survey.py`.
2. Set `API_BASE` (and any toggles) in `llmSurvey.html`.
3. `pip install flask flask_cors openai gunicorn`
4. Obtain certificates with Certbot.
5. Run the backend with Gunicorn (command above).
6. Host `llmSurvey.html` and point your DNS at the server.
7. Visit `https://mydomain.com/llmSurvey.html?PROLIFIC_PID=test` and walk the flow.

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
