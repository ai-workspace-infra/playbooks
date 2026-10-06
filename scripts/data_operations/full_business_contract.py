"""Reviewed Accounts business scope for explicit two-hop UAT initialization.

Operational migration/checkpoint ledgers and Supabase auth/storage schemas are
not business rows. Their identities must never be replayed across environments.
New public business tables require a reviewed scope update before initialization.
"""
LEGACY_BUSINESS_TABLES = (
    "account_billing_profiles", "account_policy_snapshots", "account_quota_states",
    "admin_settings", "agents", "audit_logs", "billing_ledger", "billing_plans",
    "billing_source_sync_state", "bridge_credentials", "email_blacklist",
    "homepage_video_settings", "identities", "node_health_snapshots", "nodes",
    "oauth_exchange_codes", "overlay_config_acks", "overlay_device_credentials",
    "overlay_devices", "overlay_enrollment_sessions", "overlay_invites",
    "overlay_networks", "overlay_nodes", "overlay_registrations",
    "overlay_signed_config_acks", "rbac_permissions", "rbac_role_permissions",
    "rbac_roles", "sandbox_bindings", "scheduler_decisions", "sessions",
    "stripe_webhook_events", "subscriptions", "task_namespaces", "task_runs",
    "task_session_events", "task_sessions", "tenant_domains", "tenant_memberships",
    "tenants", "traffic_minute_buckets", "traffic_stat_checkpoints", "users",
    "xworkmate_profiles",
)
BUSINESS_TABLES = tuple(sorted(LEGACY_BUSINESS_TABLES + (
    "account_lifecycle_events", "mfa_recovery_codes", "password_recovery_challenges",
    "finance_invoices", "finance_payments", "finance_refunds", "finance_operations",
    "finance_operation_events",
)))
CONTROL_TABLES = ("schema_migrations", "system_release_checkpoints")


def validate_source_tables(tables):
    actual = set(tables) - set(CONTROL_TABLES)
    if not set(LEGACY_BUSINESS_TABLES).issubset(actual) or not actual.issubset(BUSINESS_TABLES):
        raise ValueError("Source business table scope differs from the reviewed contract")


def readonly_policy_sql(tables=BUSINESS_TABLES, role="readonly_release"):
    if (not tables or tuple(tables) != tuple(t for t in BUSINESS_TABLES if t in tables)
            or role != "readonly_release"):
        raise ValueError("Arbitrary role or table scope is prohibited")
    names = ",".join("'" + name + "'" for name in tables)
    grants = "\n".join(f'GRANT SELECT ON TABLE public."{name}" TO readonly_release;' for name in tables)
    return grants + f"""
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY[{names}] LOOP
  IF (SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass('public.' || tab)) THEN
   IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=to_regclass('public.' || tab) AND polname='release_initialization_readonly') THEN
    IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=to_regclass('public.' || tab) AND polname='release_initialization_readonly' AND polcmd='r' AND polroles=ARRAY[(SELECT oid FROM pg_roles WHERE rolname='readonly_release')] AND pg_get_expr(polqual,polrelid)='true' AND polwithcheck IS NULL) THEN
     RAISE EXCEPTION 'Existing readonly policy differs from approved contract';
    END IF;
   ELSE
   EXECUTE format('CREATE POLICY release_initialization_readonly ON public.%I FOR SELECT TO readonly_release USING (true)',tab);
   END IF;
  END IF;
 END LOOP;
END $$;
"""
