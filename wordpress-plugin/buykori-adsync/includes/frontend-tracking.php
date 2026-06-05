<?php
/**
 * Buykori AdSync — Frontend Tracking
 *
 * ওয়েবসাইটের ফ্রন্টএন্ডে অটোমেটিক ইভেন্ট ট্র্যাকিং:
 * - PageView (প্রতিটি পেজে)
 * - ViewContent (WooCommerce প্রোডাক্ট পেজে)
 * - AddToCart (কার্টে যোগ করলে — AJAX দিয়ে)
 * - InitiateCheckout (চেকআউট পেজে)
 * - Purchase (Thank You / Order Received পেজে)
 *
 * Cache plugin bypass: সব ট্র্যাকিং AJAX/REST API দিয়ে চলে,
 * তাই LiteSpeed/WP Rocket ক্যাশ করলেও ডাটা ফ্রেশ থাকে।
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

// ─── Inject Tracker Script ────────────────────────────────────────────────────
/**
 * Resolve the configured tracking mode.
 *
 * One-page landing stores often place product and checkout UI on the same
 * screen. In that case checkout events should wait for user intent instead of
 * firing immediately on page load.
 */
function buykorigw_resolve_tracking_mode( $settings ) {
    $mode = isset( $settings['tracking_mode'] ) ? $settings['tracking_mode'] : 'auto';
    if ( ! in_array( $mode, array( 'auto', 'standard', 'one_page' ), true ) ) {
        $mode = 'auto';
    }

    if ( $mode !== 'auto' ) {
        return $mode;
    }

    // PHP cannot reliably detect checkout widgets rendered later by builders.
    // Pass auto through so the browser resolver can combine PHP and live DOM hints.
    return 'auto';
}

add_action( 'wp_enqueue_scripts', 'buykorigw_enqueue_tracker_script' );
add_action( 'wp_footer', 'buykorigw_inject_tracker', 5 );

function buykorigw_enqueue_tracker_script() {
    $settings = buykorigw_get_settings();
    if ( empty( $settings['api_key'] ) ) {
        return;
    }

    $js_file = BUYKORIGW_PLUGIN_DIR . 'assets/js/tracker.js';
    $version = file_exists( $js_file ) ? filemtime( $js_file ) : BUYKORIGW_VERSION;

    wp_enqueue_script(
        'buykorigw-tracker',
        plugins_url( 'assets/js/tracker.js', dirname( __FILE__ ) ),
        array(),
        $version,
        true
    );
}

function buykorigw_current_post_has_shortcode( $shortcodes ) {
    global $post;
    if ( ! $post || empty( $post->post_content ) ) {
        return false;
    }

    foreach ( (array) $shortcodes as $shortcode ) {
        if ( has_shortcode( $post->post_content, $shortcode ) ) {
            return true;
        }
    }

    return false;
}

function buykorigw_current_page_is_wc_page( $page_key ) {
    if ( ! function_exists( 'wc_get_page_id' ) || ! function_exists( 'get_queried_object_id' ) ) {
        return false;
    }

    $page_id = wc_get_page_id( $page_key );
    return $page_id > 0 && (int) get_queried_object_id() === (int) $page_id;
}

function buykorigw_is_checkout_context() {
    $standard = ( function_exists( 'is_checkout' ) && is_checkout() )
        || buykorigw_current_page_is_wc_page( 'checkout' )
        || buykorigw_current_post_has_shortcode( array( 'woocommerce_checkout' ) );

    if ( $standard ) {
        return true;
    }

    // FunnelKit / WooFunnels checkout detection. Keep this shortcode-based so
    // minor plugin class-name changes do not break checkout context detection.
    if ( buykorigw_current_post_has_shortcode( array( 'wfacp_forms', 'wfacp_form', 'fk_checkout', 'funnelkit_checkout' ) ) ) {
        return true;
    }

    // CartFlows checkout detection
    if ( buykorigw_current_post_has_shortcode( array( 'cartflows_checkout' ) ) ) {
        return true;
    }

    return false;
}

function buykorigw_is_cart_context() {
    return ( function_exists( 'is_cart' ) && is_cart() )
        || buykorigw_current_page_is_wc_page( 'cart' )
        || buykorigw_current_post_has_shortcode( array( 'woocommerce_cart' ) );
}

function buykorigw_is_product_listing_context() {
    $has_archive = ( function_exists( 'is_shop' ) && is_shop() )
        || ( function_exists( 'is_product_category' ) && is_product_category() )
        || ( function_exists( 'is_product_tag' ) && is_product_tag() );

    return $has_archive || buykorigw_current_post_has_shortcode(
        array(
            'products',
            'product_category',
            'product_categories',
            'sale_products',
            'best_selling_products',
            'recent_products',
            'featured_products',
            'top_rated_products',
            'add_to_cart',
        )
    );
}

