-- Apply explicitly after 0001 in one transaction. This area is unreviewed source
-- material; it is intentionally separate from the published policy catalog.
CREATE TABLE public.collected_policy_runs (
    source text NOT NULL CHECK (source = 'youthcenter'),
    run_id text NOT NULL CHECK (run_id ~ '^[0-9a-f]{32}$'),
    completed_at timestamptz NOT NULL,
    batch_sha256 text NOT NULL CHECK (batch_sha256 ~ '^[0-9a-f]{64}$'),
    record_count integer NOT NULL CHECK (record_count BETWEEN 0 AND 50000),
    new_count integer NOT NULL CHECK (new_count BETWEEN 0 AND 50000),
    changed_count integer NOT NULL CHECK (changed_count BETWEEN 0 AND 50000),
    unchanged_count integer NOT NULL CHECK (unchanged_count BETWEEN 0 AND 50000),
    missing_count integer NOT NULL CHECK (missing_count BETWEEN 0 AND 50000),
    PRIMARY KEY (source, run_id),
    CHECK (new_count + changed_count + unchanged_count = record_count)
);

-- Also prevents two generations from sharing the completion instant.
CREATE UNIQUE INDEX collected_policy_runs_latest
    ON public.collected_policy_runs (source, completed_at DESC);

CREATE TABLE public.collected_policies (
    source text NOT NULL CHECK (source = 'youthcenter'),
    external_id text NOT NULL
        CHECK (length(external_id) BETWEEN 1 AND 200 AND btrim(external_id) <> ''),
    material_sha256 text NOT NULL CHECK (material_sha256 ~ '^[0-9a-f]{64}$'),
    snapshot_ref text NOT NULL CHECK (btrim(snapshot_ref) <> ''),
    fetched_at timestamptz NOT NULL,
    fields jsonb NOT NULL CHECK (jsonb_typeof(fields) = 'object'),
    last_run_id text NOT NULL CHECK (last_run_id ~ '^[0-9a-f]{32}$'),
    missing_detected_at timestamptz,
    PRIMARY KEY (source, external_id),
    FOREIGN KEY (source, last_run_id)
        REFERENCES public.collected_policy_runs (source, run_id),
    CHECK (missing_detected_at IS NULL OR missing_detected_at >= fetched_at)
);

CREATE INDEX collected_policies_active_source
    ON public.collected_policies (source, external_id)
    WHERE missing_detected_at IS NULL;

CREATE TABLE public.collected_policy_changes (
    source text NOT NULL CHECK (source = 'youthcenter'),
    run_id text NOT NULL CHECK (run_id ~ '^[0-9a-f]{32}$'),
    external_id text NOT NULL
        CHECK (length(external_id) BETWEEN 1 AND 200 AND btrim(external_id) <> ''),
    kind text NOT NULL CHECK (kind IN ('new', 'changed', 'missing')),
    previous_sha256 text
        CHECK (previous_sha256 IS NULL OR previous_sha256 ~ '^[0-9a-f]{64}$'),
    current_sha256 text
        CHECK (current_sha256 IS NULL OR current_sha256 ~ '^[0-9a-f]{64}$'),
    record_payload jsonb NOT NULL CHECK (jsonb_typeof(record_payload) = 'object'),
    PRIMARY KEY (source, run_id, external_id),
    FOREIGN KEY (source, run_id)
        REFERENCES public.collected_policy_runs (source, run_id),
    FOREIGN KEY (source, external_id)
        REFERENCES public.collected_policies (source, external_id),
    CHECK (
        (kind = 'new' AND previous_sha256 IS NULL AND current_sha256 IS NOT NULL)
        OR (kind = 'changed' AND previous_sha256 IS NOT NULL
            AND current_sha256 IS NOT NULL AND previous_sha256 <> current_sha256)
        OR (kind = 'missing' AND previous_sha256 IS NOT NULL AND current_sha256 IS NULL)
    ),
    CHECK (record_payload ? 'source_name'
           AND record_payload -> 'source_name' IS NOT NULL
           AND jsonb_typeof(record_payload -> 'source_name') = 'string'
           AND btrim(record_payload ->> 'source_name') <> ''
           AND record_payload ->> 'source_name' = source),
    CHECK (record_payload ? 'external_id'
           AND record_payload -> 'external_id' IS NOT NULL
           AND jsonb_typeof(record_payload -> 'external_id') = 'string'
           AND btrim(record_payload ->> 'external_id') <> ''
           AND record_payload ->> 'external_id' = external_id),
    CHECK (record_payload ? 'record_sha256'
           AND record_payload -> 'record_sha256' IS NOT NULL
           AND jsonb_typeof(record_payload -> 'record_sha256') = 'string'
           AND btrim(record_payload ->> 'record_sha256') <> ''),
    CHECK (record_payload ? 'snapshot_ref'
           AND record_payload -> 'snapshot_ref' IS NOT NULL
           AND jsonb_typeof(record_payload -> 'snapshot_ref') = 'string'
           AND btrim(record_payload ->> 'snapshot_ref') <> ''),
    CHECK (record_payload ? 'fetched_at'
           AND record_payload -> 'fetched_at' IS NOT NULL
           AND jsonb_typeof(record_payload -> 'fetched_at') = 'string'
           AND btrim(record_payload ->> 'fetched_at') <> ''),
    CHECK (record_payload ? 'fields'
           AND record_payload -> 'fields' IS NOT NULL
           AND jsonb_typeof(record_payload -> 'fields') = 'object'),
    CHECK (
        (kind = 'missing' AND record_payload ->> 'record_sha256' = previous_sha256)
        OR (kind IN ('new', 'changed')
            AND record_payload ->> 'record_sha256' = current_sha256)
    )
);

-- New/changed events and missing events are consumed together after each run.
CREATE INDEX collected_policy_changes_pending
    ON public.collected_policy_changes (source, run_id, kind, external_id);

-- Supports the changes -> collected_policies foreign key and record history.
CREATE INDEX collected_policy_changes_record_history
    ON public.collected_policy_changes (source, external_id, run_id);

COMMENT ON TABLE public.collected_policy_runs IS
    'Complete unreviewed source generations. Not published policy data.';
COMMENT ON TABLE public.collected_policies IS
    'Latest unreviewed source snapshot per policy. Never read by the app catalog reader.';
COMMENT ON TABLE public.collected_policy_changes IS
    'Append-only material and missing transitions derived from stored generations.';

INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0005_collected_policy_generations');
