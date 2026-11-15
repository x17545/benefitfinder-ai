-- Apply explicitly after 0001 in one transaction. Never executed by application startup.
CREATE TABLE public.analysis_daily_usage (
    day date PRIMARY KEY,
    used integer NOT NULL,
    CONSTRAINT analysis_daily_usage_used_range CHECK (used BETWEEN 1 AND 100)
);

INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0003_analysis_usage');
