-- Stop F12 workers, apply explicitly after 0007, then upgrade all workers.
-- Existing source payloads, output files, review decisions and publications are untouched.
ALTER TABLE public.f12_analysis_tasks
    ADD COLUMN analysis_version integer NOT NULL DEFAULT 1
        CHECK (analysis_version BETWEEN 1 AND 1000);
-- Every new worker must explicitly identify its analysis version.
ALTER TABLE public.f12_analysis_tasks ALTER COLUMN analysis_version DROP DEFAULT;
ALTER TABLE public.f12_analysis_tasks
    DROP CONSTRAINT f12_analysis_tasks_policy_id_input_sha256_key,
    ADD CONSTRAINT f12_analysis_tasks_input_version_key
        UNIQUE (policy_id, input_sha256, analysis_version),
    DROP CONSTRAINT f12_analysis_tasks_last_error_check,
    ADD CONSTRAINT f12_analysis_tasks_last_error_check CHECK (last_error IN (
        'f12_analysis_failed', 'f12_analysis_timeout', 'f12_artifact_failed',
        'f12_source_changed', 'f12_lease_expired', 'f12_invalid_input',
        'f12_analysis_version_changed'));

CREATE INDEX f12_analysis_tasks_ready_version
    ON public.f12_analysis_tasks (analysis_version, next_attempt_at, task_id)
    WHERE status = 'pending';
CREATE INDEX f12_analysis_tasks_expired_version
    ON public.f12_analysis_tasks (analysis_version, lease_until, task_id)
    WHERE status = 'running';
DROP INDEX public.f12_analysis_tasks_ready;
DROP INDEX public.f12_analysis_tasks_expired;

COMMENT ON COLUMN public.f12_analysis_tasks.analysis_version IS
    'Extraction semantics version; separate from unchanged official input SHA256. Legacy rows are version 1.';
INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0011_f12_analysis_versions');
