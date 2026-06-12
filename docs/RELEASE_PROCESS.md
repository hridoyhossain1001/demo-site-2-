# Release Process

Keep the server version, plugin version, zip package, and changelog in sync.

## Version Rules

- WordPress plugin header version in `wordpress-plugin/buykori-adsync/buykori-adsync.php`.
- `BUYKORIGW_VERSION` in the same file.
- Server `PLUGIN_VERSION` default in `app/routers/plugin.py`.
- Plugin `readme.txt` changelog.

All four should describe the same release.

## Release Steps

1. Update plugin code.
2. Update server code if needed.
3. Update changelog.
4. Run Python compile checks.
5. Run `pytest`.
6. Run PHP lint for every plugin PHP file.
7. Rebuild `wordpress-plugin/buykori-adsync.zip` with `python scripts/ops/zip_plugin.py`.
8. Deploy the rebuilt `wordpress-plugin/buykori-adsync.zip` with the server files so `/api/v1/plugin/download` never points to a missing or stale package.
9. Verify `/api/v1/plugin/info` returns `package_available: true`, the expected `version`, and the new `package_sha256`.
10. Run `alembic upgrade head`.
11. Test update flow in staging.
12. Release to production clients.

## Rollback

- Keep the last known working zip as a backup.
- If the update flow fails, restore the previous zip and version metadata.
- If a database migration causes an issue, stop the app and restore from database backup before retrying.
