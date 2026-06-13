<?php
/**
 * Buykori AdSync — WooCommerce Order Hooks
 *
 * এটিই মূল সমাধান — "ডাবল কনফার্মেশন" সমস্যা এখানে সমাধান হয়।
 *
 * কাজের ধরন:
 * 1. অর্ডার স্ট্যাটাস "Completed" বা "Processing" হলে অটোমেটিক
 *    Buykori AdSync API-তে confirm রিকোয়েস্ট পাঠায়।
 * 2. API কল ফেইল হলে Action Scheduler দিয়ে retry করে।
 * 3. অর্ডার পেজে একটি Meta Box দেখায় — tracking status বোঝা যায়।
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

// ─── 1. Order Status Change → Auto Confirm Deferred Purchase ──────────────────
add_action( 'woocommerce_order_status_completed', 'buykorigw_on_order_status_change', 20, 1 );
add_action( 'woocommerce_order_status_processing', 'buykorigw_on_order_status_change', 20, 1 );
add_action( 'woocommerce_order_status_changed', 'buykorigw_on_order_status_change', 20, 4 );
add_action( 'woocommerce_order_status_cancelled', 'buykorigw_on_order_cancelled', 20, 1 );
add_action( 'woocommerce_order_status_failed', 'buykorigw_on_order_cancelled', 20, 1 );
add_action( 'woocommerce_order_status_refunded', 'buykorigw_on_order_cancelled', 20, 1 );

// ─── 0. Attribution Snapshot: Save cookies/UTM at checkout (before redirect) ───
add_action( 'woocommerce_checkout_update_order_meta', 'buykorigw_save_attribution_snapshot', 10, 1 );
add_action( 'woocommerce_new_order', 'buykorigw_save_attribution_snapshot', 10, 1 );
add_action( 'woocommerce_checkout_order_created', 'buykorigw_save_attribution_snapshot', 5, 1 );
add_action( 'woocommerce_store_api_checkout_order_processed', 'buykorigw_save_attribution_snapshot', 5, 1 );
add_action( 'woocommerce_checkout_order_created', 'buykorigw_send_order_initiate_checkout_fallback', 999, 1 );
add_action( 'woocommerce_checkout_order_processed', 'buykorigw_send_order_initiate_checkout_fallback', 30, 1 );
add_action( 'woocommerce_thankyou', 'buykorigw_send_order_initiate_checkout_fallback', 5, 1 );
add_action( 'woocommerce_checkout_order_created', 'buykorigw_mark_incomplete_checkout_recovered', 20, 1 );
add_action( 'woocommerce_checkout_order_processed', 'buykorigw_mark_incomplete_checkout_recovered', 20, 1 );
add_action( 'woocommerce_store_api_checkout_order_processed', 'buykorigw_mark_incomplete_checkout_recovered', 20, 1 );
add_action( 'woocommerce_thankyou', 'buykorigw_mark_incomplete_checkout_recovered', 20, 1 );

function buykorigw_mark_incomplete_checkout_recovered( $order_or_id ) {
    $order = is_a( $order_or_id, 'WC_Order' ) ? $order_or_id : wc_get_order( $order_or_id );
    if ( ! $order || $order->get_meta( '_buykorigw_incomplete_checkout_recovered' ) ) {
        return;
    }
    if ( function_exists( 'buykorigw_convert_incomplete_checkout' ) && buykorigw_convert_incomplete_checkout( $order ) ) {
        $order->update_meta_data( '_buykorigw_incomplete_checkout_recovered', 1 );
        $order->save();
    }
}

/**
 * buykorigw_save_attribution_snapshot()
 *
 * চেকআউটে অর্ডার তৈরি হওয়ার সময় সমস্ত attribution data
 * (কুকি, UTM, IP, UA) WooCommerce order meta-তে সেভ করে।
 * পেমেন্ট গেটওয়ে (bKash/Nagad/SSLCommerz) redirect-এর পরও
 * থ্যাংক ইউ পেজে ডেটা available থাকে।
 */
