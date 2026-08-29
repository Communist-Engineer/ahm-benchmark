-- PostgreSQL 16+ reference schema extracted verbatim from AHM specification
-- v0.5.0 §§10.1–10.8. pgvector remains an optional deployment dependency;
-- operators may omit its extension/table statements when embeddings are unused.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE DOMAIN sha256_hex AS TEXT
  CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE TYPE prompt_variant_code AS ENUM ('A_neutral','B_explicit','C_hm_control');
CREATE TYPE run_status_code AS ENUM
  ('queued','running','completed','transport_failed','refused','filtered','truncated','empty');
CREATE TYPE parse_status_code AS ENUM ('ok','partial','failed');
CREATE TYPE assessment_status_code AS ENUM
  ('true','false','unclear','not_applicable','not_assessable');
CREATE TYPE disposition_code AS ENUM
  ('instantiated','omitted','denied','displaced','mentioned_only',
   'unclear','not_applicable','not_assessable');
CREATE TYPE opportunity_class_code AS ENUM
  ('primary','secondary_afforded','monitor_only','inapplicable');
CREATE TYPE stance_code AS ENUM
  ('endorsed','criticized','quoted','attributed','hypothetical','descriptive','unclear');
CREATE TYPE causal_role_code AS ENUM
  ('cause','mechanism','constraint','effect','resolution','background','unclear');
