# Migration and Backup Runbook

## Daily backup

Install the versioned user cron entry:

```bash
crontab /var/www/buykori-adsync/scripts/ops/buykori-backup.cron
```

Backups are custom-format PostgreSQL archives with a SHA-256 manifest. They are
stored under `/var/www/buykori-adsync/.backups/postgresql`, retained for 30 days, and must
also be copied to an off-server destination.

Set `BACKUP_UPLOAD_COMMAND` in the cron environment after configuring an
off-server tool such as `rclone`. The command receives `BACKUP_FILE` and
`BACKUP_MANIFEST`.

## Verify a backup

```bash
pg_restore --list /var/www/buykori-adsync/.backups/postgresql/buykori-*.dump >/dev/null
sha256sum /var/www/buykori-adsync/.backups/postgresql/buykori-*.dump
```

The daily non-disruptive preparation check runs after the backup. Run it
manually at any time without enabling migration mode:

```bash
scripts/ops/migration_readiness.sh
```

At least monthly, restore the newest dump into a disposable PostgreSQL
database on a staging server and test login, API health, workers, and row
counts.

When the database role has permission to create disposable databases, run:

```bash
scripts/ops/db_restore_test.sh
```

If the production database role cannot create databases, create a disposable
restore database with a privileged account first, then run the test against
that explicit target. The script refuses non-test-looking database names unless
you override it deliberately.

```bash
RESTORE_TEST_DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/buykori_restore_test" \
RESTORE_TEST_CONFIRM=buykori_restore_test \
scripts/ops/db_restore_test.sh
```

This mode drops and recreates only the `public` schema in the disposable target
database. Do not point it at production or staging application databases.

## Migration cutover

1. Restore a recent backup on the destination and complete smoke tests.
2. Lower DNS TTL to 300 seconds at least 48 hours before cutover.
3. Enable the source migration lock:
   `scripts/ops/migration_lock.sh on "VPS migration"`
4. Confirm mutating API calls return HTTP 503 and Supervisor workers restarted.
5. Run `scripts/ops/db_backup.sh` and transfer the newest dump and manifest.
6. Verify SHA-256 on the destination, restore, and run migrations.
7. Preserve the existing `.env` secrets, especially encryption and signing keys.
8. Start the destination services and run login/API/webhook smoke tests.
9. Change DNS to the destination.
10. Keep the source locked and available for rollback for at least 7 days.

Unlock the source only when aborting the migration:

```bash
scripts/ops/migration_lock.sh off
```

## Stabilized release rollout

This release changes the backend, client portal, and WordPress plugin together.
Use this order on staging:

1. Build and verify the client portal, then deploy the portal containing the
   global fetch wrapper. Backend client-session mutations require its CSRF
   header after the backend rollout.
2. Build the WordPress plugin package with
   `python scripts/ops/zip_plugin.py`. Install or reconnect the updated plugin
   on the staging store so it receives its dedicated CAPI signing secret.
3. Set the staging backend environment:
   `FRAUD_AUTO_HOLD_THRESHOLD=90`,
   `ALLOW_GLOBAL_COURIER_WEBHOOK_SECRET_FALLBACK=false`, and
   `ALLOW_GLOBAL_STEADFAST_WEBHOOK_TOKEN_FALLBACK=false`.
4. Keep `ALLOW_CAPI_API_KEY_SIGNING_FALLBACK=true` only during a controlled
   compatibility window for existing production plugins. Disable it after
   affected stores reconnect/update and signed event traffic is confirmed.
   New installs and staging should use `false`.
5. Dry-run the backend scope:
   `python scripts/ops/release_readiness.py`, then
   `python scripts/ops/staging_limited_deploy.py`.
6. Before applying to staging, set
   `STAGING_DEPLOY_CONFIRM=staging`,
   `PORTAL_RELEASE_CONFIRM=csrf-wrapper`, and
   `PLUGIN_RELEASE_CONFIRM=capi-signing-secret`.
   Use `deploy/staging.env.example` as the configuration checklist.
7. Apply migrations and backend scope, then run:
   `python scripts/ops/staging_smoke_check.py`.
8. Verify client login/logout, a cookie-authenticated mutation, signed event
   ingest, a high-risk Purchase entering `fraud_review`, normal Purchase
   delivery, and shared-store monthly quota rejection.

Do not enable strict CAPI fallback-off in production until updated plugins have
received `capi_signing_secret`; otherwise locked-domain event ingest can return
HTTP 403.

## Root-only preparation

The deployment user cannot perform these items. Complete them with root access:

- Configure a 2-4 GB swap file.
- Audit Docker containers, images, volumes, and the localhost port 8080 service.
- Verify UFW exposes only SSH, HTTP, and HTTPS.
- Cap persistent journal storage and verify log rotation.
- Export Nginx, Supervisor, PostgreSQL, Redis, Certbot, cron, and firewall config.
- Enable provider-level Droplet backups as an additional disaster-recovery layer.
