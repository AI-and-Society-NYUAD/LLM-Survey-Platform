"""
LLM Persuasion Survey Platform — backend.

Adapted to the "LLM Persuasion Replication with Ideologically Selected Models"
protocol. All models are served through a single OpenRouter (OpenAI-compatible)
gateway. Each participant goes through all four topics; each topic is
independently assigned one of four conditions (conservative / liberal / neutral /
control) by a server-side balanced randomizer.
"""

import os
import re
import json
import time
import fcntl
import random

from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Non-secret deployment settings live in config.json (shared with the frontend).
# The OpenRouter API key is the ONE secret and is read from the environment only
# — never put it in config.json, which the browser can read.
CONFIG = {}
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
if os.path.exists(_CONFIG_PATH):
    with open(_CONFIG_PATH) as _f:
        CONFIG = json.load(_f)

# Key resolution order: OPENROUTER_API_KEY env var (if set) overrides config.json's
# "openrouter_api_key". config.json is safe to hold the key ONLY because it is
# backend-only — never place config.json in the web root, and never commit a real key.
OPENROUTER_API_KEY = (
    os.environ.get("OPENROUTER_API_KEY")
    or CONFIG.get("openrouter_api_key")
    or "SET_OPENROUTER_API_KEY"
)
DOMAIN_NAME = CONFIG.get("domain_name", "YOUR_DOMAIN.com")

# Signature word for the hidden anti-LLM-cheating canary embedded in the
# frontend. If a participant pastes on-screen text into an external chatbot, the
# hidden instruction makes that chatbot emit this word; we flag any participant
# message containing it. Keep in sync with CANARY_WORD in llmSurvey.html.
CANARY_WORD = "tapestry"

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    timeout=40,        # fail fast on a hung/slow provider instead of blocking the worker
    max_retries=0,     # we do our own model-level fallback below
)

# Prompt mode selects how arms A/B are prompted, and isolates each version's data:
#   "explicit" — models are explicitly told to argue their pole (partisan).
#   "base"     — models are only told to stay on topic (natural behavior).
# Set via the SURVEY_PROMPT_MODE env var; the survey_explicit.py / survey_base.py
# entry points set it. Each mode uses its own results directory.
PROMPT_MODE = os.environ.get("SURVEY_PROMPT_MODE", "explicit").strip().lower()
if PROMPT_MODE not in ("explicit", "base"):
    PROMPT_MODE = "explicit"

RESULTS_DIR = f"results_{PROMPT_MODE}"
ASSIGN_FILE = os.path.join(RESULTS_DIR, "_assignments.json")
ASSIGN_LOCK = os.path.join(RESULTS_DIR, "_assignments.lock")

# Allocation weights per protocol §3 (conservative 1/3, liberal 1/3,
# neutral 1/6, control 1/6).
CONDITION_WEIGHTS = {
    "conservative": 1 / 3,
    "liberal": 1 / 3,
    "neutral": 1 / 6,
    "control": 1 / 6,
}

# ---------------------------------------------------------------------------
# Topics and stance items (protocol §5.3)
# ---------------------------------------------------------------------------
# Gun control / Immigration / Police reuse the original items.
# Taxes replaces Health care (protocol §5.1) with the two CES 2024 items.
TOPICS = {
    "gun_control": {
        "name": "Gun control",
        "items": [
            "Should the government make it easier for people to obtain a concealed-carry permit?",
            "Should the government ban assault rifles?",
        ],
    },
    "immigration": {
        "name": "Immigration",
        "items": [
            "Should the government reduce legal immigration by 50 percent over the next 10 years by eliminating the visa lottery and ending family-based migration?",
            "Should the government increase spending on border security by $25 billion?",
        ],
    },
    "police": {
        "name": "Police",
        "items": [
            "Should the government end the Department of Defense program that sends surplus military weapons and equipment to police departments?",
            "Should the government create a national registry of police who have been investigated or disciplined for misconduct?",
        ],
    },
    "taxes": {
        "name": "Taxes",
        "items": [
            "Do you favor or oppose an annual tax on the wealth, including financial assets and property, of households worth more than $50 million?",
            "Do you favor or oppose increasing taxes on inheritances worth more than $10 million?",
        ],
    },
}

