# CLAUDE.md — LLM Persuasion Survey Platform (persuasion-v2)

Project state for a new session. **Active branch: `persuasion-v2-protocol`** (not merged to main; no PR). The code on that branch is the source of truth. (Sensitive deployment details — server IP/SSH, keys — are in the assistant's local project memory, not committed here.)

## What this is
A between-conditions survey measuring whether short LLM conversations shift US participants' political attitudes. Adapts the original platform (arXiv 2505.04171) to the "ideologically selected models" protocol. Within-subjects: each participant does all 4 topics (Gun control, Immigration, Police, Taxes); each topic is independently assigned an arm, server-side balanced 1/3 conservative · 1/3 liberal · 1/6 neutral · 1/6 control.

## Files
- `survey.py` — shared Flask backend (all logic). Reads `config.json`. Endpoints: `/assign` (single-blind balanced assignment), `/chat`, `/start`, `/end`, `/stance` (post only), `/checks` (per-topic §7.6), `/survey` (instrument), `/complete`.
- `survey_explicit.py` / `survey_base.py` — entry points: set `SURVEY_PROMPT_MODE` then `from survey import app`. Run as `gunicorn survey_explicit:app` / `survey_base:app`.
- `llmSurvey.html` — single-file frontend (served static by Apache).
- `config.json` — backend-only (NEVER web-serve): `openrouter_api_key`, `completion_code`, `domain_name`, `port`, ssl paths. Key may also come from `OPENROUTER_API_KEY` env (overrides).
- `test_models.py` — pings every roster model endpoint (PASS/empty/FAIL).
- `README.md` — deployment + two-version explanation.

## Two versions (shared code; `SURVEY_PROMPT_MODE` = explicit|base)
- **explicit**: conservative/liberal arms get a GENTLE partisan prompt (lean + "argue gently, don't pressure"); the specific item is injected and the model is told its own view + to answer directly.
- **base**: conservative/liberal arms get only "discuss naturally, stay on topic" (no steering — natural behavior).
- **neutral** arm (both): balanced Appendix-A "present both sides".
- All prompts append LENGTH (≤100–150 words) + FOLLOWUP (end by offering to help, don't probe the participant's opinion).
- Separate data + balancing per version: `results_explicit/`, `results_base/` (each has its own `_assignments.json`).
- Frontend picks backend by `?mode=` (explicit→:5000, base→:5001).

## Flow
Consent → 18-page instrument (ONE question/page; generic validator; "None of these" exclusivity on media/networks; ZIP = 5 digits) → per topic: chat arms = 3-min timed chat (CHAT_SECONDS=180, no message minimum) then ONE-item post-stance (Support/Oppose); control = single direct stance → after all topics: per-CHAT-topic §7.6 checks (perceived_neutrality, self_reported_persuasion, post_trust) → two general post items (perceived_neutrality_post_general, trust_post_general) → completion code (from `/complete`).

## Data format (per participant: `results_<mode>/<pid>.json`)
`prolificPID, created, prompt_mode, completed, assignment{order, topics{topic:{condition,lean,model,model_name,chat}}}, instrument{...incl perceived_neutrality_pre, trust_pre, party_id, ideology...}, topics{topic:{post{responses{item_idx:Support/Oppose}, item_index}, checks{...}, agrees_with_llm, llm_advocated_stance, transcript[], timing}}, wrapup{perceived_neutrality_post_general, trust_post_general}, canary_triggered`.
- `agrees_with_llm` (1/0) + `llm_advocated_stance` set only for chat conservative/liberal arms.
- CONSERVATIVE_STANCE map (which Support/Oppose is the conservative side): gun {0:Support,1:Oppose}, immigration {0:Support,1:Support}, police {0:Oppose,1:Oppose}, taxes {0:Oppose,1:Oppose}.
- Real Prolific IDs match `^[A-Za-z0-9]{24}$`; "completed fully" = `completed` present. Test IDs (1245, armtest1, chk, …) and the `_assignments.json` file must be excluded.

## Model roster (4 per pole; in survey.py MODELS)
Selected from the user's PCA-distance top-10s, mapped to OpenRouter slugs (re-verify before launch). Subs: Grok 3/4→grok-4.3; DeepSeek Chat V3.2→deepseek-chat-v3.1. Dropped: GPT-OSS 20B (refuses), Mistral Nemo (answer-order bias), Phi 3.5 MoE/Falcon 3/Calme 3.3 (not on OpenRouter). Neutral arm = Claude 4.5 Sonnet (in no A/B pole). Reasoning disabled per call with a fallback for mandatory-reasoning models (e.g. GPT-OSS); empty/None choices retried. Canary word "tapestry" detects pasted external-LLM use.

## Dev / test
- Local preview: serve the dir + run survey.py with a stubbed `client.chat.completions.create`; drive via the preview browser tools.
- `test_models.py` (needs the key) checks all endpoints.
- Force a test arm sequence without polluting balancing: insert a crafted `{order,topics}` into `results_<mode>/_assignments.json` (counts untouched), open `?PROLIFIC_PID=<pid>` (+`&mode=base` for base). Run per mode separately (explicit/base stores are independent). e.g. `armtest1` = neutral→conservative→liberal→control.

## Pulling & analyzing pilot data
Read-only over SSH: `ssh root@<server> 'cd /root/LLM-Survey-Platform && tar czf - results_base results_explicit' > pilot.tgz`, extract, then load all non-`.lock` JSONs, filter to 24-char Prolific IDs with `completed`. Key outcomes: conservative-coded post stance by arm (between-subjects shift), `agrees_with_llm` by pole/version, per-conversation checks, pre→post general beliefs, canary flags. Pilot N≈50/version → descriptive only.

## Harness constraints
The auto-mode classifier BLOCKS: editing the shared Apache config, systemd, killing/creating processes on the prod server, creating live assignments. The USER runs all server write/restart/kill commands (provide exact commands). Read-only SSH and external curl to public endpoints are allowed.

## Deployment (high level; specifics in private memory)
Apache serves the frontend on 443. Two gunicorn services run the API on ports 5000 (explicit) and 5001 (base) with `--worker-class gthread -w 4 --threads 16 --timeout 120` (THREADED — sync workers ignore `--threads` and a slow /chat starves /assign) and the Let's Encrypt certs, kept alive in a `tmux` session. Frontend served from a `/persuasion/` path. Prolific URLs differ only by `&mode=base`; placeholder must be exactly `{{%PROLIFIC_PID%}}`. NOTE: serving the API on non-standard ports 5000/5001 risks some participants' networks blocking it — a 443 reverse-proxy is the robust pre-launch fix.

## Open items
- Pre-launch: re-verify model slugs; move API behind 443 (reverse proxy) to avoid port-blocks; reset `_assignments.json` + clear test result files; open a PR.
- Pilot done (see analysis): explicit version shows a persuasion signal (esp. conservative arm), base version little/none; base interaction raised general LLM trust/neutrality while explicit did not.
- Deferred: capture STUDY_ID/SESSION_ID; per-version completion codes; "fix model, vary side" mechanism arm.
