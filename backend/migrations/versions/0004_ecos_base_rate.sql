-- Apply explicitly after 0001; no automatic startup DDL or initial rate.
CREATE TABLE public.ecos_base_rate (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    annual_rate numeric(18, 12) NOT NULL CHECK (annual_rate BETWEEN 0 AND 1),
    observed_on date NOT NULL,
    checked_at timestamptz NOT NULL,
    stat_code text NOT NULL DEFAULT '722Y001' CHECK (stat_code = '722Y001'),
    item_code text NOT NULL DEFAULT '0101000' CHECK (item_code = '0101000'),
    unit_name text NOT NULL DEFAULT '연%' CHECK (unit_name = '연%'),
    CHECK (observed_on <= (checked_at AT TIME ZONE 'Asia/Seoul')::date)
);

INSERT INTO public.benefitfinder_schema_migrations (version)
VALUES ('0004_ecos_base_rate');