function buykorigw_save_attribution_snapshot( $order_or_id ) {
    $order = is_a( $order_or_id, 'WC_Order' )
        ? $order_or_id
        : ( function_exists( 'wc_get_order' ) ? wc_get_order( $order_or_id ) : null );
    if ( ! $order ) {
        return;
    }

    $current_hook = function_exists( 'current_filter' ) ? current_filter() : '';
    $created_via = method_exists( $order, 'get_created_via' ) ? strtolower( (string) $order->get_created_via() ) : '';
    if ( $current_hook === 'woocommerce_new_order' ) {
        if ( is_admin() && ! wp_doing_ajax() ) {
            return;
        }
        if ( $created_via && ! in_array( $created_via, array( 'checkout', 'store-api', 'rest-api' ), true ) ) {
            return;
        }
    }

    $wrote_snapshot = false;

    // Cookies
    $cookie_keys = array( '_fbp', '_fbc', '_ttp', '_ttclid', '_ga', '_buykorigw_vid', '_buykorigw_ic_sent', '_buykorigw_ic_event_id', '_buykorigw_bk_platform', '_buykorigw_bk_campaign_id', '_buykorigw_bk_adset_id', '_buykorigw_bk_ad_id' );
    foreach ( $cookie_keys as $key ) {
        $value = isset( $_COOKIE[ $key ] ) ? sanitize_text_field( wp_unslash( $_COOKIE[ $key ] ) ) : '';
        if ( ! empty( $value ) && ! $order->get_meta( '_buykorigw_snapshot' . $key ) ) {
            $order->update_meta_data( '_buykorigw_snapshot' . $key, $value );
            $wrote_snapshot = true;
        }
    }

    // GA4 session cookie (_ga_XXXXXX)
    foreach ( $_COOKIE as $key => $val ) {
        if ( strpos( $key, '_ga_' ) === 0 ) {
            $ga_session_val = sanitize_text_field( wp_unslash( $val ) );
            $parts = explode( '.', $ga_session_val );
            if ( count( $parts ) >= 3 && ! $order->get_meta( '_buykorigw_snapshot_ga_session_id' ) ) {
                $order->update_meta_data( '_buykorigw_snapshot_ga_session_id', $parts[2] );
                $wrote_snapshot = true;
            }
            break;
        }
    }

    // GA4 client_id from _ga cookie
    $ga_cookie = isset( $_COOKIE['_ga'] ) ? sanitize_text_field( wp_unslash( $_COOKIE['_ga'] ) ) : '';
    if ( $ga_cookie ) {
        $parts = explode( '.', $ga_cookie );
        if ( count( $parts ) >= 4 && ! $order->get_meta( '_buykorigw_snapshot_ga_client_id' ) ) {
            $order->update_meta_data( '_buykorigw_snapshot_ga_client_id', $parts[ count( $parts ) - 2 ] . '.' . $parts[ count( $parts ) - 1 ] );
            $wrote_snapshot = true;
        }
    }

    // UTM params
    $utm_keys = array( 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term' );
    foreach ( $utm_keys as $key ) {
        $val = isset( $_COOKIE[ '_buykorigw_' . $key ] ) ? sanitize_text_field( wp_unslash( $_COOKIE[ '_buykorigw_' . $key ] ) ) : '';
        if ( ! empty( $val ) && ! $order->get_meta( '_buykorigw_snapshot_' . $key ) ) {
            $order->update_meta_data( '_buykorigw_snapshot_' . $key, $val );
            $wrote_snapshot = true;
        }
    }

    // IP and User Agent
    $ip = function_exists( 'buykorigw_get_real_ip' ) ? buykorigw_get_real_ip() : ( $_SERVER['REMOTE_ADDR'] ?? '' );
    $ua = sanitize_text_field( $_SERVER['HTTP_USER_AGENT'] ?? '' );
    if ( $ip && ! $order->get_meta( '_buykorigw_snapshot_ip' ) ) {
        $order->update_meta_data( '_buykorigw_snapshot_ip', $ip );
        $wrote_snapshot = true;
    }
    if ( $ua && ! $order->get_meta( '_buykorigw_snapshot_ua' ) ) {
        $order->update_meta_data( '_buykorigw_snapshot_ua', $ua );
        $wrote_snapshot = true;
    }

    if ( $wrote_snapshot ) {
        $order->update_meta_data( '_buykorigw_snapshot_saved', 1 );
        $order->save();
    }
}

function buykorigw_add_order_note( $order_id, $note ) {
    if ( ! function_exists( 'wc_get_order' ) ) {
        return;
    }

    $order = wc_get_order( $order_id );
    if ( ! $order || ! method_exists( $order, 'add_order_note' ) ) {
        return;
    }

    $order->add_order_note( '[Buykori AdSync] ' . $note );
}

function buykorigw_order_contents_payload( $order ) {
    $settings = buykorigw_get_settings();
    $content_format = isset( $settings['content_id_format'] ) ? $settings['content_id_format'] : 'id';
    $enable_variations = isset( $settings['enable_variations'] ) ? (bool) $settings['enable_variations'] : true;

    $content_ids = array();
    $contents    = array();
    $num_items   = 0;

    foreach ( $order->get_items() as $item ) {
        $product_id   = $item->get_product_id();
        $variation_id = $item->get_variation_id();
        $product      = $item->get_product();
        $quantity     = max( 1, (int) $item->get_quantity() );

        if ( ! $product_id ) {
            continue;
        }

        $final_id = (string) $product_id;

        if ( $enable_variations && $variation_id ) {
            $final_id = (string) $variation_id;
            $var_product = wc_get_product( $variation_id );
            if ( $content_format === 'sku' && $var_product ) {
                $sku = $var_product->get_sku();
                if ( ! empty( $sku ) ) {
                    $final_id = $sku;
                }
            }
        } else {
            if ( $content_format === 'sku' && $product ) {
                $sku = $product->get_sku();
                if ( ! empty( $sku ) ) {
                    $final_id = $sku;
                }
            }
        }

        $content_ids[] = $final_id;

        $product_name = $item->get_name();
        $product_categories = function_exists( 'wc_get_product_terms' )
            ? implode( ', ', wp_list_pluck( wc_get_product_terms( $product_id, 'product_cat' ), 'name' ) )
            : '';

        $content_item = array(
            'id'           => $final_id,
            'content_id'   => $final_id,
            'content_type' => 'product',
            'title'        => $product_name,
            'name'         => $product_name,
            'content_name' => $product_name,
            'content_category' => $product_categories,
            'quantity'     => $quantity,
            'price'        => (float) ( $item->get_total() / $quantity ),
            'item_price'   => (float) ( $item->get_total() / $quantity ),
        );

        if ( $variation_id ) {
            $variation = wc_get_product( $variation_id );
            if ( $variation ) {
                $attributes = $variation->get_variation_attributes();
                $formatted_attributes = array();
                foreach ( $attributes as $tax => $slug ) {
                    $name = str_replace( 'attribute_', '', $tax );
                    if ( taxonomy_exists( $name ) ) {
                        $label = wc_attribute_label( $name );
                        $term = get_term_by( 'slug', $slug, $name );
                        $val = $term ? $term->name : $slug;
                    } else {
                        $label = $name;
                        $val = $slug;
                    }
                    $formatted_attributes[ $label ] = $val;
                }
                if ( ! empty( $formatted_attributes ) ) {
                    $content_item['attributes'] = $formatted_attributes;
                }
            }
            foreach ( $item->get_formatted_meta_data( '' ) as $meta ) {
                $attribute_label = trim( wp_strip_all_tags( (string) $meta->display_key ) );
                $attribute_value = trim( wp_strip_all_tags( (string) $meta->display_value ) );
                if ( $attribute_label !== '' && $attribute_value !== '' ) {
                    if ( empty( $content_item['attributes'] ) || ! is_array( $content_item['attributes'] ) ) {
                        $content_item['attributes'] = array();
                    }
                    $content_item['attributes'][ $attribute_label ] = $attribute_value;
                }
            }
        }

        $contents[] = $content_item;
        $num_items += $quantity;
    }

    return array( $content_ids, $contents, $num_items );
}

function buykorigw_apply_order_attribution_user_data( &$user_data, $order ) {
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
}

function buykorigw_send_order_initiate_checkout_fallback( $order_or_id ) {
    $settings = buykorigw_get_settings();

    if ( empty( $settings['enable_checkout'] ) || empty( $settings['api_key'] ) ) {
        return;
    }

    $order = is_a( $order_or_id, 'WC_Order' ) ? $order_or_id : ( function_exists( 'wc_get_order' ) ? wc_get_order( $order_or_id ) : null );
    if ( ! $order ) {
        return;
    }

    $order_id = $order->get_id();
    if ( $order->get_meta( '_buykorigw_initiate_checkout_sent' ) ) {
        return;
    }

    $browser_ic_sent  = $order->get_meta( '_buykorigw_snapshot_buykorigw_ic_sent' );
    $browser_event_id = $order->get_meta( '_buykorigw_snapshot_buykorigw_ic_event_id' );
    $current_ic_sent  = isset( $_COOKIE['_buykorigw_ic_sent'] ) ? sanitize_text_field( wp_unslash( $_COOKIE['_buykorigw_ic_sent'] ) ) : '';
    $current_event_id = isset( $_COOKIE['_buykorigw_ic_event_id'] ) ? sanitize_text_field( wp_unslash( $_COOKIE['_buykorigw_ic_event_id'] ) ) : '';

    if ( empty( $browser_event_id ) && ! empty( $current_event_id ) ) {
        $browser_event_id = $current_event_id;
    }

    list( $content_ids, $contents, $num_items ) = buykorigw_order_contents_payload( $order );
    if ( empty( $content_ids ) || empty( $contents ) ) {
        if ( ! empty( $settings['debug_mode'] ) ) {
            buykorigw_add_order_note( $order_id, 'InitiateCheckout fallback skipped because order items/content_ids were not ready yet.' );
        }
        return;
    }

    $snapshot_ip = $order->get_meta( '_buykorigw_snapshot_ip' );
    $snapshot_ua = $order->get_meta( '_buykorigw_snapshot_ua' );
    $user_data = array(
        'client_ip_address' => $order->get_customer_ip_address() ?: ( $snapshot_ip ?: ( function_exists( 'buykorigw_get_real_ip' ) ? buykorigw_get_real_ip() : ( $_SERVER['REMOTE_ADDR'] ?? '' ) ) ),
        'client_user_agent' => $order->get_customer_user_agent() ?: ( $snapshot_ua ?: ( $_SERVER['HTTP_USER_AGENT'] ?? '' ) ),
    );

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
    buykorigw_apply_order_attribution_user_data( $user_data, $order );

    $custom_data = array(
        'value'          => (float) $order->get_total(),
        'currency'       => $order->get_currency(),
        'content_ids'    => $content_ids,
        'contents'       => $contents,
        'content_type'   => 'product',
        'num_items'      => $num_items,
        'order_id'       => (string) $order_id,
        'trigger_reason' => 'order_create_fallback',
    );

    $utm_keys = array( 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'bk_platform', 'bk_campaign_id', 'bk_adset_id', 'bk_ad_id' );
    foreach ( $utm_keys as $key ) {
        $val = $order->get_meta( '_buykorigw_snapshot_' . $key );
        if ( ! $val ) {
            $val = $order->get_meta( '_buykorigw_snapshot__buykorigw_' . $key );
        }
        if ( $val ) {
            $custom_data[ $key ] = $val;
        }
    }

    $event_payload = array(
        'event_name'       => 'InitiateCheckout',
        'event_time'       => time(),
        'event_id'         => $browser_event_id ?: ( 'wc_initiate_checkout_' . $order_id ),
        'event_source_url' => function_exists( 'wc_get_checkout_url' ) ? wc_get_checkout_url() : home_url( '/' ),
        'action_source'    => 'website',
        'user_data'        => $user_data,
        'custom_data'      => buykorigw_add_marketing_params( $custom_data ),
    );

    // This is a best-effort fallback for the browser event. Dispatch without
    // blocking checkout while the durable Purchase sync uses Action Scheduler.
    $sent = buykorigw_send_event( $event_payload, false );
    if ( $sent ) {
        $order->update_meta_data( '_buykorigw_initiate_checkout_sent', 1 );
        $order->update_meta_data( '_buykorigw_initiate_checkout_sent_at', current_time( 'mysql' ) );
        $order->save();
        buykorigw_add_order_note( $order_id, 'InitiateCheckout fallback event queued after order items were ready.' );
    }
}

function buykorigw_claim_confirm_attempt( $order_id, $status ) {
    static $attempted = array();

    $key = absint( $order_id ) . ':' . buykorigw_normalize_order_status_slug( $status );
    if ( isset( $attempted[ $key ] ) ) {
        return false;
    }

    $attempted[ $key ] = true;
    return true;
}

function buykorigw_on_order_status_change( $order_id, $old_status = '', $new_status = '', $order_from_hook = null ) {
    $settings = buykorigw_get_settings();

    // Only proceed if deferred_purchase is ON
    if ( ! $settings['deferred_purchase'] ) {
        return;
    }

    if ( empty( $settings['api_key'] ) || empty( $settings['gateway_url'] ) ) {
        return;
    }

    $current_action = current_action();
    $status_from_action = '';
    if ( strpos( $current_action, 'woocommerce_order_status_' ) === 0 && $current_action !== 'woocommerce_order_status_changed' ) {
        $status_from_action = substr( $current_action, strlen( 'woocommerce_order_status_' ) );
    }
    $current_status = buykorigw_normalize_order_status_slug( $new_status ?: $status_from_action );
    if ( empty( $current_status ) ) {
        return;
    }

    if ( ! in_array( $current_status, buykorigw_get_confirm_statuses( $settings ), true ) ) {
        return;
    }

    // Prevent duplicate confirms — use WC_Order for HPOS compatibility
    $order = is_a( $order_from_hook, 'WC_Order' ) ? $order_from_hook : wc_get_order( $order_id );
    if ( ! $order ) {
        return;
    }

    $already_confirmed = $order->get_meta( '_buykorigw_confirmed' );
    if ( $already_confirmed ) {
        if ( $settings['debug_mode'] ) {
            error_log( "[Buykori AdSync] Order #$order_id already confirmed, skipping." );
        }
        return;
    }

    if ( ! buykorigw_claim_confirm_attempt( $order_id, $current_status ) ) {
        return;
    }

    // Send confirm request to Buykori AdSync
    $success = buykorigw_confirm_order( $order_id );

    if ( $success ) {
        $order->update_meta_data( '_buykorigw_confirmed', 1 );
        $order->update_meta_data( '_buykorigw_confirmed_at', current_time( 'mysql' ) );
        $order->update_meta_data( '_buykorigw_confirm_status', 'confirmed' );
        $order->add_order_note( '[Buykori AdSync] Order confirmed in AdSync. Purchase delivery follows the configured courier/deferred workflow.' );
        $order->save();

        if ( $settings['debug_mode'] ) {
            error_log( "[Buykori AdSync] ✅ Order #$order_id auto-confirmed successfully." );
        }
    } else {
        // Schedule retry via Action Scheduler
        buykorigw_schedule_retry( $order_id );

        $order->update_meta_data( '_buykorigw_confirm_status', 'retry_scheduled' );
        $order->add_order_note( '[Buykori AdSync] Purchase event confirm failed; retry scheduled.' );
        $order->save();

        if ( $settings['debug_mode'] ) {
            error_log( "[Buykori AdSync] ⚠️ Order #$order_id confirm failed, retry scheduled." );
        }
    }
}


// ─── 2. Confirm API Call ───────────────────────────────────────────────────────
function buykorigw_on_order_cancelled( $order_id ) {
    $settings = buykorigw_get_settings();

    if ( empty( $settings['api_key'] ) || empty( $settings['gateway_url'] ) ) {
        return;
    }

    $order = wc_get_order( $order_id );
    if ( ! $order ) {
        return;
    }

    // ─── If Order was ALREADY sent/confirmed → Trigger standard Refund event! ───
    $already_tracked   = $order->get_meta( '_buykorigw_tracked' );
    $already_confirmed = $order->get_meta( '_buykorigw_confirmed' );
    $refund_sent       = $order->get_meta( '_buykorigw_refunded_event_sent' );
    $confirm_status    = $order->get_meta( '_buykorigw_confirm_status' );

    if ( $settings['deferred_purchase'] && ! $already_confirmed ) {
        if ( $confirm_status === 'cancelled' ) {
            return;
        }

        $success = buykorigw_cancel_order( $order_id );

        if ( $success ) {
            $order->update_meta_data( '_buykorigw_confirm_status', 'cancelled' );
            $order->update_meta_data( '_buykorigw_cancelled_at', current_time( 'mysql' ) );
            $order->add_order_note( '[Buykori AdSync] Pending Purchase event cancelled. Nothing was sent to ad platforms.' );
            $order->save();

            if ( $settings['debug_mode'] ) {
                error_log( "[Buykori AdSync] âœ… Pending Purchase cancelled for order #$order_id." );
            }
        } else {
            buykorigw_schedule_cancel_retry( $order_id );
            $order->update_meta_data( '_buykorigw_confirm_status', 'cancel_retry_scheduled' );
            $order->add_order_note( '[Buykori AdSync] Pending Purchase cancel sync failed; retry scheduled.' );
            $order->save();

            if ( $settings['debug_mode'] ) {
                error_log( "[Buykori AdSync] âš ï¸ Pending Purchase cancel failed for order #$order_id; retry scheduled." );
            }
        }
        return;
    }

    if ( ( $already_tracked || $already_confirmed ) && ! $refund_sent ) {
        $success = buykorigw_send_refund_event( $order );
        if ( $success ) {
            $order->update_meta_data( '_buykorigw_refunded_event_sent', 1 );
            $order->update_meta_data( '_buykorigw_confirm_status', 'refund_event_sent' );
            $order->add_order_note( '[Buykori AdSync] Order was cancelled/refunded; server-side Refund event successfully sent to Meta CAPI, TikTok & GA4.' );
            $order->save();
        } else {
            $order->add_order_note( '[Buykori AdSync] Order was cancelled/refunded; failed to send server-side Refund event.' );
            $order->save();
        }
        return;
    }

}


function buykorigw_confirm_order( $order_id ) {
    $settings = buykorigw_get_settings();
    $url      = rtrim( $settings['gateway_url'], '/' ) . '/events/confirm';
    $body     = wp_json_encode( array(
        'order_id' => (string) $order_id,
    ) );

    $headers = array_merge( array(
        'Content-Type' => 'application/json',
        'X-API-Key'    => $settings['api_key'],
        'X-CAPI-Origin'=> buykorigw_site_origin(),
    ), buykorigw_signed_headers( $settings['api_key'], $body ) );

    $response = wp_remote_post( $url, array(
        'timeout'   => 15,
        'sslverify' => true,
        'headers'   => $headers,
        'body'      => $body,
    ) );

    if ( is_wp_error( $response ) ) {
        // Critical failure — always log regardless of debug_mode
        error_log( '[Buykori AdSync] Confirm failed for order #' . $order_id . ': ' . $response->get_error_message() );
        return false;
    }

    $code = wp_remote_retrieve_response_code( $response );
    $body = json_decode( wp_remote_retrieve_body( $response ), true );

    if ( $code === 200 && isset( $body['status'] ) && $body['status'] === 'success' ) {
        return true;
    }

    // 404 = no pending event found; do not mark the order confirmed.
    if ( $code === 404 ) {
        if ( $settings['debug_mode'] ) {
            error_log( "[Buykori AdSync] No pending event for order #$order_id (404). Possibly already confirmed." );
        }
        return false;
    }

    // Non-200 or unexpected response — always log
    error_log( "[Buykori AdSync] Confirm HTTP $code for order #$order_id." );

    return false;
}


function buykorigw_cancel_order( $order_id ) {
    $settings = buykorigw_get_settings();
    $url      = rtrim( $settings['gateway_url'], '/' ) . '/events/cancel';
    $body     = wp_json_encode( array(
        'order_id' => (string) $order_id,
    ) );

    $headers = array_merge( array(
        'Content-Type' => 'application/json',
        'X-API-Key'    => $settings['api_key'],
    ), buykorigw_signed_headers( $settings['api_key'], $body ) );

    $response = wp_remote_post( $url, array(
        'timeout'   => 15,
        'sslverify' => true,
        'headers'   => $headers,
        'body'      => $body,
    ) );

    if ( is_wp_error( $response ) ) {
        error_log( '[Buykori AdSync] Cancel failed for order #' . $order_id . ': ' . $response->get_error_message() );
        return false;
    }

    $code = wp_remote_retrieve_response_code( $response );
    $body = json_decode( wp_remote_retrieve_body( $response ), true );

    if ( $code === 200 && isset( $body['status'] ) && $body['status'] === 'success' ) {
        return true;
    }

    if ( $code === 404 ) {
        if ( $settings['debug_mode'] ) {
            error_log( "[Buykori AdSync] No pending event to cancel for order #$order_id (404)." );
        }
        return true;
    }

    error_log( "[Buykori AdSync] Cancel HTTP $code for order #$order_id." );
    return false;
}


// ─── 3. Action Scheduler Retry Queue ───────────────────────────────────────────
function buykorigw_schedule_retry( $order_id ) {
    // Use WooCommerce Action Scheduler if available, otherwise WP-Cron
    if ( function_exists( 'as_schedule_single_action' ) ) {
        if (
            function_exists( 'as_next_scheduled_action' )
            && as_next_scheduled_action( 'buykorigw_retry_confirm', array( 'order_id' => $order_id ), 'buykori-adsync' )
        ) {
            return;
        }

        // Retry after 5 minutes
        as_schedule_single_action(
            time() + 300,
            'buykorigw_retry_confirm',
            array( 'order_id' => $order_id ),
            'buykori-adsync'
        );
    } else {
        if ( wp_next_scheduled( 'buykorigw_retry_confirm_cron', array( $order_id ) ) ) {
            return;
        }

        // Fallback: WP-Cron
        wp_schedule_single_event(
            time() + 300,
            'buykorigw_retry_confirm_cron',
            array( $order_id )
        );
    }
}


function buykorigw_schedule_cancel_retry( $order_id ) {
    if ( function_exists( 'as_schedule_single_action' ) ) {
        if (
            function_exists( 'as_next_scheduled_action' )
            && as_next_scheduled_action( 'buykorigw_retry_cancel', array( 'order_id' => $order_id ), 'buykori-adsync' )
        ) {
            return;
        }

        as_schedule_single_action(
            time() + 300,
            'buykorigw_retry_cancel',
            array( 'order_id' => $order_id ),
            'buykori-adsync'
        );
    } else {
        if ( wp_next_scheduled( 'buykorigw_retry_cancel_cron', array( $order_id ) ) ) {
            return;
        }

        wp_schedule_single_event(
            time() + 300,
            'buykorigw_retry_cancel_cron',
            array( $order_id )
        );
    }
}

// Action Scheduler callback
add_action( 'buykorigw_retry_confirm', 'buykorigw_retry_confirm_handler' );

function buykorigw_retry_confirm_handler( $args ) {
    $order_id = $args['order_id'] ?? 0;
    if ( ! $order_id ) {
        return;
    }

    $settings = buykorigw_get_settings();

    if ( buykorigw_get_order_meta( $order_id, '_buykorigw_confirm_status' ) === 'cancelled' ) {
        return;
    }

    // Already confirmed? Skip
    if ( buykorigw_get_order_meta( $order_id, '_buykorigw_confirmed' ) ) {
        return;
    }

    $retry_count = (int) buykorigw_get_order_meta( $order_id, '_buykorigw_retry_count' );

    $success = buykorigw_confirm_order( $order_id );

    if ( $success ) {
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirmed', 1 );
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirmed_at', current_time( 'mysql' ) );
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirm_status', 'confirmed' );
        buykorigw_add_order_note( $order_id, 'Order confirmed in AdSync by retry. Purchase delivery follows the configured courier/deferred workflow.' );

        if ( $settings['debug_mode'] ) {
            error_log( "[Buykori AdSync] ✅ Retry success for order #$order_id (attempt $retry_count)" );
        }
    } else {
        $retry_count++;
        buykorigw_update_order_meta( $order_id, '_buykorigw_retry_count', $retry_count );

        // Max 5 retries (5min, 10min, 20min, 40min, 80min)
        if ( $retry_count < 5 ) {
            $delay = 300 * pow( 2, $retry_count - 1 ); // Exponential backoff
            if ( function_exists( 'as_schedule_single_action' ) ) {
                if (
                    ! function_exists( 'as_next_scheduled_action' )
                    || ! as_next_scheduled_action( 'buykorigw_retry_confirm', array( 'order_id' => $order_id ), 'buykori-adsync' )
                ) {
                    as_schedule_single_action(
                        time() + $delay,
                        'buykorigw_retry_confirm',
                        array( 'order_id' => $order_id ),
                        'buykori-adsync'
                    );
                }
            } elseif ( ! wp_next_scheduled( 'buykorigw_retry_confirm_cron', array( $order_id ) ) ) {
                wp_schedule_single_event(
                    time() + $delay,
                    'buykorigw_retry_confirm_cron',
                    array( $order_id )
                );
            }
            buykorigw_update_order_meta( $order_id, '_buykorigw_confirm_status', 'retry_' . $retry_count );

            if ( $settings['debug_mode'] ) {
                error_log( "[Buykori AdSync] Retry #$retry_count for order #$order_id, next in {$delay}s" );
            }
        } else {
            buykorigw_update_order_meta( $order_id, '_buykorigw_confirm_status', 'failed' );
            buykorigw_add_order_note( $order_id, 'Purchase event confirm failed after max retries.' );

            if ( $settings['debug_mode'] ) {
                error_log( "[Buykori AdSync] ❌ Max retries reached for order #$order_id" );
            }
        }
    }
}

// WP-Cron fallback callback
add_action( 'buykorigw_retry_confirm_cron', 'buykorigw_retry_confirm_cron_handler' );

function buykorigw_retry_confirm_cron_handler( $order_id ) {
    buykorigw_retry_confirm_handler( array( 'order_id' => $order_id ) );
}


// ─── 4. Admin Order Meta Box — Tracking Status ─────────────────────────────────
add_action( 'buykorigw_retry_cancel', 'buykorigw_retry_cancel_handler' );

function buykorigw_retry_cancel_handler( $args ) {
    $order_id = $args['order_id'] ?? 0;
    if ( ! $order_id ) {
        return;
    }

    if ( buykorigw_get_order_meta( $order_id, '_buykorigw_confirmed' ) ) {
        return;
    }

    if ( buykorigw_get_order_meta( $order_id, '_buykorigw_confirm_status' ) === 'cancelled' ) {
        return;
    }

    $settings    = buykorigw_get_settings();
    $retry_count = (int) buykorigw_get_order_meta( $order_id, '_buykorigw_cancel_retry_count' );
    $success     = buykorigw_cancel_order( $order_id );

    if ( $success ) {
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirm_status', 'cancelled' );
        buykorigw_update_order_meta( $order_id, '_buykorigw_cancelled_at', current_time( 'mysql' ) );
        buykorigw_add_order_note( $order_id, 'Pending Purchase event cancelled by retry. Nothing was sent to ad platforms.' );
        if ( $settings['debug_mode'] ) {
            error_log( "[Buykori AdSync] ✅ Cancel retry success for order #$order_id." );
        }
        return;
    }

    $retry_count++;
    buykorigw_update_order_meta( $order_id, '_buykorigw_cancel_retry_count', $retry_count );

    if ( $retry_count < 5 ) {
        $delay = 300 * pow( 2, $retry_count - 1 );
        if ( function_exists( 'as_schedule_single_action' ) ) {
            as_schedule_single_action(
                time() + $delay,
                'buykorigw_retry_cancel',
                array( 'order_id' => $order_id ),
                'buykori-adsync'
            );
        } elseif ( ! wp_next_scheduled( 'buykorigw_retry_cancel_cron', array( $order_id ) ) ) {
            wp_schedule_single_event(
                time() + $delay,
                'buykorigw_retry_cancel_cron',
                array( $order_id )
            );
        }
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirm_status', 'cancel_retry_' . $retry_count );
    } else {
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirm_status', 'cancel_failed' );
        buykorigw_add_order_note( $order_id, 'Pending Purchase cancel sync failed after max retries.' );
    }
}

add_action( 'buykorigw_retry_cancel_cron', 'buykorigw_retry_cancel_cron_handler' );

function buykorigw_retry_cancel_cron_handler( $order_id ) {
    buykorigw_retry_cancel_handler( array( 'order_id' => $order_id ) );
}

add_action( 'add_meta_boxes', 'buykorigw_add_order_meta_box' );
add_action( 'admin_enqueue_scripts', 'buykorigw_order_admin_assets' );

function buykorigw_order_admin_assets( $hook ) {
    if ( ! in_array( $hook, array( 'post.php', 'post-new.php', 'woocommerce_page_wc-orders' ), true ) ) {
        return;
    }

    $screen = get_current_screen();
    if ( ! $screen || ! in_array( $screen->id, array( 'shop_order', 'woocommerce_page_wc-orders' ), true ) ) {
        return;
    }

    wp_enqueue_style(
        'buykorigw-admin-order',
        BUYKORIGW_PLUGIN_URL . 'assets/css/admin-order.css',
        array(),
        BUYKORIGW_VERSION
    );

    wp_enqueue_script(
        'buykorigw-admin-order',
        BUYKORIGW_PLUGIN_URL . 'assets/js/admin-order.js',
        array(),
        BUYKORIGW_VERSION,
        true
    );
}

function buykorigw_add_order_meta_box() {
    // Support both legacy (post) and HPOS (woocommerce_page_wc-orders) screens
    $screens = array( 'shop_order', 'woocommerce_page_wc-orders' );
    foreach ( $screens as $screen ) {
        add_meta_box(
            'buykorigw_tracking_status',
            '⚡ Buykori AdSync — Tracking Status',
            'buykorigw_render_order_meta_box',
            $screen,
            'side',
            'default'
        );
    }
}

function buykorigw_render_order_meta_box( $post_or_order ) {
    if ( $post_or_order instanceof WP_Post ) {
        $order_id = $post_or_order->ID;
    } elseif ( is_a( $post_or_order, 'WC_Order' ) ) {
        $order_id = $post_or_order->get_id();
    } else {
        $order_id = 0;
    }

    if ( ! $order_id ) {
        echo '<p class="buykorigw-order-muted">Order ID not found.</p>';
        return;
    }

    $order = wc_get_order( $order_id );
    if ( $order ) {
        $tracked      = $order->get_meta( '_buykorigw_tracked' );
        $confirmed    = $order->get_meta( '_buykorigw_confirmed' );
        $confirmed_at = $order->get_meta( '_buykorigw_confirmed_at' );
        $status       = $order->get_meta( '_buykorigw_confirm_status' );
        $retry_count  = $order->get_meta( '_buykorigw_retry_count' );
    } else {
        $tracked = $confirmed = $confirmed_at = $status = $retry_count = '';
    }
    $settings = buykorigw_get_settings();

    echo '<div class="buykorigw-order-status">';

    if ( $tracked ) {
        echo '<div class="buykorigw-order-ok">Purchase event tracked</div>';
    } else {
        echo '<div class="buykorigw-order-muted">Purchase event not yet tracked</div>';
    }

    if ( $settings['deferred_purchase'] ) {
        echo '<hr class="buykorigw-order-divider">';

        if ( $confirmed ) {
            echo '<div class="buykorigw-order-ok">Confirmed & sent to Facebook</div>';
            if ( $confirmed_at ) {
                echo '<div class="buykorigw-order-note">' . esc_html( $confirmed_at ) . '</div>';
            }
        } elseif ( $status === 'cancelled' ) {
            echo '<div class="buykorigw-order-danger">Cancelled - Purchase event was not sent</div>';
        } elseif ( $status === 'refunded_after_confirm' ) {
            echo '<div class="buykorigw-order-warning">Refunded after event was already sent</div>';
        } elseif ( $status === 'cancel_failed' ) {
            echo '<div class="buykorigw-order-danger">Cancel sync failed (max retries reached)</div>';
        } elseif ( strpos( $status, 'cancel_retry' ) !== false ) {
            echo '<div class="buykorigw-order-warning">Cancel retry in progress</div>';
        } elseif ( $status === 'failed' ) {
            echo '<div class="buykorigw-order-danger">Confirm failed (max retries reached)</div>';
            echo '<div class="buykorigw-order-actions"><button type="button" class="button button-small buykorigw-manual-confirm" data-buykorigw-order-id="' . esc_attr( $order_id ) . '" data-buykorigw-nonce="' . esc_attr( wp_create_nonce( 'buykorigw_manual_confirm' ) ) . '">Retry Now</button></div>';
        } elseif ( strpos( $status, 'retry' ) !== false ) {
            echo '<div class="buykorigw-order-warning">Retry in progress (attempt ' . intval( $retry_count ) . '/5)</div>';
        } else {
            echo '<div class="buykorigw-order-info">Pending - waiting for order status change</div>';
            $confirm_status_labels = array_map( 'ucfirst', buykorigw_get_confirm_statuses( $settings ) );
            echo '<div class="buykorigw-order-muted buykorigw-order-note">Auto-confirm on: <strong>' . esc_html( implode( ', ', $confirm_status_labels ) ) . '</strong></div>';
        }
    }

    echo '</div>';
}


// ─── 5. AJAX: Manual Confirm from Meta Box ─────────────────────────────────────
add_action( 'wp_ajax_buykorigw_manual_confirm', 'buykorigw_ajax_manual_confirm' );

function buykorigw_ajax_manual_confirm() {
    check_ajax_referer( 'buykorigw_manual_confirm', 'nonce' );

    if ( ! current_user_can( 'manage_woocommerce' ) ) {
        wp_send_json_error( 'Permission denied' );
    }

    $order_id = intval( $_POST['order_id'] ?? 0 );
    if ( ! $order_id ) {
        wp_send_json_error( 'Invalid order ID' );
    }

    $success = buykorigw_confirm_order( $order_id );

    if ( $success ) {
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirmed', 1 );
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirmed_at', current_time( 'mysql' ) );
        buykorigw_update_order_meta( $order_id, '_buykorigw_confirm_status', 'confirmed' );
        buykorigw_add_order_note( $order_id, 'Order confirmed in AdSync manually. Purchase delivery follows the configured courier/deferred workflow.' );
        wp_send_json_success( 'Order confirmed!' );
    } else {
        wp_send_json_error( 'Confirm failed' );
    }
}

function buykorigw_send_refund_event( $order ) {
    $settings = buykorigw_get_settings();
    $order_id = $order->get_id();

    list( $content_ids, $contents, $num_items ) = buykorigw_order_contents_payload( $order );
    if ( empty( $content_ids ) || empty( $contents ) ) {
        return false;
    }

    $snapshot_ip = $order->get_meta( '_buykorigw_snapshot_ip' );
    $snapshot_ua = $order->get_meta( '_buykorigw_snapshot_ua' );
    $user_data = array(
        'client_ip_address' => $order->get_customer_ip_address() ?: ( $snapshot_ip ?: ( function_exists( 'buykorigw_get_real_ip' ) ? buykorigw_get_real_ip() : ( $_SERVER['REMOTE_ADDR'] ?? '' ) ) ),
        'client_user_agent' => $order->get_customer_user_agent() ?: ( $snapshot_ua ?: ( $_SERVER['HTTP_USER_AGENT'] ?? '' ) ),
    );

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
    buykorigw_apply_order_attribution_user_data( $user_data, $order );

    $custom_data = array(
        'value'          => (float) $order->get_total(),
        'currency'       => $order->get_currency(),
        'content_ids'    => $content_ids,
        'contents'       => $contents,
        'content_type'   => 'product',
        'num_items'      => $num_items,
        'order_id'       => (string) $order_id,
        'trigger_reason' => 'order_refund_hook',
    );

    $utm_keys = array( 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'bk_platform', 'bk_campaign_id', 'bk_adset_id', 'bk_ad_id' );
    foreach ( $utm_keys as $key ) {
        $val = $order->get_meta( '_buykorigw_snapshot_' . $key );
        if ( ! $val ) {
            $val = $order->get_meta( '_buykorigw_snapshot__buykorigw_' . $key );
        }
        if ( $val ) {
            $custom_data[ $key ] = $val;
        }
    }

    // Inject GA4 client_id and session_id from snapshot for Measurement Protocol
    $ga4_client_id  = $order->get_meta( '_buykorigw_snapshot_ga_client_id' );
    $ga4_session_id = $order->get_meta( '_buykorigw_snapshot_ga_session_id' );
    if ( $ga4_client_id ) {
        $custom_data['client_id'] = $ga4_client_id;
    }
    if ( $ga4_session_id ) {
        $custom_data['session_id'] = $ga4_session_id;
    }

    $event_payload = array(
        'event_name'       => 'Refund',
        'event_time'       => time(),
        'event_id'         => 'wc_refund_' . $order_id,
        'event_source_url' => $order->get_checkout_order_received_url(),
        'action_source'    => 'website',
        'user_data'        => $user_data,
        'custom_data'      => buykorigw_add_marketing_params( $custom_data ),
    );

    return buykorigw_send_event( $event_payload, true );
}
