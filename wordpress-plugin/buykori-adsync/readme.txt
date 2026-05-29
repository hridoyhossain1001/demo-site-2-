=== Buykori AdSync — Server-Side Tracking ===
Contributors: buykorigw
Tags: facebook, capi, server-side tracking, woocommerce, pixel, ga4, tiktok
Requires at least: 5.8
Tested up to: 6.7
Requires PHP: 7.4
Stable tag: 1.2.6
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

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
