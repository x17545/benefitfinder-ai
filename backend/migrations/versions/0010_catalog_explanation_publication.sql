-- Explicitly apply after 0009. Existing unbound F13 fixtures retain their format.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.benefitfinder_schema_migrations
                   WHERE version='0009_gov24_policy_sources') THEN
        RAISE EXCEPTION 'catalog_explanation_prerequisite_missing';
    END IF;
END $$;

ALTER TABLE public.f13_policy_documents ADD COLUMN catalog_record_sha256 text
    CHECK (catalog_record_sha256 IS NULL OR catalog_record_sha256 ~ '^[0-9a-f]{64}$');

CREATE FUNCTION public.guard_catalog_explanation_document() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE release record;
BEGIN
    IF TG_OP='UPDATE' AND OLD.catalog_record_sha256 IS NOT NULL THEN
        IF ROW(NEW.policy_id,NEW.document_version,NEW.policy_name,NEW.category,
               NEW.source_url,NEW.checked_at,NEW.published_at,NEW.catalog_record_sha256)
           IS DISTINCT FROM
           ROW(OLD.policy_id,OLD.document_version,OLD.policy_name,OLD.category,
               OLD.source_url,OLD.checked_at,OLD.published_at,OLD.catalog_record_sha256) THEN
            RAISE EXCEPTION 'catalog_explanation_document_is_immutable';
        END IF;
        IF OLD.publication_status='retired' AND NEW.publication_status='published' THEN
            RAISE EXCEPTION 'catalog_explanation_requires_new_version';
        END IF;
    END IF;
    IF NEW.publication_status<>'published' THEN RETURN NEW; END IF;
    IF NEW.catalog_record_sha256 IS NULL THEN
        IF EXISTS (SELECT 1 FROM public.policy_catalog_releases r
                   WHERE r.policy_id=NEW.policy_id AND r.publication_status='published'
                     AND jsonb_typeof(r.payload->'explanation_clauses')='array') THEN
            RAISE EXCEPTION 'catalog_explanation_binding_required';
        END IF;
        RETURN NEW;
    END IF;
    SELECT r.* INTO release FROM public.policy_catalog_releases r
    WHERE r.policy_id=NEW.policy_id AND r.document_version=NEW.document_version
      AND r.approved_sha256=NEW.catalog_record_sha256 AND r.review_status='approved'
      AND r.publication_status='published';
    IF NOT FOUND THEN RAISE EXCEPTION 'catalog_explanation_release_not_current'; END IF;
    IF jsonb_typeof(release.payload->'explanation_clauses') IS DISTINCT FROM 'array'
       OR jsonb_array_length(release.payload->'explanation_clauses') NOT BETWEEN 1 AND 200
       OR NEW.policy_name IS DISTINCT FROM release.payload#>>'{benefit,title}'
       OR NEW.category IS DISTINCT FROM release.payload#>>'{benefit,categories,0}'
       OR NEW.source_url IS DISTINCT FROM release.payload#>>'{benefit,source,url}'
       OR NEW.checked_at IS DISTINCT FROM
          (release.payload#>>'{benefit,source,checked_at}')::timestamptz
       OR NEW.published_at IS DISTINCT FROM release.published_at
       OR release.reviewed_at<NEW.checked_at THEN
        RAISE EXCEPTION 'catalog_explanation_document_mismatch';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_catalog_explanation_document() FROM PUBLIC;
CREATE TRIGGER catalog_explanation_document_guard BEFORE INSERT OR UPDATE
    ON public.f13_policy_documents FOR EACH ROW
    EXECUTE FUNCTION public.guard_catalog_explanation_document();

CREATE FUNCTION public.guard_catalog_explanation_clause() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE document record; release record; expected jsonb; matches integer;
BEGIN
    IF TG_OP='UPDATE' AND EXISTS (
        SELECT 1 FROM public.f13_policy_documents d
        WHERE d.policy_id=OLD.policy_id AND d.document_version=OLD.document_version
          AND d.catalog_record_sha256 IS NOT NULL) THEN
        IF ROW(NEW.policy_id,NEW.document_version,NEW.clause_id,NEW.title,NEW.body,
               NEW.source_location,NEW.topics,NEW.reviewed_by,NEW.reviewed_at,NEW.published_at)
           IS DISTINCT FROM
           ROW(OLD.policy_id,OLD.document_version,OLD.clause_id,OLD.title,OLD.body,
               OLD.source_location,OLD.topics,OLD.reviewed_by,OLD.reviewed_at,OLD.published_at) THEN
            RAISE EXCEPTION 'catalog_explanation_clause_is_immutable';
        END IF;
        IF (OLD.publication_status='retired' AND NEW.publication_status='published')
           OR (OLD.review_status<>'approved' AND NEW.review_status='approved') THEN
            RAISE EXCEPTION 'catalog_explanation_requires_new_version';
        END IF;
    END IF;
    SELECT d.* INTO document FROM public.f13_policy_documents d
    WHERE d.policy_id=NEW.policy_id AND d.document_version=NEW.document_version;
    IF NOT FOUND THEN RAISE EXCEPTION 'catalog_explanation_document_missing'; END IF;
    IF document.catalog_record_sha256 IS NULL THEN
        IF NEW.publication_status='published' AND EXISTS (
            SELECT 1 FROM public.policy_catalog_releases r
            WHERE r.policy_id=NEW.policy_id AND r.publication_status='published'
              AND jsonb_typeof(r.payload->'explanation_clauses')='array') THEN
            RAISE EXCEPTION 'catalog_explanation_binding_required';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.publication_status<>'published' THEN RETURN NEW; END IF;
    SELECT r.* INTO release FROM public.policy_catalog_releases r
    WHERE r.policy_id=NEW.policy_id AND r.document_version=NEW.document_version
      AND r.approved_sha256=document.catalog_record_sha256 AND r.review_status='approved'
      AND r.publication_status='published';
    IF NOT FOUND OR document.publication_status<>'published' THEN
        RAISE EXCEPTION 'catalog_explanation_release_not_current';
    END IF;
    SELECT count(*) INTO matches
    FROM jsonb_array_elements(release.payload->'explanation_clauses') AS c(value)
    WHERE c.value->>'id'=NEW.clause_id;
    IF matches<>1 THEN RAISE EXCEPTION 'catalog_explanation_clause_mismatch'; END IF;
    SELECT c.value INTO expected
    FROM jsonb_array_elements(release.payload->'explanation_clauses') AS c(value)
    WHERE c.value->>'id'=NEW.clause_id;
    IF NEW.title IS DISTINCT FROM expected->>'title'
       OR NEW.body IS DISTINCT FROM expected#>>'{evidence,quote}'
       OR NEW.source_location IS DISTINCT FROM expected#>>'{evidence,location}'
       OR to_jsonb(NEW.topics) IS DISTINCT FROM expected->'topics'
       OR NEW.review_status<>'approved'
       OR NEW.reviewed_by IS DISTINCT FROM release.reviewed_by
       OR NEW.reviewed_at IS DISTINCT FROM release.reviewed_at
       OR NEW.published_at IS DISTINCT FROM release.published_at
       OR document.source_url IS DISTINCT FROM expected#>>'{evidence,source,url}'
       OR document.document_version IS DISTINCT FROM expected#>>'{evidence,source,document_version}'
       OR document.checked_at IS DISTINCT FROM
          (expected#>>'{evidence,source,checked_at}')::timestamptz THEN
        RAISE EXCEPTION 'catalog_explanation_clause_mismatch';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_catalog_explanation_clause() FROM PUBLIC;
CREATE TRIGGER catalog_explanation_clause_guard BEFORE INSERT OR UPDATE
    ON public.f13_clauses FOR EACH ROW
    EXECUTE FUNCTION public.guard_catalog_explanation_clause();

CREATE FUNCTION public.retire_catalog_explanations() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF OLD.publication_status='published' AND
       (NEW.publication_status<>'published' OR NEW.review_status<>'approved') THEN
        UPDATE public.f13_clauses c SET publication_status='retired'
        WHERE c.policy_id=OLD.policy_id AND c.document_version=OLD.document_version
          AND c.publication_status='published' AND EXISTS (
              SELECT 1 FROM public.f13_policy_documents d
              WHERE d.policy_id=c.policy_id AND d.document_version=c.document_version
                AND d.catalog_record_sha256=OLD.approved_sha256);
        UPDATE public.f13_policy_documents d SET publication_status='retired'
        WHERE d.policy_id=OLD.policy_id AND d.document_version=OLD.document_version
          AND d.catalog_record_sha256=OLD.approved_sha256 AND d.publication_status='published';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.retire_catalog_explanations() FROM PUBLIC;
CREATE TRIGGER catalog_explanations_retire AFTER UPDATE ON public.policy_catalog_releases
    FOR EACH ROW EXECUTE FUNCTION public.retire_catalog_explanations();

-- Catalogue INSERT is the only caller privilege needed. Complete F13 rows are
-- derived from that exact approved payload within the same transaction.
CREATE FUNCTION public.publish_catalog_explanations() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE clause jsonb;
BEGIN
    IF NEW.publication_status<>'published' OR NEW.review_status<>'approved'
       OR NEW.payload->'explanation_clauses' IS NULL THEN RETURN NEW; END IF;
    IF TG_OP='UPDATE' AND OLD.publication_status='published' THEN RETURN NEW; END IF;
    IF jsonb_typeof(NEW.payload->'explanation_clauses') IS DISTINCT FROM 'array'
       OR jsonb_array_length(NEW.payload->'explanation_clauses') NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION 'invalid_catalog_explanation_clauses';
    END IF;
    UPDATE public.f13_clauses SET publication_status='retired'
    WHERE policy_id=NEW.policy_id AND publication_status='published';
    UPDATE public.f13_policy_documents SET publication_status='retired'
    WHERE policy_id=NEW.policy_id AND publication_status='published';
    INSERT INTO public.f13_policy_documents
        (policy_id,document_version,policy_name,category,source_url,checked_at,
         publication_status,published_at,catalog_record_sha256)
    VALUES (NEW.policy_id,NEW.document_version,NEW.payload#>>'{benefit,title}',
            NEW.payload#>>'{benefit,categories,0}',NEW.payload#>>'{benefit,source,url}',
            (NEW.payload#>>'{benefit,source,checked_at}')::timestamptz,
            'published',NEW.published_at,NEW.approved_sha256);
    FOR clause IN SELECT value FROM jsonb_array_elements(NEW.payload->'explanation_clauses') LOOP
        INSERT INTO public.f13_clauses
            (policy_id,document_version,clause_id,title,body,source_location,topics,
             review_status,reviewed_by,reviewed_at,publication_status,published_at)
        VALUES (NEW.policy_id,NEW.document_version,clause->>'id',clause->>'title',
                clause#>>'{evidence,quote}',clause#>>'{evidence,location}',
                ARRAY(SELECT jsonb_array_elements_text(clause->'topics')),
                'approved',NEW.reviewed_by,NEW.reviewed_at,'published',NEW.published_at);
    END LOOP;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.publish_catalog_explanations() FROM PUBLIC;
CREATE TRIGGER catalog_explanations_publish AFTER INSERT OR UPDATE ON public.policy_catalog_releases
    FOR EACH ROW EXECUTE FUNCTION public.publish_catalog_explanations();

INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0010_catalog_explanation_publication');