function buykorigw_inject_tracker() {
    $settings = buykorigw_get_settings();

    // Don't load if no API key
    if ( empty( $settings['api_key'] ) ) {
        return;
    }
    $low_resource_mode = false;

    // Pass config to JS
    $tracker_data = array(
        'ajax_url'    => admin_url( 'admin-ajax.php' ),
        'rest_url'    => rest_url( 'buykori/v1/track' ),
        'atc_receipts_url' => rest_url( 'buykori/v1/atc-receipts' ),
        'incomplete_checkout_url' => rest_url( 'buykori/v1/incomplete-checkout' ),
        'store_cart_url' => rest_url( 'wc/store/v1/cart' ),
        'nonce'       => wp_create_nonce( 'buykorigw_track_nonce' ),
        'rest_nonce'  => wp_create_nonce( 'wp_rest' ),
        'tracking_mode' => buykorigw_resolve_tracking_mode( $settings ),
        'content_id_format' => isset( $settings['content_id_format'] ) ? $settings['content_id_format'] : 'id',
        'currency' => function_exists( 'get_woocommerce_currency' ) ? get_woocommerce_currency() : 'BDT',
        'enable_hybrid' => isset( $settings['enable_hybrid'] ) ? (bool) $settings['enable_hybrid'] : false,
        'enable_variations' => true,
        'fb_pixel_id'  => isset( $settings['fb_pixel_id'] ) ? trim( $settings['fb_pixel_id'] ) : '',
        'tt_pixel_id'  => isset( $settings['tt_pixel_id'] ) ? trim( $settings['tt_pixel_id'] ) : '',
        'events'      => array(
            'pageview'       => $low_resource_mode ? false : (bool) $settings['enable_pageview'],
            'lead'           => (bool) $settings['enable_lead'],
            'search'         => $low_resource_mode ? false : (bool) $settings['enable_search'],
            'viewcontent'    => $low_resource_mode ? false : (bool) $settings['enable_viewcontent'],
            'addtocart'      => (bool) $settings['enable_addtocart'],
            'viewcart'       => (bool) $settings['enable_viewcart'],
            'removefromcart' => (bool) $settings['enable_removefromcart'],
            'checkout'       => (bool) $settings['enable_checkout'],
            'addpaymentinfo' => (bool) $settings['enable_addpaymentinfo'],
            'purchase'       => (bool) $settings['enable_purchase'],
        ),
    );

    $is_product = function_exists( 'is_product' ) && is_product();
    $is_checkout = buykorigw_is_checkout_context()
        && ( ! function_exists( 'is_order_received_page' ) || ! is_order_received_page() );
    $is_cart = buykorigw_is_cart_context();
    $is_thankyou = function_exists( 'is_order_received_page' ) && is_order_received_page();
    $is_product_listing = buykorigw_is_product_listing_context();
    $tracker_data['page_context'] = array(
        'has_product'         => $is_product,
        'has_product_listing' => $is_product_listing,
        'has_checkout'        => $is_checkout,
        'has_cart'            => $is_cart,
        'is_thankyou'         => $is_thankyou,
        'is_search'           => function_exists( 'is_search' ) && is_search(),
        'is_landing'          => ( function_exists( 'is_front_page' ) && is_front_page() )
            || ( function_exists( 'is_home' ) && is_home() ),
    );

    // Add product data if on a WooCommerce product page
    if ( ! $low_resource_mode && $is_product && $settings['enable_viewcontent'] ) {
        global $product;
        if ( $product && is_a( $product, 'WC_Product' ) ) {
            $tracker_data['product'] = array(
                'id'       => $product->get_id(),
                'sku'      => $product->get_sku() ?: (string) $product->get_id(),
                'name'     => $product->get_name(),
                'price'    => (float) $product->get_price(),
                'currency' => get_woocommerce_currency(),
                'category' => implode( ', ', wp_list_pluck( wc_get_product_terms( $product->get_id(), 'product_cat' ), 'name' ) ),
            );
        }
    }

    // Detect page type
    $tracker_data['page_type'] = 'other';
    if ( $is_product ) {
        $tracker_data['page_type'] = 'product';
    } elseif ( $is_product_listing ) {
        $tracker_data['page_type'] = 'product_listing';
    } elseif ( $is_checkout ) {
        $tracker_data['page_type'] = 'checkout';
    } elseif ( $is_cart ) {
        $tracker_data['page_type'] = 'cart';
    } elseif ( $is_thankyou ) {
        $tracker_data['page_type'] = 'thankyou';
    } elseif ( is_search() ) {
        $tracker_data['page_type'] = 'search';
        $tracker_data['search_string'] = get_search_query();
    }

    // Reuse an already-loaded cart on landing pages as well. Embedded checkout
    // builders frequently preload it without making is_checkout() true.
    $has_loaded_cart = function_exists( 'WC' ) && WC() && WC()->cart && ! WC()->cart->is_empty();
    if ( function_exists( 'buykorigw_get_cart_event_data' ) && ( $is_cart || $is_checkout || $has_loaded_cart ) ) {
        $cart_data = buykorigw_get_cart_event_data();
        if ( ! empty( $cart_data ) ) {
            $tracker_data['cart'] = $cart_data;
        }
    }

    if (
        ! $low_resource_mode
        && ! empty( $settings['enable_viewcontent'] )
        && empty( $tracker_data['product'] )
        && $has_loaded_cart
    ) {
        $cart_items = WC()->cart->get_cart();
        $first_item = reset( $cart_items );
        $product_id = ! empty( $first_item['variation_id'] ) ? $first_item['variation_id'] : ( $first_item['product_id'] ?? 0 );
        $product    = $product_id ? wc_get_product( $product_id ) : false;
        if ( $product && is_a( $product, 'WC_Product' ) ) {
            $tracker_data['product'] = array(
                'id'       => $product->get_id(),
                'sku'      => $product->get_sku() ?: (string) $product->get_id(),
                'name'     => $product->get_name(),
                'price'    => (float) $product->get_price(),
                'currency' => get_woocommerce_currency(),
                'category' => implode( ', ', wp_list_pluck( wc_get_product_terms( $product->get_id(), 'product_cat' ), 'name' ) ),
                'source'   => 'cart_session',
            );
        }
    }

    echo '<span id="buykorigw-tracker-config" hidden data-config="' . esc_attr( wp_json_encode( $tracker_data ) ) . '"></span>' . "\n";
}

