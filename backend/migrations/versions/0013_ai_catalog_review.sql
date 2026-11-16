-- Explicit migration: preserve existing human records and make AI provenance distinct.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.benefitfinder_schema_migrations
                   WHERE version = '0012_official_document_sources') THEN
        RAISE EXCEPTION 'ai_catalog_review_prerequisite_missing';
    END IF;
END $$;

ALTER TABLE public.policy_catalog_releases
    ADD COLUMN review_type text NOT NULL DEFAULT 'human' CHECK (review_type IN ('human', 'ai')),
    ADD COLUMN ai_review jsonb;

CREATE FUNCTION public.guard_ai_catalog_review() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE review jsonb;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.review_type IS DISTINCT FROM OLD.review_type
                            OR NEW.ai_review IS DISTINCT FROM OLD.ai_review) THEN
        RAISE EXCEPTION 'catalog_review_provenance_immutable';
    END IF;
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
REVOKE ALL ON FUNCTION public.guard_ai_catalog_review() FROM PUBLIC;
CREATE TRIGGER catalog_ai_review_guard BEFORE INSERT OR UPDATE
    ON public.policy_catalog_releases FOR EACH ROW
    EXECUTE FUNCTION public.guard_ai_catalog_review();

INSERT INTO public.benefitfinder_schema_migrations(version) VALUES ('0013_ai_catalog_review');
