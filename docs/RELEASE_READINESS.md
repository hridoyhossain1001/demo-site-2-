# Release Readiness Status

Updated: 2026-06-13

## Local status

- Release readiness gate: passed
- Alembic graph: one head, `c2d3e4f5a6b7`
- Staging limited deploy dry-run: passed, 44 deployable paths
- Client portal lint/build: passed
- WordPress plugin package: rebuilt, version `1.2.51`
- Plugin package SHA-256:
  `8e1fb5a683b19147287c1117a3f1081266a7964be7e4c7d15c462c037a64647d`

Run the local gate before every staging or production attempt:

```bash
python scripts/ops/release_readiness.py
python scripts/ops/staging_limited_deploy.py
```

## Required rollout order

1. Deploy the updated client portal containing the CSRF/credentials fetch
   wrapper.
2. Make the rebuilt WordPress plugin package available and reconnect/update
   staging stores so they receive `capi_signing_secret`.
3. Configure the staging backend environment as documented in
   `docs/MIGRATION_RUNBOOK.md`.
4. Apply the 44-path staging backend/plugin scope and migrations.
5. Run automated staging smoke checks and the documented manual workflow
   checks.

## External blockers before staging apply

- A separately configured staging SSH host and deploy user.
- A recent verified staging database backup/restore point.
- Confirmation that the updated client portal is deployed.
- Confirmation that the updated WordPress plugin is installed/reconnected on
  the staging store.
- Staging secrets and environment values, including the temporary CAPI
  compatibility-window decision.

The staging deploy tool will refuse `--apply` until these acknowledgements are
set:

```bash
STAGING_DEPLOY_CONFIRM=staging
PORTAL_RELEASE_CONFIRM=csrf-wrapper
PLUGIN_RELEASE_CONFIRM=capi-signing-secret
```

Use `deploy/staging.env.example` as the non-secret configuration checklist.
Keep real staging hostnames and credentials outside the repository.

## Production deployment record

Production deployment completed on 2026-06-13.

- Pre-deploy database backup archive was fresh and passed `pg_restore --list`.
- Production `.env` backup:
  `.env.pre-release-20260613T124607Z`
- Controlled release scope: 62 paths, including the backend-served client portal
  bundle and WordPress plugin package.
- Alembic upgraded from `f5g6h7i8j9k0` to `c2d3e4f5a6b7`.
- Web and both event workers restarted and are running.
- Internal/external health, authenticated admin summary/queue, portal asset,
  plugin ZIP, and live event ingest smoke checks passed.

During the first restart, the web process correctly refused startup because
production lacked the newly required `ADMIN_JWT_SECRET`. A new random signing
secret was generated, services were restarted, and health checks passed.
Existing admin sessions were invalidated; `ADMIN_API_KEY` was not changed.

`ALLOW_CAPI_API_KEY_SIGNING_FALLBACK=true` is temporarily enabled for existing
WordPress plugins. After stores update/reconnect and receive
`capi_signing_secret`, set it to `false`, restart services, and verify signed
event traffic remains `202 Accepted`.
