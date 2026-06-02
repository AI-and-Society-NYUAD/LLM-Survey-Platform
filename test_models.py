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
    print(f"Testing {len(slugs)} unique model endpoints via OpenRouter...\n")

    ok, fail = [], []
    for slug in sorted(slugs):
        start = time.time()
        try:
            resp = client.chat.completions.create(
                model=slug,
                messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                max_tokens=16,
            )
            txt = (resp.choices[0].message.content or "").strip().replace("\n", " ")[:40]
            print(f"PASS  {slug:42s} {time.time()-start:5.1f}s  «{txt}»")
            ok.append(slug)
        except Exception as e:
            msg = str(e).replace("\n", " ")[:140]
            print(f"FAIL  {slug:42s}        {msg}")
            fail.append((slug, msg))

    print(f"\n{len(ok)} passed, {len(fail)} failed.")
    if fail:
        print("\nFailures (slug -> arms that use it):")
        for slug, msg in fail:
            print(f"  - {slug}\n      error: {msg}\n      used by: {', '.join(slugs[slug])}")
        sys.exit(2)
    print("\nAll model endpoints are responsive.")


if __name__ == "__main__":
    main()
