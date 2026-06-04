<?php
/**
 * Plugin Name:       Buykori AdSync — Server-Side Tracking
 * Plugin URI:        https://buykori.app/
 * Description:       Server-Side Facebook CAPI, TikTok, and GA4 tracking for WooCommerce with one-page landing support, SHA-256 PII hashing, and deferred purchase control.
 * Version:           1.2.38
 * Requires at least: 5.8
 * Requires PHP:      7.4
 * Author:            Buykori AdSync
 * Author URI:        https://buykori.app/
 * License:           GPL v2 or later
 * License URI:       https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain:       buykori-adsync
 * WC requires at least: 5.0
 * WC tested up to:   9.0
 */

if (!defined('ABSPATH')) {
    exit; // Exit if accessed directly
}

// ─── Plugin Constants ──────────────────────────────────────────────────────────
define('BUYKORIGW_VERSION', '1.2.38');
define('BUYKORIGW_OPTIONAL_EVENTS_POLICY_VERSION', '1.2.33');

define('BUYKORIGW_PLUGIN_FILE', __FILE__);
define('BUYKORIGW_PLUGIN_DIR', plugin_dir_path(__FILE__));
define('BUYKORIGW_PLUGIN_URL', plugin_dir_url(__FILE__));
define('BUYKORIGW_OPTION_KEY', 'buykorigw_settings');

// Default AdSync API URL
define('BUYKORIGW_DEFAULT_GATEWAY_URL', 'https://api.buykori.app/api/v1');

// ─── Declare WooCommerce HPOS & Blocks Compatibility ──────────────────────────
add_action('before_woocommerce_init', function () {
    if (class_exists('\Automattic\WooCommerce\Utilities\FeaturesUtil')) {
        \Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility(
            'custom_order_tables',
            BUYKORIGW_PLUGIN_FILE,
            true
        );
        \Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility(
            'cart_checkout_blocks',
            BUYKORIGW_PLUGIN_FILE,
            true
        );
    }
});

// ─── Activation Hook ───────────────────────────────────────────────────────────
register_activation_hook(__FILE__, 'buykorigw_activate');

function buykorigw_activate()
{
    // Set default options if not already set
    if (!get_option(BUYKORIGW_OPTION_KEY)) {
        $defaults = array(
            'api_key' => '',
            'gateway_url' => BUYKORIGW_DEFAULT_GATEWAY_URL,
            // Core Events
            'enable_pageview' => 1,
            'enable_lead' => 0,
            'enable_search' => 0,
            // WooCommerce Events
            'enable_viewcontent' => 1,
            'enable_addtocart' => 1,
            'enable_viewcart' => 0,
            'enable_removefromcart' => 0,
            'enable_checkout' => 1,
            'enable_addpaymentinfo' => 0,
            'enable_purchase' => 1,
            // Advanced
            'low_resource_mode' => 0,
            'tracking_mode' => 'auto',
            'deferred_purchase' => 0,  // 1 = hold purchase until order completed
            'auto_confirm_status' => 'completed', // wc status that triggers confirm
            'debug_mode' => 0,
            'content_id_format' => 'id', // Default to WooCommerce database ID
            'enable_variations' => 1,
            'connected_site_host' => '',
            'connected_client_name' => '',
            'connected_at' => '',
            'connect_warning' => '',
            'installation_id' => buykorigw_get_installation_id(false),
        );
        update_option(BUYKORIGW_OPTION_KEY, $defaults);
    }
}

add_action('plugins_loaded', 'buykorigw_apply_optional_event_defaults');

function buykorigw_apply_optional_event_defaults()
{
    $applied_version = get_option('buykorigw_optional_events_policy_version', '');
    if (version_compare((string) $applied_version, BUYKORIGW_OPTIONAL_EVENTS_POLICY_VERSION, '>=')) {
        return;
    }

    $settings = get_option(BUYKORIGW_OPTION_KEY, array());
    if (!is_array($settings) || empty($settings)) {
        update_option('buykorigw_optional_events_policy_version', BUYKORIGW_OPTIONAL_EVENTS_POLICY_VERSION);
        return;
    }

    foreach (array(
        'enable_lead',
        'enable_search',
        'enable_viewcart',
        'enable_removefromcart',
        'enable_addpaymentinfo',
    ) as $event_key) {
        $settings[$event_key] = 0;
    }

    update_option(BUYKORIGW_OPTION_KEY, $settings);
    update_option('buykorigw_optional_events_policy_version', BUYKORIGW_OPTIONAL_EVENTS_POLICY_VERSION);
}

// ─── Deactivation Hook ─────────────────────────────────────────────────────────
register_deactivation_hook(__FILE__, 'buykorigw_deactivate');

function buykorigw_deactivate()
{
    // Clean up scheduled actions if any
    if (function_exists('as_unschedule_all_actions')) {
        as_unschedule_all_actions('buykorigw_retry_confirm');
        as_unschedule_all_actions('buykorigw_retry_cancel');
        as_unschedule_all_actions('buykorigw_sync_purchase');
    }
    wp_clear_scheduled_hook('buykorigw_sync_purchase');

    // প্লাগিন বন্ধ করলে ক্যাশ ক্লিয়ার করে দাও যাতে ট্র্যাকিং স্ক্রিপ্ট সঙ্গে সঙ্গে সরে যায়
    buykorigw_purge_all_caches();
}

// ─── Auto-Purge Cache on Settings Save ────────────────────────────────────────
// সেটিংস সেভ করার সাথে সাথে ক্যাশ ক্লিয়ার করে দাও
add_action('update_option_' . BUYKORIGW_OPTION_KEY, 'buykorigw_purge_all_caches', 10, 0);
add_action('template_redirect', 'buykorigw_disable_cache_on_tracking_pages', 0);

function buykorigw_disable_cache_on_tracking_pages()
{
    $is_dynamic_tracking_page = (
        (function_exists('is_cart') && is_cart())
        || (function_exists('is_checkout') && is_checkout())
        || (function_exists('is_order_received_page') && is_order_received_page())
    );

    if (!$is_dynamic_tracking_page) {
        return;
    }

    if (!defined('DONOTCACHEPAGE')) {
        define('DONOTCACHEPAGE', true);
    }
    if (!defined('DONOTCACHEOBJECT')) {
        define('DONOTCACHEOBJECT', true);
    }
    if (!defined('DONOTCACHEDB')) {
        define('DONOTCACHEDB', true);
    }

    do_action('litespeed_control_set_nocache', 'Buykori AdSync dynamic tracking page');
}

/**
 * buykorigw_purge_all_caches()
 *
 * WP Rocket, LiteSpeed, W3 Total Cache, WP Super Cache,
 * SiteGround Optimizer, WP Fastest Cache এবং Autoptimize-র
 * ক্যাশ স্বয়ংক্রিয়ভাবে ক্লিয়ার করে।
 *
 * যখন Buykori AdSync সেটিংস পরিবর্তন হয় বা প্লাগিন ডিঅ্যাক্টিভেট হয়,
 * তখন এই ফাংশনটি কল হয়।
 */
