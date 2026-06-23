# CLAUDE.md — LLM Persuasion Survey Platform (persuasion-v2)

Project state for a new session. **Active branch: `persuasion-v2-protocol`** (not merged to main; no PR). The code on that branch is the source of truth. (Sensitive deployment details — server IP/SSH, keys — are in the assistant's local project memory, not committed here.)

## What this is
A survey measuring whether short LLM conversations shift US participants' political attitudes. Adapts the original platform (arXiv 2505.04171) to the "ideologically selected models" protocol. Fully within-subjects: each participant does all 4 topics (Gun control, Immigration, Police, Taxes); each topic is independently assigned one of SIX server-side-balanced cells (equal 1/6) = {conservative, liberal} × {explicit, base} advocacy + neutral + control. So BOTH direction AND strength (explicit/base) are randomized per-topic within each participant (strength was between-subjects in the pilots; now within). Strength changes only the system prompt, never the model.

## Files
- `survey.py` — Flask backend (all logic), run directly as `gunicorn survey:app` (ONE service; no entry points, no `SURVEY_PROMPT_MODE`). Reads `config.json`. Endpoints: `/assign` (single-blind balanced assignment), `/chat`, `/start`, `/end`, `/stance` (post only), `/checks` (per-topic §7.6), `/survey` (instrument), `/complete`.
- `llmSurvey.html` — single-file frontend (served static by Apache). Single backend URL (`API_PORT`=5000); no `?mode=` param.
- `config.json` — backend-only (NEVER web-serve): `openrouter_api_key`, `completion_code`, `domain_name`, `port`, ssl paths. Key may also come from `OPENROUTER_API_KEY` env (overrides).
- `test_models.py` — pings every roster model endpoint (PASS/empty/FAIL).
- `README.md` — deployment + strength-within-subject explanation.
- (`survey_explicit.py` / `survey_base.py` were removed when strength became within-subject.)

## Strength = explicit | base (per-topic, within-subject)
Each topic's assignment entry carries a `strength` (`explicit`/`base` for advocacy cells; `None` for neutral/control). `CELLS` maps the 6 cells → (condition, strength); `CELL_WEIGHTS` = equal 1/6; balancing is over the 6 cells per topic. `build_system_prompt(topic, lean, strength)` and the `/chat` advocacy injection key off the per-topic `strength` (no global mode):
- **explicit** advocacy: a GENTLE partisan prompt (lean + "argue gently, don't pressure"); the specific item is injected and the model is told its own view + to answer directly.
- **base** advocacy: only "discuss naturally, stay on topic" (no steering — natural behavior).
- **neutral**: balanced Appendix-A "present both sides" (mode-invariant). **control**: no chat.
- All prompts append LENGTH (≤100–150 words) + FOLLOWUP (end by offering to help, don't probe the participant's opinion).
- ONE data dir `results/` + one `results/_assignments.json` (balances the 6 cells). Old pilot data stays in `results_explicit/` `results_base/` (analyzed under the old between-deployment design).

## Flow
Consent → 18-page instrument (ONE question/page; generic validator; "None of these" exclusivity on media/networks; ZIP = 5 digits) → per topic: chat arms = 3-min timed chat (CHAT_SECONDS=180, no message minimum) then ONE-item post-stance (Support/Oppose); control = single direct stance → after all topics: per-CHAT-topic §7.6 checks (perceived_neutrality, self_reported_persuasion, post_trust) → two general post items (perceived_neutrality_post_general, trust_post_general) → completion code (from `/complete`).

## Data format (per participant: `results/<pid>.json`)
`prolificPID, created, prompt_mode (now always "mixed"), completed, assignment{order, topics{topic:{condition,strength,lean,model,model_name,chat}}}, instrument{...incl perceived_neutrality_pre, trust_pre, party_id, ideology...}, topics{topic:{post{responses{item_idx:Support/Oppose}, item_index}, checks{...}, agrees_with_llm, llm_advocated_stance, transcript[], timing}}, wrapup{perceived_neutrality_post_general, trust_post_general}, canary_triggered`.
- NEW: per-topic `assignment.topics[t].strength` ∈ {explicit, base, null} is the within-subject strength factor (null for neutral/control). Participant-level `prompt_mode` is now the constant "mixed" (strength lives per-topic).
- `agrees_with_llm` (1/0) + `llm_advocated_stance` set only for chat conservative/liberal arms.
- CONSERVATIVE_STANCE map (which Support/Oppose is the conservative side): gun {0:Support,1:Oppose}, immigration {0:Support,1:Support}, police {0:Oppose,1:Oppose}, taxes {0:Oppose,1:Oppose}.
- Real Prolific IDs match `^[A-Za-z0-9]{24}$`; "completed fully" = `completed` present. Test IDs (1245, armtest1, chk, …) and the `_assignments.json` file must be excluded.

## Model roster (4 per pole; in survey.py MODELS)
Selected from the user's PCA-distance top-10s, mapped to OpenRouter slugs (re-verify before launch). Subs: Grok 3/4→grok-4.3; DeepSeek Chat V3.2→deepseek-chat-v3.1. Dropped: GPT-OSS 20B (refuses), Mistral Nemo (answer-order bias), Phi 3.5 MoE/Falcon 3/Calme 3.3 (not on OpenRouter). Neutral arm = Claude 4.5 Sonnet (in no A/B pole). Reasoning disabled per call with a fallback for mandatory-reasoning models (e.g. GPT-OSS); empty/None choices retried. Canary word "tapestry" detects pasted external-LLM use.

## Dev / test
- Local preview: serve the dir + run survey.py with a stubbed `client.chat.completions.create`; drive via the preview browser tools.
- `test_models.py` (needs the key) checks all endpoints.
- Force a test cell sequence without polluting balancing: insert a crafted `{order,topics}` (each topic entry now needs `condition`+`strength`, e.g. `{"condition":"conservative","strength":"base",...}`) into `results/_assignments.json` (counts untouched), open `?PROLIFIC_PID=<pid>` (no `&mode=` anymore — one backend).

## Pulling & analyzing pilot data
Read-only over SSH: `ssh root@<server> 'cd /root/LLM-Survey-Platform && tar czf - results' > data.tgz` (new design writes only `results/`; the old pilots are in `results_explicit/`+`results_base/` if still present), extract, then load all non-`.lock` JSONs, filter to 24-char Prolific IDs with `completed`. Outcomes: conservative-coded post stance by cell, `agrees_with_llm` by pole, the §7.6 checks, pre→post general beliefs, canary flags. NOTE: with strength within-subject, `prompt_mode` is "mixed" and strength is per-slot (`assignment.topics[t].strength`) — `analyze_pilot.py`/`glmm_build.py` must read strength PER-SLOT now, not by data dir (they currently bucket by `prompt_mode`/dir).

## Harness constraints
The auto-mode classifier BLOCKS: editing the shared Apache config, systemd, killing/creating processes on the prod server, creating live assignments. The USER runs all server write/restart/kill commands (provide exact commands). Read-only SSH and external curl to public endpoints are allowed.

## Deployment (high level; specifics in private memory)
Apache serves the frontend on 443. ONE gunicorn service now runs the API on port 5000 (`gunicorn survey:app`) with `--worker-class gthread -w 4 --threads 16 --timeout 120` (THREADED — sync workers ignore `--threads` and a slow /chat starves /assign) and the Let's Encrypt certs, kept alive in a `tmux` session. (The old :5001 base service is retired.) Frontend served from a `/persuasion/` path. ONE Prolific URL for everyone (no `&mode=base`); placeholder must be exactly `{{%PROLIFIC_PID%}}`. NOTE: serving the API on non-standard port 5000 risks some participants' networks blocking it — a 443 reverse-proxy is the robust pre-launch fix.

## Open items
- Strength is now WITHIN-subject (per-topic): single backend `survey:app`, 6 cells balanced equal-1/6, `results/`. DONE in code (pending review/push). Still TODO: update `analyze_pilot.py`/`glmm_build.py` to read per-slot `strength` (not by data dir); redo the power analysis / pre-reg (N=2,000/version no longer maps — there's one sample, and explicit-vs-base is now a within-person contrast, more efficient).
- Pre-launch: re-verify model slugs; move API behind 443 (reverse proxy) to avoid port-blocks; reset `results/_assignments.json` + clear test result files; open a PR.
- Pilot done (see analysis): explicitly-steered LLMs persuade out-partisans (H2 p=.007), base ≈ null, neutral ≈ control; base interaction raised general LLM trust/neutrality while explicit did not.
- Deferred: capture STUDY_ID/SESSION_ID; per-version completion codes; "fix model, vary side" mechanism arm.
