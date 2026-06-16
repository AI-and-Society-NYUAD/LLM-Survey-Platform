#!/usr/bin/env python3
"""Analyze the persuasion pilot(s). Self-contained (does NOT import survey.py).

Pass one or more directories of participant JSON files; the script buckets each
record by its `prompt_mode` field (explicit/base), so you can mix folders freely
to COMBINE OLD + NEW pilots, e.g.:

    python3 analyze_pilot.py ~/Desktop/pilot_data/results_explicit \
        ~/Desktop/pilot_data/results_base ./new_pull/results_explicit ./new_pull/results_base

With no args it defaults to ./results_explicit and ./results_base.

Records are de-duplicated by (prolificPID, prompt_mode), keeping the latest
`created` (so re-pulling a superset of an earlier snapshot is safe). Outcome
analysis uses only real 24-char Prolific IDs with `completed` set; test IDs and
the _assignments.json file are excluded automatically.
"""
import sys, os, glob, json, re, math
from collections import Counter, defaultdict

PROLIFIC = re.compile(r'^[A-Za-z0-9]{24}$')
# Which Support/Oppose answer is the CONSERVATIVE side of each item (index = item_index).
CONSERVATIVE_STANCE = {
    "gun_control": {0: "Support", 1: "Oppose"},   # easier concealed-carry / ban assault rifles
    "immigration": {0: "Support", 1: "Support"},  # cut legal immigration / +border spending
    "police":      {0: "Oppose",  1: "Oppose"},   # end 1033 program / misconduct registry
    "taxes":       {0: "Oppose",  1: "Oppose"},   # wealth tax / inheritance tax
}
TOPICS = list(CONSERVATIVE_STANCE)

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def party3(p):
    p = (p or '').lower()
    return 'Dem' if 'democrat' in p else 'Rep' if 'republican' in p else 'Ind' if 'independent' in p else 'NA'

def ideo3(p):
    p = (p or '').lower()
    return 'Con' if 'conservative' in p else 'Lib' if 'liberal' in p else 'Mod'

def M(xs):
    xs = [x for x in xs if x is not None]
    return (round(sum(xs)/len(xs), 2), len(xs)) if xs else ('-', 0)

def cc(d, tk):
    """Conservative-coded post stance for topic tk: 1=chose conservative side, 0=liberal, None=missing."""
    t = d.get('topics', {}).get(tk, {}); post = t.get('post', {}); ii = post.get('item_index')
    if not isinstance(ii, int): return None
    r = post.get('responses', {}).get(str(ii)) or post.get('responses', {}).get(ii)
    return (1 if r == CONSERVATIVE_STANCE[tk][ii] else 0) if r in ('Support', 'Oppose') else None

def load(dirs):
    """Load + de-dup by (pid, prompt_mode), keeping the latest `created`."""
    best = {}
    for dirn in dirs:
        for f in glob.glob(os.path.join(dirn, '*.json')):
            if f.endswith('.lock'): continue
            pid = os.path.basename(f)[:-5]
            if pid == '_assignments': continue
            try: d = json.load(open(f))
            except Exception: continue
            mode = d.get('prompt_mode') or ('base' if 'base' in dirn else 'explicit')
            key = (pid, mode)
            if key not in best or (d.get('created', 0) or 0) >= (best[key].get('created', 0) or 0):
                best[key] = d
    out = defaultdict(list)
    for (pid, mode), d in best.items():
        out[mode].append((pid, d))
    return out