function buykorigw_purge_all_caches()
{
    $purged = array();

    // ── WP Rocket ──────────────────────────────────────────────────────
    if (function_exists('rocket_clean_domain')) {
        rocket_clean_domain();
        $purged[] = 'WP Rocket';
    }

    // ── LiteSpeed Cache ─────────────────────────────────────────────────
    if (class_exists('\LiteSpeed\Purge')) {
        do_action('litespeed_purge_all');
        $purged[] = 'LiteSpeed Cache';
    } elseif (defined('LSCWP_V')) {
        do_action('litespeed_purge_all');
        $purged[] = 'LiteSpeed Cache';
    }

    // ── W3 Total Cache ──────────────────────────────────────────────────
    if (function_exists('w3tc_flush_all')) {
        w3tc_flush_all();
        $purged[] = 'W3 Total Cache';
    }

    // ── WP Super Cache ──────────────────────────────────────────────────
    if (function_exists('wp_cache_clear_cache')) {
        wp_cache_clear_cache();
        $purged[] = 'WP Super Cache';
    }

    // ── SiteGround Optimizer ────────────────────────────────────────────
    if (class_exists('SiteGround_Optimizer\Supercacher\Supercacher')) {
        \SiteGround_Optimizer\Supercacher\Supercacher::purge_cache();
        $purged[] = 'SiteGround Optimizer';
    }

    // ── WP Fastest Cache ────────────────────────────────────────────────
    if (isset($GLOBALS['wp_fastest_cache']) && method_exists($GLOBALS['wp_fastest_cache'], 'deleteCache')) {
        $GLOBALS['wp_fastest_cache']->deleteCache(true);
        $purged[] = 'WP Fastest Cache';
    }

    // ── Autoptimize ─────────────────────────────────────────────────────
    if (class_exists('autoptimizeCache') && method_exists('autoptimizeCache', 'clearall')) {
        autoptimizeCache::clearall();
        $purged[] = 'Autoptimize';
    }

    // ── Breeze (Cloudways) ──────────────────────────────────────────────
    if (class_exists('Breeze_Admin')) {
        do_action('breeze_clear_all_cache');
        $purged[] = 'Breeze';
    }

    // ── Swift Performance ───────────────────────────────────────────────
    if (class_exists('Swift_Performance_Cache') && method_exists('Swift_Performance_Cache', 'clear_all_cache')) {
        \Swift_Performance_Cache::clear_all_cache();
        $purged[] = 'Swift Performance';
    }

    // ── Generic WordPress Object Cache (Memcache / Redis) ───────────────
    wp_cache_flush();

    if (!empty($purged)) {
        error_log('[Buykori AdSync] Cache purged: ' . implode(', ', $purged));
    }
}

// ─── Helper: Get Plugin Settings ───────────────────────────────────────────────
function buykorigw_get_settings()
{
    $settings = get_option(BUYKORIGW_OPTION_KEY, array());
    return wp_parse_args($settings, array(
        'api_key' => '',
        'gateway_url' => BUYKORIGW_DEFAULT_GATEWAY_URL,
        // Core Events
        'enable_pageview' => 1,
        'enable_lead' => 0,
        'enable_search' => 0,
        // WooCommerce Events
        'enable_viewcontent' => 1,
        'enable_addtocart' => 1,
        'enable_viewcart' => 0,
        'enable_removefromcart' => 0,
        'enable_checkout' => 1,
        'enable_addpaymentinfo' => 0,
        'enable_purchase' => 1,
        // Advanced
        'low_resource_mode' => 0,
        'tracking_mode' => 'auto',
        'deferred_purchase' => 0,
        'auto_confirm_status' => 'completed',
        'debug_mode' => 0,
        'content_id_format' => 'id',
        'enable_variations' => 1,
        // Hybrid Tracking Settings
        'enable_hybrid' => 0,
        'fb_pixel_id' => '',
        'tt_pixel_id' => '',
        'connected_site_host' => '',
        'connected_client_name' => '',
        'connected_at' => '',
        'connect_warning' => '',
        'installation_id' => buykorigw_get_installation_id(false),
    ));
}

function buykorigw_generate_installation_id()
{
    if (function_exists('wp_generate_uuid4')) {
        return wp_generate_uuid4();
    }
    return wp_generate_password(36, false, false);
}

function buykorigw_get_installation_id($persist = true)
{
    $settings = get_option(BUYKORIGW_OPTION_KEY, array());
    $installation_id = sanitize_text_field($settings['installation_id'] ?? '');
    if (!empty($installation_id)) {
        return $installation_id;
    }

    $installation_id = buykorigw_generate_installation_id();
    if ($persist) {
        $settings['installation_id'] = $installation_id;
        update_option(BUYKORIGW_OPTION_KEY, $settings);
    }
    return $installation_id;
}

function buykorigw_site_origin()
{
    $parts = wp_parse_url(home_url());
    if (empty($parts['host'])) {
        return '';
    }
    $scheme = !empty($parts['scheme']) ? $parts['scheme'] : 'https';
    return $scheme . '://' . strtolower($parts['host']);
}

function buykorigw_signed_headers($api_key, $body)
{
    $timestamp = (string) time();
    $signature = hash_hmac('sha256', $timestamp . '.' . $body, $api_key);

    return array(
        'X-CAPI-Origin' => buykorigw_site_origin(),
        'X-CAPI-Timestamp' => $timestamp,
        'X-CAPI-Signature' => $signature,
        'X-Buykori-Installation-ID' => buykorigw_get_installation_id(),
    );
}

function buykorigw_normalize_host($host)
{
    $host = strtolower(trim((string) $host));
    if (strpos($host, 'www.') === 0) {
        $host = substr($host, 4);
    }
    return $host;
}

function buykorigw_host_allowed($request_host, $allowed_host)
{
    $request_host = buykorigw_normalize_host($request_host);
    $allowed_host = buykorigw_normalize_host($allowed_host);

    if (empty($request_host) || empty($allowed_host)) {
        return false;
    }

    if ($request_host === $allowed_host) {
        return true;
    }

    $suffix = '.' . $allowed_host;
    return substr($request_host, -strlen($suffix)) === $suffix;
}

function buykorigw_first_party_cookie_options($days = 90)
{
    $host = wp_parse_url(home_url(), PHP_URL_HOST);
    $host = preg_replace('/^www\./', '', strtolower((string) $host));
    $options = array(
        'expires'  => time() + ((int) $days * DAY_IN_SECONDS),
        'path'     => '/',
        'secure'   => is_ssl(),
        'httponly' => false,
        'samesite' => 'Lax',
    );

    if (
        strpos($host, '.') !== false
        && !filter_var($host, FILTER_VALIDATE_IP)
        && $host !== 'localhost'
    ) {
        $options['domain'] = '.' . $host;
    }

    return $options;
}

function buykorigw_first_party_cookie_options_seconds($seconds = 1200)
{
    $options = buykorigw_first_party_cookie_options(0);
    $options['expires'] = time() + (int) $seconds;
    return $options;
}

