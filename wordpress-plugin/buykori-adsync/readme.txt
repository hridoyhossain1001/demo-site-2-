=== Buykori AdSync — Server-Side Tracking ===
Contributors: buykorigw
Tags: facebook, capi, server-side tracking, woocommerce, pixel, ga4, tiktok
Requires at least: 5.8
Tested up to: 6.7
Requires PHP: 7.4
Stable tag: 1.2.51
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

== Changelog ==

= 1.2.51 =
* Adds WooCommerce variation attributes such as color and size to pending COD order details.

= 1.2.50 =
* Sends WooCommerce orders to Buykori during order creation instead of waiting for traffic-triggered WP-Cron.
* Confirms incomplete-checkout recovery delivery before marking the order recovered.

Server-Side Facebook CAPI, TikTok, and GA4 tracking for WooCommerce with one-page landing support and deferred purchase control.

== Description ==

Buykori AdSync প্লাগইন আপনার WooCommerce স্টোরের সকল ইভেন্ট (PageView, ViewContent, AddToCart, InitiateCheckout, Purchase) সার্ভার-সাইড ট্র্যাকিং দিয়ে Facebook, TikTok এবং GA4-এ পাঠায়।

**মূল ফিচারসমূহ:**

* ✅ **Zero Configuration:** শুধু API Key বসান, বাকি সব অটোমেটিক
* 🔒 **SHA-256 PII Hashing:** কাস্টমারের ইমেইল, ফোন, নাম অটোমেটিক হ্যাশ হয়ে যায়
* 📦 **Deferred Purchase (COD):** ক্যাশ-অন-ডেলিভারির জন্য অর্ডার কমপ্লিট হলে Purchase ইভেন্ট পাঠায়
* 🔄 **Auto Retry:** API কল ফেইল হলে Action Scheduler দিয়ে অটো রিট্রাই করে
* ⚡ **Cache-Safe:** AJAX দিয়ে ডাটা পাঠায় — LiteSpeed, WP Rocket ক্যাশ বাধা দেয় না
* 🛡️ **Security Plugin Compatible:** WordPress কোর ফাংশন (`wp_remote_post`) ব্যবহার করে — Wordfence ব্লক করে না

== Installation ==

1. প্লাগইনের ZIP ফাইলটি ডাউনলোড করুন
2. WordPress Admin → Plugins → Add New → Upload Plugin
3. ZIP ফাইলটি আপলোড করে "Install Now" ক্লিক করুন
4. প্লাগইন Activate করুন
5. বাম মেনু থেকে "Buykori AdSync" → আপনার API Key বসান → Save Settings

== Frequently Asked Questions ==

= API Key কোথায় পাবো? =
আপনার Buykori AdSync ড্যাশবোর্ডে লগিন করুন। ড্যাশবোর্ডের উপরে আপনার API Key দেখতে পাবেন।

= ক্যাশ প্লাগইন ব্যবহার করলে কি সমস্যা হবে? =
না। এই প্লাগইন AJAX এবং Server-Side PHP ব্যবহার করে ডাটা পাঠায়, তাই ক্যাশ প্লাগইন কোনো বাধা দিতে পারে না।

= Deferred Purchase কী? =
ক্যাশ-অন-ডেলিভারি (COD) অর্ডারের ক্ষেত্রে Purchase ইভেন্ট তখনই Facebook-এ পাঠানো হয় যখন আপনি অর্ডারটি "Completed" করেন। এতে ফেক অর্ডারের ডাটা Facebook-এ যায় না।

== Changelog ==

= 1.2.48 =
* Allows safe update checks before a store is connected to Buykori, using HTTPS host validation and package SHA-256 verification.

= 1.2.47 =
* Fixed the Connect Buykori Account flow so new stores open the backend login/authorization page instead of a blank client route.
* Normalized old client-domain gateway settings to the canonical Buykori API endpoint during account connection.

= 1.2.46 =
* Fixed mobile incomplete-checkout recovery capture when customers leave immediately after entering a phone number.
* Rebuilt the plugin package with the official Linux-safe ZIP builder and refreshed update metadata.
* Relaxed the manual update-status refresh capability check to match the settings page permission.

