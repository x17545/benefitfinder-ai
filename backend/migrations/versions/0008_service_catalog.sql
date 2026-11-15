-- Explicitly after 0002 and 0006. Uses the same source-bound release history.
ALTER TABLE public.policy_catalog_releases ADD CONSTRAINT policy_catalog_payload_kind CHECK (
    COALESCE(
        (NOT (payload ? 'kind') AND NOT ((payload -> 'benefit') ? 'service'))
        OR (payload -> 'kind' = '"service"'::jsonb
            AND jsonb_typeof(payload #> '{benefit,service}') = 'object'
            AND NOT ((payload -> 'benefit') ? 'benefit_amount')
            AND NOT (payload ? 'payments') AND NOT (payload ? 'payment_evidence')),
        false)
);

CREATE FUNCTION public.guard_catalog_benefit_kind() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE kind text := COALESCE(NEW.payload ->> 'kind', 'cash');
BEGIN
    -- The same order as collection/publication: source first, policy second.
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
CREATE TRIGGER catalog_benefit_kind_guard BEFORE INSERT ON public.policy_catalog_releases
    FOR EACH ROW EXECUTE FUNCTION public.guard_catalog_benefit_kind();

COMMENT ON CONSTRAINT policy_catalog_payload_kind ON public.policy_catalog_releases IS
    'Cash and service releases share approvals/source invalidation, never a cash payment schedule.';
INSERT INTO public.benefitfinder_schema_migrations (version) VALUES ('0008_service_catalog');
