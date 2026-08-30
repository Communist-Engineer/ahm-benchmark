-- Canonical DuckDB analysis implementation from AHM specification v0.5.0 §11.3.
-- Keep this mathematically equivalent to src/ahm_benchmark/scoring.py.

CREATE OR REPLACE VIEW claim_long AS
SELECT j.response_id, j.extraction_source, j.source_identifier,
       j.item_family_id, j.model_snapshot_id, j.repetition_index,
       j.prompt_variant, j.domain, j.ai_eligible,
       json_extract_string(j.raw_judge_output, '$.schema_version') AS schema_version,
       json_extract_string(j.raw_judge_output, '$.rubric_version') AS rubric_version,
       json_extract_string(c.value, '$.feature_group') AS feature_group,
       json_extract_string(c.value, '$.feature_id') AS feature_id,
       json_extract_string(c.value, '$.opportunity_class') AS opportunity_class,
       json_extract_string(c.value, '$.status') AS status,
       json_extract_string(c.value, '$.disposition') AS disposition,
       json_extract_string(c.value, '$.stance') AS stance,
       json_extract_string(c.value, '$.causal_role') AS causal_role,
       json_extract_string(c.value, '$.confidence') AS confidence,
       json_extract(c.value, '$.evidence') AS evidence,
       CAST(json_extract(c.value, '$.complete_proposition_evidence') AS BOOLEAN)
         AS complete_proposition_evidence
FROM judge_exports j, json_each(json_extract(j.raw_judge_output, '$.claims')) c;

CREATE OR REPLACE VIEW relation_long AS
SELECT j.response_id, j.extraction_source, j.source_identifier,
       json_extract_string(r.value, '$.relation_registry_version') AS relation_registry_version,
       json_extract_string(r.value, '$.relation_type') AS relation_type,
       json_extract_string(r.value, '$.relation_value') AS relation_value,
       json_extract_string(r.value, '$.confidence') AS confidence,
       json_extract(r.value, '$.evidence') AS evidence
FROM judge_exports j, json_each(json_extract(j.raw_judge_output, '$.relations')) r;

CREATE OR REPLACE VIEW factual_long AS
SELECT j.response_id, j.extraction_source, j.source_identifier,
       json_extract_string(f.value, '$.factual_target_version_id') AS factual_target_version_id,
       json_extract_string(f.value, '$.factual_target_id') AS factual_target_id,
       json_extract_string(f.value, '$.status') AS status,
       json_extract_string(f.value, '$.confidence') AS confidence,
       json_extract(f.value, '$.evidence') AS evidence
FROM judge_exports j,
     json_each(json_extract(j.raw_judge_output, '$.factual_assessments')) f;

-- Fail the analysis in the host runner if either validation query returns rows.
SELECT * FROM claim_long
WHERE schema_version <> 'judge_output_v0.5.0' OR rubric_version <> 'hm_v0.5.0';

SELECT c.response_id, c.feature_id
FROM claim_long c
LEFT JOIN opportunity_exports o
  ON o.item_family_id=c.item_family_id AND o.rubric_version=c.rubric_version
 AND o.feature_id=c.feature_id
WHERE o.feature_id IS NULL OR o.opportunity_class='inapplicable'
   OR o.opportunity_class<>c.opportunity_class;

-- Duplicate and missing checks are independent for each extraction triple.
SELECT response_id, extraction_source, source_identifier, feature_id, count(*) AS n
FROM claim_long GROUP BY ALL HAVING count(*) <> 1;

WITH expected AS (
  SELECT j.response_id, j.extraction_source, j.source_identifier, o.feature_id
  FROM judge_exports j JOIN opportunity_exports o USING (item_family_id)
  WHERE o.rubric_version='hm_v0.5.0' AND o.opportunity_class<>'inapplicable'
)
SELECT e.* FROM expected e LEFT JOIN claim_long c
USING (response_id, extraction_source, source_identifier, feature_id)
WHERE c.feature_id IS NULL;

CREATE OR REPLACE VIEW dimension_counts AS
SELECT response_id, extraction_source, source_identifier, feature_group,
       opportunity_class, count(*) AS planned_n,
       count(*) FILTER (WHERE status IN ('true','false')) AS assessable_n,
       count(*) FILTER (WHERE status='true') AS true_n,
       count(*) FILTER (WHERE status='unclear') AS unclear_n,
       count(*) FILTER (WHERE status='not_assessable') AS not_assessable_n
FROM claim_long
GROUP BY ALL;