// ─── Tracker JavaScript ────────────────────────────────────────────────────────
// ─── AJAX Handler: Track Event (Server-Side — bypasses cache) ──────────────────
add_action( 'wp_ajax_buykorigw_track_event', 'buykorigw_ajax_track_event' );
add_action( 'wp_ajax_nopriv_buykorigw_track_event', 'buykorigw_ajax_track_event' );

function buykorigw_ajax_track_event() {
    $nonce = isset( $_POST['nonce'] ) ? sanitize_text_field( wp_unslash( $_POST['nonce'] ) ) : '';
    if ( ! wp_verify_nonce( $nonce, 'buykorigw_track_nonce' ) ) {
        wp_send_json_error( 'Invalid nonce', 403 );
    }

    // ─── Origin Validation (replaces nonce for cache-safe security) ─────
    $allowed_host = parse_url( home_url(), PHP_URL_HOST );
    if ( ! $allowed_host ) {
        $allowed_host = $_SERVER['HTTP_HOST'] ?? '';
    }

    $request_origin = isset( $_SERVER['HTTP_ORIGIN'] ) ? $_SERVER['HTTP_ORIGIN'] : '';
    $request_referer = isset( $_SERVER['HTTP_REFERER'] ) ? $_SERVER['HTTP_REFERER'] : '';

    $origin_valid = false;

    // Check Origin
    if ( ! empty( $request_origin ) ) {
        $origin_host = parse_url( $request_origin, PHP_URL_HOST );
        if ( buykorigw_host_allowed( $origin_host, $allowed_host ) ) {
            $origin_valid = true;
        }
    }

    // Check Referer if Origin is missing or invalid
    if ( ! $origin_valid && ! empty( $request_referer ) ) {
        $referer_host = parse_url( $request_referer, PHP_URL_HOST );
        if ( buykorigw_host_allowed( $referer_host, $allowed_host ) ) {
            $origin_valid = true;
        }
    }

    if ( ! $origin_valid ) {
        wp_send_json_error( 'Invalid origin' );
    }

    if ( buykorigw_ajax_rate_limited() ) {
        wp_send_json_error( 'Rate limit exceeded', 429 );
    }

    // ─── Whitelist allowed event names ──────────────────────────────────
    $allowed_events = array( 'PageView', 'ViewContent', 'AddToCart', 'ViewCart', 'RemoveFromCart', 'InitiateCheckout', 'AddPaymentInfo', 'Purchase', 'Lead', 'Search', 'Identify', 'Refund' );

    // Also allow user-defined custom events from the Custom Event Builder
    if ( defined( 'BUYKORIGW_CUSTOM_EVENTS_KEY' ) ) {
        $custom_events = get_option( BUYKORIGW_CUSTOM_EVENTS_KEY, array() );
        foreach ( $custom_events as $ce ) {
            if ( ! empty( $ce['name'] ) && ! empty( $ce['enabled'] ) ) {
                $allowed_events[] = $ce['name'];
            }
        }
    }

    $settings   = buykorigw_get_settings();
    $event_name = isset( $_POST['event_name'] ) ? sanitize_text_field( wp_unslash( $_POST['event_name'] ) ) : '';
    $event_id   = isset( $_POST['event_id'] ) ? sanitize_text_field( wp_unslash( $_POST['event_id'] ) ) : '';
    $event_json = isset( $_POST['event_data'] ) ? wp_unslash( $_POST['event_data'] ) : '{}';
    $page_url   = isset( $_POST['page_url'] ) ? esc_url_raw( wp_unslash( $_POST['page_url'] ) ) : '';
    $fbp        = isset( $_POST['fbp'] ) ? sanitize_text_field( wp_unslash( $_POST['fbp'] ) ) : '';
    $fbc        = isset( $_POST['fbc'] ) ? sanitize_text_field( wp_unslash( $_POST['fbc'] ) ) : '';
    $ttp        = isset( $_POST['ttp'] ) ? sanitize_text_field( wp_unslash( $_POST['ttp'] ) ) : '';
    $ttclid     = isset( $_POST['ttclid'] ) ? sanitize_text_field( wp_unslash( $_POST['ttclid'] ) ) : '';
    $external_id = isset( $_POST['external_id'] ) ? sanitize_text_field( wp_unslash( $_POST['external_id'] ) ) : '';
    $fbclid     = isset( $_POST['fbclid'] ) ? sanitize_text_field( wp_unslash( $_POST['fbclid'] ) ) : '';
    $ga_cookie  = isset( $_POST['_ga'] ) ? sanitize_text_field( wp_unslash( $_POST['_ga'] ) ) : '';
    $ga_session = isset( $_POST['ga_session_id'] ) ? sanitize_text_field( wp_unslash( $_POST['ga_session_id'] ) ) : '';
    if ( empty( $event_name ) || ! in_array( $event_name, $allowed_events, true ) ) {
        wp_send_json_error( 'Invalid event name' );
    }

    $custom_data = json_decode( $event_json, true );
    if ( ! is_array( $custom_data ) ) {
        $custom_data = array();
    }

    if ( ! empty( $ga_cookie ) ) {
        $custom_data['_ga'] = $ga_cookie;
    }
    if ( ! empty( $ga_session ) ) {
        $custom_data['session_id'] = $ga_session;
    }

    $custom_data = function_exists( 'buykorigw_normalize_event_custom_data' )
        ? buykorigw_normalize_event_custom_data( $custom_data, $event_name, $settings )
        : buykorigw_add_marketing_params( $custom_data );

    if ( empty( $fbp ) && ! empty( $_COOKIE['_fbp'] ) ) {
        $fbp = sanitize_text_field( wp_unslash( $_COOKIE['_fbp'] ) );
    }
    if ( empty( $fbp ) ) {
        $fbp = 'fb.1.' . (string) ( time() * 1000 ) . '.' . wp_rand( 1000000000, 9999999999 );
    }
    if ( empty( $fbc ) && ! empty( $_COOKIE['_fbc'] ) ) {
        $fbc = sanitize_text_field( wp_unslash( $_COOKIE['_fbc'] ) );
    }
    if ( empty( $fbc ) && ! empty( $fbclid ) ) {
        $fbc = 'fb.1.' . (string) ( time() * 1000 ) . '.' . $fbclid;
    }
    if ( empty( $ttp ) && ! empty( $_COOKIE['_ttp'] ) ) {
        $ttp = sanitize_text_field( wp_unslash( $_COOKIE['_ttp'] ) );
    }
    if ( empty( $ttp ) ) {
        $ttp = wp_generate_uuid4();
    }
    if ( empty( $ttclid ) && ! empty( $_COOKIE['_ttclid'] ) ) {
        $ttclid = sanitize_text_field( wp_unslash( $_COOKIE['_ttclid'] ) );
    }
    if ( empty( $external_id ) && ! empty( $_COOKIE['_buykorigw_vid'] ) ) {
        $external_id = sanitize_text_field( wp_unslash( $_COOKIE['_buykorigw_vid'] ) );
    }
    if ( empty( $external_id ) ) {
        $external_id = 'bk.' . time() . '.' . wp_rand( 1000000000, 9999999999 );
    }
    if ( empty( $custom_data['utm_source'] ) && ! empty( $ttclid ) ) {
        $custom_data['utm_source'] = 'tiktok';
        $custom_data['campaign_source'] = 'tiktok';
    } elseif ( empty( $custom_data['utm_source'] ) && ! empty( $fbc ) ) {
        $custom_data['utm_source'] = 'facebook';
        $custom_data['campaign_source'] = 'facebook';
    }

    // Build user_data with PII hashing
    $user_data = array(
        'client_ip_address' => buykorigw_get_real_ip(),
        'client_user_agent' => sanitize_text_field( $_SERVER['HTTP_USER_AGENT'] ?? '' ),
    );

    // Add fbp/fbc cookies for Facebook matching
    if ( ! empty( $fbp ) ) {
        $user_data['fbp'] = $fbp;
    }
    if ( ! empty( $fbc ) ) {
        $user_data['fbc'] = $fbc;
    }
    if ( ! empty( $ttp ) ) {
        $user_data['ttp'] = $ttp;
    }
    if ( ! empty( $ttclid ) ) {
        $user_data['ttclid'] = $ttclid;
    }
    if ( ! empty( $external_id ) ) {
        $user_data['external_id'] = array( buykorigw_hash( $external_id ) );
    }

    buykorigw_apply_identity_data( $user_data, wp_unslash( $_POST ) );

    if ( $event_name === 'Identify' ) {
        wp_send_json_success( 'Identity updated' );
    }

    // If user is logged in, hash their email and name
    if ( is_user_logged_in() ) {
        $user = wp_get_current_user();
        buykorigw_apply_identity_data( $user_data, array(
            'em' => $user->user_email,
            'fn' => $user->first_name,
            'ln' => $user->last_name,
        ), false );
    }

    // Build event payload
    $event_payload = array(
        'event_name'  => $event_name,
        'event_time'  => time(),
        'event_source_url' => $page_url,
        'action_source'    => 'website',
        'user_data'   => $user_data,
    );

    // Add custom_data if present
    if ( ! empty( $custom_data ) ) {
        $event_payload['custom_data'] = $custom_data;
    }

    $event_payload['event_id'] = ! empty( $event_id ) ? $event_id : 'wp_' . $event_name . '_' . time() . '_' . wp_rand( 1000, 9999 );

    // Send to gateway
    buykorigw_send_event( $event_payload, false );
    if ( $event_name === 'InitiateCheckout' && function_exists( 'buykorigw_mark_initiate_checkout_sent' ) ) {
        buykorigw_mark_initiate_checkout_sent( $event_payload['event_id'] );
    }

    wp_send_json_success( 'Event tracked' );
}