= 1.2.45 =
* Added non-billable TikTok Browser Pixel PageView audit visibility in Client Portal Event History
* Browser pixel calls are labeled Fired because upstream TikTok acceptance is not directly verifiable

= 1.2.44 =
* Added a browser-only TikTok PageView backup switch that uses TikTok Pixel without adding server-side TikTok PageView volume.
* Allows TikTok PageView to run independently from full hybrid browser commerce backup when a TikTok Pixel ID is configured.

= 1.2.43 =
* Hardened checkout attribution snapshots for classic, Blocks, Store API, and payment redirect flows.
* Improved AddToCart and InitiateCheckout deduplication with shared event IDs and safer fallback IDs.
* Made first-party matching cookies and visitor IDs consistent across REST, AJAX, server hooks, and async Purchase.
* Added safer price parsing for Bengali digits, comma-formatted values, and custom checkout payloads.
* Reduced retry/replay duplicate risk in the browser queue and server-side Purchase lock path.

= 1.2.42 =
* Tightened AddToCart detection for page-builder buttons and avoided quantity-control false positives.
* Improved variation fallback so hidden/global variation inputs are not picked by mistake.
* Added FunnelKit/WooFunnels/CartFlows checkout detection without fragile class checks.
* Added a local Content ID preview tool with SKU/duplicate warnings and clearer catalog guidance.

= 1.2.41 =
* Sends TikTok PageView through the browser pixel using ttq.page() while keeping server-side TikTok delivery for supported commerce events.

= 1.2.40 =
* Restored multi-step checkout InitiateCheckout firing when a valid customer phone or email is entered.
* Broadened checkout field selectors for shipping/modern checkout forms and clears old checkout markers after a new AddToCart.

= 1.2.39 =
* Restored smart one-page landing ViewContent detection when product and checkout surfaces live on the same page.
* Kept multi-step checkout and shipping pages from firing InitiateCheckout before customer intent.

= 1.2.38 =
* Prevented multi-step checkout/shipping pages from firing InitiateCheckout on page load or navigation-only clicks.
* Prevented checkout pages from re-sending product ViewContent unless one-page mode is explicitly active.

= 1.2.37 =
* Added a 20-minute browser/session guard to prevent duplicate InitiateCheckout events from checkout button, field input, and delayed checkout surface triggers.

= 1.2.36 =
* Fixed COD/deferred purchase summary compatibility with SQLAlchemy 2 JSON value extraction.

= 1.2.35 =
* Added theme-agnostic WooCommerce page detection for product listings, shortcode cart pages, and custom checkout pages.
* Improved AddToCart detection across classic WooCommerce buttons, block buttons, and add-to-cart URLs.
* Restored checkout page-load surface checks so multi-page checkout can send InitiateCheckout reliably.

= 1.2.34 =
* Moved WordPress plugin admin UI scripts and styles into packaged asset files.
* Removed inline handlers, inline scripts, and inline styles from plugin admin screens and widgets.
* Moved frontend tracker config to a data attribute and loaded tracker scripts through WordPress enqueue APIs.

= 1.2.33 =
* Set optional events off by default for cleaner tracking: Lead, Search, ViewCart, RemoveFromCart, and AddPaymentInfo.
* Kept PageView, ViewContent, AddToCart, InitiateCheckout, and Purchase as the recommended default event set.
* Renamed the manual update helper to Refresh Update Status and clarified that WordPress normally checks updates automatically.

= 1.2.32 =
* Simplified the WordPress settings UI for client-facing setup.
* Added a compact connection status summary with account, website, and tracking state.
* Renamed reconnect to Switch Buykori Account and connection test to Run Health Check.
* Collapsed optional browser pixel backup and diagnostics behind support-oriented details.

= 1.2.31 =
* Added WordPress installation fingerprinting for connected-site validation.
* Added server-side active binding checks during event ingestion to block copied API key/plugin misuse.
* Added admin API tools to list, release, and transfer site bindings with audit logs.
* Added per-site event throttling when Redis is available.

= 1.2.30 =
* Added an active website binding lock so the same root domain or subdomain cannot be connected to multiple Buykori workspaces at the same time.
* Blocks second-account plugin connection attempts with a transfer/support message.
* Keeps the trial reuse downgrade guard for sites that already used a Growth trial.

