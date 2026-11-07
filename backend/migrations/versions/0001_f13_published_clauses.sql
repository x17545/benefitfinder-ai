-- Apply explicitly in one transaction. Never executed by application startup.
CREATE TABLE IF NOT EXISTS public.benefitfinder_schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE public.f13_policy_documents (
    policy_id text NOT NULL CHECK (policy_id ~ '^youth:[A-Za-z0-9:_-]{1,100}$'),
    document_version text NOT NULL CHECK (length(btrim(document_version)) BETWEEN 1 AND 120),
    policy_name text NOT NULL CHECK (length(btrim(policy_name)) BETWEEN 1 AND 120),
    category text NOT NULL CHECK (category IN ('employment', 'housing')),
    source_url text NOT NULL CHECK (source_url ~ '^https://[^/ ]+' AND length(source_url) <= 2048),
    checked_at timestamptz NOT NULL,
    publication_status text NOT NULL DEFAULT 'draft'
        CHECK (publication_status IN ('draft', 'published', 'retired')),
    published_at timestamptz,
    PRIMARY KEY (policy_id, document_version),
    CHECK (publication_status <> 'published' OR published_at IS NOT NULL)
);

-- One current published document per policy. Retire the old version explicitly first.
CREATE UNIQUE INDEX f13_one_published_document
    ON public.f13_policy_documents (policy_id) WHERE publication_status = 'published';

CREATE TABLE public.f13_clauses (
    policy_id text NOT NULL,
    document_version text NOT NULL,
    clause_id text NOT NULL CHECK (length(btrim(clause_id)) BETWEEN 1 AND 120),
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 120),
    body text NOT NULL CHECK (length(btrim(body)) BETWEEN 1 AND 2500),
    source_location text NOT NULL CHECK (length(btrim(source_location)) BETWEEN 1 AND 500),
    topics text[] NOT NULL CHECK (cardinality(topics) BETWEEN 1 AND 20
                                AND array_position(topics, NULL) IS NULL),
    review_status text NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'approved', 'rejected')),
    reviewed_by text,
    reviewed_at timestamptz,
    publication_status text NOT NULL DEFAULT 'draft'
        CHECK (publication_status IN ('draft', 'published', 'retired')),
    published_at timestamptz,
    PRIMARY KEY (policy_id, document_version, clause_id),
    FOREIGN KEY (policy_id, document_version)
        REFERENCES public.f13_policy_documents (policy_id, document_version),
    CHECK (review_status <> 'approved' OR
           (reviewed_by IS NOT NULL AND length(btrim(reviewed_by)) > 0
            AND reviewed_at IS NOT NULL)),
    CHECK (publication_status <> 'published' OR
           (review_status = 'approved' AND reviewed_by IS NOT NULL
            AND published_at IS NOT NULL AND reviewed_at IS NOT NULL
            AND published_at >= reviewed_at))
);

CREATE INDEX f13_published_clauses_policy_version
    ON public.f13_clauses (policy_id, document_version, clause_id)
    WHERE publication_status = 'published' AND review_status = 'approved';

INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0001_f13_published_clauses');