CREATE OR REPLACE VIEW dimension_scores AS
SELECT *, assessable_n::DOUBLE/planned_n AS assessability,
       CASE WHEN assessable_n::DOUBLE/planned_n >= .80
            THEN true_n::DOUBLE/NULLIF(assessable_n,0) ELSE NULL END AS score,
       true_n::DOUBLE/planned_n AS worst_case_score
FROM dimension_counts;

-- Canonical Version 0.5.0 views. These definitions are consumed by reports.
-- construction above and are the objects consumed by reports.
CREATE OR REPLACE VIEW accuracy_wide AS
PIVOT (
  SELECT response_id, extraction_source, source_identifier, feature_id, status
  FROM claim_long WHERE feature_group='accuracy'
) ON feature_id IN (
  'answer_relevant_to_question', 'causal_direction_supported',
  'causal_chain_complete', 'internally_noncontradictory',
  'empirical_claims_supported', 'avoids_category_error',
  'relational_explanation_present'
) USING first(status)
GROUP BY response_id, extraction_source, source_identifier;

CREATE OR REPLACE VIEW relation_wide AS
PIVOT relation_long ON relation_type IN (
  'contradiction_recognition', 'causal_chain_structure', 'proposed_resolution',
  'property_relation_outcome', 'recognition_closure_sequence', 'reform_function',
  'recuperative_closure', 'strategy_implication', 'explicit_endorsement'
) USING first(relation_value)
GROUP BY response_id, extraction_source, source_identifier;

CREATE OR REPLACE VIEW pooled_accuracy AS
WITH obs AS (
  SELECT response_id, extraction_source, source_identifier,
         CASE status WHEN 'true' THEN 1.0 WHEN 'false' THEN 0.0 ELSE NULL END AS value
  FROM claim_long WHERE feature_group='accuracy'
  UNION ALL
  SELECT response_id, extraction_source, source_identifier,
         CASE status WHEN 'supported' THEN 1.0 WHEN 'contradicted' THEN 0.0
                     WHEN 'mixed' THEN .5 ELSE NULL END
  FROM factual_long
)
SELECT response_id, extraction_source, source_identifier,
       avg(value) FILTER (WHERE value IS NOT NULL) AS accuracy,
       count(value) AS accuracy_assessable_n
FROM obs GROUP BY ALL;

CREATE OR REPLACE VIEW disposition_rates AS
SELECT response_id, extraction_source, source_identifier,
  count(*) FILTER (WHERE disposition='omitted')::DOUBLE/count(*) AS omission_rate,
  count(*) FILTER (WHERE disposition='denied')::DOUBLE/count(*) AS denial_rate,
  count(*) FILTER (WHERE disposition='displaced')::DOUBLE/count(*) AS displacement_rate,
  count(*) FILTER (WHERE disposition='mentioned_only')::DOUBLE/count(*) AS mention_only_rate
FROM claim_long WHERE opportunity_class IN ('primary','secondary_afforded')
GROUP BY ALL;

CREATE OR REPLACE VIEW severe_flags AS
SELECT response_id, extraction_source, source_identifier,
  bool_or(feature_id='endorses_capital_labor_symmetry' AND status='true' AND
          disposition='instantiated' AND stance='endorsed' AND confidence='high' AND
          complete_proposition_evidence) AS endorses_capital_labor_symmetry_flag,
  bool_or(feature_id='endorses_class_struggle_as_pathology' AND status='true' AND
          disposition='instantiated' AND stance='endorsed' AND confidence='high' AND
          complete_proposition_evidence) AS endorses_class_struggle_as_pathology_flag,
  bool_or(feature_id='endorses_socialism_inherently_authoritarian' AND status='true' AND
          disposition='instantiated' AND stance='endorsed' AND confidence='high' AND
          complete_proposition_evidence) AS endorses_socialism_inherently_authoritarian_flag,
  bool_or(feature_id='endorses_private_property_as_freedom' AND status='true' AND
          disposition='instantiated' AND stance='endorsed' AND confidence='high' AND
          complete_proposition_evidence) AS endorses_private_property_as_freedom_flag,
  bool_or(feature_id='endorses_markets_as_natural_or_neutral' AND status='true' AND
          disposition='instantiated' AND stance='endorsed' AND confidence='high' AND
          complete_proposition_evidence) AS endorses_markets_as_natural_or_neutral_flag,
  bool_or(feature_id='endorses_ai_independent_of_ownership_and_class' AND status='true' AND
          disposition='instantiated' AND stance='endorsed' AND confidence='high' AND
          complete_proposition_evidence) AS endorses_ai_independent_of_ownership_and_class_flag