function buykorigw_mark_initiate_checkout_sent($event_id = '')
{
    $timestamp = (string) time();
    $event_id  = sanitize_text_field((string) $event_id);
    
    // Use 20 minutes (1200 seconds) lifespan instead of 1 day to prevent event ID reuse lockout
    $options   = buykorigw_first_party_cookie_options_seconds(1200);

    setcookie('_buykorigw_ic_sent', $timestamp, $options);
    $_COOKIE['_buykorigw_ic_sent'] = $timestamp;

    if ($event_id !== '') {
        setcookie('_buykorigw_ic_event_id', $event_id, $options);
        $_COOKIE['_buykorigw_ic_event_id'] = $event_id;
    }
}

function buykorigw_recent_initiate_checkout_marker($timestamp, $max_age_seconds = 1800)
{
    $timestamp = (int) $timestamp;
    if ($timestamp <= 0) {
        return false;
    }

    return abs(time() - $timestamp) <= (int) $max_age_seconds;
}

// ─── Helper: Send Event to Buykori AdSync (Server-Side via wp_remote_post) ─────
function buykorigw_set_last_event_error($message = '', $code = 0, $body = '')
{
    $GLOBALS['buykorigw_last_event_error'] = array(
        'message' => sanitize_text_field((string) $message),
        'code'    => (int) $code,
        'body'    => substr(wp_strip_all_tags((string) $body), 0, 500),
    );
}

function buykorigw_get_last_event_error()
{
    $error = $GLOBALS['buykorigw_last_event_error'] ?? array();
    return is_array($error) ? $error : array();
}

function buykorigw_send_event($event_data, $blocking = true)
{
    $settings = buykorigw_get_settings();
    buykorigw_set_last_event_error();

    if (empty($settings['api_key']) || empty($settings['gateway_url'])) {
        buykorigw_set_last_event_error('API Key or AdSync API URL is missing.');
        if ($settings['debug_mode']) {
            error_log('[Buykori AdSync] API Key or AdSync API URL is missing.');
        }
        return false;
    }

    $url = rtrim($settings['gateway_url'], '/') . '/events';

    $body = wp_json_encode(array('data' => array($event_data)));

    $headers = array_merge(array(
        'Content-Type' => 'application/json',
        'X-API-Key' => $settings['api_key'],
    ), buykorigw_signed_headers($settings['api_key'], $body));

    $response = wp_remote_post($url, array(
        'timeout' => 10,
        'redirection' => 0,
        'httpversion' => '1.1',
        'blocking' => (bool) $blocking,
        'sslverify' => true,
        'headers' => $headers,
        'body' => $body,
    ));

    if (is_wp_error($response)) {
        buykorigw_set_last_event_error($response->get_error_message());
        if ($settings['debug_mode']) {
            error_log('[Buykori AdSync] Send event failed: ' . $response->get_error_message());
        }
        return false;
    }

    if (!$blocking) {
        return true;
    }

    $code = wp_remote_retrieve_response_code($response);
    if ($code >= 200 && $code < 300) {
        return true;
    }

    if ($settings['debug_mode']) {
        error_log('[Buykori AdSync] Send event HTTP ' . $code . ': ' . wp_remote_retrieve_body($response));
    }
    buykorigw_set_last_event_error('Gateway returned a non-success response.', $code, wp_remote_retrieve_body($response));
    return false;
}

// ─── Helper: SHA-256 Hash (PII fields — email, name, city, etc.) ────────────────
function buykorigw_hash($value)
{
    $value = trim((string) $value);
    if ($value === '') {
        return '';
    }

    // Already hashed (64 hex chars)?
    if (preg_match('/^[a-f0-9]{64}$/', $value)) {
        return $value;
    }

    // Normalize: lowercase, strip non-word chars for name/city fields
    $value = strtolower($value);
    return hash('sha256', $value);
}

// ─── Helper: SHA-256 Hash Phone with Bangladesh E.164 Normalization ─────────────
function buykorigw_hash_phone($phone)
{
    $phone = trim((string) $phone);
    if ($phone === '') {
        return '';
    }

    // Already hashed?
    if (preg_match('/^[a-f0-9]{64}$/', $phone)) {
        return $phone;
    }

    // Strip non-numeric characters
    $phone = preg_replace('/[^0-9]/', '', $phone);

    // Bangladesh E.164 normalization:
    // 01XXXXXXXXX (11 digits, local BD) → 8801XXXXXXXXX
    // 1XXXXXXXXX  (10 digits, no leading zero) → 8801XXXXXXXXX
    // 8801XXXXXXXXX (13 digits, already E.164) → keep as-is
    if (strlen($phone) === 11 && strpos($phone, '01') === 0) {
        $phone = '88' . $phone;
    } elseif (strlen($phone) === 10 && strpos($phone, '1') === 0) {
        $phone = '880' . $phone;
    } elseif (strpos($phone, '880') !== 0 && strpos($phone, '0') !== 0) {
        // Non-BD numbers: strip leading zeros (original behavior)
        $phone = ltrim($phone, '0');
    }

    return hash('sha256', $phone);
}

function buykorigw_identity_fields()
{
    return array('em', 'ph', 'fn', 'ln', 'ct', 'st', 'zp', 'country');
}

function buykorigw_identity_cookie_name($field)
{
    return '_buykorigw_id_' . preg_replace('/[^a-z0-9_]/', '', (string) $field);
}

function buykorigw_cache_identity_hash($field, $hashed)
{
    if (empty($hashed) || !preg_match('/^[a-f0-9]{64}$/', $hashed)) {
        return;
    }

    $cookie_name = buykorigw_identity_cookie_name($field);
    $cookie_opts = array(
        'expires'  => time() + (180 * DAY_IN_SECONDS),
        'path'     => '/',
        'secure'   => is_ssl(),
        'httponly' => false,
        'samesite' => 'Lax',
    );

    setcookie($cookie_name, $hashed, $cookie_opts);
    $_COOKIE[$cookie_name] = $hashed;
}

function buykorigw_get_cached_identity_hash($field)
{
    $cookie_name = buykorigw_identity_cookie_name($field);
    $hashed = isset($_COOKIE[$cookie_name]) ? sanitize_text_field(wp_unslash($_COOKIE[$cookie_name])) : '';
    return preg_match('/^[a-f0-9]{64}$/', $hashed) ? $hashed : '';
}

function buykorigw_hash_identity_field($field, $raw_value)
{
    $raw_value = sanitize_text_field((string) $raw_value);
    if ($raw_value === '') {
        return '';
    }

    return $field === 'ph' ? buykorigw_hash_phone($raw_value) : buykorigw_hash($raw_value);
}

function buykorigw_apply_identity_data(&$user_data, $raw_values = array(), $use_cache = true)
{
    foreach (buykorigw_identity_fields() as $field) {
        $raw_value = isset($raw_values[$field]) ? $raw_values[$field] : '';
        $hashed = buykorigw_hash_identity_field($field, $raw_value);

        if ($hashed) {
            $user_data[$field] = array($hashed);
            buykorigw_cache_identity_hash($field, $hashed);
            continue;
        }

        if ($use_cache && empty($user_data[$field])) {
            $cached = buykorigw_get_cached_identity_hash($field);
            if ($cached) {
                $user_data[$field] = array($cached);
            }
        }
    }
}