function buykorigw_add_marketing_params( $custom_data ) {
    $keys = array( 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'campaign_source' );
    foreach ( $keys as $key ) {
        if ( empty( $custom_data[ $key ] ) && ! empty( $_COOKIE[ '_buykorigw_' . $key ] ) ) {
            $custom_data[ $key ] = buykorigw_normalize_campaign_value( $key, sanitize_text_field( wp_unslash( $_COOKIE[ '_buykorigw_' . $key ] ) ) );
        } elseif ( ! empty( $custom_data[ $key ] ) ) {
            $custom_data[ $key ] = buykorigw_normalize_campaign_value( $key, $custom_data[ $key ] );
        }
    }
    if ( empty( $custom_data['campaign_source'] ) && ! empty( $custom_data['utm_source'] ) ) {
        $custom_data['campaign_source'] = $custom_data['utm_source'];
    }
    if ( empty( $custom_data['utm_source'] ) && ! empty( $_COOKIE['_ttclid'] ) ) {
        $custom_data['utm_source'] = 'tiktok';
        $custom_data['campaign_source'] = 'tiktok';
    } elseif ( empty( $custom_data['utm_source'] ) && ! empty( $_COOKIE['_fbc'] ) ) {
        $custom_data['utm_source'] = 'facebook';
        $custom_data['campaign_source'] = 'facebook';
    }
    return $custom_data;
}


