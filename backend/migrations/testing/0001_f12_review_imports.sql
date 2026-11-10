-- Test-only provenance for approved, UNPUBLISHED F12 imports.
-- Apply explicitly after versions/0001, only in a dedicated local *_test DB.
CREATE TABLE public.f13_test_review_imports (
    policy_id text NOT NULL,
    document_version text NOT NULL,
    clause_id text NOT NULL,
    raw_policy_id text NOT NULL,
    pair_sha256 text NOT NULL CHECK (pair_sha256 ~ '^[0-9a-f]{64}$'),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    export_sha256 text NOT NULL CHECK (export_sha256 ~ '^[0-9a-f]{64}$'),
    item_kind text NOT NULL CHECK (item_kind IN ('rule', 'eligibility', 'amount', 'timing')),
    approval_reason text NOT NULL CHECK (length(btrim(approval_reason)) BETWEEN 1 AND 1000),
    candidate jsonb NOT NULL CHECK (jsonb_typeof(candidate) = 'object'),
    imported_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (policy_id, document_version, clause_id),
    FOREIGN KEY (policy_id, document_version, clause_id)
        REFERENCES public.f13_clauses (policy_id, document_version, clause_id)
);