// ─── Helper: HPOS-Compatible Order Meta Get ─────────────────────────────────────
function buykorigw_get_order_meta($order_id, $key, $single = true)
{
    if (function_exists('wc_get_order')) {
        $order = wc_get_order($order_id);
        if ($order && method_exists($order, 'get_meta')) {
            return $order->get_meta($key, $single);
        }
    }
    return get_post_meta($order_id, $key, $single);
}

// ─── Helper: HPOS-Compatible Order Meta Update ──────────────────────────────────
function buykorigw_update_order_meta($order_id, $key, $value)
{
    if (function_exists('wc_get_order')) {
        $order = wc_get_order($order_id);
        if ($order && method_exists($order, 'update_meta_data')) {
            $order->update_meta_data($key, $value);
            $order->save();
            return;
        }
    }
    update_post_meta($order_id, $key, $value);
}

// ─── Helper: WooCommerce Cart Payload for Checkout/Cart Events ─────────────────
function buykorigw_ensure_wc_cart_loaded()
{
    if (!function_exists('WC')) {
        return false;
    }

    if (WC()->cart) {
        return true;
    }

    if (function_exists('wc_load_cart')) {
        wc_load_cart();
    } elseif (class_exists('WC_Cart')) {
        WC()->cart = new WC_Cart();
    }

    return (bool) WC()->cart;
}

function buykorigw_get_cart_event_data()
{
    if (!buykorigw_ensure_wc_cart_loaded()) {
        return array();
    }

    $settings = buykorigw_get_settings();
    $content_format = isset($settings['content_id_format']) ? $settings['content_id_format'] : 'id';
    $enable_variations = true;

    $cart_ids  = array();
    $contents  = array();
    $num_items = 0;

    foreach (WC()->cart->get_cart() as $cart_item) {
        $product_id   = isset($cart_item['product_id']) ? (int) $cart_item['product_id'] : 0;
        $variation_id = isset($cart_item['variation_id']) ? (int) $cart_item['variation_id'] : 0;
        $quantity     = isset($cart_item['quantity']) ? (int) $cart_item['quantity'] : 0;

        if ($product_id <= 0 || $quantity <= 0) {
            continue;
        }

        $product = null;
        $final_id = (string) $product_id;

        if ($enable_variations && $variation_id > 0) {
            $product = wc_get_product($variation_id);
            $final_id = (string) $variation_id;
            if ($content_format === 'sku' && $product) {
                $sku = $product->get_sku();
                if (!empty($sku)) {
                    $final_id = $sku;
                }
            }
        } else {
            $product = wc_get_product($product_id);
            if ($content_format === 'sku' && $product) {
                $sku = $product->get_sku();
                if (!empty($sku)) {
                    $final_id = $sku;
                }
            }
        }

        $cart_ids[] = $final_id;
        $item_price = $product ? (float) wc_get_price_to_display($product) : 0;

        $content_item = array(
            'id'           => $final_id,
            'content_id'   => $final_id,
            'content_type' => 'product',
            'content_name' => $product ? $product->get_name() : '',
            'quantity'     => $quantity,
            'item_price'   => $item_price,
        );

        if ($enable_variations && $variation_id > 0 && $product) {
            $attributes = $product->get_variation_attributes();
            $formatted_attributes = array();
            foreach ($attributes as $tax => $slug) {
                $name = str_replace('attribute_', '', $tax);
                if (taxonomy_exists($name)) {
                    $label = wc_attribute_label($name);
                    $term = get_term_by('slug', $slug, $name);
                    $val = $term ? $term->name : $slug;
                } else {
                    $label = $name;
                    $val = $slug;
                }
                $formatted_attributes[$label] = $val;
            }
            if (!empty($formatted_attributes)) {
                $content_item['attributes'] = $formatted_attributes;
            }
        }

        $contents[] = $content_item;
        $num_items += $quantity;
    }

    if (empty($contents)) {
        return array();
    }

    return array(
        'content_ids' => array_values(array_unique($cart_ids)),
        'contents'    => $contents,
        'value'       => (float) WC()->cart->get_cart_contents_total(),
        'currency'    => function_exists('get_woocommerce_currency') ? get_woocommerce_currency() : 'BDT',
        'num_items'   => $num_items,
        'content_type'=> 'product',
    );
}

// ─── Server AddToCart receipt queue ───────────────────────────────────────────
add_action('woocommerce_add_to_cart', 'buykorigw_server_add_to_cart', 20, 6);

function buykorigw_atc_receipt_queue()
{
    if (!function_exists('WC') || !WC()->session) {
        return array();
    }

    $queue = WC()->session->get('buykorigw_atc_receipts', array());
    return is_array($queue) ? $queue : array();
}

function buykorigw_set_atc_receipt_queue($queue)
{
    if (!function_exists('WC') || !WC()->session) {
        return;
    }

    WC()->session->set('buykorigw_atc_receipts', array_slice(array_values($queue), -20));
}

function buykorigw_recent_atc_intent_marker($max_age_seconds = 120)
{
    $timestamp = isset($_COOKIE['_buykorigw_atc_intent'])
        ? (int) sanitize_text_field(wp_unslash($_COOKIE['_buykorigw_atc_intent']))
        : 0;

    return $timestamp > 0 && (time() - $timestamp) <= $max_age_seconds;
}

function buykorigw_atc_intent_marker_timestamp()
{
    return isset($_COOKIE['_buykorigw_atc_intent'])
        ? (int) sanitize_text_field(wp_unslash($_COOKIE['_buykorigw_atc_intent']))
        : 0;
}