// ─── Helper: Lightweight AJAX Rate Limit ───────────────────────────────────
function buykorigw_normalize_campaign_value( $key, $value ) {
    $value = trim( (string) $value );
    if ( $value === '' || preg_match( '/^__.*__$/', $value ) ) {
        return '';
    }

    if ( in_array( $key, array( 'utm_source', 'campaign_source' ), true ) ) {
        $value = strtolower( $value );
        $value = preg_replace( '/[^a-z0-9]+/', '_', $value );
        $value = trim( $value, '_' );
    } else {
        $value = sanitize_text_field( $value );
    }

    return $value;
}

function buykorigw_ajax_rate_limited() {
    $ip = sanitize_text_field( $_SERVER['REMOTE_ADDR'] ?? '' );
    if ( empty( $ip ) ) {
        return false;
    }

    $limit      = 120;
    $window     = 60;
    $cache_key  = 'buykorigw_ajax_rate_' . md5( $ip );
    $hit_count  = (int) get_transient( $cache_key );

    if ( $hit_count >= $limit ) {
        return true;
    }

    set_transient( $cache_key, $hit_count + 1, $window );
    return false;
}


// ─── WooCommerce: Purchase Event on Thank You Page (Server-Side) ───────────────
add_action( 'woocommerce_thankyou', 'buykorigw_schedule_purchase_sync', 10, 1 );
add_action( 'woocommerce_checkout_order_processed', 'buykorigw_track_deferred_purchase_after_checkout', 30, 1 );
add_action( 'woocommerce_store_api_checkout_order_processed', 'buykorigw_track_deferred_purchase_after_store_api_checkout', 30, 1 );
add_action( 'buykorigw_sync_purchase', 'buykorigw_sync_purchase_handler', 10, 1 );

