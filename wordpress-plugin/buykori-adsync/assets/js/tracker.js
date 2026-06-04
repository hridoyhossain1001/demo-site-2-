(function() {
    'use strict';

    var cfg = readTrackerConfig();
    if (!cfg.ajax_url && !cfg.rest_url) return;
    var trackingMode = cfg.tracking_mode || 'auto';
    var pageContext = cfg.page_context || {};
    var resolvedContext = '';
    var pageInstanceId = String(Date.now()) + '_' + Math.floor(Math.random() * 1000000);
    persistMarketingParams();
    persistTikTokClickId();
    ensureFirstPartyCookies();
    initializeHybridPixels();
    setTimeout(flushQueuedEvents, 100);
    setTimeout(function() { syncAtcReceipts(null, 0); }, 250);

    function readTrackerConfig() {
        var node = document.getElementById('buykorigw-tracker-config');
        if (node) {
            try {
                return JSON.parse(node.getAttribute('data-config') || '{}');
            } catch (e) {}
        }
        return window.buykorigw_config || {};
    }

    function initializeHybridPixels() {
        if (!cfg.enable_hybrid) return;

        // Gather cached customer data for advanced matching
        var fbUserData = {};
        var ttUserData = {};
        var cookieFields = {
            em: '_buykorigw_id_em',
            ph: '_buykorigw_id_ph',
            fn: '_buykorigw_id_fn',
            ln: '_buykorigw_id_ln',
            ct: '_buykorigw_id_ct',
            st: '_buykorigw_id_st',
            zp: '_buykorigw_id_zp',
            country: '_buykorigw_id_country'
        };
        Object.keys(cookieFields).forEach(function(key) {
            var val = getCookie(cookieFields[key]);
            if (val) {
                fbUserData[key] = val;
                if (key === 'em') ttUserData.email = val;
                if (key === 'ph') ttUserData.phone_number = val;
            }
        });
        var externalId = getCookie('_buykorigw_vid');
        if (externalId) {
            fbUserData.external_id = externalId;
            ttUserData.external_id = externalId;
        }

        if (cfg.fb_pixel_id && !window.fbq) {
            !function(f,b,e,v,n,t,s)
            {if(f.fbq)return;n=f.fbq=function(){n.callMethod?
            n.callMethod.apply(n,arguments):n.queue.push(arguments)};
            if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
            n.queue=[];t=b.createElement(e);t.async=!0;
            t.src=v;s=b.getElementsByTagName(e)[0];
            s.parentNode.insertBefore(t,s)}(window, document,'script',
            'https://connect.facebook.net/en_US/fbevents.js');
            if (Object.keys(fbUserData).length) {
                fbq('init', cfg.fb_pixel_id, fbUserData);
            } else {
                fbq('init', cfg.fb_pixel_id);
            }
        }

        if (cfg.tt_pixel_id && !window.ttq) {
            !function (w, d, t) {
              w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"];
              ttq.setAndDefer=function(t,e){t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}};for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);
              ttq.instance=function(t){var e=ttq._i[t]||[];return e};ttq.load=function(e,n){var r="https://analytics.tiktok.com/i18n/pixel/events.js";ttq._i=ttq._i||{},ttq._i[e]=[],ttq._i[e]._u=r,ttq._t=ttq._t||{},ttq._t[e]=+new Date,ttq._o=ttq._o||{},ttq._o[e]=n||{};var o=d.createElement("script");o.type="text/javascript",o.async=!0,o.src=r;var a=d.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)};
            }(window, document, 'ttq');
            ttq.load(cfg.tt_pixel_id);
            if (Object.keys(ttUserData).length) {
                ttq.identify(ttUserData);
            }
        }
    }

    function isOnePageMode() {
        if (trackingMode === 'one_page') return true;
        if (trackingMode === 'standard') return false;
        if (isExplicitCheckoutPage()) return false;
        return resolvePageContext() === 'embedded_one_page_checkout';
    }

    function bodyHasClass(name) {
        return !!(document.body && document.body.classList && document.body.classList.contains(name));
    }

    function isExplicitCheckoutPage() {
        var path = (window.location && window.location.pathname ? window.location.pathname : '').toLowerCase();
        if (cfg.page_type === 'checkout' || !!pageContext.has_checkout || bodyHasClass('woocommerce-checkout')) return true;
        return !!(path.match(/checkout|checkouts|order-pay|\/step\/checkout/) && !path.match(/order-received|thank-you/));
    }

    function hasProductDetailSurface() {
        return !!document.querySelector(
            'body.single-product, form.cart, .single_add_to_cart_button, [name="add-to-cart"], ' +
            '[name="product_id"], .product_title, .summary.entry-summary, [itemtype*="schema.org/Product"]'
        );
    }

    function hasProductListSurface() {
        return !!document.querySelector(
            '.products .product, ul.products li.product, .wc-block-grid__product, ' +
            '.wp-block-woocommerce-product-template .product, .wp-block-woocommerce-all-products, ' +
            '[class*="product-grid"], [data-product_id], [data-product-id], .add_to_cart_button'
        );
    }

    function resolvePageContext() {
        var hasEmbeddedCheckout = !!document.querySelector(
            '.wcf-embed-checkout-form, .cartflows-checkout, .cartflows-container, ' +
            '[data-block-name="woocommerce/checkout"], .wp-block-woocommerce-checkout, .wc-block-checkout'
        );
        var hasCheckout = !!pageContext.has_checkout || !!document.querySelector(
            'form.checkout, form.woocommerce-checkout, .woocommerce-checkout, .wc-block-checkout, ' +
            '[data-block-name="woocommerce/checkout"], #customer_details, #order_review, #place_order, ' +
            '.wc-block-components-checkout-place-order-button, .wcf-embed-checkout-form, .cartflows-checkout, ' +
            '.wp-block-woocommerce-checkout, .wc-block-checkout__form, form[name="checkout"]'
        );
        var hasProduct = !!pageContext.has_product || hasProductDetailSurface();
        var hasProductList = !!pageContext.has_product_listing || hasProductListSurface();

        if (pageContext.is_thankyou || isThankYouFlowPage()) return 'thank_you';
        if (isExplicitCheckoutPage()) return 'standard_checkout';
        if (hasCheckout && (hasEmbeddedCheckout || hasProduct || hasProductList || !!cfg.product)) {
            return 'embedded_one_page_checkout';
        }
        if (hasCheckout) return pageContext.has_checkout ? 'standard_checkout' : 'embedded_one_page_checkout';
        if (hasProduct || pageContext.has_product) return 'native_product';
        if (hasProductList) return 'product_listing';
        if (pageContext.has_cart) return 'cart';
        return 'generic_page';
    }

    function refreshResolvedContext() {
        resolvedContext = resolvePageContext();
        return resolvedContext;
    }

    function getSelectedVariationInfo() {
        if (!cfg.enable_variations) return null;
        var form = document.querySelector('form.cart.variations_form');
        if (!form) return null;
        var varIdInput = form.querySelector('[name="variation_id"], .variation_id');
        if (!varIdInput) return null;
        var variationId = varIdInput.value;
        if (!variationId || variationId === '0') return null;

        var attributes = {};
        var selects = form.querySelectorAll('select[name^="attribute_"]');
        selects.forEach(function(select) {
            var name = select.name.replace('attribute_', '');
            if (select.value) {
                attributes[name] = select.value;
            }
        });
        var radios = form.querySelectorAll('input[type="radio"][name^="attribute_"]:checked');
        radios.forEach(function(radio) {
            var name = radio.name.replace('attribute_', '');
            if (radio.value) {
                attributes[name] = radio.value;
            }
        });
        var hiddens = form.querySelectorAll('input[type="hidden"][name^="attribute_"]');
        hiddens.forEach(function(hidden) {
            var name = hidden.name.replace('attribute_', '');
            if (hidden.value) {
                attributes[name] = hidden.value;
            }
        });

        var price = null;
        var sku = null;
        try {
            var variationsDataAttr = form.getAttribute('data-product_variations');
            if (variationsDataAttr) {
                var variations = JSON.parse(variationsDataAttr);
                if (Array.isArray(variations)) {
                    var found = variations.find(function(v) {
                        return String(v.variation_id) === String(variationId);
                    });
                    if (found) {
                        if (found.display_price !== undefined) {
                            price = parseFloat(found.display_price);
                        }
                        if (found.sku) {
                            sku = found.sku;
                        }
                    }
                }
            }
        } catch(e) {}

        return {
            id: variationId,
            sku: sku,
            price: price,
            attributes: attributes
        };
    }

    function eventOnce(key, ttlSeconds) {
        var storageKey = 'buykorigw_evt_' + key;
        var now = Date.now();
        var ttl = (ttlSeconds || 1800) * 1000;
        try {
            var last = parseInt(sessionStorage.getItem(storageKey) || '0', 10);
            if (last && (now - last) < ttl) return false;
            sessionStorage.setItem(storageKey, String(now));
        } catch(e) {
            if (eventOnce.memory[storageKey]) return false;
            eventOnce.memory[storageKey] = now;
        }
        return true;
    }
    eventOnce.memory = {};

    function currentPathKey() {
        return canonicalPagePath().replace(/[^a-zA-Z0-9_-]+/g, '_');
    }

    function simpleHash(value) {
        var hash = 0;
        var input = String(value || '');
        for (var i = 0; i < input.length; i++) {
            hash = ((hash << 5) - hash) + input.charCodeAt(i);
            hash |= 0;
        }
        return Math.abs(hash).toString(36);
    }

    function eventFingerprint(eventName, eventData) {
        var ids = eventData && eventData.content_ids ? eventData.content_ids.join('_') : '';
        if (eventName === 'PageView') return pageInstanceId;
        if (eventName === 'ViewContent') return pageInstanceId + '_' + ids;
        if (eventName === 'InitiateCheckout') {
            return simpleHash(JSON.stringify((eventData && eventData.contents) || []) + ':' + ((eventData && eventData.value) || 0));
        }
        if (eventName === 'AddToCart') {
            var revision = 1;
            try {
                revision = parseInt(sessionStorage.getItem('buykorigw_cart_revision') || '0', 10) + 1;
                sessionStorage.setItem('buykorigw_cart_revision', String(revision));
            } catch(e) {}
            return ids + '_' + revision;
        }
        return pageInstanceId + '_' + Math.floor(Math.random() * 1000000);
    }

    function canonicalPagePath() {
        var path = (window.location.pathname || '/').toLowerCase();
        var search = (window.location.search || '').toLowerCase();
        if (path.indexOf('/product/') === 0) {
            return path.replace(/\/+$/, '/') || '/';
        }
        if (path.indexOf('/step/checkout') === 0 || path.indexOf('/checkout') === 0 || path.indexOf('/cart') === 0) {
            return path.replace(/\/+$/, '/') || '/';
        }
        if (path.indexOf('order-received') !== -1 || path.indexOf('thank-you') !== -1 || search.indexOf('wcf-order=') !== -1 || search.indexOf('wcf-key=') !== -1) {
            return 'thankyou';
        }
        return path.replace(/\/+$/, '/') || '/';
    }

    function shouldSendPageView() {
        return true;
    }

    function getText(selector, root) {
        var el = (root || document).querySelector(selector);
        return el ? String(el.textContent || '').trim() : '';
    }

    function parsePrice(value) {
        if (value === null || value === undefined) return 0;
        var cleaned = String(value).replace(/[^0-9.,-]+/g, '').replace(/,/g, '');
        var parsed = parseFloat(cleaned);
        return isNaN(parsed) ? 0 : parsed;
    }

    function getProductDataFromElement(el) {
        if (!el) return null;
        var productId = el.getAttribute('data-product_id') ||
            el.getAttribute('data-product-id') ||
            el.getAttribute('data-productid') ||
            el.getAttribute('value') ||
            '';
        var addButton = el.matches && el.matches('.add_to_cart_button, .single_add_to_cart_button')
            ? el
            : el.querySelector('.add_to_cart_button, .single_add_to_cart_button, [data-product_id], [data-product-id]');
        if (!productId && addButton) {
            productId = addButton.getAttribute('data-product_id') || addButton.getAttribute('data-product-id') || '';
        }
        if (!productId && el.querySelector) {
            var productInput = el.querySelector('[name="product_id"], [name="add-to-cart"]');
            productId = productInput ? (productInput.value || productInput.getAttribute('value') || '') : '';
        }
        if (!productId && cfg.product && cfg.product.id) {
            productId = String(cfg.product.id);
        }
        if (!productId) return null;

        var sku = (addButton && addButton.getAttribute('data-product_sku')) || el.getAttribute('data-product_sku') || el.getAttribute('data-product-sku') || '';
        var contentId = (cfg.content_id_format === 'sku' && sku) ? sku : String(productId);
        var name = (addButton && addButton.getAttribute('data-product_name')) ||
            el.getAttribute('data-product_name') ||
            el.getAttribute('data-product-name') ||
            getText('.woocommerce-loop-product__title, .product_title, h1, h2, h3, [itemprop="name"]', el) ||
            (cfg.product ? cfg.product.name : '');
        var price = parsePrice(
            (addButton && addButton.getAttribute('data-product_price')) ||
            el.getAttribute('data-product_price') ||
            el.getAttribute('data-product-price') ||
            getText('.price .amount, .price, [data-product-price]', el) ||
            (cfg.product ? cfg.product.price : 0)
        );

        return {
            id: contentId,
            raw_id: String(productId),
            name: name,
            price: price,
            currency: (cfg.product ? cfg.product.currency : '') || cfg.currency || 'BDT',
            category: (cfg.product ? cfg.product.category : '') || ''
        };
    }

    function productPayloadFromData(productData) {
        if (!productData) return null;
        var item = {
            id: String(productData.id),
            content_id: String(productData.id),
            content_type: 'product',
            content_name: productData.name || '',
            content_category: productData.category || '',
            quantity: 1,
            item_price: productData.price || 0,
            price: productData.price || 0
        };
        return {
            content_ids: [String(productData.id)],
            contents: [item],
            content_name: productData.name || '',
            content_type: 'product',
            content_category: productData.category || '',
            value: productData.price || 0,
            currency: productData.currency || 'BDT'
        };
    }

    var cartSnapshot = {};
    (cfg.cart && cfg.cart.contents ? cfg.cart.contents : []).forEach(function(item) {
        var id = String(item.content_id || item.id || '');
        if (id) cartSnapshot[id] = parseInt(item.quantity || 0, 10);
    });

    function storeCartItemData(item) {
        if (!item) return null;
        var rawId = String(item.variation && item.variation.length && item.id ? item.id : (item.id || ''));
        if (!rawId) return null;
        var id = (cfg.content_id_format === 'sku' && item.sku) ? item.sku : rawId;
        var minorUnit = item.prices && item.prices.currency_minor_unit !== undefined
            ? parseInt(item.prices.currency_minor_unit, 10) : 2;
        var divisor = Math.pow(10, minorUnit);
        var price = item.prices && item.prices.price !== undefined ? parseFloat(item.prices.price) / divisor : 0;
        return {
            id: String(id),
            raw_id: rawId,
            name: item.name || '',
            price: price || 0,
            currency: (item.prices && item.prices.currency_code) || cfg.currency || 'BDT',
            category: ''
        };
    }

    function reconcileBlocksCart() {
        if (!cfg.store_cart_url || !window.fetch) return;
        fetch(cfg.store_cart_url, { credentials: 'same-origin' })
            .then(function(response) { return response.ok ? response.json() : null; })
            .then(function(cart) {
                if (!cart || !Array.isArray(cart.items)) return;
                var selected = null;
                var nextSnapshot = {};
                cart.items.forEach(function(item) {
                    var data = storeCartItemData(item);
                    if (!data) return;
                    var quantity = parseInt(item.quantity || 0, 10);
                    nextSnapshot[data.id] = quantity;
                    if (!selected && quantity > (cartSnapshot[data.id] || 0)) selected = data;
                });
                cartSnapshot = nextSnapshot;
                if (!selected) return;
                var payload = productPayloadFromData(selected);
                if (!payload) return;
                payload.trigger_reason = 'wc_blocks_cart_reconcile';
                clearCheckoutMarkers();
                syncAtcReceipts(payload, 0);
            }).catch(function() {});
    }

    function discoverLandingProduct() {
        if (cfg.product && cfg.product.id) return cfg.product;
        var surface = document.querySelector(
            '[data-buykori-product], form.cart, .single_add_to_cart_button, ' +
            '[name="product_id"], [name="add-to-cart"], .products .product, ul.products li.product, ' +
            '.wc-block-grid__product, .wp-block-woocommerce-product-template .product, [data-product_id], [data-product-id]'
        );
        var data = getProductDataFromElement(surface);
        if (!data || !data.raw_id) return null;
        cfg.product = {
            id: data.raw_id,
            sku: data.id !== data.raw_id ? data.id : '',
            name: data.name,
            price: data.price,
            currency: data.currency,
            category: data.category,
            source: 'dom_discovery'
        };
        return cfg.product;
    }

    // ─── First-Party Cookie Helpers ──────────────────────────────────────
    function ensureFirstPartyCookies() {
        if (!getCookie('_fbp')) {
            var fbp = 'fb.1.' + Date.now() + '.' + Math.floor(Math.random() * 9000000000 + 1000000000);
            setCookieLocal('_fbp', fbp, 90);
        }
        var fbclid = getQueryParam('fbclid');
        if (fbclid) {
            var currentFbc = getCookie('_fbc');
            if (!currentFbc || currentFbc.split('.').pop() !== fbclid) {
                var fbc = 'fb.1.' + Date.now() + '.' + fbclid;
                setCookieLocal('_fbc', fbc, 90);
            }
        }
        if (!getCookie('_ttp')) {
            var ttp = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                var r = Math.random() * 16 | 0;
                return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
            });
            setCookieLocal('_ttp', ttp, 90);
        }
        if (!getCookie('_buykorigw_vid')) {
            setCookieLocal('_buykorigw_vid', createVisitorId(), 180);
        }
    }

    function createVisitorId() {
        if (window.crypto && window.crypto.getRandomValues) {
            var bytes = new Uint32Array(4);
            window.crypto.getRandomValues(bytes);
            return 'bk.' + Date.now() + '.' + Array.prototype.map.call(bytes, function(n) {
                return n.toString(16);
            }).join('');
        }
        return 'bk.' + Date.now() + '.' + Math.floor(Math.random() * 9000000000 + 1000000000);
    }

    function getExternalId() {
        var vid = getCookie('_buykorigw_vid');
        if (!vid) {
            vid = createVisitorId();
            setCookieLocal('_buykorigw_vid', vid, 180);
        }
        return vid;
    }

    function setCookieLocal(name, value, days) {
        var expires = "";
        if (days !== undefined && days !== null) {
            var d = new Date();
            d.setTime(d.getTime() + (days * 24 * 60 * 60 * 1000));
            expires = "; expires=" + d.toUTCString();
        }
        var domain = getCookieDomain();
        document.cookie = name + '=' + encodeURIComponent(value) + expires + '; path=/' + domain + '; SameSite=Lax';
    }

    function clearCheckoutMarkers() {
        setCookieLocal('_buykorigw_ic_sent', '', -1);
        setCookieLocal('_buykorigw_ic_event_id', '', -1);
    }

    var initiateCheckoutGuardSeconds = 20 * 60;

    function hasRecentInitiateCheckoutMarker() {
        var timestamp = parseInt(getCookie('_buykorigw_ic_sent') || '0', 10);
        if (!timestamp) return false;
        return Math.abs(Math.floor(Date.now() / 1000) - timestamp) <= initiateCheckoutGuardSeconds;
    }

    var lastAddToCartIntentAt = 0;

    function markAddToCartIntent() {
        lastAddToCartIntentAt = Math.floor(Date.now() / 1000);
        setCookieLocal('_buykorigw_atc_intent', String(Math.floor(Date.now() / 1000)), 0.0014); // About 2 minutes
    }

    function hasRecentAddToCartIntent() {
        var timestamp = parseInt(getCookie('_buykorigw_atc_intent') || '0', 10);
        timestamp = Math.max(timestamp, lastAddToCartIntentAt);
        return !!timestamp && (Math.floor(Date.now() / 1000) - timestamp) <= 120;
    }

    function getCookieDomain() {
        var domain = "";
        try {
            var host = window.location.hostname;
            if (host.indexOf('.') !== -1 && !/^[0-9.]+$/.test(host) && host !== 'localhost') {
                var parts = host.split('.');
                var commonSecondLevelTlds = {
                    ac: true,
                    co: true,
                    com: true,
                    edu: true,
                    gov: true,
                    net: true,
                    org: true
                };
                if (
                    parts.length > 2 &&
                    parts[parts.length - 1].length === 2 &&
                    commonSecondLevelTlds[parts[parts.length - 2]]
                ) {
                    domain = "; domain=." + parts.slice(-3).join('.');
                } else if (parts.length > 2) {
                    domain = "; domain=." + parts.slice(-2).join('.');
                } else {
                    domain = "; domain=." + host;
                }
            }
        } catch(e) {}
        return domain;
    }

    function markInitiateCheckoutSent(eventId) {
        setCookieLocal('_buykorigw_ic_sent', String(Math.floor(Date.now() / 1000)), 0.014); // 20 minutes
        if (eventId) {
            setCookieLocal('_buykorigw_ic_event_id', eventId, 0.014); // 20 minutes
        }
    }

    // ─── GA4 Cookie Capture ──────────────────────────────────────────────
    function getGA4ClientId() {
        var ga = getCookie('_ga');
        if (ga) {
            var parts = ga.split('.');
            if (parts.length >= 4) return parts[parts.length - 2] + '.' + parts[parts.length - 1];
        }
        return '';
    }

    function getGA4SessionId() {
        var cookies = document.cookie.split(';');
        for (var i = 0; i < cookies.length; i++) {
            var c = cookies[i].trim();
            if (c.indexOf('_ga_') === 0) {
                var val = c.split('=')[1] || '';
                var parts = val.split('.');
                if (parts.length >= 3) return parts[2];
            }
        }
        return '';
    }

    // ─── Helper: Send event via REST API (primary) or AJAX (fallback) ────
    var cachedDeviceContext = null;
    function getDeviceContext() {
        if (cachedDeviceContext) return cachedDeviceContext;
        var ua = navigator.userAgent || '';
        var lower = ua.toLowerCase();
        var browser = 'Unknown';
        if (lower.indexOf('edg/') !== -1 || lower.indexOf('edgios') !== -1) browser = 'Edge';
        else if (lower.indexOf('opr/') !== -1 || lower.indexOf('opera') !== -1) browser = 'Opera';
        else if (lower.indexOf('samsungbrowser') !== -1) browser = 'Samsung Internet';
        else if (lower.indexOf('firefox') !== -1 || lower.indexOf('fxios') !== -1) browser = 'Firefox';
        else if (lower.indexOf('crios') !== -1 || lower.indexOf('chrome') !== -1) browser = 'Chrome';
        else if (lower.indexOf('safari') !== -1) browser = 'Safari';

        var os = 'Unknown';
        if (lower.indexOf('android') !== -1) os = 'Android';
        else if (/iphone|ipad|ipod/.test(lower)) os = 'iOS';
        else if (lower.indexOf('windows') !== -1) os = 'Windows';
        else if (lower.indexOf('mac os') !== -1 || lower.indexOf('macintosh') !== -1) os = 'macOS';
        else if (lower.indexOf('linux') !== -1) os = 'Linux';

        var type = 'desktop';
        if (lower.indexOf('ipad') !== -1 || lower.indexOf('tablet') !== -1) type = 'tablet';
        else if (lower.indexOf('mobile') !== -1 || lower.indexOf('iphone') !== -1 || (lower.indexOf('android') !== -1 && lower.indexOf('mobile') !== -1)) type = 'mobile';

        cachedDeviceContext = {
            _bk_device_type: type,
            _bk_device_os: os,
            _bk_device_browser: browser,
            _bk_screen_width: window.screen && window.screen.width ? window.screen.width : window.innerWidth,
            _bk_screen_height: window.screen && window.screen.height ? window.screen.height : window.innerHeight
        };
        return cachedDeviceContext;
    }

    function sendEvent(eventName, eventData, synchronous) {
        eventData = normalizeEventData(eventData || {});

        var ga4ClientId = getGA4ClientId();
        var ga4SessionId = getGA4SessionId();
        if (ga4ClientId) eventData['_ga'] = ga4ClientId;
        if (ga4SessionId) eventData['ga_session_id'] = ga4SessionId;
        if (!eventData.page_location) eventData.page_location = window.location.href;
        if (!eventData.page_path) eventData.page_path = window.location.pathname + window.location.search;
        var deviceContext = getDeviceContext();
        Object.keys(deviceContext).forEach(function(key) {
            if (eventData[key] === undefined || eventData[key] === null || eventData[key] === '') {
                eventData[key] = deviceContext[key];
            }
        });

        var eventId = '';
        if (eventName === 'InitiateCheckout') {
            // A new customer intent gets a fresh event ID. The cookie stores the
            // latest ID only so the later order-created fallback can deduplicate
            // against this browser event.
            eventId = 'wp_' + eventName + '_' + eventFingerprint(eventName, eventData) + '_' + pageInstanceId;
            markInitiateCheckoutSent(eventId);
        } else {
            eventId = 'wp_' + eventName + '_' + eventFingerprint(eventName, eventData);
        }

        var payload = {
            event_name: eventName,
            event_data: eventData,
            event_id: eventId,
            page_url: window.location.href,
            page_title: document.title,
            fbp: getCookie('_fbp') || '',
            fbc: getCookie('_fbc') || '',
            ttp: getCookie('_ttp') || '',
            ttclid: getTikTokClickId(),
            fbclid: getQueryParam('fbclid') || '',
            external_id: getExternalId(),
            _ga: getCookie('_ga') || '',
            ga_session_id: ga4SessionId
        };

        triggerHybridPixel(eventName, eventData, eventId);

        var piiSelectors = {
            em: ['#billing_email', 'input[name="billing_email"]', 'input[type="email"]', 'input[id^="email"]', 'input[autocomplete="email"]', '#email'],
            ph: ['#billing_phone', 'input[name="billing_phone"]', 'input[type="tel"]', 'input[id^="tel"]', 'input[autocomplete="tel"]', '#phone'],
            fn: ['#billing_first_name', 'input[name="billing_first_name"]', 'input[autocomplete="given-name"]', '#first-name'],
            ln: ['#billing_last_name', 'input[name="billing_last_name"]', 'input[autocomplete="family-name"]', '#last-name'],
            ct: ['#billing_city', 'input[name="billing_city"]'],
            st: ['#billing_state', 'select[name="billing_state"], input[name="billing_state"]'],
            zp: ['#billing_postcode', 'input[name="billing_postcode"]'],
            country: ['#billing_country', 'select[name="billing_country"], input[name="billing_country"]']
        };
        Object.keys(piiSelectors).forEach(function(key) {
            var value = getFieldValue(piiSelectors[key]);
            if (value) payload[key] = value;
        });

        var jsonBody = JSON.stringify(payload);

        if (synchronous) {
            var url = cfg.rest_url || cfg.ajax_url;
            try {
                var xhr = new XMLHttpRequest();
                xhr.open('POST', url, false);
                if (cfg.rest_url) {
                    xhr.setRequestHeader('Content-Type', 'application/json');
                    if (cfg.rest_nonce) xhr.setRequestHeader('X-WP-Nonce', cfg.rest_nonce);
                    xhr.send(jsonBody);
                } else {
                    xhr.send(buildAjaxFormData(eventName, eventData, eventId));
                }
            } catch(e) {}
            return;
        }

        if (cfg.rest_url && window.fetch) {
            sendViaRestWithRetry(jsonBody, eventName, eventData, eventId, 0);
        } else {
            sendViaAjax(eventName, eventData, eventId);
        }
    }

    function triggerHybridPixel(eventName, eventData, eventId) {
        if (!cfg.enable_hybrid || eventName === 'Identify') return;
        var browserParams = {};
        if (eventData.value !== undefined) browserParams.value = parseFloat(eventData.value);
        if (eventData.currency !== undefined) browserParams.currency = eventData.currency;
        if (eventData.content_name !== undefined) browserParams.content_name = eventData.content_name;
        if (eventData.content_type !== undefined) browserParams.content_type = eventData.content_type;
        if (eventData.content_ids !== undefined) browserParams.content_ids = eventData.content_ids;
        if (eventData.contents !== undefined) browserParams.contents = eventData.contents;
        if (eventData.num_items !== undefined) browserParams.num_items = eventData.num_items;

        if (window.fbq && cfg.fb_pixel_id) {
            fbq('track', eventName, browserParams, { eventID: eventId });
        }
        if (window.ttq && cfg.tt_pixel_id) {
            ttq.track(eventName, browserParams, { event_id: eventId });
        }
    }

    function acknowledgeAtcReceipts(eventIds) {
        if (!cfg.atc_receipts_url || !window.fetch || !eventIds.length) return;
        fetch(cfg.atc_receipts_url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ event_ids: eventIds }),
            keepalive: true
        }).catch(function() {});
    }

    var atcReceiptSyncInFlight = false;
    function syncAtcReceipts(fallbackPayload, attempt) {
        attempt = attempt || 0;
        if (!cfg.atc_receipts_url || !window.fetch) {
            if (fallbackPayload) sendEvent('AddToCart', fallbackPayload);
            return;
        }
        if (atcReceiptSyncInFlight) {
            if (fallbackPayload && attempt < 2) {
                setTimeout(function() { syncAtcReceipts(fallbackPayload, attempt + 1); }, 350);
            }
            return;
        }
        atcReceiptSyncInFlight = true;
        fetch(cfg.atc_receipts_url, { credentials: 'same-origin' })
            .then(function(response) { return response.ok ? response.json() : null; })
            .then(function(data) {
                atcReceiptSyncInFlight = false;
                var receipts = data && Array.isArray(data.receipts) ? data.receipts : [];
                if (receipts.length) {
                    var ids = [];
                    receipts.forEach(function(receipt) {
                        if (!receipt || !receipt.event_id || !receipt.event_data) return;
                        ids.push(receipt.event_id);
                        clearCheckoutMarkers();
                        triggerHybridPixel('AddToCart', normalizeEventData(receipt.event_data), receipt.event_id);
                    });
                    acknowledgeAtcReceipts(ids);
                    return;
                }
                if (fallbackPayload && attempt < 2) {
                    setTimeout(function() { syncAtcReceipts(fallbackPayload, attempt + 1); }, (attempt + 1) * 450);
                } else if (fallbackPayload) {
                    sendEvent('AddToCart', fallbackPayload);
                }
            }).catch(function() {
                atcReceiptSyncInFlight = false;
                if (fallbackPayload && attempt < 2) {
                    setTimeout(function() { syncAtcReceipts(fallbackPayload, attempt + 1); }, (attempt + 1) * 450);
                } else if (fallbackPayload) {
                    sendEvent('AddToCart', fallbackPayload);
                }
            });
    }

    function sendViaRestWithRetry(jsonBody, eventName, eventData, eventId, attempt) {
        queueRestEvent(jsonBody);
        var headers = {'Content-Type': 'application/json'};
        if (cfg.rest_nonce) headers['X-WP-Nonce'] = cfg.rest_nonce;
        fetch(cfg.rest_url, {
            method: 'POST',
            headers: headers,
            body: jsonBody,
            keepalive: true
        }).then(function(response) {
            if (response && response.ok) {
                removeQueuedEvent(eventId);
                return;
            }
            if (attempt < 2) {
                setTimeout(function() {
                    sendViaRestWithRetry(jsonBody, eventName, eventData, eventId, attempt + 1);
                }, (attempt + 1) * 700);
                return;
            }
            sendViaAjax(eventName, eventData, eventId);
        }).catch(function() {
            if (attempt < 2) {
                setTimeout(function() {
                    sendViaRestWithRetry(jsonBody, eventName, eventData, eventId, attempt + 1);
                }, (attempt + 1) * 700);
                return;
            }
            sendViaAjax(eventName, eventData, eventId);
        });
    }

    function getQueuedEvents() {
        try {
            return JSON.parse(localStorage.getItem('buykorigw_event_queue') || '[]');
        } catch(e) {
            return [];
        }
    }

    function queueRestEvent(jsonBody) {
        try {
            var payload = JSON.parse(jsonBody);
            if (!payload || !payload.event_id) return;
            var queue = getQueuedEvents().filter(function(item) {
                return item && item.event_id !== payload.event_id;
            });
            queue.push({ event_id: payload.event_id, body: jsonBody, queued_at: Date.now() });
            localStorage.setItem('buykorigw_event_queue', JSON.stringify(queue.slice(-20)));
        } catch(e) {}
    }

    function removeQueuedEvent(eventId) {
        try {
            var queue = getQueuedEvents().filter(function(item) {
                return item && item.event_id !== eventId;
            });
            localStorage.setItem('buykorigw_event_queue', JSON.stringify(queue));
        } catch(e) {}
    }

    function flushQueuedEvents() {
        if (!cfg.rest_url || !window.fetch) return;
        getQueuedEvents().forEach(function(item) {
            if (!item || !item.body || !item.event_id) return;
            if (item.queued_at && Date.now() - item.queued_at > 24 * 60 * 60 * 1000) {
                removeQueuedEvent(item.event_id);
                return;
            }
            var payload;
            try { payload = JSON.parse(item.body); } catch(e) { return; }
            sendViaRestWithRetry(item.body, payload.event_name || '', payload.event_data || {}, item.event_id, 0);
        });
    }

    function buildAjaxFormData(eventName, eventData, eventId) {
        var fd = new FormData();
        fd.append('action', 'buykorigw_track_event');
        fd.append('nonce', cfg.nonce);
        fd.append('event_name', eventName);
        fd.append('event_id', eventId || '');
        fd.append('event_data', JSON.stringify(eventData));
        fd.append('page_url', eventData.page_location || window.location.href);
        fd.append('page_title', document.title);
        fd.append('fbp', getCookie('_fbp') || '');
        fd.append('fbc', getCookie('_fbc') || '');
        fd.append('ttp', getCookie('_ttp') || '');
        fd.append('ttclid', getTikTokClickId());
        fd.append('fbclid', getQueryParam('fbclid') || '');
        fd.append('external_id', getExternalId());
        fd.append('_ga', getCookie('_ga') || '');
        fd.append('ga_session_id', getGA4SessionId());
        appendCustomerData(fd);
        return fd;
    }

    function sendViaAjax(eventName, eventData, eventId) {
        var fd = buildAjaxFormData(eventName, eventData, eventId);
        if (navigator.sendBeacon) {
            navigator.sendBeacon(cfg.ajax_url, fd);
        } else {
            fetch(cfg.ajax_url, { method: 'POST', body: fd, keepalive: true });
        }
    }

    function getCookie(name) {
        var match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
        return match ? decodeURIComponent(match[2]) : '';
    }

    function isValidContentId(id) {
        var s = String(id || '').trim();
        if (!s) return false;
        // 3-letter ISO currency codes (BDT, USD, EUR…) কে reject করো
        if (/^[A-Z]{3}$/i.test(s)) return false;
        return true;
    }

    function normalizeEventData(data) {
        var out = {};
        Object.keys(data || {}).forEach(function(key) {
            if (data[key] !== undefined && data[key] !== null && data[key] !== '') {
                out[key] = data[key];
            }
        });

        if (out.content_ids) {
            out.content_ids = (Array.isArray(out.content_ids) ? out.content_ids : [out.content_ids])
                .map(function(id) { return String(id || '').trim(); })
                .filter(isValidContentId); // currency string যেন না ঢোকে
            if (!out.content_ids.length) delete out.content_ids;
        }

        if (out.contents && Array.isArray(out.contents)) {
            out.contents = out.contents.map(function(item) {
                if (!item) return false;
                var id = item.content_id || item.id;
                if (!id || !isValidContentId(id)) return false;
                item.id = item.id || String(id);
                item.content_id = item.content_id || String(id);
                item.content_type = item.content_type || 'product';
                item.quantity = Math.max(1, parseInt(item.quantity || 1, 10));
                if (item.item_price === undefined && item.price !== undefined) {
                    item.item_price = parseFloat(item.price) || 0;
                }
                return item;
            }).filter(Boolean);
            if (!out.contents.length) delete out.contents;
        }

        if (!out.contents && out.content_ids && out.content_ids.length) {
            var fallbackPrice = out.content_ids.length === 1 ? (parseFloat(out.value || 0) || 0) : 0;
            out.contents = out.content_ids.map(function(id) {
                return {
                    id: String(id),
                    content_id: String(id),
                    content_type: 'product',
                    quantity: 1,
                    item_price: fallbackPrice
                };
            });
        }

        var marketing = getMarketingParams();
        Object.keys(marketing).forEach(function(key) {
            if (!out[key] && marketing[key]) out[key] = marketing[key];
        });

        return out;
    }

    function getFieldValue(selectors) {
        for (var i = 0; i < selectors.length; i++) {
            var el = document.querySelector(selectors[i]);
            if (el && el.value && String(el.value).trim()) {
                return String(el.value).trim();
            }
        }
        return '';
    }

    function getCustomerData() {
        var fields = {
            em: ['#billing_email', 'input[name="billing_email"]', 'input[type="email"]'],
            ph: ['#billing_phone', 'input[name="billing_phone"]', 'input[type="tel"]'],
            fn: ['#billing_first_name', 'input[name="billing_first_name"]'],
            ln: ['#billing_last_name', 'input[name="billing_last_name"]'],
            ct: ['#billing_city', 'input[name="billing_city"]'],
            st: ['#billing_state', 'select[name="billing_state"], input[name="billing_state"]'],
            zp: ['#billing_postcode', 'input[name="billing_postcode"]'],
            country: ['#billing_country', 'select[name="billing_country"], input[name="billing_country"]']
        };
        var out = {};
        Object.keys(fields).forEach(function(key) {
            var value = getFieldValue(fields[key]);
            if (value) out[key] = value;
        });
        return out;
    }

    function hasStrongCustomerData() {
        var data = getCustomerData();
        return !!normalizeRecoveryPhone(data.ph || '');
    }

    function appendCustomerData(formData) {
        var fields = getCustomerData();
        Object.keys(fields).forEach(function(key) {
            formData.append(key, fields[key]);
        });
    }

    function getQueryParam(name) {
        try {
            return new URLSearchParams(window.location.search).get(name) || '';
        } catch(e) {
            return '';
        }
    }

    function getQueryParamFromUrl(url, name) {
        if (!url) return '';
        try {
            return new URL(url, window.location.href).searchParams.get(name) || '';
        } catch(e) {
            return '';
        }
    }

    function persistMarketingParams() {
        var keys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'campaign_source'];
        keys.forEach(function(key) {
            var value = normalizeCampaignValue(key, getQueryParam(key));
            if (value) {
                document.cookie = '_buykorigw_' + key + '=' + encodeURIComponent(value) + '; path=/; max-age=' + (30 * 24 * 60 * 60) + '; SameSite=Lax';
            }
        });
    }

    // ─── UTM attributions fallback based on Click ID if UTM is absent ─────
    function getMarketingParams() {
        var out = {};
        ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'campaign_source'].forEach(function(key) {
            out[key] = normalizeCampaignValue(key, getQueryParam(key) || getCookie('_buykorigw_' + key) || '');
        });

        if (!out.campaign_source && out.utm_source) out.campaign_source = out.utm_source;
        if (!out.utm_source && getTikTokClickId()) out.utm_source = 'tiktok';
        if (!out.campaign_source && out.utm_source) out.campaign_source = out.utm_source;
        if (!out.utm_source && getCookie('_fbc')) {
            out.utm_source = 'facebook';
            out.campaign_source = 'facebook';
        }
        return out;
    }

    function normalizeCampaignValue(key, value) {
        value = String(value || '').trim();
        if (!value || /^__.*__$/.test(value)) return '';

        if (key === 'utm_source' || key === 'campaign_source') {
            return value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
        }
        return value;
    }

    function persistTikTokClickId() {
        var ttclid = getQueryParam('ttclid');
        if (ttclid) {
            setCookieLocal('_ttclid', ttclid, 90);
        }
    }

    function getTikTokClickId() {
        return getQueryParam('ttclid') || getCookie('_ttclid') || '';
    }

    var currentPath = (window.location && window.location.pathname ? window.location.pathname : '').toLowerCase();

    function isThankYouFlowPage() {
        var search = (window.location && window.location.search ? window.location.search : '').toLowerCase();
        return !!(
            cfg.page_type === 'thankyou' ||
            currentPath.indexOf('order-received') !== -1 ||
            currentPath.indexOf('thank-you') !== -1 ||
            search.indexOf('wcf-order=') !== -1 ||
            search.indexOf('wcf-key=') !== -1
        );
    }

    // Clear InitiateCheckout cookies on thank you pages to prevent event ID reuse on next checkout.
    if (isThankYouFlowPage()) {
        clearCheckoutMarkers();
    }

    // ─── 1. PageView ───────────────────────────────────────────────────
    if (cfg.events && cfg.events.pageview && shouldSendPageView()) {
        sendEvent('PageView', {});
    }

    // ─── 2. ViewContent (Product Page) ─────────────────────────────────
    function sendViewContentOnce() {
        refreshResolvedContext();
        if (!cfg.events || !cfg.events.viewcontent) return;
        if (trackingMode !== 'one_page' && isExplicitCheckoutPage()) return;
        if (resolvedContext !== 'native_product' && resolvedContext !== 'embedded_one_page_checkout') return;
        if (!discoverLandingProduct()) return;
        if (!eventOnce('ViewContent:' + String(cfg.product.id || '') + ':' + currentPathKey(), 1800)) return;

        var contentIdFormat = cfg.content_id_format || 'id';
        var productId = (contentIdFormat === 'sku' && cfg.product.sku) ? cfg.product.sku : String(cfg.product.id);
        var productPrice = cfg.product.price;
        var attributes = null;

        var variationInfo = getSelectedVariationInfo();
        if (variationInfo) {
            productId = (contentIdFormat === 'sku' && variationInfo.sku) ? variationInfo.sku : String(variationInfo.id);
            if (variationInfo.price !== null) {
                productPrice = variationInfo.price;
            }
            attributes = variationInfo.attributes;
        }

        var item = {
            id: productId,
            content_id: productId,
            content_type: 'product',
            content_name: cfg.product.name,
            content_category: cfg.product.category || '',
            quantity: 1,
            item_price: productPrice,
            price: productPrice
        };
        if (attributes) {
            item.attributes = attributes;
        }

        sendEvent('ViewContent', {
            content_ids: [productId],
            contents: [item],
            content_name: cfg.product.name,
            content_type: 'product',
            content_category: cfg.product.category || '',
            value: productPrice,
            currency: cfg.product.currency
        });
    }

    refreshResolvedContext();
    if (cfg.events && cfg.events.viewcontent && (cfg.product || discoverLandingProduct())) {
        if (typeof IntersectionObserver !== 'undefined') {
            var productSurface = document.querySelector('.product, .summary, form.cart, [data-product_id]');
            if (productSurface) {
                var productViewTimer = null;
                var viewObserver = new IntersectionObserver(function(entries) {
                    entries.forEach(function(entry) {
                        if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
                            clearTimeout(productViewTimer);
                            productViewTimer = setTimeout(function() {
                                sendViewContentOnce();
                                viewObserver.disconnect();
                            }, 1000);
                        } else {
                            clearTimeout(productViewTimer);
                        }
                    });
                }, { threshold: [0, 0.5, 0.75] });
                viewObserver.observe(productSurface);
            } else if (!isOnePageMode()) {
                setTimeout(sendViewContentOnce, 1200);
            }
        } else {
            sendViewContentOnce();
        }

        // Variation change dynamic trigger
        if (typeof jQuery !== 'undefined' && cfg.enable_variations) {
            jQuery(document.body).on('found_variation', function(e, variation) {
                if (variation && variation.variation_id) {
                    var varId = String(variation.variation_id);
                    if (eventOnce('ViewContentVar:' + varId + ':' + currentPathKey(), 300)) {
                        var contentIdFormat = cfg.content_id_format || 'id';
                        var productId = (contentIdFormat === 'sku' && variation.sku) ? variation.sku : varId;
                        var productPrice = parseFloat(variation.display_price || cfg.product.price);

                        var attributes = {};
                        if (variation.attributes) {
                            Object.keys(variation.attributes).forEach(function(k) {
                                attributes[k.replace('attribute_', '')] = variation.attributes[k];
                            });
                        }

                        var item = {
                            id: productId,
                            content_id: productId,
                            content_type: 'product',
                            content_name: cfg.product.name,
                            content_category: cfg.product.category || '',
                            quantity: 1,
                            item_price: productPrice,
                            price: productPrice
                        };
                        if (Object.keys(attributes).length) {
                            item.attributes = attributes;
                        }

                        sendEvent('ViewContent', {
                            content_ids: [productId],
                            contents: [item],
                            content_name: cfg.product.name,
                            content_type: 'product',
                            content_category: cfg.product.category || '',
                            value: productPrice,
                            currency: cfg.product.currency
                        });
                    }
                }
            });
        }
    }

    function observeLandingProductCards() {
        if (!cfg.events || !cfg.events.viewcontent || !isOnePageMode() || typeof IntersectionObserver === 'undefined') return;
        if (trackingMode !== 'one_page' && isExplicitCheckoutPage()) return;
        if (cfg.page_type === 'product' && cfg.product) return;
        var selector = [
            '[data-buykori-product]',
            '[data-product_id]',
            '[data-product-id]',
            'form.cart',
            '.single_add_to_cart_button',
            '.woocommerce-checkout-review-order-table',
            '.wc-block-components-order-summary',
            '.products .product',
            '.wc-block-grid__product',
            '.wp-block-woocommerce-product-template .product'
        ].join(', ');
        var cards = Array.prototype.slice.call(document.querySelectorAll(selector)).filter(function(el) {
            return getProductDataFromElement(el);
        });
        if (!cards.length) return;

        var timers = [];
        var observer = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                var el = entry.target;
                var idx = cards.indexOf(el);
                var data = getProductDataFromElement(el);
                if (!data) return;
                if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
                    clearTimeout(timers[idx]);
                    timers[idx] = setTimeout(function() {
                        if (!eventOnce('ViewContentCard:' + String(data.id) + ':' + currentPathKey(), 1800)) return;
                        var payload = productPayloadFromData(data);
                        if (payload) sendEvent('ViewContent', payload);
                        observer.unobserve(el);
                    }, 1000);
                } else {
                    clearTimeout(timers[idx]);
                }
            });
        }, { threshold: [0, 0.5, 0.75] });

        cards.forEach(function(card) {
            observer.observe(card);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            refreshResolvedContext();
            observeLandingProductCards();
            sendViewContentOnce();
        });
    } else {
        observeLandingProductCards();
        sendViewContentOnce();
    }

    if (typeof MutationObserver !== 'undefined') {
        var smartContextTimer;
        var smartContextObserver = new MutationObserver(function() {
            clearTimeout(smartContextTimer);
            smartContextTimer = setTimeout(function() {
                var previousContext = resolvedContext;
                refreshResolvedContext();
                if (resolvedContext !== previousContext || resolvedContext === 'embedded_one_page_checkout') {
                    observeLandingProductCards();
                }
            }, 350);
        });
        smartContextObserver.observe(document.body || document.documentElement, { childList: true, subtree: true });
    }

    // ─── 3. AddToCart ──────────────────────────────────────────────────
    if (cfg.events && cfg.events.addtocart) {
        var addToCartFiredViaAjax = false;

        // Mark real user intent before WooCommerce or a landing builder starts
        // its cart request. Automatic cart hydration must not count as AddToCart.
        document.addEventListener('click', function(e) {
            if (!e.isTrusted) return;
            var btn = e.target.closest(
                '.add_to_cart_button, .single_add_to_cart_button, [name="add-to-cart"], ' +
                '[data-buykori-addtocart], [data-buykori-atc], .buykorigw-addtocart-intent, ' +
                '.wc-block-components-product-button__button, a[href*="add-to-cart="], ' +
                'button[value][name="add-to-cart"], input[value][name="add-to-cart"]'
            );
            if (btn) markAddToCartIntent();
        }, true);

        // jQuery AJAX event — most reliable (fires AFTER WooCommerce confirms add)
        if (typeof jQuery !== 'undefined') {
            jQuery(document.body).on('added_to_cart', function(e, fragments, hash, $btn) {
                if (!hasRecentAddToCartIntent()) return;
                addToCartFiredViaAjax = true;
                var pid = ($btn ? $btn.attr('data-product_id') : '') || '';
                var pname = ($btn ? $btn.attr('data-product_name') : '') || '';
                var pprice = $btn ? parseFloat($btn.attr('data-product_price') || 0) : 0;

                var variationInfo = getSelectedVariationInfo();
                var attributes = null;
                if (variationInfo) {
                    pid = variationInfo.id;
                    if (variationInfo.sku && cfg.content_id_format === 'sku') {
                        pid = variationInfo.sku;
                    }
                    if (variationInfo.price !== null) {
                        pprice = variationInfo.price;
                    }
                    attributes = variationInfo.attributes;
                } else if (pid) {
                    if (cfg.product && String(cfg.product.id) === String(pid)) {
                        pname = pname || cfg.product.name;
                        if (cfg.content_id_format === 'sku' && cfg.product.sku) {
                            pid = cfg.product.sku;
                        }
                        pprice = pprice || cfg.product.price;
                    } else if (cfg.content_id_format === 'sku' && $btn && $btn.attr('data-product_sku')) {
                        pid = $btn.attr('data-product_sku');
                    }
                } else {
                    // pid ফাঁকা হলে cfg.product থেকে নাও (shop/archive page-এ $btn.data-product_id miss করতে পারে)
                    if (cfg.product && cfg.product.id) {
                        pid = (cfg.content_id_format === 'sku' && cfg.product.sku) ? cfg.product.sku : String(cfg.product.id);
                        pname = pname || cfg.product.name;
                        pprice = pprice || cfg.product.price;
                    } else if ($btn) {
                        // DOM থেকে শেষ চেষ্টা
                        var closestProduct = $btn.closest('[data-product_id], [data-product-id]');
                        if (closestProduct && closestProduct.length) {
                            pid = closestProduct.attr('data-product_id') || closestProduct.attr('data-product-id') || '';
                        }
                    }
                }

                // validate: pid যেন currency/text না হয় — numeric বা sku হতে হবে
                if (!pid || /^[a-z]{3}$/i.test(String(pid).trim())) {
                    return; // BDT/USD এর মতো 3-letter currency code reject করো
                }

                var item = {
                    id: String(pid),
                    content_id: String(pid),
                    content_type: 'product',
                    content_name: pname || (cfg.product ? cfg.product.name : ''),
                    quantity: 1,
                    item_price: pprice,
                    price: pprice
                };
                if (attributes) {
                    item.attributes = attributes;
                }

                if (!eventOnce('AddToCart:' + String(pid) + ':' + currentPathKey(), 2)) return;
                clearCheckoutMarkers();
                syncAtcReceipts({
                    content_ids: [String(pid)],
                    contents: [item],
                    content_name: pname || (cfg.product ? cfg.product.name : ''),
                    content_type: 'product',
                    value: pprice,
                    currency: (cfg.product ? cfg.product.currency : 'BDT')
                }, 0);
            });
        }

        document.body.addEventListener('wc-blocks_added_to_cart', function() {
            if (!hasRecentAddToCartIntent()) return;
            addToCartFiredViaAjax = true;
            syncAtcReceipts(null, 0);
            reconcileBlocksCart();
        });

        // Click fallback 
        document.addEventListener('click', function(e) {
            var btn = e.target.closest(
                '.add_to_cart_button, .single_add_to_cart_button, [name="add-to-cart"], ' +
                '.wc-block-components-product-button__button, a[href*="add-to-cart="]'
            );
            if (!btn) return;

            // AJAX-enabled button
            if (btn.classList.contains('ajax_add_to_cart')) return;

            var hasVariationsForm = !!document.querySelector('form.cart.variations_form');
            var variationInfo = getSelectedVariationInfo();
            var productId = '';
            var productPrice = 0;
            var productName = '';
            var attributes = null;

            if (variationInfo) {
                productId = (cfg.content_id_format === 'sku' && variationInfo.sku) ? variationInfo.sku : String(variationInfo.id);
                if (variationInfo.price !== null) {
                    productPrice = variationInfo.price;
                } else {
                    productPrice = cfg.product ? cfg.product.price : 0;
                }
                productName = cfg.product ? cfg.product.name : '';
                attributes = variationInfo.attributes;
            } else if (hasVariationsForm) {
                return;
            } else if (cfg.product) {
                productId = (cfg.content_id_format === 'sku' && cfg.product.sku) ? cfg.product.sku : String(cfg.product.id);
                productPrice = cfg.product.price;
                productName = cfg.product.name;
            } else {
                var pid = btn.getAttribute('data-product_id') ||
                    btn.getAttribute('data-product-id') ||
                    btn.getAttribute('value') ||
                    getQueryParamFromUrl(btn.getAttribute('href') || '', 'add-to-cart') ||
                    '';
                productId = pid;
                if (cfg.content_id_format === 'sku' && btn.getAttribute('data-product_sku')) {
                    productId = btn.getAttribute('data-product_sku');
                }
                productPrice = parseFloat(btn.getAttribute('data-product_price') || 0);
                productName = btn.getAttribute('data-product_name') || '';
            }

            if (!productId) return;

            var item = {
                id: String(productId),
                content_id: String(productId),
                content_type: 'product',
                content_name: productName,
                content_category: (cfg.product ? cfg.product.category : '') || '',
                quantity: 1,
                item_price: productPrice,
                price: productPrice
            };
            if (attributes) {
                item.attributes = attributes;
            }

            if (!eventOnce('AddToCart:' + String(productId) + ':' + currentPathKey(), 2)) return;
            clearCheckoutMarkers();
            // A normal form submission navigates away before success is known.
            // The server receipt is consumed after the response or next page load.
            setTimeout(function() { syncAtcReceipts(null, 0); }, 700);
        });

        document.addEventListener('click', function(e) {
            if (!isOnePageMode()) return;
            var cta = e.target.closest('[data-buykori-addtocart], [data-buykori-atc], .buykorigw-addtocart-intent');
            if (!cta) return;
            if (cta.closest('.add_to_cart_button, .single_add_to_cart_button')) return;
            var surface = cta.closest('[data-buykori-product], [data-product_id], [data-product-id], .product, .wc-block-grid__product') || cta;
            var data = getProductDataFromElement(surface);
            if (!data) return;
            if (!eventOnce('AddToCartIntent:' + String(data.id) + ':' + currentPathKey(), 2)) return;
            clearCheckoutMarkers();
            var payload = productPayloadFromData(data);
            if (payload) {
                payload.trigger_reason = 'landing_cta_click';
                syncAtcReceipts(payload, 0);
            }
        }, true);
    }

    // ─── 4. InitiateCheckout ───────────────────────────────────────────
    function checkoutPayload(reason) {
        var checkoutData = cfg.cart || {};
        var value = parseFloat(checkoutData.value || 0);
        var contents = checkoutData.contents || [];
        var contentIds = checkoutData.content_ids || [];
        var numItems = parseInt(checkoutData.num_items || 0, 10);

        if (value === 0 && cfg.product) {
            var contentIdFormat = cfg.content_id_format || 'id';
            var productId = (contentIdFormat === 'sku' && cfg.product.sku) ? cfg.product.sku : String(cfg.product.id);
            var productPrice = parseFloat(cfg.product.price || 0);

            var variationInfo = getSelectedVariationInfo();
            if (variationInfo) {
                productId = (contentIdFormat === 'sku' && variationInfo.sku) ? variationInfo.sku : String(variationInfo.id);
                if (variationInfo.price !== null) {
                    productPrice = variationInfo.price;
                }
            }

            contentIds = [productId];
            contents = [{
                id: productId,
                content_id: productId,
                content_type: 'product',
                content_name: cfg.product.name,
                content_category: cfg.product.category || '',
                quantity: 1,
                item_price: productPrice,
                price: productPrice
            }];
            value = productPrice;
            numItems = 1;
        }

        return {
            content_ids: contentIds,
            contents: contents,
            content_type: 'product',
            value: value,
            currency: checkoutData.currency || (cfg.product ? cfg.product.currency : 'BDT'),
            num_items: numItems,
            trigger_reason: reason || ''
        };
    }

    function normalizeRecoveryPhone(phone) {
        var digits = String(phone || '').replace(/[^0-9]/g, '');
        if (digits.length === 11 && digits.indexOf('01') === 0) return '88' + digits;
        if (digits.length === 10 && digits.indexOf('1') === 0) return '880' + digits;
        if (digits.length === 13 && digits.indexOf('8801') === 0) return digits;
        return '';
    }

    var incompleteCheckoutTimer = null;
    function scheduleIncompleteCheckoutCapture() {
        if (!cfg.incomplete_checkout_url || !window.fetch || isThankYouFlowPage()) return;
        clearTimeout(incompleteCheckoutTimer);
        incompleteCheckoutTimer = setTimeout(captureIncompleteCheckout, 1200);
    }

    function captureIncompleteCheckout() {
        var customer = getCustomerData();
        var phone = normalizeRecoveryPhone(customer.ph || '');
        if (!phone) return;
        var checkout = checkoutPayload('incomplete_checkout_capture');
        var campaignData = {};
        ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'].forEach(function(key) {
            var value = getQueryParam(key);
            if (value) campaignData[key] = value;
        });
        var addressParts = [
            getFieldValue(['#billing_address_1', 'input[name="billing_address_1"]']),
            getFieldValue(['#billing_address_2', 'input[name="billing_address_2"]']),
            getFieldValue(['#billing_city', 'input[name="billing_city"]']),
            getFieldValue(['#billing_state', 'select[name="billing_state"], input[name="billing_state"]'])
        ].filter(Boolean);
        fetch(cfg.incomplete_checkout_url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-WP-Nonce': cfg.rest_nonce || ''
            },
            body: JSON.stringify({
                visitor_id: getExternalId(),
                phone: phone,
                customer_name: [customer.fn || '', customer.ln || ''].join(' ').trim(),
                email: customer.em || '',
                address: addressParts.join(', '),
                products: checkout.contents || [],
                amount: checkout.value || 0,
                currency: checkout.currency || 'BDT',
                page_url: window.location.href,
                campaign_data: campaignData
            })
        }).catch(function() {});
    }

    var initiateCheckoutSent = false;
    function sendInitiateCheckoutOnce(reason, synchronous) {
        if (!cfg.events || !cfg.events.checkout) return;
        if (isThankYouFlowPage()) return;
        if (initiateCheckoutSent) return;
        if (hasRecentInitiateCheckoutMarker()) {
            initiateCheckoutSent = true;
            return;
        }
        initiateCheckoutSent = true;
        sendEvent('InitiateCheckout', checkoutPayload(reason), !!synchronous);
    }

    function sendInitiateCheckoutWhenReady(reason, force, synchronous) {
        if (force || hasStrongCustomerData()) {
            sendInitiateCheckoutOnce(reason, synchronous);
        }
    }

    function hasCheckoutCartData() {
        var checkoutData = cfg.cart || {};
        if (parseFloat(checkoutData.value || 0) > 0) return true;
        if (parseInt(checkoutData.num_items || 0, 10) > 0) return true;
        if (checkoutData.content_ids && checkoutData.content_ids.length) return true;
        if (checkoutData.contents && checkoutData.contents.length) return true;
        if (cfg.product && parseFloat(cfg.product.price || 0) > 0) return true;
        return isCheckoutFlowPage();
    }

    function sendInitiateCheckoutOnSurface(reason) {
        // Checkout/shipping page visibility is not enough conversion intent.
        // Real InitiateCheckout should wait for trusted customer input, place order,
        // or checkout form submission so multi-step stores do not fire on page load.
        return;
    }

    function scheduleCheckoutSurfaceChecks(prefix) {
        if (!isCheckoutFlowPage()) return;
        setTimeout(function() {
            sendInitiateCheckoutOnSurface((prefix || 'checkout') + '_page_load');
        }, 1200);
        setTimeout(function() {
            sendInitiateCheckoutOnSurface((prefix || 'checkout') + '_delayed_page_load');
        }, 4000);
    }

    function hasCheckoutSurface() {
        return !!(
            document.querySelector('body.woocommerce-checkout, form.checkout, form.woocommerce-checkout, form[name="checkout"], .woocommerce-checkout, .wc-block-checkout, #customer_details, #order_review, #place_order, .wcf-embed-checkout-form, .cartflows-checkout, [data-block-name="woocommerce/checkout"], .wp-block-woocommerce-checkout, .wc-block-checkout__form')
            || document.querySelector('#billing_email, #billing_phone, input[name="billing_email"], input[name="billing_phone"]')
        );
    }

    var checkoutIntentBound = false;
    function bindCheckoutIntentTracking() {
        if (checkoutIntentBound) return;
        checkoutIntentBound = true;
        var intentSelector = [
            '#billing_email',
            '#billing_phone',
            '#billing_first_name',
            'input[name^="billing_"]',
            'select[name^="billing_"]',
            'textarea[name^="order_"]',
            'input[autocomplete="email"]',
            'input[autocomplete="tel"]',
            'input[autocomplete="given-name"]',
            'input[autocomplete="family-name"]',
            '.woocommerce-checkout input',
            '.woocommerce-checkout select',
            '.wc-block-checkout input',
            '.wc-block-checkout select',
            '.wcf-embed-checkout-form input',
            '.wcf-embed-checkout-form select',
            '.cartflows-checkout input',
            '.cartflows-checkout select'
        ].join(', ');

        function maybeFireFromField(e) {
            if (!e.isTrusted) return;
            var target = e.target;
            if (!target || !target.matches || !target.matches(intentSelector)) return;
            if (target.type === 'hidden' || target.type === 'checkbox' || target.type === 'radio') return;
            sendInitiateCheckoutWhenReady('checkout_field_input', false);
            scheduleIncompleteCheckoutCapture();
        }

        document.addEventListener('input', maybeFireFromField, true);
        document.addEventListener('change', maybeFireFromField, true);
        document.addEventListener('click', function(e) {
            if (!e.isTrusted) return;
            if (e.target.closest('#place_order, .wc-block-components-checkout-place-order-button, [name="woocommerce_checkout_place_order"]')) {
                sendInitiateCheckoutWhenReady('place_order_click', true, true);
            }
            if (e.target.closest('.checkout-button, .wc-forward[href*="checkout"], a[href*="checkout"], [data-buykori-checkout], [data-buykori-checkout-intent], .buykorigw-checkout-intent, .wcf-next-button, .wcf-embed-checkout-form button[type="submit"]')) {
                sendInitiateCheckoutWhenReady('checkout_button_click', false, true);
            }
        }, true);
        document.addEventListener('submit', function(e) {
            if (!e.isTrusted) return;
            if (e.target.matches('form.checkout, form.woocommerce-checkout, .woocommerce-checkout form')) {
                sendInitiateCheckoutWhenReady('checkout_submit', true, true);
            }
        }, true);

        function validateEmail(email) {
            return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
        }
        function validatePhone(phone) {
            return !!normalizeRecoveryPhone(phone);
        }

        document.addEventListener('blur', function(e) {
            var target = e.target;
            if (!target || !target.matches) return;
            var isEmail = target.matches('#billing_email, input[name="billing_email"], input[type="email"]');
            var isPhone = target.matches('#billing_phone, input[name="billing_phone"], input[type="tel"]');
            if (isEmail || isPhone) {
                var val = String(target.value).trim();
                if (isEmail && validateEmail(val)) {
                    sendEvent('Identify', { em: val });
                } else if (isPhone && validatePhone(val)) {
                    sendEvent('Identify', { ph: val });
                    scheduleIncompleteCheckoutCapture();
                }
            }
        }, true);
    }

    function isCheckoutFlowPage() {
        if (isThankYouFlowPage()) return false;
        if (cfg.page_type === 'checkout' || !!pageContext.has_checkout || bodyHasClass('woocommerce-checkout')) return true;
        var path = (window.location && window.location.pathname ? window.location.pathname : '').toLowerCase();
        if (path.match(/checkout|checkouts|order-pay/) && !path.match(/order-received|thank-you/)) return true;
        return hasCheckoutSurface();
    }

    if (cfg.events && cfg.events.checkout) {
        bindCheckoutIntentTracking();
        scheduleCheckoutSurfaceChecks('checkout');

        document.addEventListener('DOMContentLoaded', function() {
            bindCheckoutIntentTracking();
            scheduleCheckoutSurfaceChecks('checkout_dom_ready');
        });

        setTimeout(function() {
            bindCheckoutIntentTracking();
            scheduleCheckoutSurfaceChecks('checkout_late_bind');
        }, 1200);

        if (typeof MutationObserver !== 'undefined') {
            var checkoutObserverTimeout;
            var checkoutObserver = new MutationObserver(function() {
                if (initiateCheckoutSent && checkoutIntentBound) {
                    checkoutObserver.disconnect();
                    return;
                }
                clearTimeout(checkoutObserverTimeout);
                checkoutObserverTimeout = setTimeout(function() {
                    if (isCheckoutFlowPage()) {
                        bindCheckoutIntentTracking();
                        if (initiateCheckoutSent && checkoutIntentBound) {
                            checkoutObserver.disconnect();
                        }
                    }
                }, 150);
            });
            checkoutObserver.observe(document.body, { childList: true, subtree: true });
        }
    }

    // ─── 5. ViewCart (WooCommerce Cart Page) ───────────────────────────
    if (cfg.events && cfg.events.viewcart && cfg.page_type === 'cart') {
        var cartData = cfg.cart || {};
        sendEvent('ViewCart', {
            content_ids: cartData.content_ids || [],
            contents: cartData.contents || [],
            content_type: 'product',
            value: cartData.value || 0,
            currency: cartData.currency || 'BDT',
            num_items: cartData.num_items || 0
        });
    }

    // ─── 6. RemoveFromCart ─────────────────────────────────────────────
    if (cfg.events && cfg.events.removefromcart) {
        var removeFiredViaJQ = false;

        if (typeof jQuery !== 'undefined') {
            jQuery(document.body).on('removed_from_cart', function(e, fragments, hash, $btn) {
                removeFiredViaJQ = true;
                var pid = $btn ? $btn.attr('data-product_id') : '';
                sendEvent('RemoveFromCart', {
                    content_ids: [pid || ''],
                    content_type: 'product'
                });
            });

            jQuery(document.body).on('wc-blocks_removed_from_cart', function() {
                removeFiredViaJQ = true;
                sendEvent('RemoveFromCart', { content_type: 'product' });
            });
        }

        document.addEventListener('click', function(e) {
            var btn = e.target.closest('a.remove[data-product_id], .remove_from_cart_button');

            if (!btn) {
                btn = e.target.closest('button.wc-block-cart-item__remove-link, [aria-label*="Remove"]');
            }

            if (!btn) return;

            setTimeout(function() {
                if (removeFiredViaJQ) { removeFiredViaJQ = false; return; }
                var pid = btn.getAttribute('data-product_id') || '';

                var isClassicRemove = btn.tagName === 'A' && btn.getAttribute('href');
                sendEvent('RemoveFromCart', {
                    content_ids: [pid],
                    content_type: 'product'
                }, isClassicRemove);
            }, 0);
        });
    }

    // ─── 7. AddPaymentInfo ─────────────────────────────────────────────
    if (cfg.events && cfg.events.addpaymentinfo && isCheckoutFlowPage()) {
        var paymentFired = false;
        var lastPaymentIntentAt = 0;
        var paymentIntentWindowMs = 3000;

        function getPaymentControl(target) {
            if (!target || !target.closest) return null;

            var control = target.closest(
                'input[name="payment_method"], ' +
                'input[name="radio-control-wc-payment-method-options"], ' +
                '.wc-block-components-radio-control__input'
            );
            if (control) return control;

            var label = target.closest('label[for]');
            if (label) {
                var labelledControl = document.getElementById(label.getAttribute('for'));
                if (labelledControl && labelledControl.matches && labelledControl.matches(
                    'input[name="payment_method"], input[name="radio-control-wc-payment-method-options"], .wc-block-components-radio-control__input'
                )) {
                    return labelledControl;
                }
            }

            var option = target.closest('.wc-block-components-radio-control__option');
            if (option) {
                return option.querySelector(
                    'input[name="payment_method"], input[name="radio-control-wc-payment-method-options"], .wc-block-components-radio-control__input'
                );
            }

            return null;
        }

        function markPaymentIntent() {
            lastPaymentIntentAt = Date.now();
        }

        function hasRecentPaymentIntent() {
            return Date.now() - lastPaymentIntentAt <= paymentIntentWindowMs;
        }

        function fireAddPaymentInfo(method, reason) {
            if (paymentFired) return;
            if (!eventOnce('AddPaymentInfo:' + currentPathKey(), 1800)) return;
            paymentFired = true;
            var paymentData = cfg.cart || {};
            sendEvent('AddPaymentInfo', {
                payment_method: method || '',
                content_ids: paymentData.content_ids || [],
                contents: paymentData.contents || [],
                content_type: 'product',
                value: paymentData.value || 0,
                currency: paymentData.currency || 'BDT',
                num_items: paymentData.num_items || 0,
                trigger_reason: reason || 'payment_method_intent'
            });
        }

        function handlePaymentIntent(e) {
            if (!e.isTrusted) return;
            var control = getPaymentControl(e.target);
            if (!control) return;
            markPaymentIntent();
            fireAddPaymentInfo(control.value || '', e.type === 'change' ? 'payment_method_change' : 'payment_method_click');
        }

        document.addEventListener('click', handlePaymentIntent, true);
        document.addEventListener('change', handlePaymentIntent, true);
        document.addEventListener('keydown', function(e) {
            if (!e.isTrusted) return;
            if (e.key !== 'Enter' && e.key !== ' ') return;
            var control = getPaymentControl(e.target);
            if (!control) return;
            markPaymentIntent();
            fireAddPaymentInfo(control.value || '', 'payment_method_keyboard');
        }, true);

        if (typeof jQuery !== 'undefined') {
            jQuery(document.body).on('payment_method_selected', function() {
                if (!hasRecentPaymentIntent()) return;
                var sel = document.querySelector(
                    'input[name="payment_method"]:checked, ' +
                    '.wc-block-components-radio-control__input:checked, ' +
                    'input[name="radio-control-wc-payment-method-options"]:checked'
                );
                fireAddPaymentInfo(sel ? sel.value : '', 'payment_method_selected');
            });
        }
    }

    // ─── 8. Lead (Form Submissions) ────────────────────────────────────
    if (cfg.events && cfg.events.lead) {
        document.addEventListener('submit', function(e) {
            var form = e.target;
            if (form.classList.contains('woocommerce-checkout') ||
                form.classList.contains('woocommerce-cart-form') ||
                form.getAttribute('role') === 'search') return;
            if (form.id === 'loginform' || form.id === 'registerform') return;
            sendEvent('Lead', {});
        });
    }

    // ─── 9. Search ─────────────────────────────────────────────────────
    if (cfg.events && cfg.events.search && cfg.page_type === 'search' && cfg.search_string) {
        sendEvent('Search', { search_string: cfg.search_string });
    }

})();