function buykorigw_server_add_to_cart($cart_item_key, $product_id, $quantity, $variation_id, $variation, $cart_item_data)
{
    $settings = buykorigw_get_settings();
    if (empty($settings['enable_addtocart']) || empty($settings['api_key']) || !function_exists('WC') || !WC()->session) {
        return;
    }
    if (!buykorigw_recent_atc_intent_marker()) {
        return;
    }
    $intent_timestamp = buykorigw_atc_intent_marker_timestamp();
    $consumed_timestamp = (int) WC()->session->get('buykorigw_last_atc_intent', 0);
    if ($intent_timestamp <= $consumed_timestamp) {
        return;
    }
    WC()->session->set('buykorigw_last_atc_intent', $intent_timestamp);

    $selected_id = !empty($variation_id) ? (int) $variation_id : (int) $product_id;
    $product = $selected_id ? wc_get_product($selected_id) : false;
    if (!$product) {
        return;
    }

    $counter = (int) WC()->session->get('buykorigw_atc_counter', 0) + 1;
    WC()->session->set('buykorigw_atc_counter', $counter);
    $visitor_id = isset($_COOKIE['_buykorigw_vid']) ? sanitize_text_field(wp_unslash($_COOKIE['_buykorigw_vid'])) : '';
    if ($visitor_id === '') {
        $visitor_id = (string) WC()->session->get_customer_id();
    }
    $event_id = 'wc_atc_' . md5($visitor_id . '|' . $cart_item_key . '|' . $counter);

    $content_id = (string) $selected_id;
    if (($settings['content_id_format'] ?? 'id') === 'sku' && $product->get_sku()) {
        $content_id = $product->get_sku();
    }
    $quantity = max(1, (int) $quantity);
    $item_price = (float) wc_get_price_to_display($product);
    $custom_data = array(
        'content_ids'  => array($content_id),
        'contents'     => array(array(
            'id'           => $content_id,
            'content_id'   => $content_id,
            'content_type' => 'product',
            'content_name' => $product->get_name(),
            'quantity'     => $quantity,
            'item_price'   => $item_price,
        )),
        'content_name' => $product->get_name(),
        'content_type' => 'product',
        'value'        => $item_price * $quantity,
        'currency'     => function_exists('get_woocommerce_currency') ? get_woocommerce_currency() : 'BDT',
        'num_items'    => $quantity,
        'trigger_reason' => 'woocommerce_add_to_cart',
    );

    $user_data = array(
        'client_ip_address' => function_exists('buykorigw_get_real_ip') ? buykorigw_get_real_ip() : ($_SERVER['REMOTE_ADDR'] ?? ''),
        'client_user_agent' => sanitize_text_field($_SERVER['HTTP_USER_AGENT'] ?? ''),
    );
    foreach (array('fbp' => '_fbp', 'fbc' => '_fbc', 'ttp' => '_ttp', 'ttclid' => '_ttclid') as $key => $cookie_name) {
        if (!empty($_COOKIE[$cookie_name])) {
            $user_data[$key] = sanitize_text_field(wp_unslash($_COOKIE[$cookie_name]));
        }
    }
    if ($visitor_id !== '') {
        $user_data['external_id'] = array(buykorigw_hash($visitor_id));
    }
    buykorigw_apply_identity_data($user_data);

    $event_payload = array(
        'event_name'       => 'AddToCart',
        'event_time'       => time(),
        'event_id'         => $event_id,
        'event_source_url' => wp_get_referer() ?: home_url('/'),
        'action_source'    => 'website',
        'user_data'        => $user_data,
        'custom_data'      => function_exists('buykorigw_add_marketing_params') ? buykorigw_add_marketing_params($custom_data) : $custom_data,
    );
    buykorigw_send_event($event_payload, false);

    $queue = buykorigw_atc_receipt_queue();
    $queue[] = array(
        'event_id'   => $event_id,
        'event_data' => $custom_data,
        'created_at' => time(),
    );
    buykorigw_set_atc_receipt_queue($queue);
}

// ─── REST API Endpoint: /wp-json/buykori/v1/track ───────────────────────────────
function buykorigw_normalize_event_custom_data($custom_data, $event_name, $settings = null)
{
    if (!is_array($custom_data)) {
        $custom_data = array();
    }

    if (function_exists('buykorigw_add_marketing_params')) {
        $custom_data = buykorigw_add_marketing_params($custom_data);
    }

    if (in_array($event_name, array('AddToCart', 'InitiateCheckout', 'ViewCart', 'AddPaymentInfo'), true)) {
        $cart_data = buykorigw_get_cart_event_data();
        if (!empty($cart_data)) {
            foreach ($cart_data as $key => $value) {
                $is_empty_array = is_array($custom_data[$key] ?? null) && empty($custom_data[$key]);
                if (!isset($custom_data[$key]) || $custom_data[$key] === '' || $custom_data[$key] === 0 || $is_empty_array) {
                    $custom_data[$key] = $value;
                }
            }
        }
    }

    if (empty($custom_data['content_id']) && !empty($custom_data['content_ids']) && is_array($custom_data['content_ids'])) {
        $first_content_id = reset($custom_data['content_ids']);
        if (!empty($first_content_id)) {
            $custom_data['content_id'] = (string) $first_content_id;
        }
    }
    if (empty($custom_data['content_ids']) && !empty($custom_data['content_id'])) {
        $custom_data['content_ids'] = is_array($custom_data['content_id'])
            ? array_values(array_filter(array_map('strval', $custom_data['content_id'])))
            : array((string) $custom_data['content_id']);
    }

    if (empty($custom_data['contents']) && !empty($custom_data['content_ids']) && is_array($custom_data['content_ids'])) {
        $contents = array();
        $item_price = isset($custom_data['value']) && count($custom_data['content_ids']) === 1 ? (float) $custom_data['value'] : 0;
        foreach ($custom_data['content_ids'] as $content_id) {
            $content_id = trim((string) $content_id);
            if ($content_id === '' || preg_match('/^[A-Z]{3}$/i', $content_id)) {
                continue;
            }
            $contents[] = array(
                'id'           => $content_id,
                'content_id'   => $content_id,
                'content_type' => 'product',
                'quantity'     => 1,
                'item_price'   => $item_price,
            );
        }
        if (!empty($contents)) {
            $custom_data['contents'] = $contents;
        }
    }

    if ($settings === null) {
        $settings = buykorigw_get_settings();
    }
    $content_format = isset($settings['content_id_format']) ? $settings['content_id_format'] : 'id';
    if (function_exists('buykorigw_normalize_content_identifiers')) {
        buykorigw_normalize_content_identifiers($custom_data, $content_format);
    }

    if (!empty($custom_data['content_ids']) && is_array($custom_data['content_ids'])) {
        $custom_data['content_ids'] = array_values(array_filter($custom_data['content_ids'], function ($id) {
            $id = trim((string) $id);
            return $id !== '' && !preg_match('/^[A-Z]{3}$/i', $id);
        }));
        if (empty($custom_data['content_ids'])) {
            unset($custom_data['content_ids']);
        }
    }
    if (!empty($custom_data['contents']) && is_array($custom_data['contents'])) {
        $custom_data['contents'] = array_values(array_filter(array_map(function ($item) {
            if (!is_array($item)) {
                return null;
            }
            $id = trim((string) ($item['content_id'] ?? ($item['id'] ?? '')));
            if ($id === '' || preg_match('/^[A-Z]{3}$/i', $id)) {
                return null;
            }
            $item['id'] = isset($item['id']) ? trim((string) $item['id']) : $id;
            $item['content_id'] = isset($item['content_id']) ? trim((string) $item['content_id']) : $id;
            $item['content_type'] = $item['content_type'] ?? 'product';
            $item['quantity'] = max(1, (int) ($item['quantity'] ?? 1));
            if (!isset($item['item_price']) && isset($item['price'])) {
                $item['item_price'] = (float) $item['price'];
            }
            return $item;
        }, $custom_data['contents'])));
        if (empty($custom_data['contents'])) {
            unset($custom_data['contents']);
        }
    }

    return $custom_data;
}

