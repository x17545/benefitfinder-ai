-- Apply explicitly after 0001, 0002 and 0005, in one transaction.
-- Owner must be a trusted migration role. Runtime/collection roles must not own
-- these tables/functions or have CREATE on public. No policy is auto-approved.
CREATE TABLE public.policy_catalog_source_links (
    policy_id text NOT NULL,
    document_version text NOT NULL,
    source text NOT NULL CHECK (source = 'youthcenter'),
    external_id text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 200
                                   AND btrim(external_id) <> ''),
    material_sha256 text NOT NULL CHECK (material_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (policy_id, document_version, source, external_id),
    FOREIGN KEY (policy_id, document_version)
        REFERENCES public.policy_catalog_releases (policy_id, document_version),
    FOREIGN KEY (source, external_id)
        REFERENCES public.collected_policies (source, external_id)
);
CREATE INDEX policy_catalog_source_lookup
    ON public.policy_catalog_source_links (source, external_id, policy_id, document_version);

-- Immutable invalidation record and pending handoff. It preserves the original
-- approval; pending/retired status prevents using that approval again.
CREATE TABLE public.policy_recheck_queue (
    task_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_id text NOT NULL,
    document_version text NOT NULL,
    reason text NOT NULL CHECK (reason IN ('source_changed', 'source_missing', 'source_unlinked')),
    source text,
    external_id text,
    run_id text,
    approved_material_sha256 text CHECK (approved_material_sha256 ~ '^[0-9a-f]{64}$'),
    current_material_sha256 text CHECK (current_material_sha256 ~ '^[0-9a-f]{64}$'),
    detected_at timestamptz NOT NULL,
    approved_sha256 text NOT NULL CHECK (approved_sha256 ~ '^[0-9a-f]{64}$'),
    reviewed_by text,
    reviewed_at timestamptz,
    invalidated_document_versions text[] NOT NULL DEFAULT '{}'
        CHECK (array_position(invalidated_document_versions, NULL) IS NULL),
    FOREIGN KEY (policy_id, document_version)
        REFERENCES public.policy_catalog_releases (policy_id, document_version),
    FOREIGN KEY (source, run_id, external_id)
        REFERENCES public.collected_policy_changes (source, run_id, external_id),
    CHECK (
        (reason = 'source_unlinked' AND source IS NULL AND external_id IS NULL
         AND run_id IS NULL AND approved_material_sha256 IS NULL
         AND current_material_sha256 IS NULL)
        OR (reason IN ('source_changed', 'source_missing') AND source IS NOT NULL
            AND external_id IS NOT NULL AND run_id IS NOT NULL
            AND approved_material_sha256 IS NOT NULL
            AND ((reason = 'source_missing' AND current_material_sha256 IS NULL)
                 OR (reason = 'source_changed' AND current_material_sha256 IS NOT NULL
                     AND approved_material_sha256 <> current_material_sha256)))
    )
);
CREATE UNIQUE INDEX policy_recheck_event_once
    ON public.policy_recheck_queue (policy_id, document_version, source, external_id, run_id)
    WHERE source IS NOT NULL;
CREATE UNIQUE INDEX policy_recheck_unlinked_once
    ON public.policy_recheck_queue (policy_id, document_version) WHERE source IS NULL;
CREATE INDEX policy_recheck_policy ON public.policy_recheck_queue (policy_id, document_version);

CREATE FUNCTION public.guard_catalog_source_bindings() RETURNS trigger
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
                           AND binding ->> 'source' = 'youthcenter', false)
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
    -- Same source lock and order as the collector, before policy row changes.
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
CREATE TRIGGER catalog_source_guard BEFORE INSERT OR UPDATE
    ON public.policy_catalog_releases FOR EACH ROW
    EXECUTE FUNCTION public.guard_catalog_source_bindings();

CREATE FUNCTION public.store_catalog_source_bindings() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.publication_status = 'published' THEN
        INSERT INTO public.policy_catalog_source_links
            (policy_id, document_version, source, external_id, material_sha256)
        SELECT NEW.policy_id, NEW.document_version,
               value ->> 'source', value ->> 'external_id', value ->> 'material_sha256'
        FROM jsonb_array_elements(COALESCE(NEW.payload -> 'collection_sources', '[]'::jsonb))
        ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.store_catalog_source_bindings() FROM PUBLIC;
CREATE TRIGGER catalog_source_store AFTER INSERT OR UPDATE
    ON public.policy_catalog_releases FOR EACH ROW
    EXECUTE FUNCTION public.store_catalog_source_bindings();

