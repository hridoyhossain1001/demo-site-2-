(function() {
    'use strict';

    var cfg = window.buykorigw_config || {};
    if (!cfg.ajax_url && !cfg.rest_url) return;
    var trackingMode = cfg.tracking_mode || 'standard';
    persistMarketingParams();
    persistTikTokClickId();
    ensureFirstPartyCookies();
    initializeHybridPixels();

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
        return trackingMode === 'one_page';
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
        return (window.location.pathname || '/').replace(/[^a-zA-Z0-9_-]+/g, '_');
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

    // ─── First-Party Cookie Helpers ──────────────────────────────────────
    function ensureFirstPartyCookies() {
        if (!getCookie('_fbp')) {
            var fbp = 'fb.1.' + Date.now() + '.' + Math.floor(Math.random() * 9000000000 + 1000000000);
            setCookieLocal('_fbp', fbp, 90);
        }
        var fbclid = getQueryParam('fbclid');
        if (fbclid) {
            var fbc = 'fb.1.' + Date.now() + '.' + fbclid;
            setCookieLocal('_fbc', fbc, 90);
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
    function sendEvent(eventName, eventData, synchronous) {
        eventData = normalizeEventData(eventData || {});

        var ga4ClientId = getGA4ClientId();
        var ga4SessionId = getGA4SessionId();
        if (ga4ClientId) eventData['_ga'] = ga4ClientId;
        if (ga4SessionId) eventData['ga_session_id'] = ga4SessionId;
        if (!eventData.page_location) eventData.page_location = window.location.href;
        if (!eventData.page_path) eventData.page_path = window.location.pathname + window.location.search;

        var eventId = '';
        if (eventName === 'InitiateCheckout') {
            var ic_marker_id = getCookie('_buykorigw_ic_event_id');
            eventId = ic_marker_id || ('wp_' + eventName + '_' + Math.floor(Date.now() / 1000) + '_' + Math.floor(Math.random() * 9000 + 1000));
            markInitiateCheckoutSent(eventId);
        } else {
            eventId = 'wp_' + eventName + '_' + Math.floor(Date.now() / 1000) + '_' + Math.floor(Math.random() * 9000 + 1000);
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

        // Parallel Client-side Pixel Trigger (with identical eventId for perfect deduplication)
        if (cfg.enable_hybrid && eventName !== 'Identify') {
            var browserParams = {};
            if (eventData.value !== undefined) browserParams.value = parseFloat(eventData.value);
            if (eventData.currency !== undefined) browserParams.currency = eventData.currency;
            if (eventData.content_name !== undefined) browserParams.content_name = eventData.content_name;
            if (eventData.content_type !== undefined) browserParams.content_type = eventData.content_type;
            if (eventData.content_ids !== undefined) browserParams.content_ids = eventData.content_ids;
            if (eventData.contents !== undefined) browserParams.contents = eventData.contents;

            // 1. Meta Pixel
            if (window.fbq && cfg.fb_pixel_id) {
                fbq('track', eventName, browserParams, { eventID: eventId });
            }
            // 2. TikTok Pixel
            if (window.ttq && cfg.tt_pixel_id) {
                ttq.track(eventName, browserParams, { event_id: eventId });
            }
        }

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
            var headers = {'Content-Type': 'application/json'};
            if (cfg.rest_nonce) {
                headers['X-WP-Nonce'] = cfg.rest_nonce;
            }
            fetch(cfg.rest_url, {
                method: 'POST',
                headers: headers,
                body: jsonBody,
                keepalive: true
            }).then(function(response) {
                if (!response || !response.ok) {
                    sendViaAjax(eventName, eventData, eventId);
                }
            }).catch(function() {
                sendViaAjax(eventName, eventData, eventId);
            });
        } else {
            sendViaAjax(eventName, eventData, eventId);
        }
    }

    function buildAjaxFormData(eventName, eventData, eventId) {
        var fd = new FormData();
        fd.append('action', 'buykorigw_track_event');
        fd.append('nonce', cfg.nonce);
        fd.append('event_name', eventName);
        fd.append('event_id', eventId || '');
        fd.append('event_data', JSON.stringify(eventData));
        fd.append('page_url', window.location.href);
        fd.append('page_title', document.title);
        fd.append('fbp', getCookie('_fbp') || '');
        fd.append('fbc', getCookie('_fbc') || '');
        fd.append('ttp', getCookie('_ttp') || '');
        fd.append('ttclid', getTikTokClickId());
        fd.append('external_id', getExternalId());
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
            out.contents = out.contents.filter(function(item) {
                if (!item) return false;
                var id = item.content_id || item.id;
                return id && isValidContentId(id);
            });
            if (!out.contents.length) delete out.contents;
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
        return !!(data.em && data.ph);
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
    if (cfg.events && cfg.events.pageview && eventOnce('PageView:' + currentPathKey(), 30)) {
        sendEvent('PageView', {});
    }

    // ─── 2. ViewContent (Product Page) ─────────────────────────────────
    function sendViewContentOnce() {
        if (!cfg.events || !cfg.events.viewcontent || cfg.page_type !== 'product' || !cfg.product) return;
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

    if (cfg.events && cfg.events.viewcontent && cfg.page_type === 'product' && cfg.product) {
        if (isOnePageMode() && typeof IntersectionObserver !== 'undefined') {
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
            } else {
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
        if (cfg.page_type === 'product' && cfg.product) return;
        var selector = [
            '[data-buykori-product]',
            '[data-product_id]',
            '[data-product-id]',
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
        document.addEventListener('DOMContentLoaded', observeLandingProductCards);
    } else {
        observeLandingProductCards();
    }

    // ─── 3. AddToCart ──────────────────────────────────────────────────
    if (cfg.events && cfg.events.addtocart) {
        var addToCartFiredViaAjax = false;

        // jQuery AJAX event — most reliable (fires AFTER WooCommerce confirms add)
        if (typeof jQuery !== 'undefined') {
            jQuery(document.body).on('added_to_cart', function(e, fragments, hash, $btn) {
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
                sendEvent('AddToCart', {
                    content_ids: [String(pid)],
                    contents: [item],
                    content_name: pname || (cfg.product ? cfg.product.name : ''),
                    content_type: 'product',
                    value: pprice,
                    currency: (cfg.product ? cfg.product.currency : 'BDT')
                });
            });
        }

        // Click fallback 
        document.addEventListener('click', function(e) {
            var btn = e.target.closest('.add_to_cart_button, .single_add_to_cart_button');
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
                var pid = btn.getAttribute('data-product_id') || '';
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
            sendEvent('AddToCart', {
                content_ids: [String(productId)],
                contents: [item],
                content_name: productName,
                content_type: 'product',
                content_category: (cfg.product ? cfg.product.category : '') || '',
                value: productPrice,
                currency: cfg.product ? cfg.product.currency : 'BDT'
            });
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
                sendEvent('AddToCart', payload);
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

    var initiateCheckoutSent = false;
    function sendInitiateCheckoutOnce(reason, synchronous) {
        if (!cfg.events || !cfg.events.checkout) return;
        if (isThankYouFlowPage()) return;
        if (initiateCheckoutSent) return;
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
        if (isOnePageMode()) return;
        if (isThankYouFlowPage()) return;
        if (!hasCheckoutSurface()) return;
        sendInitiateCheckoutWhenReady(reason || 'checkout_surface_ready', hasCheckoutCartData());
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
            document.querySelector('form.checkout, form.woocommerce-checkout, .woocommerce-checkout, .wc-block-checkout, #customer_details, #order_review, #place_order')
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
            '.wc-block-checkout select'
        ].join(', ');

        function maybeFireFromField(e) {
            var target = e.target;
            if (!target || !target.matches || !target.matches(intentSelector)) return;
            if (target.type === 'hidden' || target.type === 'checkbox' || target.type === 'radio') return;
            sendInitiateCheckoutWhenReady('checkout_field_input', hasCheckoutCartData());
        }

        document.addEventListener('input', maybeFireFromField, true);
        document.addEventListener('change', maybeFireFromField, true);
        document.addEventListener('focusin', maybeFireFromField, true);
        document.addEventListener('click', function(e) {
            if (e.target.closest('#place_order, .wc-block-components-checkout-place-order-button, [name="woocommerce_checkout_place_order"]')) {
                sendInitiateCheckoutWhenReady('place_order_click', true, true);
            }
            if (e.target.closest('.checkout-button, .wc-forward[href*="checkout"], a[href*="checkout"], [data-buykori-checkout], [data-buykori-checkout-intent], .buykorigw-checkout-intent, .wcf-next-button, .wcf-submit-coupon, .wcf-embed-checkout-form button[type="submit"]')) {
                sendInitiateCheckoutWhenReady('checkout_button_click', hasCheckoutCartData(), true);
            }
        }, true);
        document.addEventListener('submit', function(e) {
            if (e.target.matches('form.checkout, form.woocommerce-checkout, .woocommerce-checkout form')) {
                sendInitiateCheckoutWhenReady('checkout_submit', true, true);
            }
        }, true);

        if (window.jQuery && window.jQuery(document.body).on) {
            window.jQuery(document.body).on('init_checkout updated_checkout checkout_place_order', function(e) {
                if (isOnePageMode() && (!e || e.type !== 'checkout_place_order')) return;
                sendInitiateCheckoutWhenReady(e && e.type ? e.type : 'woocommerce_checkout_event', true);
            });
        }

        function validateEmail(email) {
            return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
        }
        function validatePhone(phone) {
            var clean = String(phone).replace(/[^0-9]/g, '');
            return clean.length >= 8;
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
                }
            }
        }, true);
    }

    function isCheckoutFlowPage() {
        if (isThankYouFlowPage()) return false;
        if (cfg.page_type === 'checkout') return true;
        var path = (window.location && window.location.pathname ? window.location.pathname : '').toLowerCase();
        if (path.indexOf('checkout') !== -1 && !path.match(/order-received|order-pay|thank-you/)) return true;
        return hasCheckoutSurface();
    }

    if (cfg.events && cfg.events.checkout) {
        bindCheckoutIntentTracking();
        if (!isOnePageMode()) scheduleCheckoutSurfaceChecks('checkout');

        document.addEventListener('DOMContentLoaded', function() {
            bindCheckoutIntentTracking();
            if (!isOnePageMode()) scheduleCheckoutSurfaceChecks('checkout');
        });

        setTimeout(function() {
            bindCheckoutIntentTracking();
            if (!isOnePageMode()) scheduleCheckoutSurfaceChecks('checkout');
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
                        if (!isOnePageMode()) sendInitiateCheckoutOnSurface('checkout_surface_ready');
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
    if (cfg.events && cfg.events.addpaymentinfo && cfg.page_type === 'checkout') {
        var paymentFired = false;

        function fireAddPaymentInfo(method) {
            if (paymentFired) return;
            paymentFired = true;
            var paymentData = cfg.cart || {};
            sendEvent('AddPaymentInfo', {
                payment_method: method || '',
                content_ids: paymentData.content_ids || [],
                contents: paymentData.contents || [],
                content_type: 'product',
                value: paymentData.value || 0,
                currency: paymentData.currency || 'BDT',
                num_items: paymentData.num_items || 0
            });
        }

        document.addEventListener('change', function(e) {
            if (e.target.name === 'payment_method') {
                fireAddPaymentInfo(e.target.value);
            }
        });

        if (typeof jQuery !== 'undefined') {
            jQuery(document.body).on('payment_method_selected', function() {
                var sel = document.querySelector('.wc-block-components-radio-control__input:checked');
                fireAddPaymentInfo(sel ? sel.value : '');
            });
        }

        if (typeof MutationObserver !== 'undefined') {
            var payObserver = new MutationObserver(function() {
                var checked = document.querySelector(
                    '.wc-block-components-radio-control__input:checked, ' +
                    'input[name="radio-control-wc-payment-method-options"]:checked'
                );
                if (checked) fireAddPaymentInfo(checked.value);
            });
            var payContainer = document.querySelector('.wc-block-checkout, #payment');
            if (payContainer) {
                payObserver.observe(payContainer, { subtree: true, attributes: true, childList: true });
            }
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