add_action('rest_api_init', function () {
    register_rest_route('buykori/v1', '/track', array(
        'methods'             => 'POST',
        'callback'            => 'buykorigw_rest_track_event',
        'permission_callback' => '__return_true',
    ));
    register_rest_route('buykori/v1', '/atc-receipts', array(
        array(
            'methods'             => 'GET',
            'callback'            => 'buykorigw_rest_get_atc_receipts',
            'permission_callback' => '__return_true',
        ),
        array(
            'methods'             => 'POST',
            'callback'            => 'buykorigw_rest_ack_atc_receipts',
            'permission_callback' => '__return_true',
        ),
    ));
    register_rest_route('buykori/v1', '/incomplete-checkout', array(
        'methods'             => 'POST',
        'callback'            => 'buykorigw_rest_capture_incomplete_checkout',
        'permission_callback' => '__return_true',
    ));
});

add_filter('rest_authentication_errors', 'buykorigw_bypass_rest_cookie_error', 99);

function buykorigw_bypass_rest_cookie_error($errors) {
    if (is_wp_error($errors) && $errors->get_error_code() === 'rest_cookie_invalid_nonce') {
        if (isset($_SERVER['REQUEST_URI']) && (
            strpos($_SERVER['REQUEST_URI'], 'buykori/v1/track') !== false
            || strpos($_SERVER['REQUEST_URI'], 'buykori/v1/atc-receipts') !== false
            || strpos($_SERVER['REQUEST_URI'], 'buykori/v1/incomplete-checkout') !== false
        )) {
            return null;
        }
    }
    return $errors;
}

function buykorigw_forward_incomplete_checkout($path, $payload, $blocking = true)
{
    $settings = buykorigw_get_settings();
    if (empty($settings['api_key']) || empty($settings['gateway_url'])) {
        return false;
    }

    $body = wp_json_encode($payload);
    $response = wp_remote_post(rtrim($settings['gateway_url'], '/') . $path, array(
        'timeout'     => 8,
        'redirection' => 0,
        'httpversion' => '1.1',
        'blocking'    => (bool) $blocking,
        'sslverify'   => true,
        'headers'     => array_merge(array(
            'Content-Type' => 'application/json',
            'X-API-Key'    => $settings['api_key'],
        ), buykorigw_signed_headers($settings['api_key'], $body)),
        'body'        => $body,
    ));

    if (is_wp_error($response)) {
        return false;
    }
    if (!$blocking) {
        return true;
    }

    return wp_remote_retrieve_response_code($response) >= 200
        && wp_remote_retrieve_response_code($response) < 300;
}

function buykorigw_rest_capture_incomplete_checkout(WP_REST_Request $request)
{
    if (!buykorigw_rest_same_origin_allowed()) {
        return new WP_REST_Response(array('success' => false, 'message' => 'Invalid origin'), 403);
    }
    if (function_exists('buykorigw_ajax_rate_limited') && buykorigw_ajax_rate_limited()) {
        return new WP_REST_Response(array('success' => false, 'message' => 'Rate limit exceeded'), 429);
    }

    $params = $request->get_json_params();
    $phone = preg_replace('/[^0-9]/', '', (string) ($params['phone'] ?? ''));
    if (strlen($phone) < 10 || strlen($phone) > 13) {
        return new WP_REST_Response(array('success' => false, 'message' => 'Valid phone required'), 422);
    }

    $products = array();
    foreach ((array) ($params['products'] ?? array()) as $item) {
        if (!is_array($item)) {
            continue;
        }
        $products[] = array(
            'id'       => sanitize_text_field((string) ($item['id'] ?? ($item['content_id'] ?? ''))),
            'name'     => sanitize_text_field((string) ($item['content_name'] ?? ($item['name'] ?? ''))),
            'quantity' => max(1, (int) ($item['quantity'] ?? 1)),
            'price'    => max(0, (float) ($item['item_price'] ?? ($item['price'] ?? 0))),
        );
    }

    $payload = array(
        'visitor_id'    => sanitize_text_field((string) ($params['visitor_id'] ?? '')),
        'phone'         => $phone,
        'customer_name' => sanitize_text_field((string) ($params['customer_name'] ?? '')),
        'email'         => sanitize_email((string) ($params['email'] ?? '')),
        'address'       => sanitize_textarea_field((string) ($params['address'] ?? '')),
        'products'      => array_slice($products, 0, 30),
        'amount'        => max(0, (float) ($params['amount'] ?? 0)),
        'currency'      => sanitize_text_field((string) ($params['currency'] ?? 'BDT')),
        'page_url'      => esc_url_raw((string) ($params['page_url'] ?? '')),
        'campaign_data' => (object) (is_array($params['campaign_data'] ?? null) ? array_map('sanitize_text_field', $params['campaign_data']) : array()),
    );
    if (strlen($payload['visitor_id']) < 8) {
        return new WP_REST_Response(array('success' => false, 'message' => 'Visitor ID required'), 422);
    }

    $sent = buykorigw_forward_incomplete_checkout('/incomplete-checkouts/upsert', $payload);
    return new WP_REST_Response(array('success' => $sent), $sent ? 200 : 502);
}

function buykorigw_convert_incomplete_checkout($order)
{
    if (!$order || !is_a($order, 'WC_Order')) {
        return false;
    }
    $phone = $order->get_billing_phone();
    if (!$phone) {
        return false;
    }
    $payload = array(
        'visitor_id' => sanitize_text_field((string) ($_COOKIE['_buykorigw_vid'] ?? '')),
        'phone'      => $phone,
        'order_id'   => (string) $order->get_id(),
    );
    return buykorigw_forward_incomplete_checkout('/incomplete-checkouts/convert', $payload, false);
}

function buykorigw_rest_same_origin_allowed()
{
    $allowed_host = parse_url(home_url(), PHP_URL_HOST);
    if (!$allowed_host) {
        $allowed_host = $_SERVER['HTTP_HOST'] ?? '';
    }

    $request_origin  = $_SERVER['HTTP_ORIGIN'] ?? '';
    $request_referer = $_SERVER['HTTP_REFERER'] ?? '';
    if ($request_origin) {
        $origin_host = parse_url($request_origin, PHP_URL_HOST);
        if (buykorigw_host_allowed($origin_host, $allowed_host)) {
            return true;
        }
    }
    if ($request_referer) {
        $referer_host = parse_url($request_referer, PHP_URL_HOST);
        if (buykorigw_host_allowed($referer_host, $allowed_host)) {
            return true;
        }
    }
    return false;
}

function buykorigw_rest_get_atc_receipts()
{
    if (!buykorigw_rest_same_origin_allowed()) {
        return new WP_REST_Response(array('success' => false, 'message' => 'Invalid origin'), 403);
    }
    if (!buykorigw_ensure_wc_cart_loaded()) {
        return new WP_REST_Response(array('success' => true, 'receipts' => array()), 200);
    }

    $minimum_time = time() - DAY_IN_SECONDS;
    $queue = array_values(array_filter(buykorigw_atc_receipt_queue(), function ($receipt) use ($minimum_time) {
        return is_array($receipt) && !empty($receipt['event_id']) && (int) ($receipt['created_at'] ?? 0) >= $minimum_time;
    }));
    buykorigw_set_atc_receipt_queue($queue);

    return new WP_REST_Response(array('success' => true, 'receipts' => $queue), 200);
}