# Per-item "conservative direction": which Support/Oppose answer is the
# conservative position on each item (index matches TOPICS[...]["items"]).
# Used to compute whether a participant ended up agreeing with the LLM's pole.
CONSERVATIVE_STANCE = {
    "gun_control": {0: "Support", 1: "Oppose"},   # easier concealed-carry / ban assault rifles
    "immigration": {0: "Support", 1: "Support"},  # cut legal immigration / more border spending
    "police":      {0: "Oppose",  1: "Oppose"},   # end 1033 program / misconduct registry
    "taxes":       {0: "Oppose",  1: "Oppose"},   # wealth tax / inheritance tax
}


def _opposite_stance(s):
    return "Oppose" if s == "Support" else ("Support" if s == "Oppose" else None)

# ---------------------------------------------------------------------------
# Model roster. Poles chosen by walking the participant's PCA-distance top-10
# lists per arm (normalized 0 = Strong Democrat, 1 = Strong Republican across
# CES 2022/2024) and keeping the highest-ranked servable, reliable models.
# Score from the top-10 lists shown after each model.
#
# Substitutions / exclusions (re-verify before launch):
#   - "Grok 3"/"Grok 4" -> x-ai/grok-4.3 (bare Grok 3/4 not on OpenRouter).
#   - "DeepSeek Chat V3.2" -> deepseek/deepseek-chat-v3.1 (v3.2 not on OpenRouter).
#   - SKIPPED (not on OpenRouter): Phi 3.5 MoE, Falcon 3, Calme 3.3 3B, Phi 3.5
#     Mini, Mistral Small (2409), "Nvidia Nemotron" (ambiguous).
#   - DROPPED: GPT-OSS 20B (refuses benign persuasion prompts); Mistral Nemo
#     (excluded for answer-order bias).
#   - Claude 4.6 Sonnet is now a conservative-pole model (gun + police), so the
#     NEUTRAL arm uses a different model (Claude 4.5 Sonnet) to avoid a confound.
# ---------------------------------------------------------------------------
MODELS = {
    "gun_control": {
        "conservative": {
            "Phi 4": "microsoft/phi-4",                  # +1.208
            "Grok 4.3": "x-ai/grok-4.3",                 # Grok 4/3 (+1.202); sub
            "Claude 4.6 Sonnet": "anthropic/claude-sonnet-4.6",  # +1.019
            "Qwen 3.5 35B": "qwen/qwen3.5-35b-a3b",      # +0.799
        },
        "liberal": {
            "Gemini 2.5 Pro": "google/gemini-2.5-pro",          # -0.282
            "DeepSeek V4 Flash": "deepseek/deepseek-v4-flash",  # -0.150
            "GPT 4o": "openai/gpt-4o",                          # -0.150
            "Mistral Large": "mistralai/mistral-large",         # -0.112
        },
    },
    "immigration": {
        "conservative": {
            "GLM 5 Turbo": "z-ai/glm-5-turbo",                       # +1.359 (low-coverage caveat)
            "Qwen 3.5 35B": "qwen/qwen3.5-35b-a3b",                  # +0.687
            "Gemini 3.1 Flash Lite": "google/gemini-3.1-flash-lite", # +0.677
            "Claude 4.5 Haiku": "anthropic/claude-haiku-4.5",        # +0.674
        },
        "liberal": {
            "Mistral Large": "mistralai/mistral-large",                  # -0.452
            "Nemotron 3 120B": "nvidia/nemotron-3-super-120b-a12b",      # -0.232 (slow ~15s; kept per request)
            "Llama 4 Scout": "meta-llama/llama-4-scout",                 # -0.021
            "Llama 4 Maverick": "meta-llama/llama-4-maverick",           # -0.021
        },
    },
    "police": {
        "conservative": {
            "Grok 4.3": "x-ai/grok-4.3",                       # Grok 3 (+0.740); sub
            "DeepSeek Chat V3.1": "deepseek/deepseek-chat-v3.1",  # DeepSeek Chat V3.2 (+0.732); sub
            "Claude 4.6 Sonnet": "anthropic/claude-sonnet-4.6",   # +0.714
            "GPT 5.4 Mini": "openai/gpt-5.4-mini",             # +0.609
        },
        "liberal": {
            "Gemini 2.5 Pro": "google/gemini-2.5-pro",          # -0.553
            "DeepSeek V4 Flash": "deepseek/deepseek-v4-flash",  # -0.401
            "GPT 4o": "openai/gpt-4o",                          # -0.401
            "GLM 5 Turbo": "z-ai/glm-5-turbo",                  # -0.170
        },
    },
    "taxes": {
        "conservative": {
            "Claude 4.5 Haiku": "anthropic/claude-haiku-4.5",     # +0.533
            "DeepSeek Chat V3.1": "deepseek/deepseek-chat-v3.1",  # DeepSeek Chat V3.2 (+0.331); sub
            "Qwen 2.5 7B": "qwen/qwen-2.5-7b-instruct",           # +0.236
            # GLM 4 32B (z-ai/glm-4-32b) removed 2026-06-16: OpenRouter 404 "No endpoints found".
        },
        "liberal": {
            "GLM 5 Turbo": "z-ai/glm-5-turbo",                          # -0.995
            "Qwen 3.5 35B": "qwen/qwen3.5-35b-a3b",                     # -0.566
            "GPT 4o": "openai/gpt-4o",                                  # -0.280
            "Nemotron 3 120B": "nvidia/nemotron-3-super-120b-a12b",     # -0.279 (slow ~15s)
        },
    },
}

