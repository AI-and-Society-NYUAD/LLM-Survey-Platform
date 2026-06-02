#!/usr/bin/env python3
"""Smoke-test every OpenRouter model endpoint used by the survey.

Sends a tiny chat completion to each unique model slug in survey.MODELS (plus the
neutral-arm model) and reports PASS/FAIL, so you can confirm the whole roster is
reachable before launching. Each model is tested once (deduplicated by slug).

Run from the project directory with the OpenRouter key available — either:
    OPENROUTER_API_KEY=sk-or-... python3 test_models.py
or with the key set in config.json ("openrouter_api_key").
"""

import sys
import time

from survey import MODELS, NEUTRAL_MODEL, client, OPENROUTER_API_KEY


def collect_slugs():
    """Map each unique model slug -> list of arm labels that use it."""
    uses = {}
    for topic, poles in MODELS.items():
        for lean, models in poles.items():
            for name, slug in models.items():
                uses.setdefault(slug, []).append(f"{topic}/{lean} ({name})")
    uses.setdefault(NEUTRAL_MODEL[1], []).append(f"neutral ({NEUTRAL_MODEL[0]})")
    return uses


def main():
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY.startswith("SET_"):
        print("ERROR: OpenRouter API key not set. Provide OPENROUTER_API_KEY in the "
              "environment or 'openrouter_api_key' in config.json.")
        sys.exit(1)

    slugs = collect_slugs()
    # Mirror the app: same max_tokens as /chat, and a prompt that needs a real answer.
    max_tokens = 1024
    prompt = "In one short sentence, give an argument about a US public-policy issue."
    print(f"Testing {len(slugs)} unique model endpoints (max_tokens={max_tokens})...\n")

    ok, empty, fail = [], [], []
    for slug in sorted(slugs):
        start = time.time()
        try:
            # Match /chat: prefer reasoning off, fall back to plain if the endpoint
            # mandates reasoning (e.g. GPT-OSS).
            try:
                resp = client.chat.completions.create(
                    model=slug,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    extra_body={"reasoning": {"enabled": False}},
                )
            except Exception:
                resp = client.chat.completions.create(
                    model=slug,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                )
            choice = resp.choices[0]
            content = (choice.message.content or "").strip()
            fr = getattr(choice, "finish_reason", "?")
            reasoning = getattr(choice.message, "reasoning", None)
            if reasoning is None and getattr(choice.message, "model_extra", None):
                reasoning = choice.message.model_extra.get("reasoning")
            dt = time.time() - start
            if content:
                print(f"PASS  {slug:42s} {dt:5.1f}s  finish={fr:8s} «{content[:50].replace(chr(10),' ')}»")
                ok.append(slug)
            else:
                rlen = len(reasoning) if reasoning else 0
                print(f"WARN  {slug:42s} {dt:5.1f}s  EMPTY content  finish={fr}  reasoning_chars={rlen}")
                empty.append(slug)
        except Exception as e:
            msg = str(e).replace("\n", " ")[:140]
            print(f"FAIL  {slug:42s}        {msg}")
            fail.append((slug, msg))

    print(f"\n{len(ok)} returned text, {len(empty)} empty, {len(fail)} failed.")
    if empty:
        print("\nEmpty (reachable but returned no content — likely reasoning models):")
        for slug in empty:
            print(f"  - {slug}  [{', '.join(slugs[slug])}]")
    if fail:
        print("\nFailures:")
        for slug, msg in fail:
            print(f"  - {slug}: {msg}  [{', '.join(slugs[slug])}]")
    if fail or empty:
        sys.exit(2)
    print("\nAll model endpoints returned usable text.")


if __name__ == "__main__":
    main()