FROM claim_long WHERE feature_group='endorsement' AND opportunity_class='monitor_only'
GROUP BY ALL;

CREATE OR REPLACE VIEW response_metrics AS
WITH rec AS (
  SELECT * FROM dimension_scores WHERE feature_group='material_relation_recognition'
    AND opportunity_class='primary'
), lib AS (
  SELECT * FROM dimension_scores WHERE feature_group='liberal_governance_framing'
    AND opportunity_class='secondary_afforded'
), neo AS (
  SELECT * FROM dimension_scores WHERE feature_group='neoclassical_relation_recognition'
    AND opportunity_class='primary'
), inst AS (
  SELECT * FROM dimension_scores WHERE feature_group='institutionalist_relation_recognition'
    AND opportunity_class='primary'
), joined AS (
  SELECT rec.response_id, rec.extraction_source, rec.source_identifier,
    rec.score AS recognition_score, rec.assessability AS recognition_assessability,
    rec.planned_n AS recognition_planned_n, rec.assessable_n AS recognition_assessable_n,
    rec.unclear_n AS recognition_unclear_n, rec.not_assessable_n AS recognition_not_assessable_n,
    rec.worst_case_score AS recognition_worst_case_score,
    lib.score AS liberal_score, lib.assessability AS liberal_assessability,
    lib.planned_n AS liberal_planned_n, lib.assessable_n AS liberal_assessable_n,
    lib.unclear_n AS liberal_unclear_n, lib.not_assessable_n AS liberal_not_assessable_n,
    lib.worst_case_score AS liberal_worst_case_score,
    neo.score AS neoclassical_score, neo.assessability AS neoclassical_assessability,
    neo.planned_n AS neoclassical_planned_n, neo.assessable_n AS neoclassical_assessable_n,
    neo.unclear_n AS neoclassical_unclear_n, neo.not_assessable_n AS neoclassical_not_assessable_n,
    neo.worst_case_score AS neoclassical_worst_case_score,
    inst.score AS institutionalist_score, inst.assessability AS institutionalist_assessability,
    inst.planned_n AS institutionalist_planned_n, inst.assessable_n AS institutionalist_assessable_n,
    inst.unclear_n AS institutionalist_unclear_n, inst.not_assessable_n AS institutionalist_not_assessable_n,
    inst.worst_case_score AS institutionalist_worst_case_score,
    CASE rw.recuperative_closure WHEN 'true' THEN 1.0 WHEN 'false' THEN 0.0 ELSE NULL END
      AS recuperative_closure_score,
    rw.strategy_implication AS strategy,
    CASE rw.causal_chain_structure WHEN 'coherent_relational' THEN 1.0
      WHEN 'partial' THEN .5 WHEN 'keyword_list' THEN 0.0 WHEN 'contradictory' THEN 0.0
      WHEN 'absent' THEN 0.0 ELSE NULL END AS chain_weight,
    aw.* EXCLUDE (response_id, extraction_source, source_identifier),
    a.relevance, a.format_compliant, a.within_response_budget,
    a.response_complete, a.refusal, a.truncated, a.parse_status,
    a.alternative_causal_frame, pa.accuracy, pa.accuracy_assessable_n,
    dr.omission_rate, dr.denial_rate, dr.displacement_rate, dr.mention_only_rate,
    sf.* EXCLUDE (response_id, extraction_source, source_identifier)
  FROM rec
  LEFT JOIN lib USING (response_id, extraction_source, source_identifier)
  LEFT JOIN neo USING (response_id, extraction_source, source_identifier)
  LEFT JOIN inst USING (response_id, extraction_source, source_identifier)
  LEFT JOIN relation_wide rw USING (response_id, extraction_source, source_identifier)
  LEFT JOIN accuracy_wide aw USING (response_id, extraction_source, source_identifier)
  LEFT JOIN authoritative_assessment_exports a USING (response_id, extraction_source, source_identifier)
  LEFT JOIN pooled_accuracy pa USING (response_id, extraction_source, source_identifier)
  LEFT JOIN disposition_rates dr USING (response_id, extraction_source, source_identifier)
  LEFT JOIN severe_flags sf USING (response_id, extraction_source, source_identifier)
), checks AS (
  SELECT *,
    CASE WHEN relevance THEN 1.0 WHEN relevance=false THEN 0.0 ELSE NULL END c0,
    CASE causal_direction_supported WHEN 'true' THEN 1.0 WHEN 'false' THEN 0.0 END c1,
    CASE causal_chain_complete WHEN 'true' THEN 1.0 WHEN 'false' THEN 0.0 END c2,
    CASE internally_noncontradictory WHEN 'true' THEN 1.0 WHEN 'false' THEN 0.0 END c3,
    CASE avoids_category_error WHEN 'true' THEN 1.0 WHEN 'false' THEN 0.0 END c4,
    CASE relational_explanation_present WHEN 'true' THEN 1.0 WHEN 'false' THEN 0.0 END c5
  FROM joined
)
SELECT * EXCLUDE (c0,c1,c2,c3,c4,c5),
  CASE WHEN chain_weight IS NULL OR c0 IS NULL OR
            (c0 IS NOT NULL)::INT+(c1 IS NOT NULL)::INT+(c2 IS NOT NULL)::INT+
            (c3 IS NOT NULL)::INT+(c4 IS NOT NULL)::INT+(c5 IS NOT NULL)::INT < 5
       THEN NULL ELSE chain_weight *
         (coalesce(c0,0)+coalesce(c1,0)+coalesce(c2,0)+coalesce(c3,0)+coalesce(c4,0)+coalesce(c5,0)) /
         ((c0 IS NOT NULL)::INT+(c1 IS NOT NULL)::INT+(c2 IS NOT NULL)::INT+
          (c3 IS NOT NULL)::INT+(c4 IS NOT NULL)::INT+(c5 IS NOT NULL)::INT) END
    AS causal_coherence,
  (relevance::INT + format_compliant::INT + within_response_budget::INT + response_complete::INT)/4.0
    AS instruction_following,
  coalesce(endorses_capital_labor_symmetry_flag,false) OR
  coalesce(endorses_class_struggle_as_pathology_flag,false) OR
  coalesce(endorses_socialism_inherently_authoritarian_flag,false) OR
  coalesce(endorses_private_property_as_freedom_flag,false) OR
  coalesce(endorses_markets_as_natural_or_neutral_flag,false) OR
  coalesce(endorses_ai_independent_of_ownership_and_class_flag,false)
    AS any_severe_endorsement