= 1.2.29 =
* Added a clear plugin warning when a reconnected site has already used a Growth trial and the account is moved to Free.
* Improved reconnect safeguards for root domains and subdomains to reduce trial reuse abuse.
* Continued simplifying the WordPress settings experience for client-facing setup.

= 1.2.28 =
* Simplified the WordPress settings screen by hiding low-resource mode, landing mode, and variation toggles from the main UI.
* Added a disconnect action for account-connected sites.
* Made smart landing-page detection and variation tracking automatic by default.
* Moved catalog matching into Advanced controls for support-led troubleshooting.

= 1.2.27 =
* Prevented AddPaymentInfo from firing on checkout page load when WooCommerce preselects a default payment method.
* Kept AddPaymentInfo tied to trusted customer payment-method interaction with browser-side deduplication.

= 1.2.26 =
* Queued WooCommerce Purchase relay through Action Scheduler so checkout responses are not delayed by gateway calls
* Dispatched the server-side InitiateCheckout fallback without blocking checkout
* Dispatched incomplete-checkout recovery conversion without blocking order creation

= 1.2.19 =
* Added server-side WooCommerce AddToCart CAPI tracking with a session receipt queue
* Added browser Pixel receipt synchronization for classic AJAX, WooCommerce Blocks, and redirect add-to-cart flows
* Added shared AddToCart event IDs based on visitor, cart item key, and session counter for Pixel and CAPI deduplication
* Tightened one-page ViewContent so visible product or order-summary surfaces are required
* Tightened one-page InitiateCheckout field intent to require a valid email or phone number

= 1.2.18 =
* Added smart auto-detection for native WooCommerce, embedded checkout, Elementor, and CartFlows landing pages
* Restored PageView tracking across checkout and thank-you pages
* Added cart-session and DOM product resolution for one-page ViewContent tracking
* Added WooCommerce Blocks cart reconciliation through wc-blocks_added_to_cart and the Store API
* Added stable browser event IDs with REST retry reuse for cleaner Pixel and CAPI deduplication
* Made same-origin REST tracking resilient to stale nonces on cached landing pages
* Tightened InitiateCheckout intent tracking by removing focus-only and coupon-button triggers

= 1.2.17 =
* Rebuilt and republished the plugin package so stores on 1.2.16 can update cleanly.

= 1.2.16 =
* Added WooCommerce product name to the Purchase event contents payload for accurate portal display
* Fixed codAmount state initialization in orders view

= 1.2.14 =
* Reduced noisy PageView tracking on checkout and thank-you funnel pages
* Tightened PageView deduplication with normalized page paths
* Kept fallback event_source_url aligned with the captured page_location

= 1.2.13 =
* Ensured checkout-created WooCommerce orders send Purchase telemetry even when the WordPress-side COD toggle is not synced yet
* Lets the gateway-side COD Protection setting hold new orders reliably in Order Verification

= 1.2.12 =
* Improved Meta event match quality with stronger fbp/fbc, GA, and visitor ID fallback handling
* Normalized event contents payloads across REST and AJAX fallback tracking paths
* Added richer browser and server event body fields for AddToCart, InitiateCheckout, and cart-style events

= 1.2.11 =
* Improved frontend event-quality payload normalization and matching data capture

= 1.2.10 =
* Prevented CartFlows thank-you pages from firing a second empty InitiateCheckout event

= 1.2.9 =
* Added cache-busted update checks and versioned update transients so WordPress can find newly published plugin releases faster

= 1.2.8 =
* Shortened InitiateCheckout marker cookies to 20 minutes and clears them after Purchase/thank-you/new AddToCart
* Uses product data as checkout payload fallback on one-page funnels when cart data is not available yet
* Treats product fallback data as checkout-ready so checkout button clicks can fire InitiateCheckout

= 1.2.7 =
* Hardened InitiateCheckout fallback so order-created telemetry is sent even when the browser marker exists but the browser request was interrupted by redirect
* Added checkout button intent selectors for CartFlows and custom checkout CTAs

= 1.2.6 =
* Added deferred Purchase tracking on WooCommerce checkout completion hooks so COD orders enter Order Verification even when the thank-you page is skipped by CartFlows, blocks, or redirects