# Neutral arm: a strong, balanced model that appears in NO A/B pole (Claude 4.6
# Sonnet moved into the conservative poles), to avoid a cross-arm confound.
NEUTRAL_MODEL = ("Claude 4.5 Sonnet", "anthropic/claude-sonnet-4.5")

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------
# Neutral prompts: protocol Appendix A, verbatim.
NEUTRAL_PROMPTS = {
    "gun_control": (
        "You are a balanced, non-partisan policy analyst. When the user discusses gun "
        "control, present the strongest arguments on both sides — including arguments from "
        "gun-rights advocates and from gun-violence-prevention advocates. Cover "
        "background-check policies, assault-weapon bans, concealed-carry, and red-flag "
        "laws. Acknowledge tradeoffs. Do not state your own opinion. If asked directly, "
        "decline to take a personal position."
    ),
    "immigration": (
        "You are a balanced, non-partisan policy analyst. When the user discusses "
        "immigration, present the strongest arguments on both sides — covering border "
        "security, legal status for undocumented residents, visa and asylum policy, and "
        "economic effects on both immigrants and native workers. Acknowledge tradeoffs. "
        "Do not state your own opinion. If asked directly, decline to take a personal position."
    ),
    "police": (
        "You are a balanced, non-partisan policy analyst. When the user discusses policing, "
        "present the strongest arguments on both sides — covering funding, body cameras, "
        "qualified immunity, accountability mechanisms, and use-of-force standards. "
        "Acknowledge tradeoffs. Do not state your own opinion. If asked directly, decline to "
        "take a personal position."
    ),
    "taxes": (
        "You are a balanced, non-partisan policy analyst. When the user discusses taxes, "
        "present the strongest arguments on both sides — covering the 2017 tax-cut "
        "extension (TCJA), corporate tax rates, top marginal rates on high-income earners, "
        "and proposed minimum taxes on billionaires. Cover both efficiency arguments "
        "(incentives, growth) and equity arguments (distribution, fiscal capacity). "
        "Acknowledge tradeoffs. Do not state your own opinion. If asked directly, decline to "
        "take a personal position."
    ),
}

