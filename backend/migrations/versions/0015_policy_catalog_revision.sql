-- Transactional revision tracking for published policy catalog snapshots.
-- Apply explicitly after 0014. Never run as startup DDL.

CREATE TABLE public.policy_catalog_revision (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0)
);

INSERT INTO public.policy_catalog_revision (singleton, revision)
VALUES (true, 0);

CREATE FUNCTION public.bump_policy_catalog_revision()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    UPDATE public.policy_catalog_revision
       SET revision = revision + 1
     WHERE singleton = true;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'policy_catalog_revision_missing';
    END IF;

    RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION public.bump_policy_catalog_revision() FROM PUBLIC;

CREATE TRIGGER policy_catalog_revision_insert
AFTER INSERT ON public.policy_catalog_releases
FOR EACH STATEMENT
EXECUTE FUNCTION public.bump_policy_catalog_revision();

CREATE TRIGGER policy_catalog_revision_update
AFTER UPDATE ON public.policy_catalog_releases
FOR EACH STATEMENT
EXECUTE FUNCTION public.bump_policy_catalog_revision();

CREATE TRIGGER policy_catalog_revision_delete
AFTER DELETE ON public.policy_catalog_releases
FOR EACH STATEMENT
EXECUTE FUNCTION public.bump_policy_catalog_revision();

CREATE TRIGGER policy_catalog_revision_truncate
AFTER TRUNCATE ON public.policy_catalog_releases
FOR EACH STATEMENT
EXECUTE FUNCTION public.bump_policy_catalog_revision();

INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0015_policy_catalog_revision');