function buykorigw_rest_ack_atc_receipts(WP_REST_Request $request)
{
    if (!buykorigw_rest_same_origin_allowed()) {
        return new WP_REST_Response(array('success' => false, 'message' => 'Invalid origin'), 403);
    }
    if (!buykorigw_ensure_wc_cart_loaded()) {
        return new WP_REST_Response(array('success' => true), 200);
    }

    $params = $request->get_json_params();
    $ids = is_array($params['event_ids'] ?? null) ? $params['event_ids'] : array();
    $ids = array_values(array_filter(array_map('sanitize_text_field', $ids)));
    $queue = array_values(array_filter(buykorigw_atc_receipt_queue(), function ($receipt) use ($ids) {
        return empty($receipt['event_id']) || !in_array($receipt['event_id'], $ids, true);
    }));
    buykorigw_set_atc_receipt_queue($queue);

    return new WP_REST_Response(array('success' => true), 200);
}

function buykorigw_rest_track_event(WP_REST_Request $request)
{
    $rest_nonce = $request->get_header('x-wp-nonce');
    $nonce_valid = !empty($rest_nonce) && wp_verify_nonce($rest_nonce, 'wp_rest');

    // ─── Origin / Referer Validation ─────────────────────────────────────
    $allowed_host = parse_url(home_url(), PHP_URL_HOST);
    if (!$allowed_host) {
        $allowed_host = $_SERVER['HTTP_HOST'] ?? '';
    }

    $request_origin  = $_SERVER['HTTP_ORIGIN'] ?? '';
    $request_referer = $_SERVER['HTTP_REFERER'] ?? '';
    $origin_valid    = false;

    if (!empty($request_origin)) {
        $origin_host = parse_url($request_origin, PHP_URL_HOST);
        if (buykorigw_host_allowed($origin_host, $allowed_host)) {
            $origin_valid = true;
        }
    }
    if (!$origin_valid && !empty($request_referer)) {
        $referer_host = parse_url($request_referer, PHP_URL_HOST);
        if (buykorigw_host_allowed($referer_host, $allowed_host)) {
            $origin_valid = true;
        }
    }
    if (!$origin_valid) {
        return new WP_REST_Response(array('success' => false, 'message' => 'Invalid origin'), 403);
    }

    // Public storefront pages are commonly cached longer than WordPress REST
    // nonces. Same-origin validation keeps cached tracking functional while
    // still rejecting cross-site event injection.
    if (!$nonce_valid && empty($request_origin) && empty($request_referer)) {
        return new WP_REST_Response(array('success' => false, 'message' => 'Missing request origin'), 403);
    }

    // ─── Rate Limit ──────────────────────────────────────────────────────
    if (function_exists('buykorigw_ajax_rate_limited') && buykorigw_ajax_rate_limited()) {
        return new WP_REST_Response(array('success' => false, 'message' => 'Rate limit exceeded'), 429);
    }

    // ─── Parse Parameters ────────────────────────────────────────────────
    $params     = $request->get_json_params();
    if (empty($params)) {
        $params = $request->get_body_params();
    }

    $event_name = sanitize_text_field($params['event_name'] ?? '');
    $event_id   = sanitize_text_field($params['event_id'] ?? '');
    $event_json = $params['event_data'] ?? ($params['custom_data'] ?? '{}');
    $page_url   = esc_url_raw($params['page_url'] ?? '');
    $page_title = sanitize_text_field($params['page_title'] ?? '');
    $fbp        = sanitize_text_field($params['fbp'] ?? '');
    $fbc        = sanitize_text_field($params['fbc'] ?? '');
    $ttp        = sanitize_text_field($params['ttp'] ?? '');
    $ttclid     = sanitize_text_field($params['ttclid'] ?? '');
    $external_id = sanitize_text_field($params['external_id'] ?? '');
    $ga_cookie  = sanitize_text_field($params['_ga'] ?? '');
    $ga_session = sanitize_text_field($params['ga_session_id'] ?? '');

    // ─── Whitelist Event Names ───────────────────────────────────────────
    $allowed_events = array('PageView', 'ViewContent', 'AddToCart', 'ViewCart', 'RemoveFromCart', 'InitiateCheckout', 'AddPaymentInfo', 'Purchase', 'Lead', 'Search', 'Identify', 'Refund');
    if (defined('BUYKORIGW_CUSTOM_EVENTS_KEY')) {
        $custom_events = get_option(BUYKORIGW_CUSTOM_EVENTS_KEY, array());
        foreach ($custom_events as $ce) {
            if (!empty($ce['name']) && !empty($ce['enabled'])) {
                $allowed_events[] = $ce['name'];
            }
        }
    }

    if (empty($event_name) || !in_array($event_name, $allowed_events, true)) {
        return new WP_REST_Response(array('success' => false, 'message' => 'Invalid event name'), 400);
    }

    // ─── First-Party Cookie Management ───────────────────────────────────
    $normalized_cookie_host = preg_replace('/^www\./', '', strtolower($allowed_host));
    $can_set_cookie_domain = (
        strpos($normalized_cookie_host, '.') !== false
        && !filter_var($normalized_cookie_host, FILTER_VALIDATE_IP)
        && $normalized_cookie_host !== 'localhost'
    );
    $cookie_expiry = time() + (90 * 24 * 60 * 60); // 90 days
    $cookie_opts   = array(
        'expires'  => $cookie_expiry,
        'path'     => '/',
        'secure'   => is_ssl(),
        'httponly' => false,
        'samesite' => 'Lax',
    );
    if ($can_set_cookie_domain) {
        $cookie_opts['domain'] = '.' . $normalized_cookie_host;
    }

    // _fbp: auto-create if missing
    if (empty($fbp)) {
        $fbp = $_COOKIE['_fbp'] ?? '';
    }
    if (empty($fbp)) {
        $fbp = 'fb.1.' . (string) (time() * 1000) . '.' . wp_rand(1000000000, 9999999999);
    }
    setcookie('_fbp', $fbp, $cookie_opts);
    $_COOKIE['_fbp'] = $fbp;

    // _fbc: auto-create from fbclid if present
    if (empty($fbc)) {
        $fbc = $_COOKIE['_fbc'] ?? '';
    }
    if (empty($fbc) && !empty($params['fbclid'])) {
        $fbc = 'fb.1.' . (string) (time() * 1000) . '.' . sanitize_text_field($params['fbclid']);
    }
    if (!empty($fbc)) {
        setcookie('_fbc', $fbc, $cookie_opts);
        $_COOKIE['_fbc'] = $fbc;
    }

    // _ttp: auto-create if missing
    if (empty($ttp)) {
        $ttp = $_COOKIE['_ttp'] ?? '';
    }
    if (empty($ttp)) {
        $ttp = wp_generate_uuid4();
    }
    setcookie('_ttp', $ttp, $cookie_opts);
    $_COOKIE['_ttp'] = $ttp;

    // _ttclid: persist if provided
    if (!empty($ttclid)) {
        setcookie('_ttclid', $ttclid, $cookie_opts);
        $_COOKIE['_ttclid'] = $ttclid;
    }

    // Stable first-party visitor ID for Meta/TikTok external_id matching
    if (empty($external_id)) {
        $external_id = $_COOKIE['_buykorigw_vid'] ?? '';
    }
    if (empty($external_id)) {
        $external_id = 'bk.' . time() . '.' . wp_rand(1000000000, 9999999999);
    }
    setcookie('_buykorigw_vid', $external_id, $cookie_opts);
    $_COOKIE['_buykorigw_vid'] = $external_id;

    // ─── Parse Custom Data ───────────────────────────────────────────────
    $settings    = buykorigw_get_settings();
    $custom_data = is_string($event_json) ? json_decode($event_json, true) : (array) $event_json;
    if (!is_array($custom_data)) {
        $custom_data = array();
    }

    // Inject GA4 identifiers into custom_data
    if (!empty($ga_cookie)) {
        $custom_data['_ga'] = $ga_cookie;
    }
    if (!empty($ga_session)) {
        $custom_data['session_id'] = $ga_session;
    }

    $custom_data = buykorigw_normalize_event_custom_data($custom_data, $event_name, $settings);

    // ─── Build User Data ─────────────────────────────────────────────────
    $user_data = array(
        'client_ip_address' => function_exists('buykorigw_get_real_ip') ? buykorigw_get_real_ip() : ($_SERVER['REMOTE_ADDR'] ?? ''),
        'client_user_agent' => sanitize_text_field($_SERVER['HTTP_USER_AGENT'] ?? ''),
    );

    if (!empty($fbp)) {
        $user_data['fbp'] = $fbp;
    }
    if (!empty($fbc)) {
        $user_data['fbc'] = $fbc;
    }
    if (!empty($ttp)) {
        $user_data['ttp'] = $ttp;
    }
    if (!empty($ttclid)) {
        $user_data['ttclid'] = $ttclid;
    }
    if (!empty($external_id)) {
        $user_data['external_id'] = array(buykorigw_hash($external_id));
    }

    buykorigw_apply_identity_data($user_data, $params);

    if ($event_name === 'Identify') {
        return new WP_REST_Response(array('success' => true, 'message' => 'Identity updated'), 200);
    }

    // Logged-in user PII enrichment
    if (is_user_logged_in()) {
        $user = wp_get_current_user();
        buykorigw_apply_identity_data($user_data, array(
            'em' => $user->user_email,
            'fn' => $user->first_name,
            'ln' => $user->last_name,
        ), false);
    }

    // ─── Build Event Payload ─────────────────────────────────────────────
    $event_payload = array(
        'event_name'       => $event_name,
        'event_time'       => time(),
        'event_source_url' => $page_url,
        'action_source'    => 'website',
        'user_data'        => $user_data,
        'event_id'         => !empty($event_id) ? $event_id : 'wp_' . $event_name . '_' . time() . '_' . wp_rand(1000, 9999),
    );

    if (!empty($custom_data)) {
        $event_payload['custom_data'] = $custom_data;
    }

    // ─── Forward to FastAPI Backend ──────────────────────────────────────
    $sent = buykorigw_send_event($event_payload, true);
    if (!$sent) {
        $response = array(
            'success' => false,
            'message' => 'Event accepted by WordPress but gateway forwarding failed',
        );
        $last_error = buykorigw_get_last_event_error();
        if (!empty($settings['debug_mode']) && !empty($last_error)) {
            $response['gateway_error'] = array_filter(array(
                'message' => $last_error['message'] ?? '',
                'code'    => $last_error['code'] ?? 0,
                'body'    => $last_error['body'] ?? '',
            ));
        }
        return new WP_REST_Response($response, 502);
    }

    if ($event_name === 'InitiateCheckout') {
        buykorigw_mark_initiate_checkout_sent($event_payload['event_id']);
    }

    return new WP_REST_Response(array('success' => true, 'message' => 'Event tracked'), 200);
}

