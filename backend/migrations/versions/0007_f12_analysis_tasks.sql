-- Explicitly after 0001, 0005, 0006. No automatic execution or policy publication.
CREATE TABLE public.f12_analysis_tasks (
    task_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_id text NOT NULL CHECK (policy_id ~ '^youth:[A-Za-z0-9:_-]{1,100}$'),
    input_sha256 text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    input_payload jsonb NOT NULL CHECK (jsonb_typeof(input_payload) = 'object'
                                       AND octet_length(input_payload::text) <= 2097152),
    origin_recheck_id bigint REFERENCES public.policy_recheck_queue (task_id),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'needs_review', 'stale', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    claim_token text CHECK (claim_token ~ '^[0-9a-f]{32}$'),
    lease_until timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_error text CHECK (last_error IN ('f12_analysis_failed', 'f12_analysis_timeout',
        'f12_artifact_failed', 'f12_source_changed', 'f12_lease_expired', 'f12_invalid_input')),
    output_ref text CHECK (output_ref ~ '^f12-analysis/[0-9]+/[0-9a-f]{32}$'),
    output_sha256 text CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
    result_summary jsonb CHECK (jsonb_typeof(result_summary) = 'object'
                                AND octet_length(result_summary::text) <= 65536),
    UNIQUE (policy_id, input_sha256),
    CHECK ((status = 'running' AND claim_token IS NOT NULL AND lease_until IS NOT NULL
            AND attempts >= 1)
           OR (status <> 'running' AND claim_token IS NULL AND lease_until IS NULL)),
    CHECK (status <> 'needs_review' OR
           (output_ref IS NOT NULL AND output_sha256 IS NOT NULL AND result_summary IS NOT NULL))
);
CREATE INDEX f12_analysis_tasks_ready ON public.f12_analysis_tasks (next_attempt_at, task_id)
    WHERE status = 'pending';
CREATE INDEX f12_analysis_tasks_expired ON public.f12_analysis_tasks (lease_until, task_id)
    WHERE status = 'running';

CREATE TABLE public.f12_analysis_sources (
    task_id bigint NOT NULL REFERENCES public.f12_analysis_tasks (task_id),
    source text NOT NULL CHECK (source = 'youthcenter'),
    external_id text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 200
                                   AND btrim(external_id) <> ''),
    material_sha256 text NOT NULL CHECK (material_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (task_id, source, external_id),
    FOREIGN KEY (source, external_id) REFERENCES public.collected_policies (source, external_id)
);
CREATE INDEX f12_analysis_sources_lookup ON public.f12_analysis_sources (source, external_id, task_id);

CREATE FUNCTION public.invalidate_f12_analysis_sources() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    UPDATE public.f12_analysis_tasks t
    SET status = 'stale', claim_token = NULL, lease_until = NULL,
        updated_at = clock_timestamp(), last_error = 'f12_source_changed'
    WHERE t.status <> 'stale' AND EXISTS (
        SELECT 1 FROM public.f12_analysis_sources s JOIN new_f12_source_changes c
          ON c.source = s.source AND c.external_id = s.external_id
        WHERE s.task_id = t.task_id
          AND (c.current_sha256 IS NULL OR c.current_sha256 <> s.material_sha256));
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION public.invalidate_f12_analysis_sources() FROM PUBLIC;
CREATE TRIGGER collected_policy_f12_stale AFTER INSERT ON public.collected_policy_changes
    REFERENCING NEW TABLE AS new_f12_source_changes FOR EACH STATEMENT
    EXECUTE FUNCTION public.invalidate_f12_analysis_sources();

COMMENT ON TABLE public.f12_analysis_tasks IS
    'Offline extraction only. needs_review never means approved or published. No user profiles.';
COMMENT ON TABLE public.f12_analysis_sources IS
    'Exact current source hashes verified before and after an offline leased analysis.';
INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0007_f12_analysis_tasks');