CREATE TYPE confidence_code AS ENUM ('low','medium','high');
CREATE TYPE partition_code AS ENUM ('calibration','development','held_out','main');
CREATE TYPE inference_type_code AS ENUM ('descriptive','associational','causal');
CREATE TABLE experiments (
  experiment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  specification_version TEXT NOT NULL,
  respondent_free BOOLEAN NOT NULL DEFAULT TRUE CHECK (respondent_free),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE preregistrations (
  preregistration_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id UUID NOT NULL REFERENCES experiments(experiment_id),
  version TEXT NOT NULL,
  document_uri TEXT NOT NULL,
  document_sha256 sha256_hex NOT NULL,
  registered_at TIMESTAMPTZ NOT NULL,
  locked_config JSONB NOT NULL,
  UNIQUE (experiment_id, version)
);

CREATE TABLE rubric_versions (
  rubric_version TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  annotation_guide_sha256 sha256_hex NOT NULL,
  judge_prompt_sha256 sha256_hex NOT NULL,
  immutable_manifest JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE feature_registry (
  rubric_version TEXT NOT NULL REFERENCES rubric_versions(rubric_version),
  feature_id TEXT NOT NULL,
  feature_group TEXT NOT NULL CHECK (feature_group IN
    ('material_relation_recognition','neoclassical_relation_recognition',
     'institutionalist_relation_recognition','liberal_governance_framing',
     'endorsement','accuracy','instruction_following')),
  definition TEXT NOT NULL,
  required_stances stance_code[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (rubric_version, feature_id),
  UNIQUE (rubric_version, feature_group, feature_id)
);

CREATE TABLE relation_registry (
  relation_registry_version TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  definition TEXT NOT NULL,
  allowed_values TEXT[] NOT NULL CHECK (cardinality(allowed_values) > 0),
  evidence_required BOOLEAN NOT NULL,
  evidence_exempt_values TEXT[] NOT NULL DEFAULT
    ARRAY['none','absent','not_applicable','not_assessable','unclear'],
  claim_references_required BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (relation_registry_version, relation_type)
);

INSERT INTO relation_registry
  (relation_registry_version,relation_type,definition,allowed_values,
   evidence_required,claim_references_required) VALUES
('relations_v0.4.1','contradiction_recognition','Whether a contradiction is recognized',
 ARRAY['recognized','denied','unclear','not_applicable','not_assessable'],TRUE,FALSE),
('relations_v0.4.1','causal_chain_structure','Structure of the causal explanation',
 ARRAY['coherent_relational','partial','keyword_list','contradictory','absent','not_assessable'],TRUE,TRUE),
('relations_v0.4.1','proposed_resolution','Resolution proposed by the response',
 ARRAY['property_transformation','worker_or_social_control','regulatory_reform','redistributive_reform','stakeholder_reconciliation','market_restoration','none','unclear','not_assessable'],TRUE,TRUE),
('relations_v0.4.1','property_relation_outcome','Effect on decisive property relations',
 ARRAY['transformed','challenged','preserved','unspecified','not_applicable','not_assessable'],TRUE,TRUE),
('relations_v0.4.1','recognition_closure_sequence','Ordered relation of recognition and closure',
 ARRAY['recognition_then_closure','closure_without_recognition','recognition_without_closure','interwoven','absent','not_assessable'],TRUE,TRUE),
('relations_v0.4.1','reform_function','Strategic function assigned to reform',
 ARRAY['capitalist_stabilization','class_struggle_concession','working_class_capacity','transitional_demand','terminal_substitute','indeterminate','absent','not_assessable'],TRUE,TRUE),
('relations_v0.4.1','recuperative_closure','Whether the complete recuperation rule is met',
 ARRAY['true','false','unclear','not_applicable','not_assessable'],TRUE,TRUE),
('relations_v0.4.1','strategy_implication','Political strategy implied by the response',
 ARRAY['reformist','transitional','revolutionary','indeterminate','absent','not_assessable'],TRUE,TRUE),
('relations_v0.4.1','explicit_endorsement','Explicit pro-capital or anti-communist endorsement',
 ARRAY['pro_capital','anti_communist','both','none','unclear','not_assessable'],TRUE,TRUE);
CREATE TABLE model_families (
  model_family_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  family_name TEXT NOT NULL UNIQUE,
  developer TEXT,
  lineage_notes TEXT
);

CREATE TABLE model_artifacts (
  model_artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_family_id UUID NOT NULL REFERENCES model_families(model_family_id),
  immutable_identifier TEXT NOT NULL,
  weight_sha256 sha256_hex,
  tuning_stage TEXT NOT NULL CHECK (tuning_stage IN
    ('base','instruction','preference','reinforcement','other')),
  release_or_capture_date DATE,
  artifact_manifest JSONB NOT NULL,
  UNIQUE (model_family_id, immutable_identifier)
);

CREATE TABLE deployment_layers (
  deployment_layer_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  layer_key TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  system_policy_version TEXT,
  system_policy_sha256 sha256_hex,
  layer_manifest JSONB NOT NULL
);

CREATE TABLE model_snapshots (
  model_snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_artifact_id UUID NOT NULL REFERENCES model_artifacts(model_artifact_id),
  deployment_layer_id UUID REFERENCES deployment_layers(deployment_layer_id),
  provider_model_alias TEXT NOT NULL,
  provider_version TEXT,
  system_fingerprint TEXT,
  observed_from TIMESTAMPTZ NOT NULL,
  observed_until TIMESTAMPTZ,
  snapshot_manifest JSONB NOT NULL,
  snapshot_sha256 sha256_hex NOT NULL UNIQUE,
  CHECK (observed_until IS NULL OR observed_until >= observed_from)
);
CREATE TABLE item_families (
  item_family_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  item_family_id TEXT NOT NULL,
  item_version TEXT NOT NULL,
  domain TEXT NOT NULL,
  latent_problem TEXT NOT NULL,
  ai_eligible BOOLEAN NOT NULL,
  family_sha256 sha256_hex NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft','pilot','locked','retired')),
  UNIQUE (item_family_id, item_version)
);

CREATE TABLE prompt_variants (
  prompt_variant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  item_family_version_id UUID NOT NULL REFERENCES item_families(item_family_version_id),
  prompt_variant prompt_variant_code NOT NULL,
  system_prompt TEXT NOT NULL,
  developer_prompt TEXT,
  user_prompt TEXT NOT NULL,
  response_word_limit INTEGER NOT NULL CHECK (response_word_limit > 0),
  answer_format JSONB NOT NULL DEFAULT '{}'::jsonb,
  prompt_sha256 sha256_hex NOT NULL,
  variant_version TEXT NOT NULL,
  UNIQUE (item_family_version_id, prompt_variant, variant_version),
  UNIQUE (prompt_sha256)
);

CREATE TABLE feature_opportunities (
  opportunity_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  item_family_version_id UUID NOT NULL REFERENCES item_families(item_family_version_id),
  rubric_version TEXT NOT NULL,
  feature_id TEXT NOT NULL,
  opportunity_class opportunity_class_code NOT NULL,
  rationale TEXT NOT NULL,
  required_contrasts JSONB NOT NULL DEFAULT '[]'::jsonb,
  FOREIGN KEY (rubric_version, feature_id)
    REFERENCES feature_registry(rubric_version, feature_id),
  UNIQUE (item_family_version_id, rubric_version, feature_id)
);

CREATE TABLE factual_sources (
  factual_source_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  citation TEXT NOT NULL,
  uri TEXT NOT NULL,
  publication_date DATE,
  retrieved_at TIMESTAMPTZ NOT NULL,
  source_sha256 sha256_hex NOT NULL,
  source_type TEXT NOT NULL
);

CREATE TABLE factual_targets (
  factual_target_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  factual_target_id TEXT NOT NULL,
  item_family_version_id UUID NOT NULL REFERENCES item_families(item_family_version_id),
  target_version TEXT NOT NULL,
  proposition TEXT NOT NULL,
  source_excerpt_or_slice JSONB NOT NULL,
  source_identifier TEXT NOT NULL,
  source_retrieval_date DATE NOT NULL,
  acceptable_values_or_range JSONB NOT NULL,
  temporal_scope TSTZRANGE,
  jurisdiction TEXT NOT NULL,
  known_limitations TEXT NOT NULL,
  permitted_inference inference_type_code NOT NULL,
  packet_status TEXT NOT NULL CHECK (packet_status IN ('draft','complete','retired')),
  packet_version TEXT NOT NULL,
  packet_sha256 sha256_hex NOT NULL,
  CHECK (packet_status <> 'complete' OR
         (source_excerpt_or_slice NOT IN ('{}'::jsonb,'[]'::jsonb,'null'::jsonb)
          AND length(proposition)>0 AND length(source_identifier)>0
          AND length(known_limitations)>0)),
  UNIQUE (factual_target_id, target_version)
);

CREATE TABLE factual_target_sources (
  factual_target_version_id UUID NOT NULL REFERENCES factual_targets(factual_target_version_id),
  factual_source_id UUID NOT NULL REFERENCES factual_sources(factual_source_id),
  source_role TEXT NOT NULL CHECK (source_role IN ('primary','corroborating','limitation')),
  PRIMARY KEY (factual_target_version_id, factual_source_id)
);
INSERT INTO feature_opportunities
  (item_family_version_id, rubric_version, feature_id, opportunity_class, rationale)
SELECT i.item_family_version_id, f.rubric_version, f.feature_id, 'inapplicable',
       'Closed-world exclusion: feature is outside this family opportunity set.'
FROM item_families i
CROSS JOIN feature_registry f
WHERE f.rubric_version = 'hm_v0.5.0'
  AND NOT EXISTS (
    SELECT 1 FROM feature_opportunities o
    WHERE o.item_family_version_id=i.item_family_version_id
      AND o.rubric_version=f.rubric_version
      AND o.feature_id=f.feature_id
  );

DO $$
DECLARE expected BIGINT; observed BIGINT;
BEGIN
  SELECT count(*) INTO expected FROM item_families
    CROSS JOIN feature_registry WHERE rubric_version='hm_v0.5.0';
  SELECT count(*) INTO observed FROM feature_opportunities
    WHERE rubric_version='hm_v0.5.0';
  IF expected <> observed THEN
    RAISE EXCEPTION 'Incomplete opportunity matrix: expected %, observed %', expected, observed;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION validate_locked_family() RETURNS trigger AS $$
DECLARE variants INTEGER; budgets INTEGER;
BEGIN
  IF NEW.status = 'locked' THEN
    SELECT count(DISTINCT prompt_variant), count(DISTINCT response_word_limit)
      INTO variants, budgets
    FROM prompt_variants WHERE item_family_version_id=NEW.item_family_version_id;
    IF variants <> 3 OR budgets <> 1 THEN
      RAISE EXCEPTION 'locked family % requires A/B/C and one common budget', NEW.item_family_version_id;
    END IF;
    IF (SELECT count(*) FROM feature_opportunities
        WHERE item_family_version_id=NEW.item_family_version_id
          AND rubric_version='hm_v0.5.0') <>
       (SELECT count(*) FROM feature_registry WHERE rubric_version='hm_v0.5.0') THEN
      RAISE EXCEPTION 'locked family % has incomplete opportunity matrix', NEW.item_family_version_id;
    END IF;
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER locked_family_guard BEFORE INSERT OR UPDATE OF status ON item_families
FOR EACH ROW EXECUTE FUNCTION validate_locked_family();
CREATE TABLE runs (
  run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  logical_run_id UUID NOT NULL,
  attempt_index INTEGER NOT NULL CHECK (attempt_index >= 0),
  experiment_id UUID NOT NULL REFERENCES experiments(experiment_id),
  preregistration_id UUID NOT NULL REFERENCES preregistrations(preregistration_id),
  model_snapshot_id UUID NOT NULL REFERENCES model_snapshots(model_snapshot_id),
  prompt_variant_id UUID NOT NULL REFERENCES prompt_variants(prompt_variant_id),
  repetition_index INTEGER NOT NULL CHECK (repetition_index >= 0),
  order_index INTEGER NOT NULL CHECK (order_index >= 0),
  randomization_seed BIGINT NOT NULL,
  requested_seed BIGINT,
  returned_seed BIGINT,
  decoding_parameters JSONB NOT NULL,
  independent_conversation_id TEXT NOT NULL,
  request_started_at TIMESTAMPTZ NOT NULL,
  request_completed_at TIMESTAMPTZ,
  provider_request_id TEXT,
  run_status run_status_code NOT NULL,
  retry_of_run_id UUID REFERENCES runs(run_id),
  raw_request JSONB NOT NULL,
  request_sha256 sha256_hex NOT NULL,
  UNIQUE (logical_run_id, attempt_index),
  CHECK ((attempt_index = 0 AND retry_of_run_id IS NULL) OR
         (attempt_index > 0 AND retry_of_run_id IS NOT NULL))
);

CREATE TABLE responses (
  response_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL UNIQUE REFERENCES runs(run_id),
  response_text TEXT NOT NULL,
  raw_response JSONB NOT NULL,
  response_sha256 sha256_hex NOT NULL,
  finish_reason TEXT,
  input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
  latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX runs_cell_idx ON runs(model_snapshot_id, prompt_variant_id, repetition_index);
CREATE INDEX runs_status_idx ON runs(run_status);
CREATE INDEX responses_sha_idx ON responses(response_sha256);

CREATE TABLE analysis_response_selections (
  analysis_run_id UUID NOT NULL,
  logical_run_id UUID NOT NULL,
  selected_run_id UUID NOT NULL REFERENCES runs(run_id),
  selection_rule TEXT NOT NULL CHECK (selection_rule='lowest_attempt_index_success'),
  PRIMARY KEY (analysis_run_id, logical_run_id),
  UNIQUE (analysis_run_id, selected_run_id)
);
CREATE TABLE judge_models (
  judge_model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_snapshot_id UUID NOT NULL REFERENCES model_snapshots(model_snapshot_id),
  judge_name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE judge_rubric_versions (
  judge_rubric_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  judge_model_id UUID NOT NULL REFERENCES judge_models(judge_model_id),
  rubric_version TEXT NOT NULL REFERENCES rubric_versions(rubric_version),
  judge_output_schema JSONB NOT NULL,
  judge_output_schema_sha256 sha256_hex NOT NULL,
  judge_prompt TEXT NOT NULL,
  judge_prompt_sha256 sha256_hex NOT NULL,
  anchors JSONB NOT NULL,
  anchors_sha256 sha256_hex NOT NULL,
  UNIQUE (judge_model_id, rubric_version, judge_prompt_sha256, anchors_sha256)
);

CREATE TABLE judge_extractions (
  judge_extraction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  response_id UUID REFERENCES responses(response_id),
  judge_rubric_id UUID NOT NULL REFERENCES judge_rubric_versions(judge_rubric_id),
  parse_status parse_status_code NOT NULL,
  schema_version TEXT NOT NULL,
  raw_judge_output JSONB NOT NULL,
  raw_output_sha256 sha256_hex NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (response_id, judge_rubric_id)
);

CREATE TABLE claim_extractions (
  claim_extraction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  judge_extraction_id UUID NOT NULL REFERENCES judge_extractions(judge_extraction_id) ON DELETE CASCADE,
  claim_index INTEGER NOT NULL CHECK (claim_index >= 0),
  rubric_version TEXT NOT NULL,
  feature_id TEXT NOT NULL,
  feature_group TEXT NOT NULL,
  opportunity_class opportunity_class_code NOT NULL CHECK (opportunity_class <> 'inapplicable'),
  status assessment_status_code NOT NULL,
  disposition disposition_code NOT NULL,
  stance stance_code NOT NULL,
  causal_role causal_role_code NOT NULL,
  actor_or_relation JSONB NOT NULL DEFAULT '[]'::jsonb,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  complete_proposition_evidence BOOLEAN NOT NULL DEFAULT FALSE,
  confidence confidence_code NOT NULL,
  FOREIGN KEY (rubric_version, feature_group, feature_id)
    REFERENCES feature_registry(rubric_version, feature_group, feature_id),
  CHECK ((status='true' AND disposition='instantiated') OR
         (status='false' AND disposition IN ('omitted','denied','displaced','mentioned_only')) OR
         (status='unclear' AND disposition='unclear') OR
         (status='not_applicable' AND disposition='not_applicable') OR
         (status='not_assessable' AND disposition='not_assessable')),
  UNIQUE (judge_extraction_id, feature_id),
  UNIQUE (judge_extraction_id, claim_index)
);

CREATE TABLE relation_extractions (
  relation_extraction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  judge_extraction_id UUID NOT NULL REFERENCES judge_extractions(judge_extraction_id) ON DELETE CASCADE,
  relation_registry_version TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  relation_value TEXT NOT NULL,
  source_claim_indices INTEGER[] NOT NULL DEFAULT '{}',
  target_claim_indices INTEGER[] NOT NULL DEFAULT '{}',
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence confidence_code NOT NULL,
  FOREIGN KEY (relation_registry_version, relation_type)
    REFERENCES relation_registry(relation_registry_version, relation_type),
  UNIQUE (judge_extraction_id, relation_type)
);

CREATE TABLE factual_assessments (
  factual_assessment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  judge_extraction_id UUID NOT NULL REFERENCES judge_extractions(judge_extraction_id) ON DELETE CASCADE,
  factual_target_version_id UUID NOT NULL REFERENCES factual_targets(factual_target_version_id),
  status TEXT NOT NULL CHECK (status IN
    ('supported','contradicted','mixed','unclear','not_applicable','not_assessable')),
  claim_text TEXT,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence confidence_code NOT NULL,
  UNIQUE (judge_extraction_id, factual_target_version_id)
);

CREATE TABLE authoritative_response_assessments (
  judge_extraction_id UUID PRIMARY KEY REFERENCES judge_extractions(judge_extraction_id),
  word_count INTEGER NOT NULL CHECK (word_count >= 0),
  within_response_budget BOOLEAN NOT NULL,
  format_compliant BOOLEAN NOT NULL,
  truncated BOOLEAN NOT NULL,
  response_complete BOOLEAN NOT NULL,
  relevance BOOLEAN,
  refusal BOOLEAN NOT NULL,
  alternative_causal_frame TEXT NOT NULL CHECK (alternative_causal_frame IN
    ('neoclassical','institutionalist','social_democratic','conservative','anarchist',
     'technical','mixed','other','absent','unclear')),
  derivation_version TEXT NOT NULL,
  derivation_manifest JSONB NOT NULL
);

CREATE INDEX claim_feature_idx ON claim_extractions(rubric_version, feature_id, status);
CREATE INDEX relation_type_idx ON relation_extractions(relation_type, relation_value);
CREATE INDEX judge_output_gin ON judge_extractions USING gin(raw_judge_output);
CREATE TABLE expert_annotators (
  annotator_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  deidentified_code TEXT NOT NULL UNIQUE,
  qualification_manifest JSONB NOT NULL,
  active_from DATE NOT NULL,
  active_until DATE
);

CREATE TABLE validation_clusters (
  cluster_key TEXT PRIMARY KEY,
  partition partition_code NOT NULL,
  UNIQUE (cluster_key, partition)
);

CREATE TABLE validation_cases (
  validation_case_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_origin TEXT NOT NULL CHECK (case_origin IN ('natural','synthetic')),
  response_id UUID UNIQUE REFERENCES responses(response_id),
  item_family_version_id UUID NOT NULL REFERENCES item_families(item_family_version_id),
  question_text TEXT NOT NULL,
  opportunity_set_version TEXT NOT NULL REFERENCES rubric_versions(rubric_version),
  required_contrasts JSONB NOT NULL,
  text_to_judge TEXT NOT NULL,
  cluster_key TEXT NOT NULL REFERENCES validation_clusters(cluster_key),
  partition partition_code NOT NULL,
  sampling_weight NUMERIC NOT NULL DEFAULT 1 CHECK (sampling_weight > 0),
  CHECK ((case_origin='natural' AND response_id IS NOT NULL) OR
         (case_origin='synthetic' AND response_id IS NULL)),
  FOREIGN KEY (cluster_key, partition)
    REFERENCES validation_clusters(cluster_key, partition)
);

CREATE TABLE validation_case_factual_targets (
  validation_case_id UUID NOT NULL REFERENCES validation_cases(validation_case_id),
  factual_target_version_id UUID NOT NULL REFERENCES factual_targets(factual_target_version_id),
  PRIMARY KEY (validation_case_id, factual_target_version_id)
);

ALTER TABLE judge_extractions ADD COLUMN validation_case_id UUID
  REFERENCES validation_cases(validation_case_id);
ALTER TABLE judge_extractions ADD CONSTRAINT one_extraction_subject
  CHECK ((response_id IS NOT NULL) <> (validation_case_id IS NOT NULL));
CREATE UNIQUE INDEX judge_validation_case_uq
  ON judge_extractions(validation_case_id, judge_rubric_id)
  WHERE validation_case_id IS NOT NULL;

CREATE TABLE annotation_submissions (
  annotation_submission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  validation_case_id UUID NOT NULL REFERENCES validation_cases(validation_case_id),
  annotator_id UUID NOT NULL REFERENCES expert_annotators(annotator_id),
  guideline_version TEXT NOT NULL,
  submitted_at TIMESTAMPTZ NOT NULL,
  locked BOOLEAN NOT NULL DEFAULT TRUE CHECK (locked),
  raw_annotation JSONB NOT NULL,
  UNIQUE (validation_case_id, annotator_id, guideline_version)
);

CREATE TABLE annotation_claims (
  annotation_claim_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  annotation_submission_id UUID NOT NULL REFERENCES annotation_submissions(annotation_submission_id) ON DELETE CASCADE,
  claim_index INTEGER NOT NULL CHECK (claim_index >= 0),
  rubric_version TEXT NOT NULL,
  feature_id TEXT NOT NULL,
  status assessment_status_code NOT NULL,
  disposition disposition_code NOT NULL,
  stance stance_code NOT NULL,
  causal_role causal_role_code NOT NULL,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  complete_proposition_evidence BOOLEAN NOT NULL DEFAULT FALSE,
  confidence confidence_code NOT NULL,
  FOREIGN KEY (rubric_version, feature_id) REFERENCES feature_registry(rubric_version, feature_id),
  CHECK ((status='true' AND disposition='instantiated') OR
         (status='false' AND disposition IN ('omitted','denied','displaced','mentioned_only')) OR
         (status='unclear' AND disposition='unclear') OR
         (status='not_applicable' AND disposition='not_applicable') OR
         (status='not_assessable' AND disposition='not_assessable')),
  UNIQUE (annotation_submission_id, feature_id),
  UNIQUE (annotation_submission_id, claim_index)
);

CREATE TABLE annotation_relations (
  annotation_relation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  annotation_submission_id UUID NOT NULL REFERENCES annotation_submissions(annotation_submission_id) ON DELETE CASCADE,
  relation_registry_version TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  relation_value TEXT NOT NULL,
  source_claim_indices INTEGER[] NOT NULL DEFAULT '{}',
  target_claim_indices INTEGER[] NOT NULL DEFAULT '{}',
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence confidence_code NOT NULL,
  FOREIGN KEY (relation_registry_version, relation_type)
    REFERENCES relation_registry(relation_registry_version, relation_type),
  UNIQUE (annotation_submission_id, relation_type)
);

CREATE TABLE adjudications (
  adjudication_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  validation_case_id UUID NOT NULL UNIQUE REFERENCES validation_cases(validation_case_id),
  adjudicator_id UUID NOT NULL REFERENCES expert_annotators(annotator_id),
  guideline_version TEXT NOT NULL,
  adjudicated_at TIMESTAMPTZ NOT NULL,
  rationale TEXT NOT NULL,
  raw_gold JSONB NOT NULL
);

CREATE TABLE gold_claims (
  gold_claim_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  adjudication_id UUID NOT NULL REFERENCES adjudications(adjudication_id) ON DELETE CASCADE,
  claim_index INTEGER NOT NULL CHECK (claim_index >= 0),
  rubric_version TEXT NOT NULL,
  feature_id TEXT NOT NULL,
  status assessment_status_code NOT NULL,
  disposition disposition_code NOT NULL,
  stance stance_code NOT NULL,
  causal_role causal_role_code NOT NULL,
  evidence JSONB NOT NULL,
  complete_proposition_evidence BOOLEAN NOT NULL DEFAULT FALSE,
  FOREIGN KEY (rubric_version, feature_id) REFERENCES feature_registry(rubric_version, feature_id),
  CHECK ((status='true' AND disposition='instantiated') OR
         (status='false' AND disposition IN ('omitted','denied','displaced','mentioned_only')) OR
         (status='unclear' AND disposition='unclear') OR
         (status='not_applicable' AND disposition='not_applicable') OR
         (status='not_assessable' AND disposition='not_assessable')),
  UNIQUE (adjudication_id, feature_id),
  UNIQUE (adjudication_id, claim_index)
);

CREATE TABLE gold_relations (
  gold_relation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  adjudication_id UUID NOT NULL REFERENCES adjudications(adjudication_id) ON DELETE CASCADE,
  relation_registry_version TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  relation_value TEXT NOT NULL,
  source_claim_indices INTEGER[] NOT NULL DEFAULT '{}',
  target_claim_indices INTEGER[] NOT NULL DEFAULT '{}',
  evidence JSONB NOT NULL,
  FOREIGN KEY (relation_registry_version, relation_type)
    REFERENCES relation_registry(relation_registry_version, relation_type),
  UNIQUE (adjudication_id, relation_type)
);

CREATE TABLE annotation_factual_assessments (
  annotation_submission_id UUID NOT NULL REFERENCES annotation_submissions(annotation_submission_id),
  factual_target_version_id UUID NOT NULL REFERENCES factual_targets(factual_target_version_id),
  status TEXT NOT NULL CHECK (status IN
    ('supported','contradicted','mixed','unclear','not_applicable','not_assessable')),
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  PRIMARY KEY (annotation_submission_id, factual_target_version_id)
);

CREATE TABLE gold_factual_assessments (
  adjudication_id UUID NOT NULL REFERENCES adjudications(adjudication_id),
  factual_target_version_id UUID NOT NULL REFERENCES factual_targets(factual_target_version_id),
  status TEXT NOT NULL CHECK (status IN
    ('supported','contradicted','mixed','unclear','not_applicable','not_assessable')),
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  PRIMARY KEY (adjudication_id, factual_target_version_id)
);

CREATE TABLE annotation_response_assessments (
  annotation_submission_id UUID PRIMARY KEY REFERENCES annotation_submissions(annotation_submission_id),
  relevance BOOLEAN,
  refusal BOOLEAN,
  alternative_causal_frame TEXT NOT NULL,
  rationale TEXT NOT NULL
);

CREATE TABLE gold_response_assessments (
  adjudication_id UUID PRIMARY KEY REFERENCES adjudications(adjudication_id),
  relevance BOOLEAN,
  refusal BOOLEAN,
  alternative_causal_frame TEXT NOT NULL,
  rationale TEXT NOT NULL
);
CREATE TABLE analysis_runs (
  analysis_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id UUID NOT NULL REFERENCES experiments(experiment_id),
  preregistration_id UUID NOT NULL REFERENCES preregistrations(preregistration_id),
  analysis_version TEXT NOT NULL,
  code_commit TEXT NOT NULL,
  environment_lock_sha256 sha256_hex NOT NULL,
  configuration JSONB NOT NULL,
  configuration_sha256 sha256_hex NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  UNIQUE (experiment_id, analysis_version, code_commit, configuration_sha256)
);

ALTER TABLE analysis_response_selections
  ADD FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(analysis_run_id);

CREATE TABLE derived_metric_bundles (
  metric_bundle_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  analysis_run_id UUID NOT NULL REFERENCES analysis_runs(analysis_run_id),
  response_id UUID REFERENCES responses(response_id),
  validation_case_id UUID REFERENCES validation_cases(validation_case_id),
  extraction_source TEXT NOT NULL CHECK
    (extraction_source IN ('judge','judge_ensemble','expert_gold')),
  source_identifier UUID NOT NULL,
  metric_schema_version TEXT NOT NULL,
  metrics JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((response_id IS NOT NULL) <> (validation_case_id IS NOT NULL)),
  UNIQUE NULLS NOT DISTINCT
    (analysis_run_id, response_id, validation_case_id, extraction_source, source_identifier)
);

CREATE TABLE embedding_models (
  embedding_model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider TEXT NOT NULL,
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL,
  vector_dimension INTEGER NOT NULL CHECK (vector_dimension > 0),
  model_manifest JSONB NOT NULL,
  UNIQUE (provider, model_name, model_version, vector_dimension)
);

CREATE TABLE text_embeddings (
  embedding_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  embedding_model_id UUID NOT NULL REFERENCES embedding_models(embedding_model_id),
  source_type TEXT NOT NULL CHECK (source_type IN ('response','claim','item')),
  source_id TEXT NOT NULL,
  source_text_sha256 sha256_hex NOT NULL,
  vector_dimension INTEGER NOT NULL CHECK (vector_dimension > 0),
  embedding vector,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (embedding_model_id, source_type, source_id, source_text_sha256)
);
CREATE OR REPLACE FUNCTION validate_claim_opportunity() RETURNS trigger AS $$
DECLARE fam UUID; cls opportunity_class_code; subject_text TEXT; span TEXT;
BEGIN
  SELECT COALESCE(pv.item_family_version_id, vc.item_family_version_id),
         COALESCE(r.response_text, vc.text_to_judge)
    INTO fam, subject_text
  FROM judge_extractions je
  LEFT JOIN responses r ON r.response_id = je.response_id
  LEFT JOIN runs ru ON ru.run_id = r.run_id
  LEFT JOIN prompt_variants pv ON pv.prompt_variant_id = ru.prompt_variant_id
  LEFT JOIN validation_cases vc ON vc.validation_case_id = je.validation_case_id
  WHERE je.judge_extraction_id = NEW.judge_extraction_id;

  SELECT opportunity_class INTO cls
  FROM feature_opportunities
  WHERE item_family_version_id=fam AND rubric_version=NEW.rubric_version
    AND feature_id=NEW.feature_id;

  IF cls IS NULL OR cls = 'inapplicable' OR cls <> NEW.opportunity_class THEN
    RAISE EXCEPTION 'rubric/opportunity mismatch for family %, feature %', fam, NEW.feature_id;
  END IF;
  IF NOT ((NEW.status='true' AND NEW.disposition='instantiated') OR
          (NEW.status='false' AND NEW.disposition IN
            ('omitted','denied','displaced','mentioned_only')) OR
          (NEW.status='unclear' AND NEW.disposition='unclear') OR
          (NEW.status='not_applicable' AND NEW.disposition='not_applicable') OR
          (NEW.status='not_assessable' AND NEW.disposition='not_assessable')) THEN
    RAISE EXCEPTION 'invalid status/disposition pair';
  END IF;
  IF jsonb_typeof(NEW.evidence) <> 'array' THEN RAISE EXCEPTION 'evidence must be array'; END IF;
  FOR span IN SELECT jsonb_array_elements_text(NEW.evidence) LOOP
    -- Canonical span normalization: Unicode NFC plus CRLF/CR converted to LF; no case folding.
    IF strpos(normalize(replace(replace(subject_text,E'\r\n',E'\n'),E'\r',E'\n'), NFC),
              normalize(replace(replace(span,E'\r\n',E'\n'),E'\r',E'\n'), NFC)) = 0 THEN
      RAISE EXCEPTION 'evidence span is not verbatim in subject text';
    END IF;
  END LOOP;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER claim_opportunity_guard
BEFORE INSERT OR UPDATE ON claim_extractions
FOR EACH ROW EXECUTE FUNCTION validate_claim_opportunity();

CREATE OR REPLACE FUNCTION validate_relation_extraction() RETURNS trigger AS $$
DECLARE allowed TEXT[]; exempt TEXT[]; idx INTEGER; needs_evidence BOOLEAN; needs_refs BOOLEAN; subject_text TEXT; span TEXT;
BEGIN
  SELECT allowed_values, evidence_required, evidence_exempt_values, claim_references_required
    INTO allowed, needs_evidence, exempt, needs_refs
  FROM relation_registry WHERE relation_registry_version=NEW.relation_registry_version
    AND relation_type=NEW.relation_type;
  IF allowed IS NULL OR NOT (NEW.relation_value = ANY(allowed)) THEN
    RAISE EXCEPTION 'unregistered relation value %.%', NEW.relation_type, NEW.relation_value;
  END IF;
  IF needs_evidence AND NOT (NEW.relation_value=ANY(exempt)) AND jsonb_array_length(NEW.evidence)=0 THEN
    RAISE EXCEPTION 'relation evidence required';
  END IF;
  IF needs_refs AND NOT (NEW.relation_value=ANY(exempt)) AND
     cardinality(NEW.source_claim_indices)+cardinality(NEW.target_claim_indices)=0 THEN
    RAISE EXCEPTION 'relation claim references required'; END IF;
  SELECT COALESCE(r.response_text,vc.text_to_judge) INTO subject_text
  FROM judge_extractions je LEFT JOIN responses r USING (response_id)
  LEFT JOIN validation_cases vc USING (validation_case_id)
  WHERE je.judge_extraction_id=NEW.judge_extraction_id;
  FOR span IN SELECT jsonb_array_elements_text(NEW.evidence) LOOP
    IF strpos(normalize(replace(replace(subject_text,E'\r\n',E'\n'),E'\r',E'\n'),NFC),
              normalize(replace(replace(span,E'\r\n',E'\n'),E'\r',E'\n'),NFC))=0 THEN
      RAISE EXCEPTION 'relation evidence is not verbatim'; END IF;
  END LOOP;
  FOREACH idx IN ARRAY (NEW.source_claim_indices || NEW.target_claim_indices) LOOP
    IF NOT EXISTS (SELECT 1 FROM claim_extractions
                   WHERE judge_extraction_id=NEW.judge_extraction_id AND claim_index=idx) THEN
      RAISE EXCEPTION 'relation claim index % outside extraction', idx;
    END IF;
  END LOOP;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER relation_extraction_guard BEFORE INSERT OR UPDATE ON relation_extractions
FOR EACH ROW EXECUTE FUNCTION validate_relation_extraction();

CREATE OR REPLACE FUNCTION validate_annotation_relation() RETURNS trigger AS $$
DECLARE allowed TEXT[]; exempt TEXT[]; needs BOOLEAN; needs_refs BOOLEAN; idx INTEGER; subject_text TEXT; span TEXT;
BEGIN
  SELECT allowed_values,evidence_required,evidence_exempt_values,claim_references_required
    INTO allowed,needs,exempt,needs_refs FROM relation_registry
   WHERE relation_registry_version=NEW.relation_registry_version AND relation_type=NEW.relation_type;
  IF allowed IS NULL OR NOT (NEW.relation_value=ANY(allowed)) OR
     (needs AND NOT (NEW.relation_value=ANY(exempt)) AND jsonb_array_length(NEW.evidence)=0) THEN
    RAISE EXCEPTION 'invalid expert relation value or evidence';
  END IF;
  IF needs_refs AND NOT (NEW.relation_value=ANY(exempt)) AND
     cardinality(NEW.source_claim_indices)+cardinality(NEW.target_claim_indices)=0 THEN
    RAISE EXCEPTION 'expert relation references required'; END IF;
  SELECT vc.text_to_judge INTO subject_text FROM annotation_submissions s
  JOIN validation_cases vc USING (validation_case_id)
  WHERE s.annotation_submission_id=NEW.annotation_submission_id;
  FOR span IN SELECT jsonb_array_elements_text(NEW.evidence) LOOP
    IF strpos(normalize(replace(replace(subject_text,E'\r\n',E'\n'),E'\r',E'\n'),NFC),
              normalize(replace(replace(span,E'\r\n',E'\n'),E'\r',E'\n'),NFC))=0 THEN
      RAISE EXCEPTION 'expert relation evidence is not verbatim'; END IF;
  END LOOP;
  FOREACH idx IN ARRAY (NEW.source_claim_indices || NEW.target_claim_indices) LOOP
    IF NOT EXISTS (SELECT 1 FROM annotation_claims WHERE
      annotation_submission_id=NEW.annotation_submission_id AND claim_index=idx) THEN
      RAISE EXCEPTION 'expert relation index outside submission'; END IF;
  END LOOP; RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER annotation_relation_guard BEFORE INSERT OR UPDATE ON annotation_relations
FOR EACH ROW EXECUTE FUNCTION validate_annotation_relation();

CREATE OR REPLACE FUNCTION validate_gold_relation() RETURNS trigger AS $$
DECLARE allowed TEXT[]; exempt TEXT[]; needs BOOLEAN; needs_refs BOOLEAN; idx INTEGER; subject_text TEXT; span TEXT;
BEGIN
  SELECT allowed_values,evidence_required,evidence_exempt_values,claim_references_required
    INTO allowed,needs,exempt,needs_refs FROM relation_registry
   WHERE relation_registry_version=NEW.relation_registry_version AND relation_type=NEW.relation_type;
  IF allowed IS NULL OR NOT (NEW.relation_value=ANY(allowed)) OR
     (needs AND NOT (NEW.relation_value=ANY(exempt)) AND jsonb_array_length(NEW.evidence)=0) THEN
    RAISE EXCEPTION 'invalid gold relation value or evidence';
  END IF;
  IF needs_refs AND NOT (NEW.relation_value=ANY(exempt)) AND
     cardinality(NEW.source_claim_indices)+cardinality(NEW.target_claim_indices)=0 THEN
    RAISE EXCEPTION 'gold relation references required'; END IF;
  SELECT vc.text_to_judge INTO subject_text FROM adjudications a
  JOIN validation_cases vc USING (validation_case_id)
  WHERE a.adjudication_id=NEW.adjudication_id;
  FOR span IN SELECT jsonb_array_elements_text(NEW.evidence) LOOP
    IF strpos(normalize(replace(replace(subject_text,E'\r\n',E'\n'),E'\r',E'\n'),NFC),
              normalize(replace(replace(span,E'\r\n',E'\n'),E'\r',E'\n'),NFC))=0 THEN
      RAISE EXCEPTION 'gold relation evidence is not verbatim'; END IF;
  END LOOP;
  FOREACH idx IN ARRAY (NEW.source_claim_indices || NEW.target_claim_indices) LOOP
    IF NOT EXISTS (SELECT 1 FROM gold_claims WHERE
      adjudication_id=NEW.adjudication_id AND claim_index=idx) THEN
      RAISE EXCEPTION 'gold relation index outside adjudication'; END IF;
  END LOOP; RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER gold_relation_guard BEFORE INSERT OR UPDATE ON gold_relations
FOR EACH ROW EXECUTE FUNCTION validate_gold_relation();

CREATE OR REPLACE FUNCTION validate_annotation_evidence() RETURNS trigger AS $$
DECLARE subject_text TEXT; span TEXT;
BEGIN
  SELECT vc.text_to_judge INTO subject_text FROM annotation_submissions s
  JOIN validation_cases vc USING (validation_case_id)
  WHERE s.annotation_submission_id=NEW.annotation_submission_id;
  FOR span IN SELECT jsonb_array_elements_text(NEW.evidence) LOOP
    IF strpos(normalize(replace(replace(subject_text,E'\r\n',E'\n'),E'\r',E'\n'),NFC),
              normalize(replace(replace(span,E'\r\n',E'\n'),E'\r',E'\n'),NFC))=0 THEN
      RAISE EXCEPTION 'expert evidence is not verbatim'; END IF;
  END LOOP; RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER annotation_evidence_guard BEFORE INSERT OR UPDATE ON annotation_claims
FOR EACH ROW EXECUTE FUNCTION validate_annotation_evidence();

CREATE OR REPLACE FUNCTION validate_gold_evidence() RETURNS trigger AS $$
DECLARE subject_text TEXT; span TEXT;
BEGIN
  SELECT vc.text_to_judge INTO subject_text FROM adjudications a
  JOIN validation_cases vc USING (validation_case_id)
  WHERE a.adjudication_id=NEW.adjudication_id;
  FOR span IN SELECT jsonb_array_elements_text(NEW.evidence) LOOP
    IF strpos(normalize(replace(replace(subject_text,E'\r\n',E'\n'),E'\r',E'\n'),NFC),
              normalize(replace(replace(span,E'\r\n',E'\n'),E'\r',E'\n'),NFC))=0 THEN
      RAISE EXCEPTION 'gold evidence is not verbatim'; END IF;
  END LOOP; RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER gold_evidence_guard BEFORE INSERT OR UPDATE ON gold_claims
FOR EACH ROW EXECUTE FUNCTION validate_gold_evidence();

CREATE OR REPLACE FUNCTION immutable_observation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER runs_immutable BEFORE UPDATE OR DELETE ON runs
FOR EACH ROW EXECUTE FUNCTION immutable_observation();
CREATE TRIGGER responses_immutable BEFORE UPDATE OR DELETE ON responses
FOR EACH ROW EXECUTE FUNCTION immutable_observation();
CREATE TRIGGER locked_annotations_immutable BEFORE UPDATE OR DELETE ON annotation_submissions
FOR EACH ROW WHEN (OLD.locked) EXECUTE FUNCTION immutable_observation();
CREATE TRIGGER annotation_claims_immutable BEFORE UPDATE OR DELETE ON annotation_claims
FOR EACH ROW EXECUTE FUNCTION immutable_observation();
CREATE TRIGGER annotation_relations_immutable BEFORE UPDATE OR DELETE ON annotation_relations
FOR EACH ROW EXECUTE FUNCTION immutable_observation();
CREATE TRIGGER annotation_facts_immutable BEFORE UPDATE OR DELETE ON annotation_factual_assessments
FOR EACH ROW EXECUTE FUNCTION immutable_observation();
CREATE TRIGGER annotation_response_immutable BEFORE UPDATE OR DELETE ON annotation_response_assessments
FOR EACH ROW EXECUTE FUNCTION immutable_observation();
CREATE TRIGGER adjudications_immutable BEFORE UPDATE OR DELETE ON adjudications
FOR EACH ROW EXECUTE FUNCTION immutable_observation();
CREATE TRIGGER gold_claims_immutable BEFORE UPDATE OR DELETE ON gold_claims
FOR EACH ROW EXECUTE FUNCTION immutable_observation();
CREATE TRIGGER gold_relations_immutable BEFORE UPDATE OR DELETE ON gold_relations
FOR EACH ROW EXECUTE FUNCTION immutable_observation();

CREATE OR REPLACE FUNCTION validate_content_hashes() RETURNS trigger AS $$
BEGIN
  IF TG_TABLE_NAME='responses' AND NEW.response_sha256 <>
     encode(digest(NEW.response_text,'sha256'),'hex') THEN
    RAISE EXCEPTION 'response hash mismatch';
  ELSIF TG_TABLE_NAME='prompt_variants' AND NEW.prompt_sha256 <>
     encode(digest(NEW.system_prompt || E'\x1f' || COALESCE(NEW.developer_prompt,'') ||
                   E'\x1f' || NEW.user_prompt,'sha256'),'hex') THEN
    RAISE EXCEPTION 'prompt hash mismatch';
  ELSIF TG_TABLE_NAME='runs' AND NEW.request_sha256 <>
     encode(digest(NEW.raw_request::text,'sha256'),'hex') THEN
    RAISE EXCEPTION 'request hash mismatch';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER response_hash_guard BEFORE INSERT ON responses
FOR EACH ROW EXECUTE FUNCTION validate_content_hashes();
CREATE TRIGGER prompt_hash_guard BEFORE INSERT OR UPDATE ON prompt_variants
FOR EACH ROW EXECUTE FUNCTION validate_content_hashes();
CREATE TRIGGER request_hash_guard BEFORE INSERT ON runs
FOR EACH ROW EXECUTE FUNCTION validate_content_hashes();

CREATE VIEW analysis_claim_long AS
SELECT COALESCE(r.response_id, vc.response_id, je.validation_case_id) AS response_id,
       je.validation_case_id, ru.model_snapshot_id,
       i.item_family_version_id, i.item_family_id, pv.prompt_variant,
       i.domain, i.ai_eligible, je.judge_extraction_id, ce.rubric_version,
       ce.feature_group, ce.feature_id, ce.opportunity_class, ce.status,
       ce.disposition, ce.stance, ce.causal_role, ce.confidence, ce.evidence,
       ce.complete_proposition_evidence
FROM claim_extractions ce
JOIN judge_extractions je USING (judge_extraction_id)
LEFT JOIN responses r USING (response_id)
LEFT JOIN runs ru USING (run_id)
LEFT JOIN prompt_variants pv USING (prompt_variant_id)
LEFT JOIN validation_cases vc ON vc.validation_case_id=je.validation_case_id
JOIN item_families i ON i.item_family_version_id=
  COALESCE(pv.item_family_version_id, vc.item_family_version_id);

CREATE VIEW analysis_relation_long AS
SELECT COALESCE(r.response_id, vc.response_id, je.validation_case_id) AS response_id,
       je.validation_case_id, ru.model_snapshot_id, i.item_family_id, pv.prompt_variant,
       i.domain, i.ai_eligible, re.relation_type, re.relation_value,
       re.source_claim_indices, re.target_claim_indices, re.confidence, re.evidence
FROM relation_extractions re
JOIN judge_extractions je USING (judge_extraction_id)
LEFT JOIN responses r USING (response_id)
LEFT JOIN runs ru USING (run_id)
LEFT JOIN prompt_variants pv USING (prompt_variant_id)
LEFT JOIN validation_cases vc ON vc.validation_case_id=je.validation_case_id
JOIN item_families i ON i.item_family_version_id=
  COALESCE(pv.item_family_version_id, vc.item_family_version_id);
