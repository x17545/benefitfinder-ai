-- Explicit migration: unreviewed is not an approval and has no reviewer.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.benefitfinder_schema_migrations WHERE version='0013_ai_catalog_review') THEN
    RAISE EXCEPTION 'unreviewed_catalog_prerequisite_missing';
  END IF;
END $$;
ALTER TABLE public.policy_catalog_releases DROP CONSTRAINT policy_catalog_releases_review_status_check;
ALTER TABLE public.policy_catalog_releases ADD CONSTRAINT policy_catalog_releases_review_status_check CHECK (review_status IN ('pending','approved','rejected','unreviewed'));
ALTER TABLE public.policy_catalog_releases DROP CONSTRAINT policy_catalog_releases_review_type_check;
ALTER TABLE public.policy_catalog_releases ADD CONSTRAINT policy_catalog_releases_review_type_check CHECK (review_type IN ('human','ai','unreviewed'));
ALTER TABLE public.policy_catalog_releases ADD COLUMN unreviewed_source jsonb;
ALTER TABLE public.policy_catalog_releases ADD CONSTRAINT policy_catalog_unreviewed_type_check
  CHECK ((review_status <> 'unreviewed' OR review_type = 'unreviewed') AND
         (review_type <> 'unreviewed' OR review_status = 'unreviewed' OR
          (review_status = 'pending' AND publication_status = 'retired')));
ALTER TABLE public.policy_catalog_releases DROP CONSTRAINT policy_catalog_releases_check3;
ALTER TABLE public.policy_catalog_releases ADD CONSTRAINT policy_catalog_releases_published_review_check CHECK (
  publication_status <> 'published' OR (published_at IS NOT NULL AND
    ((review_status='approved' AND reviewed_at IS NOT NULL AND published_at >= reviewed_at)
     OR (review_status='unreviewed' AND review_type='unreviewed' AND reviewed_by IS NULL AND reviewed_at IS NULL))));

CREATE OR REPLACE FUNCTION public.guard_ai_catalog_review() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE review jsonb;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.review_type IS DISTINCT FROM OLD.review_type
                            OR NEW.ai_review IS DISTINCT FROM OLD.ai_review
                            OR NEW.unreviewed_source IS DISTINCT FROM OLD.unreviewed_source) THEN
        RAISE EXCEPTION 'catalog_review_provenance_immutable';
    END IF;
    IF NEW.review_type = 'unreviewed' THEN
        IF NEW.review_status NOT IN ('unreviewed', 'pending') OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL
           OR NEW.ai_review IS NOT NULL OR jsonb_typeof(NEW.unreviewed_source) IS DISTINCT FROM 'object'
           OR NEW.unreviewed_source - ARRAY['document_version','source_sha256','record_sha256'] <> '{}'::jsonb
           OR NEW.unreviewed_source->>'document_version' IS DISTINCT FROM NEW.document_version
           OR NEW.unreviewed_source->>'source_sha256' IS DISTINCT FROM NEW.payload->>'source_sha256'
           OR NEW.unreviewed_source->>'record_sha256' IS DISTINCT FROM NEW.approved_sha256
           OR (NEW.payload->>'kind' IS DISTINCT FROM 'service' AND
               (NEW.payload#>'{benefit,reviewed}' IS DISTINCT FROM 'false'::jsonb
                OR NEW.payload->'unresolved_combination_rules' IS DISTINCT FROM 'true'::jsonb
                OR NEW.payload->'payments' IS DISTINCT FROM 'null'::jsonb))
           OR (NEW.payload->>'kind' = 'service' AND NEW.payload->'price' IS DISTINCT FROM 'null'::jsonb) THEN
            RAISE EXCEPTION 'invalid_unreviewed_catalog_provenance';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.unreviewed_source IS NOT NULL THEN RAISE EXCEPTION 'unexpected_unreviewed_source'; END IF;
    IF NEW.review_type = 'human' THEN
        IF NEW.ai_review IS NOT NULL THEN RAISE EXCEPTION 'unexpected_ai_review'; END IF;
        RETURN NEW;
    END IF;
    review := NEW.ai_review;
    IF jsonb_typeof(review) IS DISTINCT FROM 'object'
       OR review - ARRAY['writer_agent','reviewer_agent','reviewer_model','verdict',
                         'reviewed_at','document_version','source_sha256','record_sha256'] <> '{}'::jsonb
       OR NOT COALESCE(jsonb_typeof(review->'writer_agent') = 'string'
                       AND length(btrim(review->>'writer_agent')) > 0, false)
       OR NOT COALESCE(jsonb_typeof(review->'reviewer_agent') = 'string'
                       AND length(btrim(review->>'reviewer_agent')) > 0, false)
       OR NOT COALESCE(jsonb_typeof(review->'reviewer_model') = 'string'
                       AND length(btrim(review->>'reviewer_model')) > 0, false)
       OR review->>'writer_agent' = review->>'reviewer_agent'
       OR review->>'verdict' IS DISTINCT FROM 'pass'
       OR review->>'document_version' IS DISTINCT FROM NEW.document_version
       OR review->>'source_sha256' IS DISTINCT FROM NEW.payload->>'source_sha256'
       OR review->>'record_sha256' IS DISTINCT FROM NEW.approved_sha256
       OR NEW.reviewed_by IS DISTINCT FROM 'ai:' || (review->>'reviewer_agent')
       OR NEW.reviewed_at IS DISTINCT FROM (review->>'reviewed_at')::timestamptz
       OR NEW.reviewed_at > now() THEN
        RAISE EXCEPTION 'invalid_ai_catalog_review';
    END IF;
    RETURN NEW;
END $$;

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
        IF material_changed_at IS NULL OR
           (CASE WHEN NEW.review_type = 'unreviewed' THEN NEW.published_at ELSE NEW.reviewed_at END) IS NULL
           OR (CASE WHEN NEW.review_type = 'unreviewed' THEN NEW.published_at ELSE NEW.reviewed_at END) < material_changed_at THEN
            RAISE EXCEPTION 'catalog_approval_predates_source_change';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.guard_catalog_source_bindings() FROM PUBLIC;


INSERT INTO public.benefitfinder_schema_migrations(version) VALUES ('0014_unreviewed_catalog');
