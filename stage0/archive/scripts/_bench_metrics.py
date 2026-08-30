#!/usr/bin/env python3
"""Extract benchmark metrics from a monolithic judge output row or a granular
reduced row, against its v7-patched request. Session helper for the iteration
report (not a Stage-0 deliverable)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_stage0_granular_judge_swarm as swarm

ABSENT = {"absent","not_applicable","not_assessable","none","indeterminate","unclear"}

def load_reqs(path):
    out={}
    for line in open(path):
        if not line.strip(): continue
        r=json.loads(line)
        pr=swarm.parse_request(r,220,12,6,400)
        out[pr.custom_id]=(r,pr)
    return out

def metrics_from_jo(jo, pr, banks, groups):
    claims=jo.get("claims",[]); rels=jo.get("relations",[]); facts=jo.get("factual_assessments",[])
    mr=pr.model_response
    true_c=[c for c in claims if c.get("status")=="true"]
    ev_spans=[s for c in claims for s in (c.get("evidence") or []) if s]
    exact_ok=sum(1 for c in claims for s in (c.get("evidence") or []) if s and s in mr)
    exact_bad=sum(1 for c in claims for s in (c.get("evidence") or []) if s and s not in mr)
    # feature-specific alignment (exclude accuracy)
    fs_ok=fs_bad=0
    for c in claims:
        fid=c.get("feature_id"); grp=groups.get(fid)
        if grp=="accuracy": continue
        for s in (c.get("evidence") or []):
            if not s: continue
            if s in (banks.get(fid) or []): fs_ok+=1
            else: fs_bad+=1
    bad_causal=sum(1 for c in claims if c.get("causal_role")=="cause" and c.get("status")!="true")
    nonabsent=sum(1 for r in rels if r.get("relation_value") not in ABSENT)
    return dict(claims=len(claims), true_claims=len(true_c), ev_spans=len(ev_spans),
                exact_ok=exact_ok, exact_bad=exact_bad, fs_ok=fs_ok, fs_bad=fs_bad,
                bad_causal=bad_causal, relations=len(rels), nonabsent_relations=nonabsent,
                factual=len(facts),
                factual_valid=sum(1 for f in facts if f.get("factual_target_id") not in (None,"none") and f.get("factual_target_version_id")))

def main():
    kind=sys.argv[1]          # 'mono' or 'granular'
    reqfile=sys.argv[2]; outfile=sys.argv[3]
    reqs=load_reqs(reqfile)
    for line in open(outfile):
        if not line.strip(): continue
        row=json.loads(line)
        cid=row.get("custom_id")
        if cid not in reqs:
            print("no request for",cid); continue
        r,pr=reqs[cid]; banks=pr.feature_banks; groups={f["feature_id"]:f["feature_group"] for f in pr.features}
        jo=row.get("judge_output",{})
        att=row.get("attempts",[{}])[-1] if kind=="mono" else {}
        m=metrics_from_jo(jo,pr,banks,groups)
        extra=""
        if kind=="mono":
            ru=att.get("raw_response",{}); u=ru.get("usage",{})
            fr=ru.get("choices",[{}])[0].get("finish_reason")
            el=att.get("elapsed_ms")
            extra=f" finish={fr} completion_tokens={u.get('completion_tokens')} elapsed_ms={el}"
        elif kind=="granular":
            g=row.get("granular",{})
            extra=f" row_ok={row.get('ok')} jobs_ok={g.get('n_ok')}/{g.get('n_jobs')} failed={g.get('n_failed')}"
        print(f"[{cid[:42]}] "+json.dumps(m)+extra)

if __name__=="__main__":
    main()