// ─── Admin Settings Page ────────────────────────────────────────────────────────
if (is_admin()) {
    require_once BUYKORIGW_PLUGIN_DIR . 'includes/admin-settings.php';
    require_once BUYKORIGW_PLUGIN_DIR . 'includes/dashboard-widget.php';
}



function buykorigw_normalize_content_identifiers( &$custom_data, $format ) {
    if ( $format !== 'sku' || ! function_exists( 'wc_get_product' ) ) {
        return;
    }

    // Convert content_ids
    if ( ! empty( $custom_data['content_ids'] ) && is_array( $custom_data['content_ids'] ) ) {
        $new_ids = array();
        foreach ( $custom_data['content_ids'] as $id ) {
            $product = wc_get_product( $id );
            if ( $product ) {
                $sku = $product->get_sku();
                $new_ids[] = ! empty( $sku ) ? $sku : (string) $id;
            } else {
                $new_ids[] = (string) $id;
            }
        }
        $custom_data['content_ids'] = $new_ids;
    }

    // Convert content_id (single)
    if ( ! empty( $custom_data['content_id'] ) ) {
        $product = wc_get_product( $custom_data['content_id'] );
        if ( $product ) {
            $sku = $product->get_sku();
            $custom_data['content_id'] = ! empty( $sku ) ? $sku : (string) $custom_data['content_id'];
        }
    }

    // Convert contents
    if ( ! empty( $custom_data['contents'] ) && is_array( $custom_data['contents'] ) ) {
        $new_contents = array();
        foreach ( $custom_data['contents'] as $item ) {
            $id = $item['id'] ?? ( $item['content_id'] ?? 0 );
            if ( $id ) {
                $product = wc_get_product( $id );
                if ( $product ) {
                    $sku = $product->get_sku();
                    $final_id = ! empty( $sku ) ? $sku : (string) $id;
                    if ( isset( $item['id'] ) ) {
                        $item['id'] = $final_id;
                    }
                    if ( isset( $item['content_id'] ) ) {
                        $item['content_id'] = $final_id;
                    }
                }
            }
            $new_contents[] = $item;
        }
        $custom_data['contents'] = $new_contents;
    }
}

// Frontend tracking (only on frontend, not admin/cron, but allow AJAX and REST)
if (!is_admin() || wp_doing_ajax()) {
    require_once BUYKORIGW_PLUGIN_DIR . 'includes/frontend-tracking.php';
}

// Custom events (admin UI + frontend JS — loads in both contexts)
require_once BUYKORIGW_PLUGIN_DIR . 'includes/custom-events.php';

// WooCommerce order hooks (always load — works via WP-Cron and admin)
if (class_exists('WooCommerce') || in_array('woocommerce/woocommerce.php', apply_filters('active_plugins', get_option('active_plugins')))) {
    require_once BUYKORIGW_PLUGIN_DIR . 'includes/woo-order-hooks.php';
}

// Auto-updater (check for plugin updates from our server)
if (is_admin()) {
    require_once BUYKORIGW_PLUGIN_DIR . 'includes/auto-updater.php';
}
