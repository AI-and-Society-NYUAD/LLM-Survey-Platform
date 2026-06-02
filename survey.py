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
)

RESULTS_DIR = "results"
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
# Model roster. Poles selected from the per-topic ideology scores
# (data/analysis_outputs/llm_topic_ideology_all.csv; +1 = conservative answer,
# -1 = liberal answer), restricted to models available on OpenRouter, and
# EXCLUDING the neutral-arm model (Claude 4.6 Sonnet) to avoid cross-arm
# confounds. Per-topic CSV score shown in the comment after each model.
#
# Caveats (re-verify before launch):
#   - Gun control has almost no conservative-leaning model; Qwen 3.5 35B (+0.16)
#     is the only clearly positive servable model. Grok 4.3 is included as a
#     proxy for Grok 3 (+0.33, not on OpenRouter) but its gun-control lean is
#     UNVERIFIED (Grok 4 scored ~0 on guns).
#   - Police likewise lacks a strongly conservative servable model; Phi 3.5 MoE
#     (+0.50) and Calme 3.3 (+0.25) are not on OpenRouter. Mistral Nemo (+0.25)
#     is the best available; Mistral Large (0.00) included as a weak second.
#   - TAXES is NOT in the ideology CSV — these are the protocol §6 (CES 2024
#     CC24_341) picks, UNVALIDATED against the CSV. Supply taxes scores to revise.
# ---------------------------------------------------------------------------
MODELS = {
    "gun_control": {
        "conservative": {
            "Qwen 3.5 35B": "qwen/qwen3.5-35b-a3b",                 # +0.16 (10 runs)
            "Grok 4.3": "x-ai/grok-4.3",                            # proxy for Grok 3 (+0.33); lean UNVERIFIED
        },
        "liberal": {
            "GLM 4 32B": "z-ai/glm-4-32b",                          # -1.00 (10 runs)
            "Gemma 3 27B": "google/gemma-3-27b-it",                 # -0.67 (10 runs)
            "Mistral Large": "mistralai/mistral-large",             # -0.67 (10 runs)
            "GPT 4o": "openai/gpt-4o",                              # -0.67
            "Llama 3.1 8B": "meta-llama/llama-3.1-8b-instruct",     # -0.67
        },
    },
    "immigration": {
        "conservative": {
            "Claude 4.5 Haiku": "anthropic/claude-haiku-4.5",          # +0.50
            "Gemini 3.1 Flash Lite": "google/gemini-3.1-flash-lite",   # +0.50
            "Llama 3.1 8B": "meta-llama/llama-3.1-8b-instruct",        # +0.50
            "Llama 3.1 70B": "meta-llama/llama-3.1-70b-instruct",      # +0.50
        },
        "liberal": {
            "GLM 5 Turbo": "z-ai/glm-5-turbo",                      # -1.00 (10 runs)
            "Mistral Large": "mistralai/mistral-large",             # -1.00 (10 runs)
            "Mistral Nemo": "mistralai/mistral-nemo",               # -1.00
            "Llama 4 Maverick": "meta-llama/llama-4-maverick",      # -0.50
            "Gemma 2 27B": "google/gemma-2-27b-it",                 # -0.50
        },
    },
    "police": {
        "conservative": {
            "Mistral Nemo": "mistralai/mistral-nemo",               # +0.25 (best servable)
            "Mistral Large": "mistralai/mistral-large",             # 0.00 (weak)
        },
        "liberal": {
            "Gemma 2 27B": "google/gemma-2-27b-it",                 # -1.00
            "GPT-OSS 120B": "openai/gpt-oss-120b",                  # -1.00
            "GPT 3.5 Turbo": "openai/gpt-3.5-turbo",                # -1.00
            "GPT 4o": "openai/gpt-4o",                              # -1.00
            "DeepSeek V4 Flash": "deepseek/deepseek-v4-flash",      # -0.85 (10 runs)
            "Nemotron 3 Super 120B": "nvidia/nemotron-3-super-120b-a12b",  # -0.80 (10 runs)
        },
    },
    "taxes": {  # user-assigned arms (no taxes column in the ideology CSV)
        "conservative": {
            "Grok 4.3": "x-ai/grok-4.3",  # user said "Grok 4"; bare grok-4 not on OpenRouter, using 4.3
        },
        "liberal": {
            "GPT 4o": "openai/gpt-4o",
        },
    },
}

# Neutral arm (protocol §6): Claude 4.6 Sonnet for all topics.
NEUTRAL_MODEL = ("Claude 4.6 Sonnet", "anthropic/claude-sonnet-4.6")

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


def build_system_prompt(topic_key, lean):
    """Return the system prompt for a topic given the assigned lean."""
    if lean == "neutral":
        return NEUTRAL_PROMPTS[topic_key]

    topic_name = TOPICS[topic_key]["name"].lower()
    position = POLE_POSITIONS[topic_key][lean]
    return (
        f"You are a knowledgeable, persuasive policy advocate discussing {topic_name} "
        f"with a US voter. {position} Present the strongest, most compelling case for "
        "this position, engage directly with the participant's points, and try to "
        "persuade them. Stay civil, concrete, and factual. Do not say that you are an "
        "AI model. Do not refuse to take a position. Keep the conversation focused on "
        "this topic; if asked something off-topic, gently steer back."
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
    model_slug = entry["model"]

    # Keep only user/assistant turns from the client; the system prompt is
    # authoritative and set server-side (single-blind).
    convo = [{"role": "system", "content": system_prompt}]
    for msg in history:
        if msg.get("role") in ("user", "assistant"):
            convo.append({"role": msg["role"], "content": msg.get("content", "")})

    user_msg = history[-1].get("content", "")
    # Prefer reasoning OFF: some models (e.g. Qwen 3.5) otherwise spend the whole
    # token budget on hidden reasoning and return empty content, and reasoning adds
    # large latency. But a few endpoints (e.g. GPT-OSS) MANDATE reasoning and 400 if
    # it's disabled — so fall back to a plain call when that happens.
    def _complete(disable_reasoning):
        kwargs = dict(model=model_slug, messages=convo, max_tokens=1024)
        if disable_reasoning:
            kwargs["extra_body"] = {"reasoning": {"enabled": False}}
        return client.chat.completions.create(**kwargs)

    try:
        try:
            resp = _complete(True)
        except Exception:
            resp = _complete(False)  # endpoint requires reasoning
        ai_message = resp.choices[0].message.content
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"response": "An error occurred while processing your request."}), 500

    canary_hit = CANARY_WORD.lower() in (user_msg or "").lower()

    def _mut(d):
        t = ensure_topic(d, topic_key)
        t.setdefault("transcript", [])
        t["transcript"].append({"role": "user", "content": user_msg, "ts": time.time(), **({"canary": True} if canary_hit else {})})
        t["transcript"].append({"role": "assistant", "content": ai_message, "ts": time.time()})
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