def analyze(version, rows):
    real = [(p, d) for p, d in rows if PROLIFIC.match(p)]
    completed = [(p, d) for p, d in real if d.get('completed')]
    print(f"\n############### {version.upper()} ###############")
    print(f"files={len(rows)}  real-Prolific={len(real)}  COMPLETED={len(completed)}  "
          f"incomplete(real)={len(real)-len(completed)}")
    print("  party:", dict(Counter(party3(d['instrument'].get('party_id')) for _, d in completed)),
          "| ideo:", dict(Counter(ideo3(d['instrument'].get('ideology')) for _, d in completed)))
    print("  COMPLETED IDs (approve on Prolific):")
    print("   ", " ".join(sorted(p for p, _ in completed)))

    # topic-slots
    S = []
    for pid, d in completed:
        ins = d['instrument']
        for tk, a in d['assignment']['topics'].items():
            t = d['topics'].get(tk, {}); ch = t.get('checks', {})
            S.append(dict(party=party3(ins.get('party_id')), ideo=ideo3(ins.get('ideology')),
                          arm=a['condition'], lean=a.get('lean'), topic=tk, model=a.get('model_name'),
                          cc=cc(d, tk), agree=t.get('agrees_with_llm'),
                          persu=num(ch.get('self_reported_persuasion')), neut=num(ch.get('perceived_neutrality')),
                          trust=num(ch.get('post_trust')), use=ins.get('llm_use_legacy'),
                          tpre=num(ins.get('trust_pre')), npre=num(ins.get('perceived_neutrality_pre')),
                          pint=num(ins.get('political_interest'))))

    print("\n[PRIMARY] conservative-coded stance by arm (1=chose conservative side):")
    for arm in ('conservative', 'control', 'neutral', 'liberal'):
        print(f"   {arm:12} {M([s['cc'] for s in S if s['arm']==arm])}")
    print("[PRIMARY] agrees_with_llm by pole (post matched LLM's argued side):")
    for arm in ('conservative', 'liberal'):
        print(f"   {arm:12} {M([s['agree'] for s in S if s['arm']==arm and s['agree'] is not None])}")

    print("\n[HETEROGENEITY] conservative-coded by PARTY x arm (mean[n]):")
    print(f"   {'party':5} {'cons':>10} {'control':>10} {'liberal':>10} {'neutral':>10}")
    for pty in ('Dem', 'Ind', 'Rep'):
        cells = [M([s['cc'] for s in S if s['party']==pty and s['arm']==a]) for a in ('conservative','control','liberal','neutral')]
        print(f"   {pty:5} " + " ".join(f"{v}[{n}]".rjust(10) for v, n in cells))

    print("\n[BY TOPIC] conservative-coded (cons / control / lib):")
    for tk in TOPICS:
        c = M([s['cc'] for s in S if s['topic']==tk and s['arm']=='conservative'])
        k = M([s['cc'] for s in S if s['topic']==tk and s['arm']=='control'])
        l = M([s['cc'] for s in S if s['topic']==tk and s['arm']=='liberal'])
        print(f"   {tk:12} cons={c[0]}[{c[1]}] ctrl={k[0]}[{k[1]}] lib={l[0]}[{l[1]}]")

    print("\n[BY MODEL] agrees_with_llm (treatment arms; confounded w/ pole — supplementary):")
    for mo in sorted(set(s['model'] for s in S if s['arm'] in ('conservative','liberal') and s['model'])):
        print(f"   {mo:24} {M([s['agree'] for s in S if s['model']==mo and s['arm'] in ('conservative','liberal')])}")

    print("\n[CHECKS 1-7 by arm] perceived_neutrality / self_reported_persuasion / post_trust:")
    for arm in ('conservative', 'liberal', 'neutral'):
        print(f"   {arm:12} neut={M([s['neut'] for s in S if s['arm']==arm])[0]} "
              f"persu={M([s['persu'] for s in S if s['arm']==arm])[0]} "
              f"trust={M([s['trust'] for s in S if s['arm']==arm])[0]}")

    pn = [(num(d['instrument'].get('perceived_neutrality_pre')), num(d.get('wrapup', {}).get('perceived_neutrality_post_general'))) for _, d in completed]
    tp = [(num(d['instrument'].get('trust_pre')), num(d.get('wrapup', {}).get('trust_post_general'))) for _, d in completed]
    def delta(pairs):
        dd = [b - a for a, b in pairs if a is not None and b is not None]
        return (round(sum(dd)/len(dd), 2), len(dd)) if dd else ('-', 0)
    print("\n[GENERAL LLM BELIEFS pre->post Δ] neutrality:", delta(pn), " trust:", delta(tp))

    T = [s for s in S if s['arm'] in ('conservative', 'liberal') and s['agree'] is not None]
    freq = lambda s: 1 if s['use'] in ('Often', 'Very often') else (0 if s['use'] in ('Never', 'Rarely') else None)
    print("[MODERATORS on agrees_with_llm — confounded/underpowered, directional only]:")
    print("   LLM use infrequent/frequent:", M([s['agree'] for s in T if freq(s)==0]), M([s['agree'] for s in T if freq(s)==1]))
    print("   prior trust lo(<=4)/hi(>=5):", M([s['agree'] for s in T if s['tpre'] and s['tpre']<=4]), M([s['agree'] for s in T if s['tpre'] and s['tpre']>=5]))
    print("   prior neutrality lo/hi:", M([s['agree'] for s in T if s['npre'] and s['npre']<=4]), M([s['agree'] for s in T if s['npre'] and s['npre']>=5]))

    print("[DATA QUALITY] canary_triggered:", sum(1 for _, d in completed if d.get('canary_triggered')),
          "| fallback-model turns:", sum(1 for _, d in completed for tk in d.get('topics', {})
                                          for m in d['topics'][tk].get('transcript', []) if m.get('model')))

def main():
    dirs = sys.argv[1:] or ['results_explicit', 'results_base']
    data = load(dirs)
    print("Loaded dirs:", dirs)
    print("De-duplicated records:", {v: len(r) for v, r in data.items()})
    for v in ('explicit', 'base'):
        if data.get(v): analyze(v, data[v])
    print("\nNOTE: pilot N is small — treat as DESCRIPTIVE/directional; no significance tests. "
          "For inference use a mixed-effects logit (arm + topic + arm×topic; random intercepts "
          "for participant and assigned model; arm×party for RQ4).")

if __name__ == '__main__':
    main()
