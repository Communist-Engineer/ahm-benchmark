# Stage-0 Selene Judge — Iteration Report (v7 + Granular Swarm)

**Benchmark:** Automated Historical-Materialist Benchmark, Stage 0
**Judge endpoint:** `http://127.0.0.1:8006/v1`, model `local-judge-selene-70b-bf16`
(AtlaAI/Selene-1-Llama-3.3-70B, vLLM 0.22.1, `max_model_len=8192`, ~1.8 tok/s **per request**, up to 4 concurrent).
**Scope:** stabilize monolithic Stage-0 judge extraction (v7) and prototype a granular judge swarm.

---

## 1. Problem recap

By v6 the transport, parsing, schema-closure and throughput problems were solved. The
remaining problem was **semantic extraction quality** under the compact judge prompt:

* v4/v5: evidence was often an analytic label, not an exact response substring.
* v6: a single **global** enum of exact substrings fixed the substring problem but allowed
  **feature-misaligned** evidence — e.g. `identifies_class_antagonism` marked `true` with the
  span *"The state's role in maintaining this…"* (a state/institution span), because both spans
  were members of the same global enum.

## 2. What v7 changes

### 2.1 Feature-specific evidence banks (the core fix)
`patch_stage0_requests_for_8006_v7.py` replaces the single global evidence enum with a
**per-feature evidence bank**: for each feature we select exact substrings of `MODEL_RESPONSE`
whose lexical content matches that feature (a per-feature keyword map with a per-group fallback
for unmapped features). The claims schema then uses JSON-Schema **`prefixItems`** so that
`claim[i].evidence` is constrained to feature *i*'s own bank.

* Features with **no** aligned span get `evidence: {maxItems: 0}` → forced `[]` (correct for
  genuinely absent neoclassical / institutionalist / liberal / endorsement features in an HM
  response).
* Accuracy-group features are response-level judgments; they draw from a diverse **union bank**
  and may be judged with empty evidence (`EVIDENCE_OPTIONAL_GROUPS`).
* Candidate generation was rewritten to prefer focused **clause-level** spans (4–14 content
  words), with content-token **overlap de-duplication** so a bank holds diverse, single-concept
  spans instead of many near-identical sliding windows.

Result: the judge is now *mechanically unable* to attach a state-role span to a class-antagonism
feature — that span is not in that feature's bank/grammar.

### 2.2 status ↔ causal_role coupling (second-pass fix)
The first v7 monolithic run had perfect evidence alignment but set `causal_role="cause"` on 16–17
**non-affirmative** claims (false/omitted/not_assessable). The task forbids this. Fixed with a
two-branch **`anyOf` of complete claim objects**:

* affirmative branch: `status=true`, `disposition=instantiated`, full causal-role vocabulary;
* non-affirmative branch: `status ∈ {false,unclear,not_applicable,not_assessable}`, matching
  dispositions, `causal_role ∈ {background, unclear}`.

### 2.3 vLLM/xgrammar guided-decoding findings (important, reusable)
* `prefixItems` with per-position `enum`s **is** honoured — enables per-feature evidence banks.
* `if/then/else` (and `allOf`+`if`) is **not** honoured; worse, its presence **silently disables
  the whole grammar** (the model then emitted a bare boolean for a string-enum field). Never use
  conditional schemas with this backend.
* A top-level `anyOf` **replaces** the object schema rather than refining it: `{...properties...,
  required..., anyOf:[{properties:{status:...}}]}` produced objects containing *only* `status`.
  The fix is to make **each `anyOf` branch a complete object schema**. So-constructed `anyOf` is
  correctly enforced by xgrammar.
* The response-format JSON schema is **not** counted in `prompt_tokens`, so per-feature banks can
  live entirely in the schema and keep the prompt small (≈2.3–2.7k prompt tokens; prompt+max well
  under 8192).