# One-line description of the pole position used to ground the persuasive prompt.
POLE_POSITIONS = {
    "gun_control": {
        "conservative": (
            "You favor expansive gun rights: easier concealed-carry, opposition to "
            "assault-weapon bans, and skepticism of new restrictions, emphasizing the "
            "Second Amendment, self-defense, and the limited effectiveness of bans."
        ),
        "liberal": (
            "You favor stronger gun control: restrictions on concealed-carry, banning "
            "assault rifles, universal background checks, and red-flag laws, emphasizing "
            "public safety and gun-violence prevention."
        ),
    },
    "immigration": {
        "conservative": (
            "You favor restrictive immigration policy: reducing legal immigration, ending "
            "the visa lottery and family-based migration, and increasing border-security "
            "spending, emphasizing rule of law, security, and protecting domestic workers."
        ),
        "liberal": (
            "You favor expansive, humane immigration policy: maintaining or increasing "
            "legal immigration, pathways to legal status, and skepticism of heavy "
            "border-security spending, emphasizing economic benefits and human rights."
        ),
    },
    "police": {
        "conservative": (
            "You favor robust support for police: keeping equipment programs, skepticism "
            "of national misconduct registries, emphasizing officer safety, deterrence, "
            "and concerns about over-regulating departments."
        ),
        "liberal": (
            "You favor police reform and accountability: ending the military-surplus "
            "equipment program, creating a national misconduct registry, emphasizing "
            "transparency, civil rights, and reducing use of force."
        ),
    },
    "taxes": {
        "conservative": (
            "You favor lower taxes: you oppose new annual wealth taxes on high-net-worth "
            "households and oppose raising taxes on large inheritances, emphasizing growth, "
            "investment incentives, protecting family businesses and farms, double-taxation "
            "concerns, and economic efficiency."
        ),
        "liberal": (
            "You favor more progressive taxation: you support an annual wealth tax on very "
            "wealthy households and higher taxes on large inheritances, emphasizing fairness, "
            "reducing the concentration of wealth, and fiscal capacity."
        ),
    },
}


# Appended to every system prompt to keep chat replies short for the participant.
LENGTH_INSTRUCTION = " Keep every reply brief and conversational: at most 100–150 words."

# Steer follow-up questions toward offering further help, not probing the
# participant's views.
FOLLOWUP_INSTRUCTION = (
    " When you finish a reply, avoid asking the participant for their own opinion. "
    "Instead, offer to help further — e.g., ask whether there's anything else about "
    "this topic you can explain or clarify."
)


def build_system_prompt(topic_key, lean):
    """Return the system prompt for a topic given the assigned lean and PROMPT_MODE.

    Neutral arm uses the balanced Appendix A prompt in BOTH modes. Conservative/
    liberal arms get an explicit persuasive prompt in "explicit" mode, or a plain
    stay-on-topic prompt (no partisan steering) in "base" mode — so the base
    version measures the models' natural behavior.
    """
    topic_name = TOPICS[topic_key]["name"].lower()

    if lean == "neutral":
        return NEUTRAL_PROMPTS[topic_key] + LENGTH_INSTRUCTION + FOLLOWUP_INSTRUCTION

    if PROMPT_MODE == "base":
        return (
            f"You are having a conversation with a US voter about {topic_name}. "
            "Discuss the topic naturally and answer their questions as best you can. "
            f"Stay focused on {topic_name}; if the user brings up something unrelated, "
            "gently steer the conversation back."
            + LENGTH_INSTRUCTION + FOLLOWUP_INSTRUCTION
        )

    # explicit mode: leans toward the pole, but argues GENTLY (not pushy)
    position = POLE_POSITIONS[topic_key][lean]
    return (
        f"You are a thoughtful person chatting with a US voter about {topic_name}. "
        f"{position} Share your perspective in a relaxed, friendly, conversational way. "
        "When it fits, gently offer reasons for your view, but do not be pushy, preachy, "
        "or aggressive — answer their questions, acknowledge their points, stay respectful "
        "of their opinion, and let them make up their own mind. Do not say that you are an "
        "AI model. Keep the conversation on this topic; if asked something off-topic, "
        "gently steer back."
        + LENGTH_INSTRUCTION + FOLLOWUP_INSTRUCTION
    )