function buykorigw_schedule_purchase_sync( $order_or_id ) {
    $order_id = is_object( $order_or_id ) && method_exists( $order_or_id, 'get_id' )
        ? $order_or_id->get_id()
        : (int) $order_or_id;

    if ( ! $order_id || buykorigw_get_order_meta( $order_id, '_buykorigw_tracked' ) ) {
        return;
    }

    $queued_at = (int) buykorigw_get_order_meta( $order_id, '_buykorigw_purchase_sync_queued_at' );
    if ( $queued_at && abs( time() - $queued_at ) < 600 ) {
        return;
    }

    buykorigw_update_order_meta( $order_id, '_buykorigw_purchase_sync_queued_at', time() );

    if ( function_exists( 'as_enqueue_async_action' ) ) {
        as_enqueue_async_action(
            'buykorigw_sync_purchase',
            array( 'order_id' => $order_id ),
            'buykori-adsync'
        );
        return;
    }

    if ( ! wp_next_scheduled( 'buykorigw_sync_purchase', array( $order_id ) ) ) {
        wp_schedule_single_event( time() + 1, 'buykorigw_sync_purchase', array( $order_id ) );
    }
}

function buykorigw_sync_purchase_handler( $order_id ) {
    $order_id = (int) $order_id;
    if ( ! $order_id ) {
        return;
    }

    buykorigw_update_order_meta( $order_id, '_buykorigw_purchase_sync_queued_at', '' );
    buykorigw_track_purchase( $order_id );
}

function buykorigw_track_deferred_purchase_after_checkout( $order_id ) {
    $settings = buykorigw_get_settings();

    if ( empty( $settings['enable_purchase'] ) || empty( $settings['api_key'] ) ) {
        return;
    }

    buykorigw_schedule_purchase_sync( $order_id );
}

function buykorigw_track_deferred_purchase_after_store_api_checkout( $order ) {
    $settings = buykorigw_get_settings();

    if ( empty( $settings['enable_purchase'] ) || empty( $settings['api_key'] ) || ! is_object( $order ) || ! method_exists( $order, 'get_id' ) ) {
        return;
    }

    buykorigw_schedule_purchase_sync( $order->get_id() );
}

