-- One-time, UAT-only expand migration. The existing UAT database predates
-- golang-migrate tracking: bridge, overlay registration, and XHTTP structures
-- are already present, but the nullable subscription-validity columns are not.
-- This script changes no existing row values and never drops any object.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
DECLARE
  subscription_columns integer;
  transport_columns integer;
  invalid_transport_rows bigint;
  overlay_index_columns integer;
BEGIN
  IF to_regclass('public.schema_migrations') IS NOT NULL THEN
    RAISE EXCEPTION 'UAT baseline already tracked; refusing to adopt twice';
  END IF;
  IF to_regclass('public.users') IS NULL
     OR to_regclass('public.bridge_credentials') IS NULL
     OR to_regclass('public.overlay_registrations') IS NULL
     OR to_regclass('public.overlay_networks') IS NULL THEN
    RAISE EXCEPTION 'UAT Accounts prior schema tables do not match reviewed baseline';
  END IF;
  IF to_regclass('public.bridge_credentials_user_tenant_idx') IS NULL
     OR to_regclass('public.bridge_credentials_active_user_tenant_uk') IS NULL THEN
    RAISE EXCEPTION 'UAT Accounts bridge indexes do not match reviewed baseline';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.users'::regclass
      AND conname = 'users_proxy_uuid_matches_uuid_ck'
  ) OR to_regclass('public.users_single_root_role_uk') IS NOT NULL THEN
    RAISE EXCEPTION 'UAT Accounts bridge migration is not fully present';
  END IF;
  SELECT count(*) INTO subscription_columns
    FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'users'
     AND column_name IN ('subscription_valid_from','subscription_valid_until','last_active_at','archived_at');
  IF subscription_columns <> 0 THEN
    RAISE EXCEPTION 'UAT subscription-validity columns are partially present';
  END IF;
  SELECT count(*) INTO transport_columns
    FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'overlay_networks'
     AND column_name IN ('transport_kind','transport_path','transport_mode','transport_host');
  IF transport_columns <> 4 THEN
    RAISE EXCEPTION 'UAT XHTTP transport columns do not match reviewed baseline';
  END IF;
  SELECT count(*) INTO overlay_index_columns
    FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'overlay_registrations'
     AND column_name IN (
       'owner_user_id','created_at','network_id','status','expires_at',
       'device_id','wireguard_public_key_fingerprint'
     );
  IF overlay_index_columns <> 7 THEN
    RAISE EXCEPTION 'UAT overlay registration columns do not match reviewed indexes';
  END IF;
  SELECT count(*) INTO invalid_transport_rows
    FROM public.overlay_networks
   WHERE transport_kind IS DISTINCT FROM 'vless-xhttp'
      OR transport_path IS NULL OR transport_path = ''
      OR transport_mode IS NULL OR transport_mode = '';
  IF invalid_transport_rows <> 0 THEN
    RAISE EXCEPTION 'UAT XHTTP transport data is not normalized; refusing baseline';
  END IF;
END;
$$;

-- The existing UAT table has the reviewed shape but is missing these four
-- indexes from 2026090802. Reconcile them idempotently inside this bounded
-- transaction before adopting the migration baseline.
CREATE INDEX IF NOT EXISTS overlay_registrations_owner_created_idx
  ON public.overlay_registrations (owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS overlay_registrations_network_pending_idx
  ON public.overlay_registrations (network_id, status, expires_at);
CREATE INDEX IF NOT EXISTS overlay_registrations_network_created_idx
  ON public.overlay_registrations (network_id, created_at);
CREATE INDEX IF NOT EXISTS overlay_registrations_identity_pending_idx
  ON public.overlay_registrations (network_id, device_id, wireguard_public_key_fingerprint, status, expires_at);

-- Equivalent expand-only effect of Accounts 2026091301. Newly added columns
-- remain NULL for every existing user, preserving plan/group/active settings.
ALTER TABLE public.users
  ADD COLUMN subscription_valid_from TIMESTAMPTZ,
  ADD COLUMN subscription_valid_until TIMESTAMPTZ,
  ADD COLUMN last_active_at TIMESTAMPTZ,
  ADD COLUMN archived_at TIMESTAMPTZ;

ALTER TABLE public.users
  ADD CONSTRAINT users_subscription_validity_order_ck
  CHECK (
    subscription_valid_from IS NULL
    OR subscription_valid_until IS NULL
    OR subscription_valid_until >= subscription_valid_from
  ) NOT VALID;
ALTER TABLE public.users VALIDATE CONSTRAINT users_subscription_validity_order_ck;

-- Adopt only after all prior shape/data checks and the missing expand step
-- succeed in this same transaction. golang-migrate's PostgreSQL table shape.
CREATE TABLE public.schema_migrations (
  version bigint NOT NULL PRIMARY KEY,
  dirty boolean NOT NULL
);
INSERT INTO public.schema_migrations (version, dirty) VALUES (2026091401, false);
COMMIT;
