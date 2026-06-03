<?php
/**
 * Buykori AdSync — Admin Settings Page
 *
 * WordPress Admin প্যানেলে সুন্দর সেটিংস পেজ তৈরি করে।
 * ক্লায়েন্ট শুধু API Key বসাবে, বাকি সব অটোমেটিক।
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

// ─── Register Admin Menu ───────────────────────────────────────────────────────
add_action( 'admin_menu', 'buykorigw_admin_menu' );

function buykorigw_admin_menu() {
    add_menu_page(
        'Buykori AdSync Settings',       // Page title
        'Buykori AdSync',                // Menu title
        'manage_options',              // Capability
        'buykori-adsync',                // Menu slug
        'buykorigw_settings_page',        // Callback function
        'dashicons-chart-area',        // Icon
        58                             // Position
    );
}

// ─── Register Settings ─────────────────────────────────────────────────────────
add_action( 'admin_init', 'buykorigw_register_settings' );

function buykorigw_register_settings() {
    register_setting( 'buykorigw_settings_group', BUYKORIGW_OPTION_KEY, 'buykorigw_sanitize_settings' );
}

// ─── Admin Notice: Cache Cleared After Save ────────────────────────────────────
add_action( 'admin_notices', 'buykorigw_maybe_show_cache_notice' );

function buykorigw_maybe_show_cache_notice() {
    $screen = get_current_screen();
    if ( ! $screen || $screen->id !== 'toplevel_page_buykori-adsync' ) {
        return;
    }

    if ( isset( $_GET['buykorigw_connect'] ) ) {
        $type    = sanitize_text_field( wp_unslash( $_GET['buykorigw_connect'] ) );
        $message = isset( $_GET['buykorigw_connect_msg'] )
            ? sanitize_text_field( rawurldecode( wp_unslash( $_GET['buykorigw_connect_msg'] ) ) )
            : '';
        if ( $type === 'success' ) {
            echo '<div class="notice notice-success is-dismissible" style="border-left-color:#059669;"><p><strong>Buykori AdSync:</strong> ' . esc_html( $message ?: 'WordPress site connected successfully.' ) . '</p></div>';
        } elseif ( $type === 'error' ) {
            echo '<div class="notice notice-error is-dismissible"><p><strong>Buykori AdSync:</strong> ' . esc_html( $message ?: 'Connection failed. Please try again.' ) . '</p></div>';
        }
    }

    // Only show after a settings save (settings-updated query param)
    if ( ! isset( $_GET['settings-updated'] ) || $_GET['settings-updated'] !== 'true' ) {
        return;
    }
    ?>
    <div class="notice notice-success is-dismissible" style="border-left-color:#4f46e5;">
        <p>
            <strong>⚡ Buykori AdSync:</strong>
            সেটিংস সেভ হয়েছে এবং ওয়েবসাইটের পেজ ক্যাশ <strong>স্বয়ংক্রিয়ভাবে ক্লিয়ার</strong> করা হয়েছে।
            আপনি যদি Cloudflare বা অন্য কোনো CDN ব্যবহার করেন, তাহলে সেখান থেকেও ম্যানুয়ালি ক্যাশ ক্লিয়ার করুন।
        </p>
    </div>
    <?php
}

function buykorigw_sanitize_settings( $input ) {
    $sanitized = array();
    $existing = buykorigw_get_settings();
    $sanitized['api_key']            = sanitize_text_field( $input['api_key'] ?? ( $existing['api_key'] ?? '' ) );
    $sanitized['gateway_url']        = esc_url_raw( $input['gateway_url'] ?? ( $existing['gateway_url'] ?? BUYKORIGW_DEFAULT_GATEWAY_URL ) );
    $sanitized['connected_site_host']   = sanitize_text_field( $input['connected_site_host'] ?? ( $existing['connected_site_host'] ?? '' ) );
    $sanitized['connected_client_name'] = sanitize_text_field( $input['connected_client_name'] ?? ( $existing['connected_client_name'] ?? '' ) );
    $sanitized['connected_at']          = sanitize_text_field( $input['connected_at'] ?? ( $existing['connected_at'] ?? '' ) );
    $sanitized['connect_warning']       = sanitize_text_field( $input['connect_warning'] ?? ( $existing['connect_warning'] ?? '' ) );
    $sanitized['installation_id']       = sanitize_text_field( $input['installation_id'] ?? ( $existing['installation_id'] ?? buykorigw_get_installation_id() ) );
    $sanitized['low_resource_mode']  = 0;
    // Core Events
    $sanitized['enable_pageview']       = isset( $input['enable_pageview'] ) ? 1 : 0;
    $sanitized['enable_lead']           = isset( $input['enable_lead'] ) ? 1 : 0;
    $sanitized['enable_search']         = isset( $input['enable_search'] ) ? 1 : 0;
    // WooCommerce Events
    $sanitized['enable_viewcontent']    = isset( $input['enable_viewcontent'] ) ? 1 : 0;
    $sanitized['enable_addtocart']      = isset( $input['enable_addtocart'] ) ? 1 : 0;
    $sanitized['enable_viewcart']       = isset( $input['enable_viewcart'] ) ? 1 : 0;
    $sanitized['enable_removefromcart'] = isset( $input['enable_removefromcart'] ) ? 1 : 0;
    $sanitized['enable_checkout']       = isset( $input['enable_checkout'] ) ? 1 : 0;
    $sanitized['enable_addpaymentinfo'] = isset( $input['enable_addpaymentinfo'] ) ? 1 : 0;
    $sanitized['enable_purchase']       = isset( $input['enable_purchase'] ) ? 1 : 0;
    $sanitized['tracking_mode']      = 'auto';
    $sanitized['deferred_purchase']  = isset( $input['deferred_purchase'] ) ? 1 : 0;
    $sanitized['auto_confirm_status']= sanitize_text_field( $input['auto_confirm_status'] ?? 'completed' );
    $sanitized['debug_mode']         = isset( $input['debug_mode'] ) ? 1 : 0;
    $content_id_format = sanitize_text_field( $input['content_id_format'] ?? 'id' );
    $sanitized['content_id_format']  = in_array( $content_id_format, array( 'id', 'sku' ), true ) ? $content_id_format : 'id';
    $sanitized['enable_hybrid']      = isset( $input['enable_hybrid'] ) ? 1 : 0;
    $sanitized['enable_variations']  = 1;
    $sanitized['fb_pixel_id']        = sanitize_text_field( trim( $input['fb_pixel_id'] ?? '' ) );
    $sanitized['tt_pixel_id']        = sanitize_text_field( trim( $input['tt_pixel_id'] ?? '' ) );
    return $sanitized;
}

// ─── AJAX: Connection Test ─────────────────────────────────────────────────────
add_action( 'wp_ajax_buykorigw_test_connection', 'buykorigw_test_connection' );
add_action( 'wp_ajax_buykorigw_check_update_now', 'buykorigw_check_update_now' );
add_action( 'admin_post_buykorigw_connect_start', 'buykorigw_connect_start' );
add_action( 'admin_post_buykorigw_connect_callback', 'buykorigw_connect_callback' );
add_action( 'admin_post_buykorigw_disconnect', 'buykorigw_disconnect_account' );

function buykorigw_test_connection() {
    check_ajax_referer( 'buykorigw_nonce', 'nonce' );

    if ( ! current_user_can( 'manage_options' ) ) {
        wp_send_json_error( 'Permission denied' );
    }

    $api_key     = sanitize_text_field( $_POST['api_key'] ?? '' );
    $gateway_url = esc_url_raw( $_POST['gateway_url'] ?? '' );

    if ( empty( $api_key ) || empty( $gateway_url ) ) {
        wp_send_json_error( 'API Key এবং AdSync API URL দিন।' );
    }

    // Send a test ping to the gateway health endpoint
    $url = rtrim( $gateway_url, '/' ) . '/health';

    $response = wp_remote_get( $url, array(
        'timeout'   => 30,
        'sslverify' => true,
        'headers'   => array(
            'X-API-Key' => $api_key,
        ),
    ) );

    if ( is_wp_error( $response ) ) {
        wp_send_json_error( 'Connection failed: ' . $response->get_error_message() );
    }

    $code = wp_remote_retrieve_response_code( $response );
    $body = wp_remote_retrieve_body( $response );

    if ( $code === 200 ) {
        wp_send_json_success( 'Connected successfully! Server is online.' );
    } else {
        wp_send_json_error( "Server responded with HTTP $code. Response: $body" );
    }
}

function buykorigw_check_update_now() {
    check_ajax_referer( 'buykorigw_nonce', 'nonce' );

    if ( ! current_user_can( 'update_plugins' ) ) {
        wp_send_json_error( 'Permission denied' );
    }

    if ( function_exists( 'buykorigw_clear_update_cache' ) ) {
        buykorigw_clear_update_cache();
    } else {
        delete_site_transient( 'update_plugins' );
    }

    if ( function_exists( 'wp_update_plugins' ) ) {
        wp_update_plugins();
    }

    wp_send_json_success( 'Update cache cleared. Please refresh the Plugins page or open Dashboard → Updates.' );
}

// ─── Settings Page HTML ────────────────────────────────────────────────────────
function buykorigw_connect_transient_key() {
    return 'buykorigw_connect_' . get_current_user_id();
}

function buykorigw_base64url( $data ) {
    return rtrim( strtr( base64_encode( $data ), '+/', '-_' ), '=' );
}

function buykorigw_random_urlsafe( $bytes = 32 ) {
    try {
        return buykorigw_base64url( random_bytes( $bytes ) );
    } catch ( Exception $e ) {
        return wp_generate_password( 48, false, false );
    }
}

function buykorigw_pkce_challenge( $verifier ) {
    return buykorigw_base64url( hash( 'sha256', $verifier, true ) );
}

function buykorigw_portal_url_from_gateway( $gateway_url ) {
    $parts = wp_parse_url( $gateway_url );
    if ( empty( $parts['host'] ) ) {
        return 'https://client.buykori.app';
    }

    $scheme = ! empty( $parts['scheme'] ) ? $parts['scheme'] : 'https';
    $host   = $parts['host'];
    $port   = ! empty( $parts['port'] ) ? ':' . intval( $parts['port'] ) : '';

    return $scheme . '://' . $host . $port;
}

function buykorigw_connect_redirect( $type, $message = '' ) {
    $args = array( 'page' => 'buykori-adsync', 'buykorigw_connect' => $type );
    if ( $message ) {
        $args['buykorigw_connect_msg'] = rawurlencode( $message );
    }
    wp_safe_redirect( add_query_arg( $args, admin_url( 'admin.php' ) ) );
    exit;
}

function buykorigw_connect_start() {
    check_admin_referer( 'buykorigw_connect_account' );

    if ( ! current_user_can( 'manage_options' ) ) {
        wp_die( esc_html__( 'Permission denied.', 'buykori-adsync' ) );
    }

    $settings       = buykorigw_get_settings();
    $gateway_url    = rtrim( $settings['gateway_url'] ?: BUYKORIGW_DEFAULT_GATEWAY_URL, '/' );
    $portal_url     = rtrim( buykorigw_portal_url_from_gateway( $gateway_url ), '/' );
    $state          = buykorigw_random_urlsafe( 24 );
    $code_verifier  = buykorigw_random_urlsafe( 48 );
    $code_challenge = buykorigw_pkce_challenge( $code_verifier );
    $return_url     = admin_url( 'admin-post.php?action=buykorigw_connect_callback' );

    set_transient(
        buykorigw_connect_transient_key(),
        array(
            'state'         => $state,
            'code_verifier' => $code_verifier,
            'gateway_url'   => $gateway_url,
        ),
        15 * MINUTE_IN_SECONDS
    );

    $authorize_url = add_query_arg(
        array(
            'site_url'       => home_url( '/' ),
            'return_url'     => $return_url,
            'state'          => $state,
            'code_challenge' => $code_challenge,
        ),
        $portal_url . '/plugin/connect'
    );

    wp_redirect( $authorize_url );
    exit;
}

function buykorigw_connect_callback() {
    if ( ! current_user_can( 'manage_options' ) ) {
        wp_die( esc_html__( 'Permission denied.', 'buykori-adsync' ) );
    }

    if ( ! empty( $_GET['error'] ) ) {
        buykorigw_connect_redirect( 'error', sanitize_text_field( wp_unslash( $_GET['error'] ) ) );
    }

    $code  = sanitize_text_field( wp_unslash( $_GET['code'] ?? '' ) );
    $state = sanitize_text_field( wp_unslash( $_GET['state'] ?? '' ) );
    $data  = get_transient( buykorigw_connect_transient_key() );
    delete_transient( buykorigw_connect_transient_key() );

    if ( empty( $code ) || empty( $state ) || empty( $data['state'] ) || ! hash_equals( $data['state'], $state ) ) {
        buykorigw_connect_redirect( 'error', 'Invalid or expired connection session.' );
    }

    $gateway_url = rtrim( $data['gateway_url'] ?: BUYKORIGW_DEFAULT_GATEWAY_URL, '/' );
    $response    = wp_remote_post(
        $gateway_url . '/plugin/connect/exchange',
        array(
            'timeout'   => 30,
            'sslverify' => true,
            'headers'   => array( 'Content-Type' => 'application/json' ),
            'body'      => wp_json_encode(
                array(
                    'code'         => $code,
                    'codeVerifier' => $data['code_verifier'],
                    'state'        => $state,
                    'siteUrl'      => home_url( '/' ),
                    'installationId' => buykorigw_get_installation_id(),
                )
            ),
        )
    );

    if ( is_wp_error( $response ) ) {
        buykorigw_connect_redirect( 'error', $response->get_error_message() );
    }

    $status = wp_remote_retrieve_response_code( $response );
    $body   = json_decode( wp_remote_retrieve_body( $response ), true );
    if ( $status !== 200 || empty( $body['api_key'] ) || empty( $body['gateway_url'] ) ) {
        $message = ! empty( $body['detail'] ) ? $body['detail'] : 'Buykori authorization failed.';
        buykorigw_connect_redirect( 'error', sanitize_text_field( $message ) );
    }

    $settings                          = buykorigw_get_settings();
    $settings['api_key']               = sanitize_text_field( $body['api_key'] );
    $settings['gateway_url']           = esc_url_raw( $body['gateway_url'] );
    $settings['connected_site_host']   = sanitize_text_field( $body['site_host'] ?? '' );
    $settings['connected_client_name'] = sanitize_text_field( $body['client_name'] ?? '' );
    $settings['connected_at']          = gmdate( 'c' );
    $settings['connect_warning']       = sanitize_text_field( $body['plan_warning'] ?? '' );
    $settings['installation_id']       = sanitize_text_field( $body['installation_id'] ?? buykorigw_get_installation_id() );
    update_option( BUYKORIGW_OPTION_KEY, $settings );

    buykorigw_connect_redirect( 'success' );
}

function buykorigw_notify_disconnect( $settings ) {
    if ( empty( $settings['api_key'] ) || empty( $settings['gateway_url'] ) ) {
        return;
    }

    $body = wp_json_encode( array(
        'siteUrl'        => home_url( '/' ),
        'installationId' => buykorigw_get_installation_id(),
    ) );
    wp_remote_post(
        rtrim( $settings['gateway_url'], '/' ) . '/plugin/connect/disconnect',
        array(
            'timeout'   => 8,
            'blocking'  => false,
            'sslverify' => true,
            'headers'   => array_merge( array(
                'Content-Type' => 'application/json',
                'X-API-Key'    => $settings['api_key'],
            ), buykorigw_signed_headers( $settings['api_key'], $body ) ),
            'body'      => $body,
        )
    );
}

function buykorigw_disconnect_account() {
    check_admin_referer( 'buykorigw_disconnect_account' );

    if ( ! current_user_can( 'manage_options' ) ) {
        wp_die( esc_html__( 'Permission denied.', 'buykori-adsync' ) );
    }

    $settings                          = buykorigw_get_settings();
    buykorigw_notify_disconnect( $settings );
    $settings['api_key']               = '';
    $settings['connected_site_host']   = '';
    $settings['connected_client_name'] = '';
    $settings['connected_at']          = '';
    $settings['connect_warning']       = '';
    update_option( BUYKORIGW_OPTION_KEY, $settings );

    buykorigw_connect_redirect( 'success', 'WordPress site disconnected.' );
}

function buykorigw_settings_page() {
    $settings = buykorigw_get_settings();
    $nonce    = wp_create_nonce( 'buykorigw_nonce' );
    $connect_url = wp_nonce_url( admin_url( 'admin-post.php?action=buykorigw_connect_start' ), 'buykorigw_connect_account' );
    $disconnect_url = wp_nonce_url( admin_url( 'admin-post.php?action=buykorigw_disconnect' ), 'buykorigw_disconnect_account' );
    $show_manual_setup = defined( 'BUYKORIGW_SHOW_MANUAL_SETUP' ) && BUYKORIGW_SHOW_MANUAL_SETUP;
    $show_manual_setup = (bool) apply_filters( 'buykorigw_show_manual_setup', $show_manual_setup );
    ?>
    <style>
        /* Modern CSS Variables & Base Styles */
        :root {
            --primary: #4f46e5;
            --primary-hover: #4338ca;
            --primary-light: #eef2ff;
            --slate-50: #f8fafc;
            --slate-100: #f1f5f9;
            --slate-200: #e2e8f0;
            --slate-300: #cbd5e1;
            --slate-700: #334155;
            --slate-800: #1e293b;
            --slate-900: #0f172a;
            --emerald-50: #ecfdf5;
            --emerald-100: #d1fae5;
            --emerald-600: #059669;
            --emerald-800: #065f46;
            --red-50: #fef2f2;
            --red-100: #fee2e2;
            --red-600: #dc2626;
            --red-800: #991b1b;
        }

        .buykorigw-wrap {
            max-width: 860px;
            margin: 20px 20px 32px 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            color: var(--slate-800);
        }

        /* Sticky Header */
        .buykorigw-header {
            position: sticky;
            top: 32px;
            z-index: 99;
            background: #ffffff;
            padding: 16px 24px;
            border: 1px solid var(--slate-200);
            border-left: 4px solid var(--primary);
            border-radius: 12px;
            margin-bottom: 24px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -1px rgba(0,0,0,0.025);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .buykorigw-header-left h1 {
            margin: 0 0 4px;
            font-size: 22px;
            font-weight: 700;
            line-height: 1.2;
            display: flex;
            align-items: center;
            color: var(--slate-900);
        }

        .buykorigw-header-left p {
            margin: 0;
            color: #64748b;
            font-size: 13.5px;
        }

        .buykorigw-header .version {
            background: var(--primary-light);
            color: #3730a3;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 11px;
            margin-left: 8px;
            font-weight: 600;
        }

        /* Navigation Tabs (Shadcn Style) */
        .buykorigw-nav-container {
            background: var(--slate-100);
            padding: 4px;
            border-radius: 8px;
            display: inline-flex;
            gap: 2px;
            margin-bottom: 24px;
        }

        .buykorigw-nav-tab {
            padding: 8px 16px;
            font-size: 13.5px;
            font-weight: 600;
            color: #64748b;
            cursor: pointer;
            border-radius: 6px;
            background: transparent;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .buykorigw-nav-tab:hover {
            color: var(--slate-900);
        }

        .buykorigw-nav-tab.active {
            color: var(--slate-900);
            background: #ffffff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }

        .buykorigw-tab-content {
            display: none;
        }

        .buykorigw-tab-content.active {
            display: block;
            animation: buykorigwFadeIn 0.2s ease-out;
        }

        @keyframes buykorigwFadeIn {
            from { opacity: 0; transform: translateY(4px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Modern Cards */
        .buykorigw-card {
            background: #ffffff;
            border: 1px solid var(--slate-200);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
        }

        .buykorigw-card h2 {
            margin: 0 0 16px;
            font-size: 16px;
            font-weight: 600;
            color: var(--slate-900);
            border-bottom: 1px solid var(--slate-100);
            padding-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .buykorigw-card > p {
            color: #64748b !important;
            line-height: 1.5;
            font-size: 13.5px;
            margin-top: -8px;
            margin-bottom: 16px;
        }

        /* Form Fields */
        .buykorigw-field {
            margin-bottom: 20px;
        }

        .buykorigw-field label {
            display: block;
            font-weight: 550;
            margin-bottom: 8px;
            color: var(--slate-900);
            font-size: 13.5px;
        }

        .buykorigw-field input[type="text"],
        .buykorigw-field input[type="password"],
        .buykorigw-field select {
            width: 100%;
            max-width: 100%;
            min-height: 40px;
            padding: 8px 12px;
            border: 1px solid var(--slate-300);
            border-radius: 8px;
            font-size: 13.5px;
            color: var(--slate-900);
            background: #ffffff;
            transition: all 0.2s ease;
        }

        .buykorigw-field input:focus, 
        .buykorigw-field select:focus {
            border-color: var(--primary);
            outline: none;
            box-shadow: 0 0 0 3px rgba(79,70,229,0.12);
        }

        .buykorigw-field .description {
            font-size: 12.5px;
            color: #64748b;
            margin-top: 6px;
            line-height: 1.4;
        }

        /* Modern Toggle / Switch Container */
        .buykorigw-toggle-card {
            border: 1px solid var(--slate-200);
            border-radius: 8px;
            background: var(--slate-50);
            margin-bottom: 12px;
            padding: 14px 16px;
            transition: all 0.15s ease;
        }

        .buykorigw-toggle-card:hover {
            background: var(--slate-100);
            border-color: var(--slate-300);
        }

        .buykorigw-toggle {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .buykorigw-toggle label {
            font-weight: 500;
            color: var(--slate-900);
            margin: 0;
            cursor: pointer;
            font-size: 13.5px;
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 4px;
        }

        /* Toggle Badges */
        .buykorigw-badge {
            display: inline-flex;
            align-items: center;
            padding: 1px 6px;
            font-size: 10.5px;
            font-weight: 600;
            border-radius: 9999px;
            margin-left: 6px;
        }

        .buykorigw-badge-recommended {
            background-color: var(--emerald-50);
            color: var(--emerald-800);
            border: 1px solid var(--emerald-100);
        }

        .buykorigw-badge-optional {
            background-color: var(--slate-100);
            color: #475569;
            border: 1px solid var(--slate-200);
        }

        /* Custom Switch */
        .buykorigw-switch {
            position: relative;
            width: 40px;
            height: 22px;
            flex-shrink: 0;
        }

        .buykorigw-switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }

        .buykorigw-slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: var(--slate-300);
            border-radius: 22px;
            transition: all 0.2s ease;
        }

        .buykorigw-slider:before {
            position: absolute;
            content: "";
            height: 16px;
            width: 16px;
            left: 3px;
            bottom: 3px;
            background: #ffffff;
            border-radius: 50%;
            transition: all 0.2s ease;
            box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        }

        .buykorigw-switch input:checked + .buykorigw-slider {
            background: var(--primary);
        }

        .buykorigw-switch input:checked + .buykorigw-slider:before {
            transform: translateX(18px);
        }

        /* Buttons */
        .buykorigw-btn {
            min-height: 38px;
            padding: 8px 16px;
            border: 1px solid transparent;
            border-radius: 8px;
            font-size: 13.5px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }

        .buykorigw-btn-primary {
            background: var(--primary);
            color: #ffffff;
            border-color: var(--primary-hover);
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }

        .buykorigw-btn-primary:hover {
            background: var(--primary-hover);
            color: #ffffff;
            box-shadow: 0 2px 4px rgba(79,70,229,0.15);
        }

        .buykorigw-btn-test {
            background: var(--slate-900);
            color: #ffffff;
            margin-right: 8px;
        }

        .buykorigw-btn-test:hover {
            background: var(--slate-800);
            color: #ffffff;
        }

        .buykorigw-btn-secondary {
            background: #ffffff;
            color: var(--slate-700);
            border-color: var(--slate-300);
        }

        .buykorigw-btn-secondary:hover {
            background: var(--slate-50);
            color: var(--slate-900);
            border-color: var(--slate-400);
        }

        .buykorigw-btn-danger {
            background: #ffffff;
            color: var(--red-600);
            border-color: var(--red-100);
        }

        .buykorigw-btn-danger:hover {
            background: var(--red-50);
            color: var(--red-800);
            border-color: var(--red-100);
        }

        .buykorigw-action-row {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
            margin: 14px 0 18px;
        }

        .buykorigw-advanced-details {
            border: 1px solid var(--slate-200);
            border-radius: 10px;
            background: #ffffff;
            padding: 12px 14px;
            margin-bottom: 14px;
        }

        .buykorigw-advanced-details summary {
            cursor: pointer;
            color: var(--slate-800);
            font-size: 13px;
            font-weight: 700;
        }

        .buykorigw-advanced-details[open] summary {
            margin-bottom: 14px;
        }

        /* Connection Status Badges */
        .buykorigw-conn-badge {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            margin-left: 10px;
        }
        .buykorigw-conn-badge.active {
            background: var(--emerald-50);
            color: var(--emerald-800);
            border: 1px solid var(--emerald-100);
        }
        .buykorigw-conn-badge.inactive {
            background: var(--slate-100);
            color: #64748b;
            border: 1px solid var(--slate-200);
        }

        /* Status Messages */
        .buykorigw-status {
            padding: 12px 14px;
            border-radius: 8px;
            margin-top: 14px;
            display: none;
            font-size: 13px;
            line-height: 1.4;
            font-weight: 500;
        }

        .buykorigw-status.success {
            display: block;
            background: var(--emerald-50);
            color: var(--emerald-800);
            border: 1px solid var(--emerald-100);
        }

        .buykorigw-status.error {
            display: block;
            background: var(--red-50);
            color: var(--red-800);
            border: 1px solid var(--red-100);
        }

        .buykorigw-events-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }

        .buykorigw-info-box {
            background: #eff6ff;
            border: 1px solid #bfdbfe;
            border-radius: 8px;
            padding: 14px;
            font-size: 13px;
            color: #1e3a8a;
            line-height: 1.5;
            margin-bottom: 16px;
        }

        .buykorigw-warning-box {
            background: #fffbeb;
            border: 1px solid #fde68a;
            border-radius: 8px;
            padding: 14px;
            font-size: 13px;
            color: #92400e;
            line-height: 1.5;
            margin-bottom: 16px;
        }

        .buykorigw-status-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
            margin: 16px 0 18px;
        }

        .buykorigw-status-tile {
            border: 1px solid var(--slate-200);
            border-radius: 10px;
            background: var(--slate-50);
            padding: 12px;
            min-width: 0;
        }

        .buykorigw-status-tile span {
            display: block;
            color: #64748b;
            font-size: 11px;
            font-weight: 650;
            margin-bottom: 4px;
        }

        .buykorigw-status-tile strong {
            display: block;
            color: var(--slate-900);
            font-size: 13px;
            font-weight: 700;
            overflow-wrap: anywhere;
        }

        .buykorigw-quiet-card {
            background: #ffffff;
            border: 1px solid var(--slate-200);
            border-radius: 12px;
            padding: 18px;
            margin-bottom: 18px;
        }

        .buykorigw-quiet-card h2 {
            margin-bottom: 10px;
            padding-bottom: 10px;
        }

        .buykorigw-compact-copy {
            color: #64748b;
            font-size: 13px;
            line-height: 1.5;
            margin: 0 0 14px;
        }

        /* Responsive */
        @media (max-width: 782px) {
            .buykorigw-wrap {
                margin-right: 12px;
            }
            .buykorigw-header {
                top: 46px;
                flex-direction: column;
                gap: 12px;
                align-items: flex-start;
                padding: 16px;
            }
            .buykorigw-header-right {
                width: 100%;
            }
            .buykorigw-header-right .buykorigw-btn {
                width: 100%;
            }
            .buykorigw-card {
                padding: 16px;
            }
            .buykorigw-events-grid {
                grid-template-columns: 1fr;
            }
            .buykorigw-status-grid {
                grid-template-columns: 1fr;
            }
            .buykorigw-nav-container {
                display: flex;
                width: 100%;
            }
            .buykorigw-nav-tab {
                flex: 1;
                text-align: center;
                justify-content: center;
                font-size: 12.5px;
                padding: 8px 6px;
            }
        }
    </style>

    <div class="buykorigw-wrap">
        <form method="post" action="options.php" id="buykorigw-form">
            <?php settings_fields( 'buykorigw_settings_group' ); ?>
            <input type="hidden" name="<?php echo BUYKORIGW_OPTION_KEY; ?>[low_resource_mode]" value="0">
            <input type="hidden" name="<?php echo BUYKORIGW_OPTION_KEY; ?>[tracking_mode]" value="auto">
            <input type="hidden" name="<?php echo BUYKORIGW_OPTION_KEY; ?>[enable_variations]" value="1">

            <!-- Header with Sticky Save Button -->
            <div class="buykorigw-header">
                <div class="buykorigw-header-left">
                    <h1>⚡ Buykori AdSync <span class="version">v<?php echo BUYKORIGW_VERSION; ?></span></h1>
                    <p>Connected tracking for WooCommerce stores</p>
                </div>
                <div class="buykorigw-header-right">
                    <?php submit_button( '💾 Save Settings', 'buykorigw-btn buykorigw-btn-primary', 'submit', false ); ?>
                </div>
            </div>

            <!-- Tab Navigation (Shadcn Style) -->
            <div class="buykorigw-nav-container">
                <div class="buykorigw-nav-tab active" data-tab="general">⚙️ General</div>
                <div class="buykorigw-nav-tab" data-tab="woocommerce">🛒 WooCommerce</div>
                <div class="buykorigw-nav-tab" data-tab="advanced">🛠️ Support</div>
            </div>

            <!-- ═══════════════════════════════════════════════════════════════ -->
            <!-- TAB 1: General Settings                                        -->
            <!-- ═══════════════════════════════════════════════════════════════ -->
            <div class="buykorigw-tab-content active" id="tab-general">

                <!-- Connection Settings -->
                <div class="buykorigw-card">
                    <h2>
                        🔑 Connection Settings
                        <?php if ( ! empty( $settings['api_key'] ) ) : ?>
                            <span class="buykorigw-conn-badge active">● Connected</span>
                        <?php else : ?>
                            <span class="buykorigw-conn-badge inactive">● Not Configured</span>
                        <?php endif; ?>
                    </h2>

                    <div class="buykorigw-info-box">
                        <?php if ( ! empty( $settings['api_key'] ) ) : ?>
                            Connected to <strong><?php echo esc_html( $settings['connected_client_name'] ?: 'Buykori workspace' ); ?></strong>
                            <?php if ( ! empty( $settings['connected_site_host'] ) ) : ?>
                                for <strong><?php echo esc_html( $settings['connected_site_host'] ); ?></strong>.
                            <?php endif; ?>
                        <?php else : ?>
                            Connect this WordPress site with your Buykori account. No API key copy/paste is required.
                        <?php endif; ?>
                    </div>
                    <?php if ( ! empty( $settings['connect_warning'] ) ) : ?>
                        <div class="buykorigw-warning-box">
                            <?php echo esc_html( $settings['connect_warning'] ); ?>
                        </div>
                    <?php endif; ?>

                    <div class="buykorigw-status-grid">
                        <div class="buykorigw-status-tile">
                            <span>Status</span>
                            <strong><?php echo empty( $settings['api_key'] ) ? 'Not connected' : 'Tracking active'; ?></strong>
                        </div>
                        <div class="buykorigw-status-tile">
                            <span>Workspace</span>
                            <strong><?php echo esc_html( $settings['connected_client_name'] ?: 'Not selected' ); ?></strong>
                        </div>
                        <div class="buykorigw-status-tile">
                            <span>Website</span>
                            <strong><?php echo esc_html( $settings['connected_site_host'] ?: wp_parse_url( home_url(), PHP_URL_HOST ) ); ?></strong>
                        </div>
                    </div>

                    <div class="buykorigw-action-row">
                        <a class="buykorigw-btn buykorigw-btn-primary" href="<?php echo esc_url( $connect_url ); ?>">
                            <?php echo empty( $settings['api_key'] ) ? 'Connect Buykori Account' : 'Switch Buykori Account'; ?>
                        </a>
                        <?php if ( ! empty( $settings['api_key'] ) ) : ?>
                            <a class="buykorigw-btn buykorigw-btn-danger"
                               href="<?php echo esc_url( $disconnect_url ); ?>"
                               onclick="return confirm('Disconnect this WordPress site from Buykori AdSync? Tracking will stop until you reconnect.');">
                                Disconnect
                            </a>
                        <?php endif; ?>
                    </div>

                    <input type="hidden" id="buykorigw_api_key"
                           name="<?php echo BUYKORIGW_OPTION_KEY; ?>[api_key]"
                           value="<?php echo esc_attr( $settings['api_key'] ); ?>">
                    <input type="hidden"
                           name="<?php echo BUYKORIGW_OPTION_KEY; ?>[connected_site_host]"
                           value="<?php echo esc_attr( $settings['connected_site_host'] ?? '' ); ?>">
                    <input type="hidden"
                           name="<?php echo BUYKORIGW_OPTION_KEY; ?>[connected_client_name]"
                           value="<?php echo esc_attr( $settings['connected_client_name'] ?? '' ); ?>">
                    <input type="hidden"
                           name="<?php echo BUYKORIGW_OPTION_KEY; ?>[connected_at]"
                           value="<?php echo esc_attr( $settings['connected_at'] ?? '' ); ?>">
                    <input type="hidden"
                           name="<?php echo BUYKORIGW_OPTION_KEY; ?>[connect_warning]"
                           value="<?php echo esc_attr( $settings['connect_warning'] ?? '' ); ?>">
                    <input type="hidden"
                           name="<?php echo BUYKORIGW_OPTION_KEY; ?>[installation_id]"
                           value="<?php echo esc_attr( $settings['installation_id'] ?? buykorigw_get_installation_id() ); ?>">

                    <?php if ( $show_manual_setup ) : ?>
                    <details style="margin:14px 0 18px;">
                        <summary style="cursor:pointer; font-size:12.5px; color:#64748b; font-weight:600;">Advanced manual setup</summary>

                    <div class="buykorigw-field">
                        <label for="buykorigw_manual_api_key">API Key</label>
                        <input type="password" id="buykorigw_manual_api_key"
                               name="<?php echo BUYKORIGW_OPTION_KEY; ?>[api_key]"
                               value="<?php echo esc_attr( $settings['api_key'] ); ?>"
                               placeholder="আপনার Buykori AdSync API Key পেস্ট করুন"
                               autocomplete="off">
                        <p class="description">Buykori AdSync ড্যাশবোর্ড থেকে আপনার API Key কপি করুন।</p>
                    </div>

                    </details>
                    <?php endif; ?>

                    <!-- Hidden API URL to keep backend fully functional without cluttering UI -->
                    <input type="hidden" id="buykorigw_gateway_url"
                           name="<?php echo BUYKORIGW_OPTION_KEY; ?>[gateway_url]"
                           value="<?php echo esc_attr( $settings['gateway_url'] ); ?>">

                    <button type="button" class="buykorigw-btn buykorigw-btn-test" id="buykorigw-test-btn" onclick="buykorigwTestConnection()">
                        🔍 Run Health Check
                    </button>
                    <div id="buykorigw-test-status" class="buykorigw-status"></div>
                </div>

                <!-- Core Events -->
                <div class="buykorigw-card">
                    <h2>📊 Essential Events</h2>
                    <p>Recommended defaults are already selected. Change these only when a store does not need a specific signal.</p>
                    
                    <div class="buykorigw-toggle-card" style="display:none;">
                        <div class="buykorigw-toggle">
                            <label class="buykorigw-switch">
                                <input type="checkbox"
                                       name="<?php echo BUYKORIGW_OPTION_KEY; ?>[low_resource_mode]"
                                       value="1"
                                       <?php checked( $settings['low_resource_mode'] ?? 0, 1 ); ?>>
                                <span class="buykorigw-slider"></span>
                            </label>
                            <label>
                                Low-resource mode 
                                <span style="color:#64748b; font-size:12px; font-weight:normal; margin-left:6px;">— PageView, ViewContent, Search server-side ট্র্যাকিং বন্ধ রাখবে (রিসোর্স সাশ্রয়ী)</span>
                            </label>
                        </div>
                    </div>

                    <div class="buykorigw-events-grid">
                        <?php
                        $core_events = array(
                            'enable_pageview' => array( '👁️ PageView', 'প্রতিটি পেজ ভিজিট ট্র্যাক করে', 'recommended' ),
                            'enable_lead'     => array( '📋 Lead', 'ফর্ম সাবমিশন ও সাইনআপ ট্র্যাক করে', 'optional' ),
                            'enable_search'   => array( '🔍 Search', 'সাইটে সার্চ করা ট্র্যাক করে', 'optional' ),
                        );
                        foreach ( $core_events as $key => $info ) :
                        ?>
                            <div class="buykorigw-toggle-card">
                                <div class="buykorigw-toggle">
                                    <label class="buykorigw-switch">
                                        <input type="checkbox"
                                               name="<?php echo BUYKORIGW_OPTION_KEY; ?>[<?php echo $key; ?>]"
                                               value="1"
                                               <?php checked( $settings[ $key ], 1 ); ?>>
                                        <span class="buykorigw-slider"></span>
                                    </label>
                                    <label>
                                        <?php echo $info[0]; ?>
                                        <span class="buykorigw-badge buykorigw-badge-<?php echo $info[2]; ?>">
                                            <?php echo ucfirst($info[2]); ?>
                                        </span>
                                        <span style="color:#64748b; font-size:11.5px; font-weight:normal; display:block; margin-top:2px;">
                                            <?php echo $info[1]; ?>
                                        </span>
                                    </label>
                                </div>
                            </div>
                        <?php endforeach; ?>
                    </div>
                </div>

                <!-- Hybrid Browser Tracking -->
                <div class="buykorigw-card buykorigw-quiet-card">
                    <h2>🌐 Browser Pixel Backup</h2>
                    <p class="buykorigw-compact-copy">Optional backup for stores that also want browser pixel signals. Server-side tracking remains the primary source.</p>
                    <details class="buykorigw-advanced-details">
                        <summary>Pixel backup settings</summary>
                    
                    <div class="buykorigw-toggle-card" style="margin-bottom: 20px;">
                        <div class="buykorigw-toggle">
                            <label class="buykorigw-switch">
                                <input type="checkbox"
                                       name="<?php echo BUYKORIGW_OPTION_KEY; ?>[enable_hybrid]"
                                       value="1"
                                       <?php checked( $settings['enable_hybrid'] ?? 0, 1 ); ?>>
                                <span class="buykorigw-slider"></span>
                            </label>
                            <label>ব্রাউজার ট্র্যাকিং চালু করুন (Hybrid Mode)</label>
                        </div>
                    </div>

                    <div class="buykorigw-field">
                        <label for="buykorigw_fb_pixel_id">Meta (Facebook) Pixel ID</label>
                        <input type="text" id="buykorigw_fb_pixel_id"
                               name="<?php echo BUYKORIGW_OPTION_KEY; ?>[fb_pixel_id]"
                               value="<?php echo esc_attr( $settings['fb_pixel_id'] ?? '' ); ?>"
                               placeholder="যেমন: 123456789012345">
                    </div>
                    <div class="buykorigw-field">
                        <label for="buykorigw_tt_pixel_id">TikTok Pixel ID</label>
                        <input type="text" id="buykorigw_tt_pixel_id"
                               name="<?php echo BUYKORIGW_OPTION_KEY; ?>[tt_pixel_id]"
                               value="<?php echo esc_attr( $settings['tt_pixel_id'] ?? '' ); ?>"
                               placeholder="যেমন: C1234567890ABC">
                    </div>
                    </details>
                </div>

            </div><!-- /tab-general -->


            <!-- ═══════════════════════════════════════════════════════════════ -->
            <!-- TAB 2: WooCommerce Events                                      -->
            <!-- ═══════════════════════════════════════════════════════════════ -->
            <div class="buykorigw-tab-content" id="tab-woocommerce">

                <!-- WooCommerce Tracking Events -->
                <div class="buykorigw-card">
                    <h2>🛒 Store Event Tracking</h2>
                    <p>Keep the recommended checkout and purchase signals on for better ad optimization.</p>

                    <div class="buykorigw-events-grid">
                        <?php
                        $woo_events = array(
                            'enable_viewcontent'    => array( '📦 ViewContent', 'প্রোডাক্ট পেজ ভিউ ট্র্যাক করে', 'recommended' ),
                            'enable_addtocart'      => array( '🛒 AddToCart', 'কার্টে প্রোডাক্ট যোগ করা ট্র্যাক করে', 'recommended' ),
                            'enable_viewcart'       => array( '👀 ViewCart', 'কার্ট পেজ ভিজিট ট্র্যাক করে', 'optional' ),
                            'enable_removefromcart'  => array( '❌ RemoveFromCart', 'কার্ট থেকে প্রোডাক্ট বাদ দেওয়া', 'optional' ),
                            'enable_checkout'       => array( '💳 InitiateCheckout', 'চেকআউট শুরু করা ট্র্যাক করে', 'recommended' ),
                            'enable_addpaymentinfo' => array( '🏦 AddPaymentInfo', 'পেমেন্ট ইনফো দেওয়া ট্র্যাক করে', 'optional' ),
                            'enable_purchase'       => array( '💰 Purchase', 'অর্ডার সম্পন্ন হওয়া ট্র্যাক করে', 'recommended' ),
                        );
                        foreach ( $woo_events as $key => $info ) :
                        ?>
                            <div class="buykorigw-toggle-card">
                                <div class="buykorigw-toggle">
                                    <label class="buykorigw-switch">
                                        <input type="checkbox"
                                               name="<?php echo BUYKORIGW_OPTION_KEY; ?>[<?php echo $key; ?>]"
                                               value="1"
                                               <?php checked( $settings[ $key ], 1 ); ?>>
                                        <span class="buykorigw-slider"></span>
                                    </label>
                                    <label>
                                        <?php echo $info[0]; ?>
                                        <span class="buykorigw-badge buykorigw-badge-<?php echo $info[2]; ?>">
                                            <?php echo ucfirst($info[2]); ?>
                                        </span>
                                        <span style="color:#64748b; font-size:11.5px; font-weight:normal; display:block; margin-top:2px;">
                                            <?php echo $info[1]; ?>
                                        </span>
                                    </label>
                                </div>
                            </div>
                        <?php endforeach; ?>
                    </div>
                </div>

                <!-- Landing Page Tracking Mode -->
                <div class="buykorigw-card" style="display:none;">
                    <h2>🎯 Landing Page Tracking Mode</h2>
                    <div class="buykorigw-field">
                        <label for="buykorigw_tracking_mode">Tracking detection behavior</label>
                        <select id="buykorigw_tracking_mode"
                                name="<?php echo BUYKORIGW_OPTION_KEY; ?>[tracking_mode]">
                            <option value="auto" <?php selected( $settings['tracking_mode'] ?? 'auto', 'auto' ); ?>>Smart auto-detection (Recommended)</option>
                            <option value="standard" <?php selected( $settings['tracking_mode'] ?? 'auto', 'standard' ); ?>>Advanced: force standard WooCommerce checkout</option>
                            <option value="one_page" <?php selected( $settings['tracking_mode'] ?? 'auto', 'one_page' ); ?>>Advanced: force one-page landing / embedded checkout</option>
                        </select>
                        <p class="description">Smart mode detects native WooCommerce pages, embedded checkout widgets, Elementor and CartFlows landing pages automatically. Use an advanced override only while troubleshooting a custom funnel.</p>
                    </div>
                </div>

                <!-- Product Catalog ID format mapping -->
                <div class="buykorigw-card" style="display:none;">
                    <h2>🎯 Product Catalog ID Format</h2>
                    <div class="buykorigw-field">
                        <label for="buykorigw_content_id_format">Catalog Content ID Format</label>
                        <select id="buykorigw_content_id_format"
                                name="<?php echo BUYKORIGW_OPTION_KEY; ?>[content_id_format]">
                            <option value="id" <?php selected( $settings['content_id_format'] ?? 'id', 'id' ); ?>>WooCommerce Product Database ID (e.g. 1245)</option>
                            <option value="sku" <?php selected( $settings['content_id_format'] ?? 'id', 'sku' ); ?>>Product SKU Code (e.g. BK-SHOE-44)</option>
                        </select>
                        <p class="description">ফেসবুক এবং টিকটক ক্যাটালগে প্রোডাক্ট সনাক্ত করতে কোন আইডিটি পাঠানো হবে তা সিলেক্ট করুন। এটি ক্যাটালগের ইউনিক আইডির সাথে ম্যাচ করতে হবে।</p>
                    </div>
                </div>

                <!-- Product Variation Tracking -->
                <div class="buykorigw-card" style="display:none;">
                    <h2>📦 Product Variation Tracking</h2>
                    <p>প্রোডাক্টের বিভিন্ন ভ্যারিয়েশন (যেমন: সাইজ, কালার) ট্র্যাকিং চালু করুন। এটি চালু করলে AddToCart, ViewContent এবং Purchase ইভেন্টে ভ্যারিয়েশনের আইডি এবং তার এট্রিবিউটসমূহ পাঠানো হবে।</p>
                    
                    <div class="buykorigw-toggle-card">
                        <div class="buykorigw-toggle">
                            <label class="buykorigw-switch">
                                <input type="checkbox"
                                       name="<?php echo BUYKORIGW_OPTION_KEY; ?>[enable_variations]"
                                       value="1"
                                       <?php checked( $settings['enable_variations'] ?? 0, 1 ); ?>>
                                <span class="buykorigw-slider"></span>
                            </label>
                            <label>ভ্যারিয়েশন ট্র্যাকিং চালু করুন (Variation Tracking)</label>
                        </div>
                    </div>
                </div>

                <!-- Deferred Purchase (COD) -->
                <div class="buykorigw-card">
                    <h2>📦 COD Purchase Timing</h2>
                    <p>Use this when COD stores should send Purchase only after the order reaches a confirmed status.</p>

                    <div class="buykorigw-toggle-card" style="margin-bottom: 20px;">
                        <div class="buykorigw-toggle">
                            <label class="buykorigw-switch">
                                <input type="checkbox"
                                       name="<?php echo BUYKORIGW_OPTION_KEY; ?>[deferred_purchase]"
                                       value="1"
                                       <?php checked( $settings['deferred_purchase'], 1 ); ?>>
                                <span class="buykorigw-slider"></span>
                            </label>
                            <label>Send Purchase after confirmation</label>
                        </div>
                    </div>

                    <div class="buykorigw-field">
                        <label for="buykorigw_auto_confirm">Confirmed order status</label>
                        <select id="buykorigw_auto_confirm"
                                name="<?php echo BUYKORIGW_OPTION_KEY; ?>[auto_confirm_status]">
                            <option value="processing" <?php selected( $settings['auto_confirm_status'], 'processing' ); ?>>Processing</option>
                            <option value="completed" <?php selected( $settings['auto_confirm_status'], 'completed' ); ?>>Completed</option>
                        </select>
                        <p class="description">Purchase will be sent when the order reaches this WooCommerce status.</p>
                    </div>
                </div>

            </div><!-- /tab-woocommerce -->


            <!-- ═══════════════════════════════════════════════════════════════ -->
            <!-- TAB 3: Advanced                                                -->
            <!-- ═══════════════════════════════════════════════════════════════ -->
            <div class="buykorigw-tab-content" id="tab-advanced">

                <div class="buykorigw-card">
                    <h2>Support Tools</h2>
                    <details class="buykorigw-advanced-details">
                        <summary>Catalog matching override</summary>
                        <div class="buykorigw-field">
                            <label for="buykorigw_content_id_format_advanced">Catalog Content ID Format</label>
                            <select id="buykorigw_content_id_format_advanced"
                                    name="<?php echo BUYKORIGW_OPTION_KEY; ?>[content_id_format]">
                                <option value="id" <?php selected( $settings['content_id_format'] ?? 'id', 'id' ); ?>>WooCommerce Product Database ID</option>
                                <option value="sku" <?php selected( $settings['content_id_format'] ?? 'id', 'sku' ); ?>>Product SKU Code</option>
                            </select>
                            <p class="description">Only change this if your Meta/TikTok catalog uses SKU instead of WooCommerce product IDs.</p>
                        </div>
                    </details>
                    <details class="buykorigw-advanced-details">
                        <summary>Smart detection status</summary>
                        <p class="description">Landing pages, embedded checkout widgets, CartFlows, WooCommerce Blocks, and product variations are handled automatically.</p>
                    </details>
                </div>

                <div class="buykorigw-card">
                    <h2>🛠️ Diagnostics</h2>
                    <p>Keep diagnostics off unless Buykori support asks for a temporary troubleshooting log.</p>
                    <details class="buykorigw-advanced-details">
                        <summary>Error log mode</summary>
                        <div class="buykorigw-toggle-card">
                            <div class="buykorigw-toggle">
                                <label class="buykorigw-switch">
                                    <input type="checkbox"
                                           name="<?php echo BUYKORIGW_OPTION_KEY; ?>[debug_mode]"
                                           value="1"
                                           <?php checked( $settings['debug_mode'], 1 ); ?>>
                                    <span class="buykorigw-slider"></span>
                                </label>
                                <label>Write extra troubleshooting logs</label>
                            </div>
                        </div>
                    </details>
                </div>

                <div class="buykorigw-card">
                    <h2>🎯 Custom Event Builder</h2>
                    <div class="buykorigw-info-box">
                        💡 নির্দিষ্ট বাটন ক্লিক, ফর্ম সাবমিশন, বা URL ম্যাচের মাধ্যমে কাস্টম ইভেন্ট তৈরি করতে চান?
                        <br>বাম পাশের মেনু থেকে <strong>Buykori AdSync → 🎯 Custom Events</strong> এ যান।
                    </div>
                </div>

                <div class="buykorigw-card">
                    <h2>Plugin Update</h2>
                    <p>WordPress update notice না দেখালে এখান থেকে local update cache clear করে আবার check করতে পারবেন।</p>
                    <button type="button" class="buykorigw-btn buykorigw-btn-secondary" id="buykorigw-update-btn" onclick="buykorigwCheckUpdateNow()">
                        Check Update Now
                    </button>
                    <div id="buykorigw-update-status" class="buykorigw-status"></div>
                </div>

            </div><!-- /tab-advanced -->
        </form>
    </div>

    <script>
    // ─── Tab Switching Logic ───────────────────────────────────────────────────
    (function() {
        var tabs = document.querySelectorAll('.buykorigw-nav-tab');
        var panels = document.querySelectorAll('.buykorigw-tab-content');
        var STORAGE_KEY = 'buykorigw_active_tab';

        function switchTab(tabName) {
            tabs.forEach(function(t) { t.classList.remove('active'); });
            panels.forEach(function(p) { p.classList.remove('active'); });
            var activeTab = document.querySelector('.buykorigw-nav-tab[data-tab="' + tabName + '"]');
            var activePanel = document.getElementById('tab-' + tabName);
            if (activeTab) activeTab.classList.add('active');
            if (activePanel) activePanel.classList.add('active');
            try { localStorage.setItem(STORAGE_KEY, tabName); } catch(e) {}
        }

        tabs.forEach(function(tab) {
            tab.addEventListener('click', function() {
                switchTab(this.getAttribute('data-tab'));
            });
        });

        // Restore last active tab after page reload / save
        try {
            var saved = localStorage.getItem(STORAGE_KEY);
            if (saved && document.getElementById('tab-' + saved)) {
                switchTab(saved);
            }
        } catch(e) {}
    })();

    // ─── Test Connection ───────────────────────────────────────────────────────
    function buykorigwTestConnection() {
        try {
            var btn = document.getElementById('buykorigw-test-btn');
            var status = document.getElementById('buykorigw-test-status');
            var apiKey = document.getElementById('buykorigw_api_key').value.trim();
            var gatewayUrl = document.getElementById('buykorigw_gateway_url').value.trim();

            btn.disabled = true;
            btn.textContent = '⏳ Testing...';
            status.style.display = 'none';
            status.className = 'buykorigw-status';

            if (!apiKey || !gatewayUrl) {
                status.style.display = 'block';
                status.className = 'buykorigw-status error';
                status.textContent = '❌ দয়া করে API Key দিন।';
                btn.disabled = false;
                btn.textContent = '🔍 Run Health Check';
                return;
            }

            var formData = new FormData();
            formData.append('action', 'buykorigw_test_connection');
            formData.append('nonce', '<?php echo $nonce; ?>');
            formData.append('api_key', apiKey);
            formData.append('gateway_url', gatewayUrl);

            var ajax_url = (typeof ajaxurl !== 'undefined') ? ajaxurl : '/wp-admin/admin-ajax.php';

            fetch(ajax_url, {
                method: 'POST',
                body: formData,
            })
            .then(function(res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(function(data) {
                status.style.display = 'block';
                if (data.success) {
                    status.className = 'buykorigw-status success';
                    status.innerHTML = '✅ ' + data.data;
                } else {
                    status.className = 'buykorigw-status error';
                    status.innerHTML = '❌ ' + (data.data || 'Unknown error');
                }
                btn.disabled = false;
                btn.textContent = '🔍 Run Health Check';
            })
            .catch(function(err) {
                status.style.display = 'block';
                status.className = 'buykorigw-status error';
                status.textContent = '❌ Network error: ' + err.message;
                btn.disabled = false;
                btn.textContent = '🔍 Run Health Check';
            });
        } catch (e) {
            console.error(e);
            alert("Error: " + e.message);
        }
    }
    function buykorigwCheckUpdateNow() {
        try {
            var btn = document.getElementById('buykorigw-update-btn');
            var status = document.getElementById('buykorigw-update-status');

            btn.disabled = true;
            btn.textContent = 'Checking...';
            status.style.display = 'none';
            status.className = 'buykorigw-status';

            var formData = new FormData();
            formData.append('action', 'buykorigw_check_update_now');
            formData.append('nonce', '<?php echo $nonce; ?>');

            var ajax_url = (typeof ajaxurl !== 'undefined') ? ajaxurl : '/wp-admin/admin-ajax.php';

            fetch(ajax_url, {
                method: 'POST',
                body: formData,
            })
            .then(function(res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(function(data) {
                status.style.display = 'block';
                if (data.success) {
                    status.className = 'buykorigw-status success';
                    status.innerHTML = '✅ ' + data.data;
                } else {
                    status.className = 'buykorigw-status error';
                    status.innerHTML = '❌ ' + (data.data || 'Unknown error');
                }
                btn.disabled = false;
                btn.textContent = 'Check Update Now';
            })
            .catch(function(err) {
                status.style.display = 'block';
                status.className = 'buykorigw-status error';
                status.textContent = '❌ Network error: ' + err.message;
                btn.disabled = false;
                btn.textContent = 'Check Update Now';
            });
        } catch (e) {
            console.error(e);
            alert("Error: " + e.message);
        }
    }
    </script>
    <?php
}
