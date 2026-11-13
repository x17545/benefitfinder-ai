-- Explicitly apply after 0011, in one migration transaction. The collector and
-- repository source literals must already accept these three official sources.
-- Existing approvals, publications, and raw/text hashes are not rewritten.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.benefitfinder_schema_migrations
                   WHERE version = '0011_f12_analysis_versions') THEN
        RAISE EXCEPTION 'official_document_sources_prerequisite_missing';
    END IF;
END $$;

ALTER TABLE public.collected_policy_runs DROP CONSTRAINT collected_policy_runs_source_check;
ALTER TABLE public.collected_policy_runs ADD CONSTRAINT collected_policy_runs_source_check
    CHECK (source IN ('gov24', 'official_documents', 'youthcenter'));

ALTER TABLE public.collected_policies DROP CONSTRAINT collected_policies_source_check;
ALTER TABLE public.collected_policies ADD CONSTRAINT collected_policies_source_check
    CHECK (source IN ('gov24', 'official_documents', 'youthcenter'));

ALTER TABLE public.collected_policy_changes DROP CONSTRAINT collected_policy_changes_source_check;
ALTER TABLE public.collected_policy_changes ADD CONSTRAINT collected_policy_changes_source_check
    CHECK (source IN ('gov24', 'official_documents', 'youthcenter'));

ALTER TABLE public.policy_catalog_source_links DROP CONSTRAINT policy_catalog_source_links_source_check;
ALTER TABLE public.policy_catalog_source_links ADD CONSTRAINT policy_catalog_source_links_source_check
    CHECK (source IN ('gov24', 'official_documents', 'youthcenter'));

ALTER TABLE public.f12_analysis_sources DROP CONSTRAINT f12_analysis_sources_source_check;
ALTER TABLE public.f12_analysis_sources ADD CONSTRAINT f12_analysis_sources_source_check
    CHECK (source IN ('gov24', 'official_documents', 'youthcenter'));

CREATE OR REPLACE FUNCTION public.guard_catalog_source_bindings() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    bindings jsonb;
    binding jsonb;
    observed public.collected_policies%ROWTYPE;
    material_changed_at timestamptz;
    source_name text;
BEGIN
    IF TG_OP = 'UPDATE' AND
       (NEW.policy_id IS DISTINCT FROM OLD.policy_id
        OR NEW.document_version IS DISTINCT FROM OLD.document_version
        OR NEW.payload IS DISTINCT FROM OLD.payload
        OR NEW.approved_sha256 IS DISTINCT FROM OLD.approved_sha256) THEN
        RAISE EXCEPTION 'catalog_release_is_immutable';
    END IF;
    IF NEW.publication_status <> 'published' THEN RETURN NEW; END IF;
    IF EXISTS (SELECT 1 FROM public.policy_recheck_queue q
               WHERE q.policy_id = NEW.policy_id AND q.document_version = NEW.document_version) THEN
        RAISE EXCEPTION 'catalog_version_requires_new_review';
    END IF;
    bindings := NEW.payload -> 'collection_sources';
    IF bindings IS NULL OR bindings = '[]'::jsonb THEN
        IF NEW.payload #> '{benefit,virtual_data}' = 'true'::jsonb THEN RETURN NEW; END IF;
        RAISE EXCEPTION 'catalog_source_bindings_required';
    END IF;
    IF jsonb_typeof(bindings) <> 'array' THEN
        RAISE EXCEPTION 'invalid_catalog_source_bindings';
    END IF;
    IF jsonb_array_length(bindings) NOT BETWEEN 1 AND 20 THEN
        RAISE EXCEPTION 'invalid_catalog_source_bindings';
    END IF;
    FOR binding IN SELECT value FROM jsonb_array_elements(bindings) LOOP
        IF jsonb_typeof(binding) <> 'object'
           OR NOT COALESCE(jsonb_typeof(binding -> 'source') = 'string'
                           AND binding ->> 'source' IN
                               ('gov24', 'official_documents', 'youthcenter'), false)
           OR NOT COALESCE(jsonb_typeof(binding -> 'external_id') = 'string'
                           AND length(binding ->> 'external_id') BETWEEN 1 AND 200
                           AND btrim(binding ->> 'external_id') <> '', false)
           OR NOT COALESCE(jsonb_typeof(binding -> 'material_sha256') = 'string'
                           AND binding ->> 'material_sha256' ~ '^[0-9a-f]{64}$', false)
           OR binding - ARRAY['source','external_id','material_sha256'] <> '{}'::jsonb THEN
            RAISE EXCEPTION 'invalid_catalog_source_bindings';
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM jsonb_array_elements(bindings)) <>
       (SELECT count(DISTINCT (value ->> 'source', value ->> 'external_id'))
        FROM jsonb_array_elements(bindings)) THEN
        RAISE EXCEPTION 'duplicate_catalog_source_binding';
    END IF;
    -- Binding writers take their distinct source locks in the same sorted order
    -- used by collectors and F13 writers before touching source/release rows.
    FOR source_name IN SELECT DISTINCT value ->> 'source'
                       FROM jsonb_array_elements(bindings) ORDER BY 1 LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'benefitfinder:collected_policy_generations:' || source_name, 0));
    END LOOP;
    FOR binding IN SELECT value FROM jsonb_array_elements(bindings)
                   ORDER BY value ->> 'source', value ->> 'external_id' LOOP
        SELECT * INTO observed FROM public.collected_policies
        WHERE source = binding ->> 'source' AND external_id = binding ->> 'external_id'
        FOR SHARE;
        IF NOT FOUND OR observed.missing_detected_at IS NOT NULL
           OR observed.material_sha256 <> binding ->> 'material_sha256' THEN
            RAISE EXCEPTION 'catalog_source_is_stale';
        END IF;
        IF observed.source = 'official_documents'
           AND observed.fields ->> 'text_status' IS DISTINCT FROM 'ready' THEN
            RAISE EXCEPTION 'catalog_official_document_requires_review';
        END IF;
        SELECT max(run.completed_at) INTO material_changed_at
        FROM public.collected_policy_changes c
        JOIN public.collected_policy_runs run ON run.source = c.source AND run.run_id = c.run_id
        WHERE c.source = observed.source AND c.external_id = observed.external_id
          AND c.current_sha256 = observed.material_sha256;
        IF material_changed_at IS NULL OR NEW.reviewed_at IS NULL
           OR NEW.reviewed_at < material_changed_at THEN
            RAISE EXCEPTION 'catalog_approval_predates_source_change';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.guard_catalog_source_bindings() FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.lock_f13_source_writes() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'benefitfinder:collected_policy_generations:gov24', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'benefitfinder:collected_policy_generations:official_documents', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'benefitfinder:collected_policy_generations:youthcenter', 0));
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION public.lock_f13_source_writes() FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.guard_catalog_benefit_kind() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE kind text := COALESCE(NEW.payload ->> 'kind', 'cash');
BEGIN
    -- Same source order as all collectors/F12/publication writers, then policy.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'benefitfinder:collected_policy_generations:gov24', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'benefitfinder:collected_policy_generations:official_documents', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'benefitfinder:collected_policy_generations:youthcenter', 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.policy_id, 0));
    IF EXISTS (SELECT 1 FROM public.policy_catalog_releases r
               WHERE r.policy_id = NEW.policy_id
                 AND COALESCE(r.payload ->> 'kind', 'cash') <> kind) THEN
        RAISE EXCEPTION 'catalog_policy_kind_is_immutable';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.guard_catalog_benefit_kind() FROM PUBLIC;

INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0012_official_document_sources');