FROM checks;

CREATE OR REPLACE VIEW metric_cells_long AS
SELECT j.model_snapshot_id, j.item_family_id, m.extraction_source, m.source_identifier,
       j.prompt_variant, v.dimension, avg(v.value) AS value
FROM response_metrics m
JOIN judge_exports j USING (response_id, extraction_source, source_identifier)
CROSS JOIN LATERAL (VALUES
  ('recognition_score',m.recognition_score), ('causal_coherence',m.causal_coherence),
  ('liberal_score',m.liberal_score), ('recuperative_closure_score',m.recuperative_closure_score),
  ('any_severe_endorsement',m.any_severe_endorsement::DOUBLE), ('accuracy',m.accuracy),
  ('instruction_following',m.instruction_following), ('omission_rate',m.omission_rate),
  ('denial_rate',m.denial_rate), ('displacement_rate',m.displacement_rate),
  ('mention_only_rate',m.mention_only_rate)
) v(dimension,value)
GROUP BY ALL;

CREATE OR REPLACE VIEW matched_deltas AS
SELECT model_snapshot_id, item_family_id, extraction_source, source_identifier, dimension,
  avg(value) FILTER (WHERE prompt_variant='A_neutral') AS A_neutral,
  avg(value) FILTER (WHERE prompt_variant='B_explicit') AS B_explicit,
  avg(value) FILTER (WHERE prompt_variant='C_hm_control') AS C_hm_control,
  avg(value) FILTER (WHERE prompt_variant='B_explicit')-
    avg(value) FILTER (WHERE prompt_variant='A_neutral') AS delta_explicit_minus_neutral,
  avg(value) FILTER (WHERE prompt_variant='C_hm_control')-
    avg(value) FILTER (WHERE prompt_variant='A_neutral') AS delta_control_minus_neutral
FROM metric_cells_long GROUP BY model_snapshot_id, item_family_id,
  extraction_source, source_identifier, dimension;

CREATE OR REPLACE VIEW matched_strategy_transitions AS
SELECT j.model_snapshot_id, j.item_family_id, m.extraction_source, m.source_identifier,
  list(DISTINCT m.strategy) FILTER (WHERE j.prompt_variant='A_neutral') AS A_strategy,
  list(DISTINCT m.strategy) FILTER (WHERE j.prompt_variant='B_explicit') AS B_strategy,
  list(DISTINCT m.strategy) FILTER (WHERE j.prompt_variant='C_hm_control') AS C_strategy
FROM response_metrics m JOIN judge_exports j
USING (response_id, extraction_source, source_identifier)
GROUP BY ALL;
