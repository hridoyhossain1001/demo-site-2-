from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]


def test_admin_portal_contains_courier_queue_monitor_contract():
    app_js = (WORKSPACE / "admin-portal" / "app.js").read_text(encoding="utf-8")
    index_html = (WORKSPACE / "admin-portal" / "index.html").read_text(encoding="utf-8")
    styles_css = (WORKSPACE / "admin-portal" / "styles.css").read_text(encoding="utf-8")

    assert "/admin/api/courier-booking-queue?limit=20" in app_js
    assert "/admin/api/courier-booking-queue/${jobId}/retry" in app_js
    assert "/admin/api/client-intelligence" in app_js
    assert "/admin/api/server-health" in app_js
    assert "/admin/api/logout" in app_js
    assert 'credentials: "include"' in app_js
    assert "X-Admin-CSRF-Token" in app_js
    assert "buykori_admin_csrf" in app_js
    assert "buykori_admin_jwt" not in app_js
    assert "sessionStorage" not in app_js
    assert "/admin/api/clients/${clientId}/support-notes" in app_js
    assert "function renderCourierQueue()" in app_js
    assert "function renderClientIntelligence()" in app_js
    assert "function renderOpsMonitor()" in app_js
    assert "function startCourierQueueAutoRefresh()" in app_js
    assert "function openCourierJobDrawer(jobId)" in app_js
    assert 'data-tab="courierQueue"' in index_html
    assert 'data-tab="clientIntel"' in index_html
    assert 'data-tab="opsMonitor"' in index_html
    assert 'id="serverCpuUsed"' in index_html
    assert 'id="serverCpuMeta"' in index_html
    assert 'id="courierQueueRows"' in index_html
    assert 'id="trialFollowupRows"' in index_html
    assert 'id="workerMonitorRows"' in index_html
    assert 'id="supportNotesList"' in index_html
    assert 'id="courierQueueHealthBanner"' in index_html


def test_admin_portal_contains_recovery_and_notification_operations_contract():
    app_js = (WORKSPACE / "admin-portal" / "app.js").read_text(encoding="utf-8")
    index_html = (WORKSPACE / "admin-portal" / "index.html").read_text(encoding="utf-8")
    styles_css = (WORKSPACE / "admin-portal" / "styles.css").read_text(encoding="utf-8")

    assert 'data-tab="recoveryOps"' in index_html
    assert 'id="recoveryRows"' in index_html
    assert 'id="recoveryClientFilter"' in index_html
    assert "/admin/api/incomplete-checkouts?${params.toString()}" in app_js
    assert "/admin/api/incomplete-checkouts/${checkoutId}" in app_js

    assert 'data-tab="notificationOps"' in index_html
    assert 'id="notificationRows"' in index_html
    assert 'id="whatsappInstanceRows"' in index_html
    assert "/admin/notification-jobs?${params.toString()}" in app_js
    assert 'api("/admin/whatsapp-instances")' in app_js
    assert 'id="queueDrawerOverlay"' in index_html
    assert ".queue-alert-critical" in styles_css
    assert ".queue-health-banner" in styles_css
    assert ".queue-drawer" in styles_css
    assert ".support-note" in styles_css


def test_admin_login_does_not_trim_password_before_submit():
    app_js = (WORKSPACE / "admin-portal" / "app.js").read_text(encoding="utf-8")
    login_block = app_js.split("async function loginAdmin()", 1)[1].split("async function logoutAdmin()", 1)[0]

    assert 'const username = $("adminUsername").value.trim();' in login_block
    assert 'const password = $("adminPassword").value;' in login_block
    assert '$("adminPassword").value.trim()' not in login_block
    assert "JSON.stringify({ username, password })" in login_block


def test_admin_events_label_reconstructed_samples():
    app_js = (WORKSPACE / "admin-portal" / "app.js").read_text(encoding="utf-8")
    admin_api = (WORKSPACE / "app" / "routers" / "admin_api.py").read_text(encoding="utf-8")
    events_block = app_js.split("function renderEvents()", 1)[1].split("function toggleEventDetail", 1)[0]

    assert '"isReconstructedSample": True' in admin_api
    assert '"sampleNotice": "Payload, headers, HTTP code, and upstream response are reconstructed' in admin_api
    assert "const sampleLabel = event.isReconstructedSample ? \" (reconstructed sample)\" : \"\";" in events_block
    assert "event.sampleNotice" in events_block
    assert "Payload${sampleLabel}" in events_block
    assert "HTTP Headers${sampleLabel}" in events_block
    assert "Upstream Response${sampleLabel}" in events_block


def test_admin_order_quota_is_clearly_plan_derived():
    index_html = (WORKSPACE / "admin-portal" / "index.html").read_text(encoding="utf-8")

    assert "Monthly Order Limit (Plan-derived)" in index_html
    assert 'id="editOrderLimit" readonly aria-readonly="true"' in index_html
    assert "change the plan to update this limit" in index_html


def test_admin_modal_does_not_preload_existing_secrets():
    app_js = (WORKSPACE / "admin-portal" / "app.js").read_text(encoding="utf-8")
    modal_block = app_js.split("async function openClientModal", 1)[1].split("async function saveClientEdit", 1)[0]

    assert 'modalSecrets.set("keyApi", c.api_key || "")' not in modal_block
    assert 'modalSecrets.set("keyPortal", c.portal_key || "")' not in modal_block
    assert 'modalSecrets.set("keyToken", c.access_token || "")' not in modal_block
    assert "Existing secrets are masked server-side" in modal_block
    assert "<rotate-or-copy-current-api-key>" in modal_block