### 2.4 Validator changes (`validate_stage0_judge_outputs_v7.py`)
New flags: `--min-nonabsent-relations N` and `--require-feature-specific-evidence` (each claim's
evidence must be drawn from that feature's bank; accuracy features exempt). Also added a
`causal_role=cause` on non-affirmative-status check. Banks/groups are read from the request row
attachments (`hm_evidence_banks`, `hm_feature_groups`, `hm_evidence_optional_groups`) that the v7
patcher writes (ignored by the runner).

## 3. Granular judge swarm

`run_stage0_granular_judge_swarm.py` decomposes each row into small jobs and reassembles a
row-level `judge_output_v0.5.0`:

* `--mode feature` — one job per feature (single claim, that feature's bank).
* `--mode feature_group` — one job per feature group.
* `--mode relation` — one job per relation type (9).
* `--mode factual` — one job per factual target (none if empty).
* `--mode all` — feature + relation + factual + a semantic job, reduced to one complete row.

Job IDs: `{custom_id}::feature::{claim_index}`, `::relation::{relation_type}`,
`::factual::{factual_target_id}`. Resume is keyed by job ID; concurrency via `--workers`. Feature
evidence uses the **same** v7 banks, so alignment guarantees are preserved per job. Artifacts:
`judge_swarm_jobs.<id>.jsonl`, `judge_swarm_outputs.<id>.jsonl`,
`judge_swarm_reduced.<id>.jsonl`, `judge_swarm_report.<id>.json`.

`validate_stage0_granular_swarm.py` cross-checks the reduced rows against the request contract:
claim/relation/factual cardinality, unique contiguous claim indices, exact-substring and
feature-bank evidence, factual-target identity, surfaced (non-silent) failed jobs, and preserved
`custom_id`/metadata/request hash.

<!-- RESULTS TABLES FILLED BELOW AFTER SMOKE RUNS -->

## 4. Smoke-test results

All runs used `temperature=0, top_p=1`, `--timeout 5400 --connect-timeout 10 --retries 0`,
model `local-judge-selene-70b-bf16`. Every smoke passed its validator with the strictest flags.

| Smoke test | Command scope | Result |
| --- | --- | --- |
| Monolithic no-target | `HM04-AI-IDEO-01`, `--limit 1` | **PASS** |
| Monolithic target | `HM04-HOUS-01`, `--limit 1` | **PASS** |
| Granular no-target, all variants | `HM04-AI-IDEO-01`, `--mode all` (26 feature + 9 relation + 1 semantic jobs) | **PASS** |
| Granular target, all variants | `HM04-HOUS-01`, `--mode all` (28 feature + 9 relation + 1 factual + 1 semantic jobs) | **PASS** |
| Granular standalone modes | `--mode feature` (no-target, 4/4), `--mode relation` (no-target, 9/9), `--mode factual` (housing, 1/1) | **PASS** |

Monolithic validation (both rows):
`validate_stage0_judge_outputs_v7.py --strict-failed-rows --min-model-response-chars 100
--min-true-claims 3 --min-evidence-spans 3 --min-nonabsent-relations 1
--require-feature-specific-evidence` → `VALIDATION PASSED`.

Granular validation (both rows):
`validate_stage0_granular_swarm.py --require-feature-specific-evidence --strict-failed-jobs
--min-true-claims 3 --min-evidence-spans 3 --min-nonabsent-relations 1` → `SWARM VALIDATION PASSED`.

The first v7 monolithic pass had **perfect evidence alignment** but failed strict validation on
`causal_role=cause` for 16–17 non-affirmative claims (no_target/hous); the housing pass then
surfaced 22/28 over-marked `true` claims, 13 with empty evidence (empty-bank features taking the
affirmative branch). Both were fixed at the schema level (§2.2, §2.4) and the re-runs pass.

## 5. Monolithic v7 vs granular swarm

Same two responses (HM control, rep0). "w4" = 4 concurrent workers (the server's max).

| Metric | Mono no-target | Granular no-target | Mono housing | Granular housing |
| --- | --- | --- | --- | --- |
| Wall time | **36.1 min** (1 request) | **≈15 min** (36 jobs, w4) | **40.3 min** (1 request) | **15.4 min** (39 jobs, w4) |
| Largest single completion | 3432 tokens | 189 tokens (median 148) | 3831 tokens | 182 tokens (median 149) |
| Completion truncation | none (`stop`) | none | none (`stop`) | none |
| 8192-window headroom | ~2.3k tokens spare | ~7.8k spare per job | ~1.9k spare | ~7.8k spare per job |
| Claims / expected | 26 / 26 | 26 / 26 | 28 / 28 | 28 / 28 |
| True claims | 10 | 15 | 11 | 15 |
| Nonempty evidence spans | 10 | 15 | 11 | 16 |
| Exact-substring valid | 10/10 | 15/15 | 11/11 | 16/16 |
| Feature-specific valid | ✓ (0 bad) | ✓ (0 bad) | ✓ (0 bad) | ✓ (0 bad) |
| `causal_role=cause` on non-true | 0 | 0 | 0 | 0 |
| Non-absent relations (of 9) | 1 | 8 | 9 | 9 |
| Factual assessments | – | – | 1 valid (`not_assessable`) | 1 valid (`supported`) |
| Failed jobs / rows | 0 | 0 | 0 | 0 |

**Both paths** now produce structurally valid, exactly-cardinal output with 100 % exact-substring
and feature-specific evidence and zero `causal_role` violations — the v6 misalignment is gone in
both.

**Granular advantages (measured):**
* **~2.5× faster wall clock** for a single row (15 vs 36–40 min) by spreading the row across 4
  concurrent judge calls; monolithic is one serial ~3.4–3.8k-token generation that cannot be split.
* **~23× smaller per-call output** (≈150 vs ≈3.6k tokens), so it never approaches the 8192 window —
  the truncation risk that motivated the compaction work simply disappears.
* **More complete relation extraction**: consistently 8–9 of 9 relations non-absent, whereas the
  monolithic judge is erratic (1/9 on no-target, 9/9 on housing) — evidence that a 26-claim +
  9-relation single call under-attends to the relation block.

**Granular weakness (measured):** isolated feature jobs **over-mark stance-dependent features**.
On no-target the swarm marked `endorses_capital_labor_symmetry`, `endorses_markets_as_natural_or_neutral`,
`centers_fairness_or_opportunity`, and `centers_procedure_or_rights` as `true`/`endorsed`, citing
spans the response presents **critically** (e.g. `"fair"` in scare-quotes, the *dominant ideology*
sentence it is denouncing). The monolithic judge, seeing all features and the whole response at
once, correctly marks these `false`. A single-feature prompt lacks the holistic signal that the
response's stance is HM-critical, so it mistakes *recognition-with-criticism* for *endorsement*.
(The evidence spans are still feature-aligned and exact — the error is in `status`/`stance`, not
evidence.) The granular factual job was more decisive (`supported`) than the monolithic
(`not_assessable`) but cited a weakly-related span.

## 6. Recommendation & remaining risks

**Verdict: neither is dominant; they are complementary. Monolithic v7 is good enough to green-light
the 19-row Stage-0 pass; the granular swarm is the better long-term substrate but needs a stance-
context fix before it can own endorsement/liberal-framing scoring.**

* **Monolithic v7 — ship it for the 19-row run.** It is structurally valid, evidence-aligned,
  `causal_role`-correct, and — crucially — **conservative on the monitor-only endorsement group**,
  which is exactly where false positives would be most damaging to the benchmark's meaning. Its one
  real weakness is inconsistent relation detection.
* **Granular swarm — adopt for throughput/robustness, then fix stance.** It is ~2.5× faster,
  truncation-proof, resumable per job, and richer on relations. Before it replaces the monolith for
  stance-dependent groups, give those jobs holistic context: either (a) run `--mode feature_group`
  for `liberal_governance_framing` and `endorsement` with a one-line response-stance summary in the
  prompt, or (b) prepend a short "overall response frame" note (derivable from the material-
  recognition claims or the semantic job) to every endorsement/liberal feature job. Re-test that the
  four over-marked features flip to `false`.
* **The judge model is the real bottleneck.** Selene-70B at ~1.8 tok/s/request means a monolithic
  19-row pass is ≈12 h serial; granular at w4 is ≈5 h. If wall time matters, the highest-leverage
  moves are (i) run granular at w4, (ii) raise server concurrency above 4 if the GPU allows, or
  (iii) supplement with a faster judge for the bulk of features and reserve Selene for adjudication.

**Remaining risks**
1. **Stance over-marking in granular** (above) — must be fixed before granular scores endorsement.
2. **Small feature banks** (2–4 spans) still let the monolith mark a borderline feature `true` with
   a technically-aligned but semantically-weak span; the keyword map should be widened per family as
   more families are onboarded.
3. **Keyword-map coverage**: only `HM04-AI-IDEO-01` and `HM04-HOUS-01` feature IDs are hand-mapped;
   other families fall back to group cues. Extend `FEATURE_KEYWORDS` before those families' runs.
4. **Contention**: >4 concurrent requests queue and inflate latency; keep total in-flight ≤ 4.
5. **Factual depth**: with one complete packet the judge can drift to `not_assessable`
   (monolith) or cite a weak span (granular); factual scoring deserves a dedicated evidence bank
   built from the packet proposition, not the shared union bank.

### Exact next command (only after these smokes — which passed)
The smokes justify a **granular** full Stage-0 pass (fastest, truncation-proof). First patch all 19
families, then run the swarm at 4 workers with resume:

```bash
cd stage0
# 1. Patch every family (targets file supplies the factual packets):
python3 patch_stage0_requests_for_8006_v7.py \
  --input judge_requests.selene_litellm.jsonl \
  --output judge_requests.selene_litellm.8006.v7.all.jsonl \
  --targets factual_targets.stage0.source_backed.downloaded.json \
  --model local-judge-selene-70b-bf16 \
  --max-tokens 4600 --target-max-tokens 4300 \
  --evidence-max-len 220 --evidence-min-len 12 --evidence-per-feature 6
# 2. Granular full pass (resumable; ~5 h at 1.8 tok/s/req, 4 concurrent):
python3 run_stage0_granular_judge_swarm.py \
  --input judge_requests.selene_litellm.8006.v7.all.jsonl \
  --run-id stage0.v7.full --mode all --workers 4 --resume \
  --base-url http://127.0.0.1:8006/v1 --model local-judge-selene-70b-bf16 \
  --timeout 5400 --connect-timeout 10 --retries 0
# 3. Validate the reassembled rows:
python3 validate_stage0_granular_swarm.py \
  --requests judge_requests.selene_litellm.8006.v7.all.jsonl \
  --reduced judge_swarm_reduced.stage0.v7.full.jsonl \
  --outputs judge_swarm_outputs.stage0.v7.full.jsonl \
  --require-feature-specific-evidence --strict-failed-jobs \
  --min-true-claims 3 --min-evidence-spans 3 --min-nonabsent-relations 1
```

For a **monolithic** full pass instead (conservative on endorsement, but ~12 h serial and one
request near the token ceiling), swap step 2 for
`run_stage0_judge.py --input judge_requests.selene_litellm.8006.v7.all.jsonl
--output judge_outputs.selene_litellm.8006.v7.all.jsonl --workers 4 --resume` and validate with
`validate_stage0_judge_outputs_v7.py`. Treat endorsement/liberal `true` marks from the granular
pass as provisional until the stance-context fix lands.