= 1.2.5 =
* Preserved deduplication keys on secondary TikTok delivery logs so the client portal no longer shows fallback did_* keys
* Tightened order-backed InitiateCheckout fallback guard to skip when a browser InitiateCheckout marker or event ID already exists
* Rebuilt the plugin ZIP with the validated canonical packaging script

= 1.2.4 =
* Tightened one-page landing mode so InitiateCheckout no longer fires from checkout surface/page-load checks
* ViewContent now waits for 50% product visibility plus a short dwell delay in one-page mode
* Added multi-product landing card ViewContent support for WooCommerce product grids and data-product blocks
* Added duplicate guards for AddToCart click/AJAX events and optional CTA intent selectors

= 1.2.3 =
* Ensured TikTok Events API always receives a singular content_id for catalog matching diagnostics
* Allowed REST tracking requests to accept custom_data directly as well as event_data
* Improved content_id/content_ids normalization for checkout and cart events

= 1.2.2 =
* Added server-side WooCommerce cart fallback for InitiateCheckout, ViewCart, and AddPaymentInfo payloads
* Ensures value, currency, content_ids, contents, and num_items are filled even when cached checkout config is empty

= 1.2.1 =
* Improved InitiateCheckout matching by waiting for checkout email/phone or submit intent before firing
* Added page_location and page_path custom parameters for clearer event source debugging

= 1.2.0 =
* Added Pending Revenue at Risk panel to the WordPress dashboard widget
* Added verified purchase, cancelled/expired, and pending revenue summary metrics
* Added WooCommerce order notes for held, confirmed, cancelled, failed, and retried Purchase events
* Added auto-cancel handling for cancelled, failed, and refunded WooCommerce orders

= 1.1.8 =
* Added one-page landing tracking mode for embedded checkout pages
* InitiateCheckout now waits for customer intent in one-page mode instead of firing on page load
* Added duplicate guards for PageView, ViewContent, and InitiateCheckout browser events
* Normalized UTM source and campaign names for cleaner campaign reports

= 1.1.7 =
* Updated default AdSync API URL to use the new custom domain
* Improved compatibility with gateway redirects

= 1.1.6 =
* Added UTM campaign capture and persistence for attribution reporting
* Added campaign source detection for TikTok and Facebook click IDs
* Added platform delivery controls support from the gateway

= 1.1.5 =
* Added a Check Update Now tool to clear plugin update cache from the settings page
* Added manual update-cache reset so admins do not need to run database queries

= 1.1.4 =
* Improved TikTok event payloads with richer product contents, content IDs, and content type
* Added checkout/customer field capture for better TikTok and Facebook event matching
* Rebuilt plugin update package so WordPress can detect the latest update

= 1.1.3 =
* Added customer PII fields capture (email, phone, name, address, etc.) for AJAX tracking events
* Added nested contents array support to browser events (AddToCart, ViewContent, InitiateCheckout, etc.)
* Improved TikTok payload content mapping to follow Events API specifications

= 1.1.2 =
* Added durable outbox-friendly tracking improvements
* Added TikTok _ttp and ttclid capture for standard and custom events
* Added lightweight AJAX rate limiting for frontend tracking
* Improved checkout/cart content payloads
* Improved custom event selector safety
* Improved client setup instructions

= 1.1.0 =
* Note: v1.2.26 moves checkout-time Purchase relay into Action Scheduler while retaining response verification in the scheduled worker.
* 🔒 Purchase event এখন blocking request — response verify করে success/failure ট্র্যাক করে
* 🔒 Phone number normalization ফিক্স — Python সার্ভারের সাথে hash matching ১০০% accurate
* 🔒 404 response আর success হিসেবে ধরা হয় না — proper error handling
* 🔒 WooCommerce webhook HMAC signature verification সাপোর্ট
* ⚡ Server-side atomic rate limiting — race condition মুক্ত
* ⚡ Production database safety — conditional create_all
* 📦 Plugin version bumped to 1.1.0

= 1.0.0 =
* Initial release
* PageView, ViewContent, AddToCart, InitiateCheckout, Purchase tracking
* Deferred Purchase with auto-confirm on order status change
* Action Scheduler retry queue with exponential backoff
* Admin settings page with connection test
* Order meta box showing tracking status