function buykorigw_track_purchase( $order_id ) {
    $settings = buykorigw_get_settings();

    if ( ! $settings['enable_purchase'] || empty( $settings['api_key'] ) ) {
        return;
    }

    // Prevent duplicate tracking (mark order as tracked)
    $already_tracked = buykorigw_get_order_meta( $order_id, '_buykorigw_tracked' );
    if ( $already_tracked ) {
        return;
    }

    // Clear InitiateCheckout cookies so the next checkout attempt will get a fresh event ID
    if ( function_exists( 'buykorigw_first_party_cookie_options' ) ) {
        $clear_options = buykorigw_first_party_cookie_options( -1 );
        setcookie( '_buykorigw_ic_sent', '', $clear_options );
        setcookie( '_buykorigw_ic_event_id', '', $clear_options );
    }
    if ( isset( $_COOKIE['_buykorigw_ic_sent'] ) ) {
        unset( $_COOKIE['_buykorigw_ic_sent'] );
    }
    if ( isset( $_COOKIE['_buykorigw_ic_event_id'] ) ) {
        unset( $_COOKIE['_buykorigw_ic_event_id'] );
    }

    $order = wc_get_order( $order_id );
    if ( ! $order ) {
        return;
    }

    // Build product IDs and content data
    $content_ids = array();
    $contents    = array();
    $num_items   = 0;
    $content_format = isset( $settings['content_id_format'] ) ? $settings['content_id_format'] : 'id';

    foreach ( $order->get_items() as $item ) {
        $product_id = $item->get_product_id();
        $product    = $item->get_product();

        $final_id = (string) $product_id;
        if ( $content_format === 'sku' && $product ) {
            $sku = $product->get_sku();
            if ( ! empty( $sku ) ) {
                $final_id = $sku;
            }
        }

        $content_ids[] = $final_id;

        // Product name — $item->get_name() returns the product title from WooCommerce
        $product_name = $item->get_name();

        $contents[] = array(
            'id'           => $final_id,
            'content_id'   => $final_id,
            'content_type' => 'product',
            'title'        => $product_name,  // Product name — portal এ দেখাবে
            'name'         => $product_name,  // Fallback key
            'quantity'     => $item->get_quantity(),
            'item_price'   => (float) ( $item->get_total() / max( $item->get_quantity(), 1 ) ),
        );
        $num_items += $item->get_quantity();
    }

    // Build user_data with real customer info (hashed)
    // Prefer saved attribution snapshot (from checkout) over current $_COOKIE
    // because payment gateway redirects (bKash/Nagad/SSLCommerz) destroy cookies
    $snapshot_ip = $order->get_meta( '_buykorigw_snapshot_ip' );
    $snapshot_ua = $order->get_meta( '_buykorigw_snapshot_ua' );

    $user_data = array(
        'client_ip_address' => $order->get_customer_ip_address() ?: ( $snapshot_ip ?: buykorigw_get_real_ip() ),
        'client_user_agent' => $order->get_customer_user_agent() ?: ( $snapshot_ua ?: ( $_SERVER['HTTP_USER_AGENT'] ?? '' ) ),
    );

    if ( $order->get_billing_email() ) {
        $user_data['em'] = array( buykorigw_hash( $order->get_billing_email() ) );
    }
    if ( $order->get_billing_first_name() ) {
        $user_data['fn'] = array( buykorigw_hash( $order->get_billing_first_name() ) );
    }
    if ( $order->get_billing_last_name() ) {
        $user_data['ln'] = array( buykorigw_hash( $order->get_billing_last_name() ) );
    }
    if ( $order->get_billing_phone() ) {
        $user_data['ph'] = array( buykorigw_hash_phone( $order->get_billing_phone() ) );
    }
    if ( $order->get_billing_city() ) {
        $user_data['ct'] = array( buykorigw_hash( $order->get_billing_city() ) );
    }
    if ( $order->get_billing_state() ) {
        $user_data['st'] = array( buykorigw_hash( $order->get_billing_state() ) );
    }
    if ( $order->get_billing_country() ) {
        $user_data['country'] = array( buykorigw_hash( $order->get_billing_country() ) );
    }
    if ( $order->get_billing_postcode() ) {
        $user_data['zp'] = array( buykorigw_hash( $order->get_billing_postcode() ) );
    }
    buykorigw_apply_identity_data( $user_data, array(
        'em'      => $order->get_billing_email(),
        'ph'      => $order->get_billing_phone(),
        'fn'      => $order->get_billing_first_name(),
        'ln'      => $order->get_billing_last_name(),
        'ct'      => $order->get_billing_city(),
        'st'      => $order->get_billing_state(),
        'zp'      => $order->get_billing_postcode(),
        'country' => $order->get_billing_country(),
    ), false );

    // Attribution cookies: prefer saved snapshot over $_COOKIE
    $cookie_map = array(
        'fbp'    => '_fbp',
        'fbc'    => '_fbc',
        'ttp'    => '_ttp',
        'ttclid' => '_ttclid',
    );
    foreach ( $cookie_map as $ud_key => $cookie_name ) {
        $snapshot_val = $order->get_meta( '_buykorigw_snapshot' . $cookie_name );
        $cookie_val   = isset( $_COOKIE[ $cookie_name ] ) ? sanitize_text_field( wp_unslash( $_COOKIE[ $cookie_name ] ) ) : '';
        $final_val    = ! empty( $snapshot_val ) ? $snapshot_val : $cookie_val;
        if ( ! empty( $final_val ) ) {
            $user_data[ $ud_key ] = $final_val;
        }
    }

    $snapshot_external_id = $order->get_meta( '_buykorigw_snapshot_buykorigw_vid' );
    $cookie_external_id   = isset( $_COOKIE['_buykorigw_vid'] ) ? sanitize_text_field( wp_unslash( $_COOKIE['_buykorigw_vid'] ) ) : '';
    $external_id          = ! empty( $snapshot_external_id ) ? $snapshot_external_id : $cookie_external_id;
    if ( ! empty( $external_id ) ) {
        $user_data['external_id'] = array( buykorigw_hash( $external_id ) );
    } elseif ( $order->get_customer_id() ) {
        $user_data['external_id'] = array( buykorigw_hash( 'wp_user_' . $order->get_customer_id() ) );
    }

    $billing_email       = $order->get_billing_email();
    $billing_email_domain = '';
    if ( ! empty( $billing_email ) && is_email( $billing_email ) ) {
        $at_pos = strrpos( $billing_email, '@' );
        if ( false !== $at_pos ) {
            $billing_email_domain = strtolower( substr( $billing_email, $at_pos + 1 ) );
        }
    }

    // Build event payload
    $event_payload = array(
        'event_name'       => 'Purchase',
        'event_time'       => time(),
        'event_id'         => 'wc_purchase_' . $order_id,
        'event_source_url' => $order->get_checkout_order_received_url(),
        'action_source'    => 'website',
        'user_data'        => $user_data,
        'custom_data'      => array(
            'value'        => (float) $order->get_total(),
            'currency'     => $order->get_currency(),
            'content_ids'  => $content_ids,
            'contents'     => $contents,
            'content_type' => 'product',
            'num_items'    => $num_items,
            'order_id'     => (string) $order_id,
        ),
        'raw_order_data'   => array(
            'recipient_name'    => trim( $order->get_billing_first_name() . ' ' . $order->get_billing_last_name() ),
            'recipient_phone'   => $order->get_billing_phone(),
            'recipient_address' => trim(
                implode( ', ', array_filter( array(
                    $order->get_shipping_address_1() ?: $order->get_billing_address_1(),
                    $order->get_shipping_address_2() ?: $order->get_billing_address_2(),
                    $order->get_shipping_city() ?: $order->get_billing_city(),
                    $order->get_shipping_state() ?: $order->get_billing_state(),
                    $order->get_shipping_postcode() ?: $order->get_billing_postcode(),
                ) ) )
            ),
            'cod_amount'        => (float) $order->get_total(),
        ),
    );

    if ( ! empty( $billing_email_domain ) ) {
        $event_payload['custom_data']['billing_email_domain'] = sanitize_text_field( $billing_email_domain );
    }

    if ( $order->get_billing_first_name() ) {
        $event_payload['custom_data']['billing_first_name_raw'] = sanitize_text_field( $order->get_billing_first_name() );
    }

    $event_payload['custom_data'] = buykorigw_add_marketing_params( $event_payload['custom_data'] );

    // Inject GA4 client_id and session_id from snapshot for Measurement Protocol
    $ga4_client_id  = $order->get_meta( '_buykorigw_snapshot_ga_client_id' );
    $ga4_session_id = $order->get_meta( '_buykorigw_snapshot_ga_session_id' );
    if ( $ga4_client_id ) {
        $event_payload['custom_data']['client_id'] = $ga4_client_id;
    }
    if ( $ga4_session_id ) {
        $event_payload['custom_data']['session_id'] = $ga4_session_id;
    }

    // Add UTM params from snapshot
    $utm_keys = array( 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term' );
    foreach ( $utm_keys as $key ) {
        $val = $order->get_meta( '_buykorigw_snapshot_' . $key );
        if ( $val ) {
            $event_payload['custom_data'][ $key ] = $val;
        }
    }

    $sent = false;

    // If deferred_purchase is ON, send with hold=true query param
    if ( $settings['deferred_purchase'] ) {
        $url = rtrim( $settings['gateway_url'], '/' ) . '/events?hold=true';
        $body = wp_json_encode( array( 'data' => array( $event_payload ) ) );
        $site_origin = function_exists( 'buykorigw_site_origin' ) ? buykorigw_site_origin() : home_url();
        $headers = array_merge( array(
            'Content-Type'   => 'application/json',
            'X-API-Key'      => $settings['api_key'],
            'X-CAPI-Origin'  => $site_origin,
        ), buykorigw_signed_headers( $settings['api_key'], $body ) );

        $response = wp_remote_post( $url, array(
            'timeout'   => 10,
            'sslverify' => true,
            'headers'   => $headers,
            'body'      => $body,
        ) );

        if ( is_wp_error( $response ) ) {
            // Critical failure — always log regardless of debug_mode
            error_log( '[Buykori AdSync] Deferred purchase send failed for order #' . $order_id . ': ' . $response->get_error_message() );
        }

        if ( ! is_wp_error( $response ) ) {
            $code = wp_remote_retrieve_response_code( $response );
            $sent = ( $code >= 200 && $code < 300 );
            if ( ! $sent && $settings['debug_mode'] ) {
                error_log( '[Buykori AdSync] Deferred purchase HTTP ' . $code . ': ' . wp_remote_retrieve_body( $response ) );
            }
        }
    } else {
        // Immediate send
        $sent = buykorigw_send_event( $event_payload, true );
    }

    // Mark as tracked to prevent duplicates
    if ( ! $sent ) {
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirm_status', 'send_failed' );
        return;
    }

    buykorigw_update_order_meta( $order_id, '_buykorigw_tracked', 1 );
    if ( $settings['deferred_purchase'] ) {
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirm_status', 'pending' );
    }

    if ( $settings['debug_mode'] ) {
        error_log( '[Buykori AdSync] Purchase tracked for order #' . $order_id );
    }
}


// ─── Helper: Get Real Client IP ────────────────────────────────────────────────
function buykorigw_get_real_ip() {
    $headers = array(
        'HTTP_CF_CONNECTING_IP',   // Cloudflare
        'HTTP_X_FORWARDED_FOR',    // Proxies
        'HTTP_X_REAL_IP',          // Nginx
        'REMOTE_ADDR',             // Default
    );

    foreach ( $headers as $header ) {
        if ( ! empty( $_SERVER[ $header ] ) ) {
            $ip = $_SERVER[ $header ];
            // X-Forwarded-For can have multiple IPs, take the first
            if ( strpos( $ip, ',' ) !== false ) {
                $ip = trim( explode( ',', $ip )[0] );
            }
            if ( filter_var( $ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE ) ) {
                return $ip;
            }
        }
    }

    return $_SERVER['REMOTE_ADDR'] ?? '';
}