CREATE FUNCTION public.invalidate_changed_policy_sources() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.policy_recheck_queue
        (policy_id, document_version, reason, source, external_id, run_id,
         approved_material_sha256, current_material_sha256, detected_at,
         approved_sha256, reviewed_by, reviewed_at, invalidated_document_versions)
    SELECT r.policy_id, r.document_version,
           CASE WHEN c.current_sha256 IS NULL THEN 'source_missing' ELSE 'source_changed' END,
           c.source, c.external_id, c.run_id, link.material_sha256, c.current_sha256,
           run.completed_at, r.approved_sha256, r.reviewed_by, r.reviewed_at,
           ARRAY(SELECT d.document_version FROM public.f13_policy_documents d
                 WHERE d.policy_id = r.policy_id ORDER BY d.document_version)
    FROM new_policy_changes c
    JOIN public.policy_catalog_source_links link
      ON link.source = c.source AND link.external_id = c.external_id
    JOIN public.policy_catalog_releases r
      ON r.policy_id = link.policy_id AND r.document_version = link.document_version
    JOIN public.collected_policy_runs run ON run.source = c.source AND run.run_id = c.run_id
    WHERE r.publication_status = 'published'
      AND (c.current_sha256 IS NULL OR c.current_sha256 <> link.material_sha256)
    ON CONFLICT DO NOTHING;

    UPDATE public.policy_catalog_releases r
    SET review_status = 'pending', publication_status = 'retired'
    WHERE r.publication_status = 'published' AND EXISTS (
        SELECT 1 FROM public.policy_recheck_queue q JOIN new_policy_changes c
          ON q.source = c.source AND q.run_id = c.run_id AND q.external_id = c.external_id
        WHERE q.policy_id = r.policy_id AND q.document_version = r.document_version);
    UPDATE public.f13_clauses f
    SET publication_status = 'retired',
        review_status = CASE WHEN f.review_status = 'approved' THEN 'pending' ELSE f.review_status END
    WHERE EXISTS (
        SELECT 1 FROM public.policy_recheck_queue q JOIN new_policy_changes c
          ON q.source = c.source AND q.run_id = c.run_id AND q.external_id = c.external_id
        WHERE q.policy_id = f.policy_id AND f.document_version = ANY(q.invalidated_document_versions));
    UPDATE public.f13_policy_documents f SET publication_status = 'retired'
    WHERE EXISTS (
        SELECT 1 FROM public.policy_recheck_queue q JOIN new_policy_changes c
          ON q.source = c.source AND q.run_id = c.run_id AND q.external_id = c.external_id
        WHERE q.policy_id = f.policy_id AND f.document_version = ANY(q.invalidated_document_versions));
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION public.invalidate_changed_policy_sources() FROM PUBLIC;
CREATE TRIGGER collected_policy_rechecks AFTER INSERT ON public.collected_policy_changes
    REFERENCING NEW TABLE AS new_policy_changes FOR EACH STATEMENT
    EXECUTE FUNCTION public.invalidate_changed_policy_sources();

CREATE FUNCTION public.guard_invalidated_f13_version() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.publication_status = 'published' AND EXISTS (
        SELECT 1 FROM public.policy_recheck_queue q
        WHERE q.policy_id = NEW.policy_id
          AND NEW.document_version = ANY(q.invalidated_document_versions)) THEN
        RAISE EXCEPTION 'f13_version_requires_new_review';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.guard_invalidated_f13_version() FROM PUBLIC;
CREATE TRIGGER invalidated_f13_document_guard BEFORE INSERT OR UPDATE
    ON public.f13_policy_documents FOR EACH ROW
    EXECUTE FUNCTION public.guard_invalidated_f13_version();
CREATE TRIGGER invalidated_f13_clause_guard BEFORE INSERT OR UPDATE
    ON public.f13_clauses FOR EACH ROW
    EXECUTE FUNCTION public.guard_invalidated_f13_version();

-- Existing real releases have no verified source links. Quarantine, preserving
-- approval fields and payload. Fictional releases keep their existing digest.
INSERT INTO public.policy_recheck_queue
    (policy_id, document_version, reason, detected_at, approved_sha256,
     reviewed_by, reviewed_at, invalidated_document_versions)
SELECT r.policy_id, r.document_version, 'source_unlinked', CURRENT_TIMESTAMP,
       r.approved_sha256, r.reviewed_by, r.reviewed_at,
       ARRAY(SELECT d.document_version FROM public.f13_policy_documents d
             WHERE d.policy_id = r.policy_id ORDER BY d.document_version)
FROM public.policy_catalog_releases r
WHERE r.publication_status = 'published'
  AND r.payload #> '{benefit,virtual_data}' IS DISTINCT FROM 'true'::jsonb;
UPDATE public.policy_catalog_releases r SET review_status = 'pending', publication_status = 'retired'
WHERE EXISTS (SELECT 1 FROM public.policy_recheck_queue q
              WHERE q.policy_id = r.policy_id AND q.document_version = r.document_version);
UPDATE public.f13_clauses f SET publication_status = 'retired',
    review_status = CASE WHEN f.review_status = 'approved' THEN 'pending' ELSE f.review_status END
WHERE EXISTS (SELECT 1 FROM public.policy_recheck_queue q WHERE q.policy_id = f.policy_id);
UPDATE public.f13_policy_documents f SET publication_status = 'retired'
WHERE EXISTS (SELECT 1 FROM public.policy_recheck_queue q WHERE q.policy_id = f.policy_id);

COMMENT ON TABLE public.policy_catalog_source_links IS
    'Explicit approval-bound source IDs/hashes. Never guessed from a title or URL.';
COMMENT ON TABLE public.policy_recheck_queue IS
    'Immutable invalidations pending human re-review. No automatic approval or model invocation.';
INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0006_policy_source_rechecks');