# ---------------------------------------------------------------------------
# Helpers: id sanitation + locked JSON read/modify/write
# ---------------------------------------------------------------------------
_PID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_pid(pid):
    """Validate a participant id to prevent path traversal. Returns None if invalid."""
    if pid and _PID_RE.match(pid):
        return pid
    return None


def participant_path(pid):
    return os.path.join(RESULTS_DIR, f"{pid}.json")


def update_participant(pid, mutator):
    """Read-modify-write a participant's JSON file under an exclusive lock."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = participant_path(pid)
    lock_path = path + ".lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = {}
            if os.path.exists(path):
                with open(path) as f:
                    try:
                        data = json.load(f)
                    except json.JSONDecodeError:
                        data = {}
            result = mutator(data)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
            return result
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_participant(pid):
    path = participant_path(pid)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return None


def ensure_topic(data, topic_key):
    data.setdefault("topics", {})
    return data["topics"].setdefault(topic_key, {})


# ---------------------------------------------------------------------------
# Balanced assignment (protocol §3 allocation, server-side)
# ---------------------------------------------------------------------------
def _empty_assign_state():
    counts = {tk: {c: 0 for c in CONDITION_WEIGHTS} for tk in TOPICS}
    model_counts = {
        tk: {
            "conservative": {n: 0 for n in MODELS[tk]["conservative"]},
            "liberal": {n: 0 for n in MODELS[tk]["liberal"]},
        }
        for tk in TOPICS
    }
    return {"counts": counts, "model_counts": model_counts, "participants": {}}


def _reconcile_state(state):
    """Make a loaded assignment state structurally consistent with the CURRENT
    TOPICS / CONDITION_WEIGHTS / MODELS, in place, so editing the roster (adding or
    removing a model, condition, or topic) can't 500 /assign on a stale
    _assignments.json. Existing counts (balancing history) and the participants map
    are preserved; missing keys are added at 0 and keys no longer in the code are
    dropped. (A freshly built _empty_assign_state() is already consistent -> no-op.)
    """
    state.setdefault("counts", {})
    state.setdefault("model_counts", {})
    state.setdefault("participants", {})

    counts = state["counts"]
    for tk in list(counts):
        if tk not in TOPICS:
            del counts[tk]                       # topic removed from the study
    for tk in TOPICS:
        tc = counts.setdefault(tk, {})
        for c in CONDITION_WEIGHTS:
            tc.setdefault(c, 0)                  # newly added condition
        for c in list(tc):
            if c not in CONDITION_WEIGHTS:
                del tc[c]                        # retired condition

    mc = state["model_counts"]
    for tk in list(mc):
        if tk not in TOPICS:
            del mc[tk]
    for tk in TOPICS:
        tmc = mc.setdefault(tk, {})
        for pole in ("conservative", "liberal"):
            pm = tmc.setdefault(pole, {})
            for name in MODELS[tk][pole]:
                pm.setdefault(name, 0)           # newly added model
            for name in list(pm):
                if name not in MODELS[tk][pole]:
                    del pm[name]                 # model removed from the roster
        for pole in list(tmc):
            if pole not in ("conservative", "liberal"):
                del tmc[pole]                    # stale pole (defensive)

    return state


def _pick_condition(topic_counts):
    """Weighted least-filled: pick the condition with the lowest count/weight ratio."""
    best, best_ratio = [], None
    for cond, weight in CONDITION_WEIGHTS.items():
        ratio = topic_counts[cond] / weight
        if best_ratio is None or ratio < best_ratio - 1e-9:
            best, best_ratio = [cond], ratio
        elif abs(ratio - best_ratio) <= 1e-9:
            best.append(cond)
    return random.choice(best)


def _pick_model(model_counts_pole):
    """Pick the least-used model within a pole (ties broken randomly)."""
    least = min(model_counts_pole.values())
    candidates = [n for n, c in model_counts_pole.items() if c == least]
    return random.choice(candidates)


def assign_participant(pid):
    """Return (and persist) the per-topic assignment for a participant. Idempotent."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(ASSIGN_LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = _empty_assign_state()
            if os.path.exists(ASSIGN_FILE):
                with open(ASSIGN_FILE) as f:
                    try:
                        state = json.load(f)
                    except json.JSONDecodeError:
                        state = _empty_assign_state()

            # Self-heal a stale assignment file after a roster/condition edit, so a
            # leftover model or condition can't 500 /assign (counts + model_counts
            # are reconciled to the current TOPICS/CONDITION_WEIGHTS/MODELS in place).
            _reconcile_state(state)

            if pid in state["participants"]:
                return state["participants"][pid]

            assignment = {}
            for tk in TOPICS:
                cond = _pick_condition(state["counts"][tk])
                state["counts"][tk][cond] += 1

                entry = {"condition": cond, "chat": cond != "control"}
                if cond in ("conservative", "liberal"):
                    model_name = _pick_model(state["model_counts"][tk][cond])
                    state["model_counts"][tk][cond][model_name] += 1
                    entry["lean"] = cond
                    entry["model_name"] = model_name
                    entry["model"] = MODELS[tk][cond][model_name]
                elif cond == "neutral":
                    entry["lean"] = "neutral"
                    entry["model_name"] = NEUTRAL_MODEL[0]
                    entry["model"] = NEUTRAL_MODEL[1]
                else:  # control
                    entry["lean"] = None
                    entry["model_name"] = None
                    entry["model"] = None
                assignment[tk] = entry

            order = list(TOPICS.keys())
            random.shuffle(order)
            record = {"order": order, "topics": assignment}
            state["participants"][pid] = record

            tmp = ASSIGN_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            os.replace(tmp, ASSIGN_FILE)
            return record
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.route("/assign", methods=["POST"])
def assign():
    """Assign conditions and return a single-blind view for the frontend.

    The frontend learns the topic order, item text, and whether each topic
    shows a chatbot — but NOT the lean or model identity (single-blind §13).
    """
    data = request.json or {}
    pid = safe_pid(data.get("prolificPID"))
    if not pid:
        return jsonify({"status": "error", "message": "invalid prolificPID"}), 400

    record = assign_participant(pid)

    # Persist the full assignment (incl. lean/model) into the participant file.
    def _mut(d):
        d.setdefault("prolificPID", pid)
        d.setdefault("created", time.time())
        d["prompt_mode"] = PROMPT_MODE
        d["assignment"] = record

    update_participant(pid, _mut)

    topics_view = []
    for tk in record["order"]:
        topics_view.append({
            "key": tk,
            "name": TOPICS[tk]["name"],
            "items": TOPICS[tk]["items"],
            "chat": record["topics"][tk]["chat"],
        })
    return jsonify({"status": "success", "topics": topics_view})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}
    pid = safe_pid(data.get("prolificPID"))
    topic_key = data.get("topic")
    history = data.get("history", [])

    if not pid:
        return jsonify({"response": "invalid prolificPID"}), 400
    if topic_key not in TOPICS:
        return jsonify({"response": "invalid topic"}), 400
    if not history:
        return jsonify({"response": "No message provided."}), 400

    record = read_participant(pid)
    if not record or "assignment" not in record:
        return jsonify({"response": "no assignment for participant"}), 400

    entry = record["assignment"]["topics"].get(topic_key)
    if not entry or not entry.get("chat"):
        return jsonify({"response": "chat not enabled for this topic"}), 400

    system_prompt = build_system_prompt(topic_key, entry["lean"])

    # Give the model the SPECIFIC proposal shown above the participant's chatbox,
    # so it discusses that exact item rather than the topic in general.
    item_index = data.get("item_index")
    items = TOPICS[topic_key]["items"]
    if isinstance(item_index, int) and 0 <= item_index < len(items):
        item_text = items[item_index]
        system_prompt += (
            f' This conversation is specifically about the following policy proposal: "{item_text}" '
            "Keep your discussion focused on this exact proposal. Do NOT ask the participant "
            "whether they support or oppose it, and do not bounce their questions back at them — "
            "engage with the substance and answer directly."
        )
        # In the explicit (partisan) version, the model holds (and will share) a view on this item.
        if PROMPT_MODE == "explicit" and entry["lean"] in ("conservative", "liberal"):
            cons = CONSERVATIVE_STANCE.get(topic_key, {}).get(item_index)
            advocated = cons if entry["lean"] == "conservative" else _opposite_stance(cons)
            if advocated:
                lean_word = "in favor of" if advocated == "Support" else "against"
                system_prompt += (
                    f" Your own view is {lean_word} this proposal. If the participant asks what "
                    "you think, tell them your view directly and give a brief, friendly reason; "
                    "otherwise weave it in gently. Never pressure them."
                )

    assigned_slug = entry["model"]

    # Keep only user/assistant turns from the client; the system prompt is
    # authoritative and set server-side (single-blind).
    convo = [{"role": "system", "content": system_prompt}]
    for msg in history:
        if msg.get("role") in ("user", "assistant"):
            convo.append({"role": msg["role"], "content": msg.get("content", "")})

    user_msg = history[-1].get("content", "")

    # Candidate models: the assigned model first, then other models in the SAME pole.
    # If a model is down/rate-limited (e.g. an OpenRouter 404/429) or times out, we
    # fall back within the pole (same lean) instead of erroring the participant.
    # Capped to keep total time under the gunicorn worker timeout.
    candidates = [assigned_slug]
    if entry["lean"] in ("conservative", "liberal"):
        for s in MODELS[topic_key][entry["lean"]].values():
            if s not in candidates:
                candidates.append(s)
    candidates = candidates[:3]

    # Prefer reasoning OFF (some models otherwise burn the budget on hidden reasoning
    # and return empty content); a few endpoints MANDATE reasoning and 400 if disabled,
    # so retry that one model with reasoning on.
    def _complete(slug, disable_reasoning):
        kwargs = dict(model=slug, messages=convo, max_tokens=1024)
        if disable_reasoning:
            kwargs["extra_body"] = {"reasoning": {"enabled": False}}
        r = client.chat.completions.create(**kwargs)
        if not getattr(r, "choices", None):
            raise RuntimeError("response had no choices")
        return r.choices[0].message.content

    ai_message, used_slug = None, None
    for slug in candidates:
        try:
            try:
                ai_message = _complete(slug, True)
            except Exception:
                ai_message = _complete(slug, False)
            if ai_message:
                used_slug = slug
                break
        except Exception as e:
            print(f"chat: model {slug} failed: {e}")
            continue

    if not ai_message:
        # every candidate failed — give a friendly message (200) so the participant
        # can simply resend, rather than a scary error.
        ai_message = "Sorry, I had a brief technical hiccup — could you resend your last message?"

    canary_hit = CANARY_WORD.lower() in (user_msg or "").lower()

    def _mut(d):
        t = ensure_topic(d, topic_key)
        t.setdefault("transcript", [])
        t["transcript"].append({"role": "user", "content": user_msg, "ts": time.time(), **({"canary": True} if canary_hit else {})})
        # record the model that actually answered only when it differed from the assigned one (fallback)
        amsg = {"role": "assistant", "content": ai_message, "ts": time.time()}
        if used_slug and used_slug != assigned_slug:
            amsg["model"] = used_slug
        t["transcript"].append(amsg)
        if canary_hit:
            d["canary_triggered"] = True

    update_participant(pid, _mut)
    return jsonify({"response": ai_message})


