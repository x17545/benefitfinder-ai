-- Apply explicitly in one transaction, after 0001. No startup DDL or seed data.
CREATE TABLE public.policy_catalog_releases (
    policy_id text NOT NULL CHECK (policy_id ~ '^youth:[A-Za-z0-9:_-]{1,100}$'),
    document_version text NOT NULL CHECK (length(btrim(document_version)) BETWEEN 1 AND 120),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    approved_sha256 text NOT NULL CHECK (approved_sha256 ~ '^[0-9a-f]{64}$'),
    review_status text NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'approved', 'rejected')),
    reviewed_by text,
    reviewed_at timestamptz,
    publication_status text NOT NULL DEFAULT 'draft'
        CHECK (publication_status IN ('draft', 'published', 'retired')),
    published_at timestamptz,
    PRIMARY KEY (policy_id, document_version),
    CHECK (payload #>> '{benefit,id}' IS NOT NULL
           AND payload #>> '{benefit,id}' = policy_id),
    CHECK (payload #>> '{benefit,source,document_version}' IS NOT NULL
           AND payload #>> '{benefit,source,document_version}' = document_version),
    CHECK (review_status <> 'approved' OR
           (length(btrim(reviewed_by)) > 0 AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL)),
    CHECK (publication_status <> 'published' OR
           (review_status = 'approved' AND published_at IS NOT NULL
            AND reviewed_at IS NOT NULL AND published_at >= reviewed_at))
);

CREATE UNIQUE INDEX policy_catalog_one_published_version
    ON public.policy_catalog_releases (policy_id) WHERE publication_status = 'published';

INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0002_published_policy_catalog');
