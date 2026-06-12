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