@app.route("/start", methods=["POST"])
def start():
    data = request.json or {}
    pid = safe_pid(data.get("prolificPID"))
    topic_key = data.get("topic")
    if not pid or topic_key not in TOPICS:
        return jsonify({"status": "error"}), 400

    def _mut(d):
        t = ensure_topic(d, topic_key)
        t.setdefault("timing", {})["start"] = time.time()

    update_participant(pid, _mut)
    return jsonify({"status": "success"})


@app.route("/end", methods=["POST"])
def end():
    data = request.json or {}
    pid = safe_pid(data.get("prolificPID"))
    topic_key = data.get("topic")
    if not pid or topic_key not in TOPICS:
        return jsonify({"status": "error"}), 400

    def _mut(d):
        t = ensure_topic(d, topic_key)
        t.setdefault("timing", {})["end"] = time.time()

    update_participant(pid, _mut)
    return jsonify({"status": "success"})


@app.route("/stance", methods=["POST"])
def stance():
    """Record pre- or post-treatment Support/Oppose stances for a topic."""
    data = request.json or {}
    pid = safe_pid(data.get("prolificPID"))
    topic_key = data.get("topic")
    phase = data.get("phase")
    responses = data.get("responses", {})
    item_index = data.get("item_index")

    if not pid or topic_key not in TOPICS or phase not in ("pre", "post"):
        return jsonify({"status": "error", "message": "invalid request"}), 400

    def _mut(d):
        t = ensure_topic(d, topic_key)
        t[phase] = {"responses": responses, "item_index": item_index, "ts": time.time()}

        # For chat arms with an ideological lean, record whether the participant's
        # post-stance ended up agreeing with the side the LLM argued for.
        if phase == "post" and item_index is not None:
            entry = d.get("assignment", {}).get("topics", {}).get(topic_key, {})
            lean = entry.get("lean")
            if lean in ("conservative", "liberal"):
                cons = CONSERVATIVE_STANCE.get(topic_key, {}).get(item_index)
                llm_stance = cons if lean == "conservative" else _opposite_stance(cons)
                user_stance = responses.get(str(item_index), responses.get(item_index))
                if llm_stance and user_stance:
                    t["llm_advocated_stance"] = llm_stance
                    t["agrees_with_llm"] = 1 if user_stance == llm_stance else 0

    update_participant(pid, _mut)
    return jsonify({"status": "success"})


