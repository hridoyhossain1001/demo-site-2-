import pytest

from deploy import changed_deploy
from scripts.ops import release_readiness, staging_limited_deploy


def test_changed_files_working_tree_excludes_untracked_deployables_by_default(monkeypatch):
    outputs = {
        ("diff", "--name-status", "origin/main"): "M\tapp/main.py\nD\tapp/old.py",
        ("ls-files", "--others", "--exclude-standard"): (
            "app/services/new_queue.py\n"
            "migrations/versions/new_queue.py\n"
            "client-portal/src/App.tsx"
        ),
    }
    monkeypatch.setattr(changed_deploy, "run_git", lambda args: outputs.get(tuple(args), ""))

    changes = changed_deploy.changed_files("origin/main", include_working_tree=True)

    assert ("M", "app/main.py") in changes
    assert ("D", "app/old.py") in changes
    assert ("A", "app/services/new_queue.py") not in changes
    assert ("A", "migrations/versions/new_queue.py") not in changes
    assert all(path != "client-portal/src/App.tsx" for _, path in changes)


def test_changed_files_can_include_untracked_deployables(monkeypatch):
    outputs = {
        ("diff", "--name-status", "origin/main"): "M\tapp/main.py\nD\tapp/old.py",
        ("ls-files", "--others", "--exclude-standard"): (
            "app/services/new_queue.py\n"
            "migrations/versions/new_queue.py\n"
            "client-portal/src/App.tsx"
        ),
    }
    monkeypatch.setattr(changed_deploy, "run_git", lambda args: outputs.get(tuple(args), ""))

    changes = changed_deploy.changed_files(
        "origin/main",
        include_working_tree=True,
        include_untracked=True,
    )

    assert ("M", "app/main.py") in changes
    assert ("D", "app/old.py") in changes
    assert ("A", "app/services/new_queue.py") in changes
    assert ("A", "migrations/versions/new_queue.py") in changes
    assert all(path != "client-portal/src/App.tsx" for _, path in changes)


def test_local_deployable_changes_reports_omitted_dirty_paths(monkeypatch):
    outputs = {
        ("diff", "--name-only"): "app/routers/admin_api.py\nREADME.md",
        ("diff", "--cached", "--name-only"): "requirements.txt",
        ("ls-files", "--others", "--exclude-standard"): (
            "app/static/client-portal/assets/new.js\n"
            "client-portal/src/App.tsx"
        ),
    }
    monkeypatch.setattr(changed_deploy, "run_git", lambda args: outputs.get(tuple(args), ""))

    assert changed_deploy.local_deployable_changes() == [
        "app/routers/admin_api.py",
        "app/static/client-portal/assets/new.js",
        "requirements.txt",
    ]


def test_staging_limited_manifest_is_valid_and_includes_release_dependencies():
    paths = staging_limited_deploy.read_manifest()

    assert len(paths) >= 60
    assert "app/dependencies.py" in paths
    assert "app/routers/client_auth.py" in paths
    assert "app/routers/events.py" in paths
    assert "app/services/client_secrets.py" in paths
    assert "app/services/fraud_service.py" in paths
    assert "app/services/usage_service.py" in paths
    assert "app/static/client-portal/index.html" in paths
    assert "app/static/client-portal/assets/index-DokrZTa0.js" in paths
    assert "migrations/versions/aa1b2c3d4e5f_add_woocommerce_webhook_secret.py" in paths
    assert "migrations/versions/b1c2d3e4f5a6_merge_woocommerce_secret_and_reconciliation_indexes.py" in paths
    assert "migrations/versions/c2d3e4f5a6b7_add_capi_signing_secret.py" in paths
    assert "wordpress-plugin/buykori-adsync.zip" in paths


def test_staging_limited_deploy_defaults_to_dry_run():
    command = staging_limited_deploy.build_command(
        ["app/dependencies.py"],
        apply=False,
        base="origin/main",
    )

    assert "--dry-run" in command
    assert command[-2:] == ["app/dependencies.py", "--dry-run"]


def test_staging_limited_deploy_refuses_active_host():
    env = {
        "DO_SSH_HOST": "active.example",
        "STAGING_SSH_HOST": "active.example",
        "STAGING_SSH_USER": "deploy",
        "STAGING_DEPLOY_CONFIRM": "staging",
    }

    try:
        staging_limited_deploy.staging_environment(env, apply=True)
    except ValueError as exc:
        assert "matches the active DO_SSH_HOST" in str(exc)
    else:
        raise AssertionError("Staging deploy accepted the active host")


def test_staging_limited_deploy_requires_companion_release_acknowledgements():
    env = {
        "STAGING_SSH_HOST": "staging.example",
        "STAGING_SSH_USER": "deploy",
        "STAGING_DEPLOY_CONFIRM": "staging",
    }

    with pytest.raises(ValueError, match="PORTAL_RELEASE_CONFIRM"):
        staging_limited_deploy.staging_environment(env, apply=True)

    env["PORTAL_RELEASE_CONFIRM"] = "csrf-wrapper"
    with pytest.raises(ValueError, match="PLUGIN_RELEASE_CONFIRM"):
        staging_limited_deploy.staging_environment(env, apply=True)


def test_release_readiness_gate_passes_for_current_workspace():
    assert release_readiness.readiness_failures() == []