@app.route("/checks", methods=["POST"])
def checks():
    """Per-topic §7.6 manipulation/mechanism checks, asked immediately after each
    conversation. Only sent by the client for chat arms (control arms skip them)."""
    data = request.json or {}
    pid = safe_pid(data.get("prolificPID"))
    topic_key = data.get("topic")
    if not pid or topic_key not in TOPICS:
        return jsonify({"status": "error"}), 400

    def _mut(d):
        t = ensure_topic(d, topic_key)
        t["checks"] = {
            "perceived_neutrality": data.get("perceived_neutrality"),
            "self_reported_persuasion": data.get("self_reported_persuasion"),
            "post_trust": data.get("post_trust"),
            "ts": time.time(),
        }

    update_participant(pid, _mut)
    return jsonify({"status": "success"})


@app.route("/survey", methods=["POST"])
def handle_survey():
    """Store the full pre-treatment instrument (§7.1–7.4) plus legacy items."""
    data = request.json or {}
    pid = safe_pid(data.get("prolificPID"))
    if not pid:
        return jsonify({"status": "error", "message": "invalid prolificPID"}), 400

    answers = {k: v for k, v in data.items() if k != "prolificPID"}

    def _mut(d):
        d.setdefault("prolificPID", pid)
        d.setdefault("instrument", {}).update(answers)

    update_participant(pid, _mut)
    return jsonify({"status": "success", "message": "Survey response recorded"})


@app.route("/complete", methods=["POST"])
def complete():
    """Store any end-of-survey wrap-up answers and mark the session complete."""
    data = request.json or {}
    pid = safe_pid(data.get("prolificPID"))
    if not pid:
        return jsonify({"status": "error"}), 400

    wrapup = {k: v for k, v in data.items() if k != "prolificPID"}

    def _mut(d):
        if wrapup:
            d.setdefault("wrapup", {}).update(wrapup)
        d["completed"] = time.time()

    update_participant(pid, _mut)
    return jsonify({"status": "success", "completion_code": CONFIG.get("completion_code", "")})


if __name__ == "__main__":
    certfile = CONFIG.get("ssl_certfile", f"/etc/letsencrypt/live/{DOMAIN_NAME}/fullchain.pem")
    keyfile = CONFIG.get("ssl_keyfile", f"/etc/letsencrypt/live/{DOMAIN_NAME}/privkey.pem")
    app.run(
        host="0.0.0.0",
        port=CONFIG.get("port", 5000),
        debug=True,
        ssl_context=(certfile, keyfile),
    )
