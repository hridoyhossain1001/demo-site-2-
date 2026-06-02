import{at as E,ap as e,G as j,$ as w,l as y,o as U,C as o,m as n,b as L}from"./vendor-bundle-DZPdbHSa.js";/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */const R=[{q:"How does Conversions API bypass client-side ad blockers?",a:"Unlike browser trackers that are blocked by browser lists (like EasyList), server-side events are routed from your self-hosted WordPress server directly to our cloud servers, which connect to Meta, TikTok, and Google via secure HTTP API requests on a back-channel. This bypasses ad-block extensions, Brave Shields, and content filters entirely."},{q:"Why are my events showing as 'Retrying' or 'Failed'?",a:"This normally indicates a credential issue or that the target platform's API endpoint returned a non-200 response (e.g., expired access token or invalid pixel size configuration). Click on the event in the Logs page to see the exact HTTP response code and payload details, then verify your Platform Credentials."},{q:"How does Deduplication work with browser-side pixel tracking?",a:"To prevent double-counting when utilizing both a browser pixel and server-side tracking, we transmit a matching `Event ID` and `Name` on both channels. Meta/TikTok matches these identifiers. If both are received within 48 hours, the browser event is usually preferred and the server event is deduplicated, ensuring safe reporting."},{q:"What does Event Match Quality mean in Meta CAPI?",a:"Match quality represents how many customer identifiers (like email hashes, phone numbers, state, country, IP address, user agents) were attached to your event. Passing more data points helps Meta locate the exact customer profile, raising your optimization from ~40% to ~90% for purchase tracking."}];function O({faqExpanded:N,setFaqExpanded:_,copiedStates:s,handleCopy:a,setActivePage:P,api_key:C,public_key:S,pluginReleaseInfo:c}){const[d,b]=E.useState("wordpress"),r=C?.trim()||"",i=r.length>0,u=S?.trim()||"",h=u.length>0,p=(()=>{const{protocol:m,hostname:t,origin:x}=window.location;return t==="client.buykori.app"||t==="buykori.app"||t==="www.buykori.app"?"https://api.buykori.app":t.startsWith("client.")?`${m}//${t.replace(/^client\./,"api.")}`:x})(),l=`${p}/api/v1`,A=`${p}/c`,I=`${l}/plugin/download${i?`?api_key=${encodeURIComponent(r)}`:""}`,T=c?.package_size?Math.round(c.package_size/1024):0,g=`// Buykori AdSync Custom Pixel Tracking Code
// Place this code in Shopify Settings > Customer Events > Custom Pixels

const API_KEY = "${u||"YOUR_PUBLIC_TRACKER_KEY"}";
const API_URL = "${A}";

// Helper to generate a unique event ID for deduplication
function generateEventId() {
  return 'sh_' + Date.now() + '_' + Math.floor(Math.random() * 1000000);
}

// Subscribe to PageView
analytics.subscribe("page_viewed", (event) => {
  const eventId = generateEventId();
  fetch(API_URL + "?key=" + API_KEY, {
    method: "POST",
    keepalive: true,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      data: [{
        event_name: "PageView",
        event_time: Math.floor(event.timestamp / 1000),
        event_id: eventId,
        event_source_url: event.context.document.location.href,
        action_source: "website",
        user_data: {
          client_user_agent: event.context.navigator.userAgent,
          client_ip_address: "8.8.8.8" // Server will enrich with real client IP
        }
      }]
    })
  }).catch(() => {});
});

// Subscribe to AddToCart
analytics.subscribe("product_added_to_cart", (event) => {
  const eventId = generateEventId();
  const cartLine = event.data?.cartLine;
  const merchandise = cartLine?.merchandise;
  
  fetch(API_URL + "?key=" + API_KEY, {
    method: "POST",
    keepalive: true,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      data: [{
        event_name: "AddToCart",
        event_time: Math.floor(event.timestamp / 1000),
        event_id: eventId,
        event_source_url: event.context.document.location.href,
        action_source: "website",
        custom_data: {
          value: cartLine?.cost?.totalAmount?.amount ? Number(cartLine.cost.totalAmount.amount) : 0,
          currency: cartLine?.cost?.totalAmount?.currencyCode || "BDT",
          content_ids: merchandise?.id ? [String(merchandise.id)] : [],
          content_type: "product",
          num_items: cartLine?.quantity || 1
        },
        user_data: {
          client_user_agent: event.context.navigator.userAgent
        }
      }]
    })
  }).catch(() => {});
});

// Subscribe to Checkout Started
analytics.subscribe("checkout_started", (event) => {
  const eventId = generateEventId();
  const checkout = event.data?.checkout;
  
  fetch(API_URL + "?key=" + API_KEY, {
    method: "POST",
    keepalive: true,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      data: [{
        event_name: "InitiateCheckout",
        event_time: Math.floor(event.timestamp / 1000),
        event_id: eventId,
        event_source_url: event.context.document.location.href,
        action_source: "website",
        custom_data: {
          value: checkout?.totalPrice?.amount ? Number(checkout.totalPrice.amount) : 0,
          currency: checkout?.totalPrice?.currencyCode || "BDT",
          content_ids: checkout?.lineItems?.map(item => String(item.variant?.id || '')) || [],
          content_type: "product"
        },
        user_data: {
          client_user_agent: event.context.navigator.userAgent,
          em: checkout?.email ? [checkout.email] : undefined,
          ph: checkout?.phone ? [checkout.phone] : undefined
        }
      }]
    })
  }).catch(() => {});
});`,k=`<script src="${p}/t.js?key=${u||"YOUR_PUBLIC_TRACKER_KEY"}" defer><\/script>`,v=`// 1. Identify User (before firing events, e.g. on checkout, login, or registration)
capi('setUser', {
  email: 'customer@domain.com', // Will be hashed automatically using SHA-256 inside browser
  phone: '8801700000000',
  first_name: 'Hridoy',
  last_name: 'Hossain'
});

// 2. Track Standard Event
capi('track', 'AddToCart', {
  value: 1450,
  currency: 'BDT',
  content_ids: ['prod_99'],
  content_type: 'product'
});`,f=`// Server-to-Server Conversions API (e.g. Node.js / Laravel / Python)
// POST ${l}/events
// Header: X-API-Key: ${r||"YOUR_API_KEY"}
// Header: Content-Type: application/json

{
  "data": [
    {
      "event_name": "Purchase",
      "event_time": 1716912000,
      "event_id": "order_78891", // Used for deduplication
      "event_source_url": "https://yoursite.com/checkout/thank-you",
      "action_source": "website",
      "user_data": {
        "client_ip_address": "103.112.56.2",
        "client_user_agent": "Mozilla/5.0...",
        "em": ["f660ab912e..."], // SHA-256 Hashed Email
        "ph": ["88017000..."]    // SHA-256 Hashed Phone
      },
      "custom_data": {
        "value": 1500.0,
        "currency": "BDT",
        "order_id": "78891",
        "content_type": "product",
        "contents": [
          { "id": "prod_99", "quantity": 1, "item_price": 1500.0 }
        ]
      }
    }
  ]
}`;return e.jsxs("div",{className:"space-y-6",children:[e.jsxs("div",{className:"flex border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 rounded-xl p-1.5 shadow-sm",children:[e.jsxs("button",{onClick:()=>b("wordpress"),className:`flex items-center justify-center gap-2 flex-1 py-2.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${d==="wordpress"?"bg-indigo-600 text-white shadow-sm":"text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-white hover:bg-slate-50 dark:hover:bg-slate-800"}`,children:[e.jsx(j,{className:"w-4 h-4"}),e.jsx("span",{children:"WordPress / WooCommerce"})]}),e.jsxs("button",{onClick:()=>b("shopify"),className:`flex items-center justify-center gap-2 flex-1 py-2.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${d==="shopify"?"bg-indigo-600 text-white shadow-sm":"text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-white hover:bg-slate-50 dark:hover:bg-slate-800"}`,children:[e.jsx(w,{className:"w-4 h-4"}),e.jsx("span",{children:"Shopify Store"})]}),e.jsxs("button",{onClick:()=>b("custom"),className:`flex items-center justify-center gap-2 flex-1 py-2.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${d==="custom"?"bg-indigo-600 text-white shadow-sm":"text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-white hover:bg-slate-50 dark:hover:bg-slate-800"}`,children:[e.jsx(y,{className:"w-4 h-4"}),e.jsx("span",{children:"Custom Website"})]})]}),d==="wordpress"&&e.jsxs("div",{className:"rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:bg-slate-900 dark:border-slate-800 animate-fadeIn",children:[e.jsxs("div",{className:"mb-6",children:[e.jsxs("h2",{className:"font-bold text-slate-800 text-base uppercase tracking-wider dark:text-white flex items-center gap-2",children:[e.jsx(j,{className:"w-5 h-5 text-indigo-500"}),"WooCommerce Conversions API Integration Setup"]}),e.jsx("p",{className:"text-xs text-slate-400 dark:text-slate-500 mt-1",children:"Deploy Conversions tracking client nodes inside your self-hosted WordPress panel in under 5 minutes."})]}),e.jsxs("div",{className:"space-y-8 relative before:absolute before:left-4 before:top-2 before:bottom-2 before:w-0.5 before:bg-slate-100 dark:before:bg-slate-800",children:[e.jsxs("div",{className:"flex gap-4 relative",children:[e.jsx("div",{className:"w-8.5 h-8.5 rounded-full bg-indigo-100 dark:bg-indigo-950/40 border-2 border-white dark:border-slate-900 flex items-center justify-center text-xs font-bold text-indigo-700 dark:text-indigo-400 shadow-sm shrink-0",children:"1"}),e.jsxs("div",{className:"space-y-2 flex-1",children:[e.jsx("h4",{className:"font-bold text-slate-800 text-sm dark:text-white",children:"Download and Install WordPress Helper Plugin"}),e.jsxs("p",{className:"text-xs text-slate-500 dark:text-slate-400 max-w-3xl leading-relaxed",children:["Download the pre-configured plugin, then go to ",e.jsx("b",{children:"WordPress Admin > Plugins > Add New > Upload Plugin"}),". Upload the ZIP and activate it."]}),e.jsxs("a",{href:I,className:`inline-flex items-center gap-2 px-3 py-1.5 rounded text-xs font-semibold border transition-colors ${i?"bg-indigo-600 text-white border-indigo-700 hover:bg-indigo-700":"bg-slate-100 text-slate-400 border-slate-200 pointer-events-none dark:bg-slate-800 dark:border-slate-700"}`,"aria-disabled":!i,children:[e.jsx(U,{className:"w-3.5 h-3.5"}),"Download Plugin ZIP"]}),c&&e.jsxs("p",{className:"text-[11px] text-slate-500 dark:text-slate-400",children:["Latest release v",c.version," / tested up to WordPress ",c.tested," / ",T," KB"]}),!i&&e.jsx("p",{className:"text-xs text-amber-700 dark:text-amber-400 max-w-3xl leading-relaxed",children:"Server API key has not loaded for this account. Refresh the portal before downloading the configured plugin."})]})]}),e.jsxs("div",{className:"flex gap-4 relative",children:[e.jsx("div",{className:"w-8.5 h-8.5 rounded-full bg-indigo-100 dark:bg-indigo-950/40 border-2 border-white dark:border-slate-900 flex items-center justify-center text-xs font-bold text-indigo-700 dark:text-indigo-400 shadow-sm shrink-0",children:"2"}),e.jsxs("div",{className:"space-y-2 flex-1",children:[e.jsx("h4",{className:"font-bold text-slate-800 text-sm dark:text-white",children:"Synchronize API Access Token"}),e.jsxs("p",{className:"text-xs text-slate-500 dark:text-slate-400 max-w-3xl leading-relaxed",children:["Copy your unique API Key below and paste it in the ",e.jsx("b",{children:"Buykori AdSync"})," settings page inside your WordPress panel."]}),e.jsxs("div",{className:"flex items-center gap-2 bg-slate-50/50 dark:bg-slate-950 p-2 border border-slate-200 dark:border-slate-800 rounded font-mono text-xs text-slate-800 dark:text-slate-300 max-w-md",children:[e.jsx("code",{className:"truncate",children:i?r:"Setup token unavailable"}),e.jsx("button",{onClick:()=>i&&a(r,"c_g_tkn"),disabled:!i,className:"text-slate-400 hover:text-indigo-600 ml-auto shrink-0 cursor-pointer disabled:opacity-40",title:"Copy API Key",children:s.c_g_tkn?e.jsx(o,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(n,{className:"w-3.5 h-3.5"})})]})]})]}),e.jsxs("div",{className:"flex gap-4 relative",children:[e.jsx("div",{className:"w-8.5 h-8.5 rounded-full bg-indigo-100 dark:bg-indigo-950/40 border-2 border-white dark:border-slate-900 flex items-center justify-center text-xs font-bold text-indigo-700 dark:text-indigo-400 shadow-sm shrink-0",children:"3"}),e.jsxs("div",{className:"space-y-2 flex-1",children:[e.jsx("h4",{className:"font-bold text-slate-800 text-sm dark:text-white",children:"Set AdSync Gateway URL"}),e.jsx("p",{className:"text-xs text-slate-500 dark:text-slate-400 max-w-3xl leading-relaxed",children:"Provide the API Gateway URL in your WordPress plugin settings to establish a pipeline connection:"}),e.jsxs("div",{className:"flex items-center gap-2 bg-slate-50/50 dark:bg-slate-950 p-2 border border-slate-200 dark:border-slate-800 rounded font-mono text-xs text-slate-800 dark:text-slate-300 max-w-md",children:[e.jsx("code",{className:"truncate",children:l}),e.jsx("button",{onClick:()=>a(l,"c_g_url"),className:"text-slate-400 hover:text-indigo-600 ml-auto shrink-0 cursor-pointer",title:"Copy Gateway URL",children:s.c_g_url?e.jsx(o,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(n,{className:"w-3.5 h-3.5"})})]})]})]}),e.jsxs("div",{className:"flex gap-4 relative",children:[e.jsx("div",{className:"w-8.5 h-8.5 rounded-full bg-indigo-100 dark:bg-indigo-950/40 border-2 border-white dark:border-slate-900 flex items-center justify-center text-xs font-bold text-indigo-700 dark:text-indigo-400 shadow-sm shrink-0",children:"4"}),e.jsxs("div",{className:"space-y-2 flex-1",children:[e.jsx("h4",{className:"font-bold text-slate-800 text-sm dark:text-white",children:"Verify sandbox test telemetry trace"}),e.jsx("p",{className:"text-xs text-slate-500 dark:text-slate-400 max-w-3xl leading-relaxed",children:"Use our campaign test console to fire test telemetry packets and check the event logs."}),e.jsx("button",{onClick:()=>P("campaign-builder"),className:"px-3 py-1.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200/50 rounded text-xs font-semibold shrink-0 cursor-pointer dark:bg-indigo-950/20 dark:text-indigo-400 dark:border-indigo-900/60 dark:hover:bg-indigo-900/30",children:"Go to Campaign Sandbox"})]})]})]})]}),d==="shopify"&&e.jsxs("div",{className:"rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:bg-slate-900 dark:border-slate-800 animate-fadeIn space-y-6",children:[e.jsxs("div",{children:[e.jsxs("h2",{className:"font-bold text-slate-800 text-base uppercase tracking-wider dark:text-white flex items-center gap-2",children:[e.jsx(w,{className:"w-5 h-5 text-indigo-500"}),"Shopify Server-Side Tracking Configuration"]}),e.jsx("p",{className:"text-xs text-slate-400 dark:text-slate-500 mt-1",children:"Implement hybrid tracking for Shopify using Customer Events Pixels and Server Webhooks."})]}),e.jsxs("div",{className:"space-y-3",children:[e.jsxs("div",{className:"flex items-center gap-2",children:[e.jsx("span",{className:"flex items-center justify-center w-6 h-6 rounded-full bg-indigo-100 dark:bg-indigo-900/30 text-xs font-bold text-indigo-700 dark:text-indigo-400",children:"1"}),e.jsx("h3",{className:"font-bold text-slate-800 text-sm dark:text-white",children:"Step 1: Install Custom Pixel (Client-Side Events)"})]}),e.jsxs("p",{className:"text-xs text-slate-500 dark:text-slate-400 leading-relaxed max-w-4xl",children:["Navigate to ",e.jsx("b",{children:"Shopify Admin > Settings > Customer Events"}),". Click ",e.jsx("b",{children:"Add custom pixel"}),", give it a name (e.g., ",e.jsx("code",{children:"Buykori AdSync"}),"), and paste the following tracking script inside the editor block:"]}),e.jsxs("div",{className:"relative rounded-lg overflow-hidden border border-slate-200 dark:border-slate-800",children:[e.jsxs("div",{className:"bg-slate-50 dark:bg-slate-950 px-4 py-2 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between text-xs text-slate-500",children:[e.jsx("span",{children:"Shopify Custom Pixel JavaScript"}),e.jsxs("button",{onClick:()=>a(g,"shopify_px"),className:"flex items-center gap-1 hover:text-indigo-600 dark:hover:text-indigo-400 cursor-pointer",children:[s.shopify_px?e.jsx(o,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(n,{className:"w-3.5 h-3.5"}),e.jsx("span",{children:s.shopify_px?"Copied":"Copy"})]})]}),e.jsx("pre",{className:"p-4 bg-slate-50 dark:bg-slate-950/40 text-xs font-mono overflow-x-auto max-h-72 text-slate-700 dark:text-slate-350",children:e.jsx("code",{children:g})})]}),!h&&e.jsx("p",{className:"text-xs text-amber-700 dark:text-amber-400 max-w-4xl leading-relaxed",children:"Public tracker key has not loaded for this account. Refresh the portal before installing the Shopify pixel."})]}),e.jsxs("div",{className:"space-y-3 pt-2",children:[e.jsxs("div",{className:"flex items-center gap-2",children:[e.jsx("span",{className:"flex items-center justify-center w-6 h-6 rounded-full bg-indigo-100 dark:bg-indigo-900/30 text-xs font-bold text-indigo-700 dark:text-indigo-400",children:"2"}),e.jsx("h3",{className:"font-bold text-slate-800 text-sm dark:text-white",children:"Step 2: Setup Shopify Webhooks (Server-Side Purchases)"})]}),e.jsxs("p",{className:"text-xs text-slate-500 dark:text-slate-400 leading-relaxed max-w-4xl",children:["To reliably capture ",e.jsx("b",{children:"Purchase"})," events bypass-ready even when ad-blockers are active, route Shopify order creation alerts straight to our server webhooks:"]}),e.jsxs("div",{className:"bg-slate-50 dark:bg-slate-950/20 rounded-xl p-4 border border-slate-200/60 dark:border-slate-800 space-y-3 text-xs",children:[e.jsxs("ul",{className:"list-disc pl-5 space-y-2 text-slate-600 dark:text-slate-400",children:[e.jsxs("li",{children:["Go to ",e.jsx("b",{children:"Shopify Admin > Settings > Notifications"}),", scroll down to the ",e.jsx("b",{children:"Webhooks"})," section."]}),e.jsxs("li",{children:["Click ",e.jsx("b",{children:"Create webhook"}),"."]}),e.jsxs("li",{children:["Choose Event: ",e.jsx("b",{children:"Order creation"})," (or ",e.jsx("code",{children:"orders/create"}),")."]}),e.jsxs("li",{children:["Format: ",e.jsx("b",{children:"JSON"}),"."]}),e.jsx("li",{children:"Paste the URL below inside the webhook destination endpoint:"})]}),e.jsxs("div",{className:"flex items-center gap-2 bg-slate-100 dark:bg-slate-950 p-2 border border-slate-250 dark:border-slate-800 rounded font-mono text-xs text-slate-800 dark:text-slate-300 max-w-xl",children:[e.jsx("code",{className:"truncate",children:`${l}/webhook/shopify?key=${r||"YOUR_API_KEY"}`}),e.jsx("button",{onClick:()=>a(`${l}/webhook/shopify?key=${r||"YOUR_API_KEY"}`,"sh_wh_url"),className:"text-slate-400 hover:text-indigo-600 ml-auto shrink-0 cursor-pointer",title:"Copy Shopify Webhook URL",children:s.sh_wh_url?e.jsx(o,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(n,{className:"w-3.5 h-3.5"})})]})]})]})]}),d==="custom"&&e.jsxs("div",{className:"rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:bg-slate-900 dark:border-slate-800 animate-fadeIn space-y-6",children:[e.jsxs("div",{children:[e.jsxs("h2",{className:"font-bold text-slate-800 text-base uppercase tracking-wider dark:text-white flex items-center gap-2",children:[e.jsx(y,{className:"w-5 h-5 text-indigo-500"}),"Custom Website Tracking Integration Guide"]}),e.jsx("p",{className:"text-xs text-slate-400 dark:text-slate-500 mt-1",children:"Integrate our server-side tracking stack directly into your React, Next.js, Laravel, or custom-coded application."})]}),e.jsxs("div",{className:"space-y-3",children:[e.jsxs("div",{className:"flex items-center gap-2",children:[e.jsx("span",{className:"flex items-center justify-center w-6 h-6 rounded-full bg-indigo-100 dark:bg-indigo-900/30 text-xs font-bold text-indigo-700 dark:text-indigo-400",children:"1"}),e.jsx("h3",{className:"font-bold text-slate-800 text-sm dark:text-white",children:"1. Add Client-Side Tracker (Browser Pixel)"})]}),e.jsxs("p",{className:"text-xs text-slate-500 dark:text-slate-400 leading-relaxed max-w-4xl",children:["Paste the script tag below inside your website's main layout or template ",e.jsx("code",{children:"<head>"})," block to automatically record PageViews and initialize tracking features:"]}),e.jsxs("div",{className:"flex items-center gap-2 bg-slate-50 dark:bg-slate-950 p-2.5 border border-slate-200 dark:border-slate-800 rounded font-mono text-xs text-slate-800 dark:text-slate-300",children:[e.jsx("code",{className:"truncate",children:k}),e.jsx("button",{onClick:()=>h&&a(k,"c_script"),disabled:!h,className:"text-slate-400 hover:text-indigo-600 ml-auto shrink-0 cursor-pointer",title:"Copy Script Tag",children:s.c_script?e.jsx(o,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(n,{className:"w-3.5 h-3.5"})})]}),!h&&e.jsx("p",{className:"text-xs text-amber-700 dark:text-amber-400 max-w-4xl leading-relaxed",children:"Public tracker key has not loaded for this account. Refresh the portal before copying the browser script."}),e.jsxs("p",{className:"text-xs text-slate-500 dark:text-slate-400 leading-relaxed max-w-4xl pt-1",children:["To trigger custom events or identify users (which automatically hashes PII using secure browser crypto APIs before sending), call the ",e.jsx("code",{children:"capi()"})," function:"]}),e.jsxs("div",{className:"relative rounded-lg overflow-hidden border border-slate-200 dark:border-slate-800",children:[e.jsxs("div",{className:"bg-slate-50 dark:bg-slate-950 px-4 py-2 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between text-xs text-slate-500",children:[e.jsx("span",{children:"Frontend JavaScript API Usage"}),e.jsxs("button",{onClick:()=>a(v,"custom_capi"),className:"flex items-center gap-1 hover:text-indigo-600 dark:hover:text-indigo-400 cursor-pointer",children:[s.custom_capi?e.jsx(o,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(n,{className:"w-3.5 h-3.5"}),e.jsx("span",{children:"Copy"})]})]}),e.jsx("pre",{className:"p-4 bg-slate-50 dark:bg-slate-950/40 text-xs font-mono overflow-x-auto text-slate-700 dark:text-slate-350",children:e.jsx("code",{children:v})})]})]}),e.jsxs("div",{className:"space-y-3 pt-2",children:[e.jsxs("div",{className:"flex items-center gap-2",children:[e.jsx("span",{className:"flex items-center justify-center w-6 h-6 rounded-full bg-indigo-100 dark:bg-indigo-900/30 text-xs font-bold text-indigo-700 dark:text-indigo-400",children:"2"}),e.jsx("h3",{className:"font-bold text-slate-800 text-sm dark:text-white",children:"2. Send Server-to-Server Events (Backend CAPI)"})]}),e.jsx("p",{className:"text-xs text-slate-500 dark:text-slate-400 leading-relaxed max-w-4xl",children:"Route checkout completions, subscriptions, or leads directly from your server. Make a secure POST request to the events endpoint using your server API key:"}),e.jsxs("div",{className:"relative rounded-lg overflow-hidden border border-slate-200 dark:border-slate-800",children:[e.jsxs("div",{className:"bg-slate-50 dark:bg-slate-950 px-4 py-2 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between text-xs text-slate-500",children:[e.jsx("span",{children:"REST API Event Payload (JSON)"}),e.jsxs("button",{onClick:()=>a(f,"custom_backend"),className:"flex items-center gap-1 hover:text-indigo-600 dark:hover:text-indigo-400 cursor-pointer",children:[s.custom_backend?e.jsx(o,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(n,{className:"w-3.5 h-3.5"}),e.jsx("span",{children:"Copy"})]})]}),e.jsx("pre",{className:"p-4 bg-slate-50 dark:bg-slate-950/40 text-xs font-mono overflow-x-auto text-slate-700 dark:text-slate-350",children:e.jsx("code",{children:f})})]})]})]}),e.jsxs("div",{className:"rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-4 dark:bg-slate-900 dark:border-slate-800",children:[e.jsxs("div",{children:[e.jsx("h3",{className:"font-bold text-slate-800 text-sm uppercase tracking-wide dark:text-white",children:"Deployment FAQ & Troubleshooting"}),e.jsx("p",{className:"text-xs text-slate-400 dark:text-slate-500",children:"Technical answers for server tracking mechanics and deduplication pipelines"})]}),e.jsx("div",{className:"space-y-3 pt-2",children:R.map((m,t)=>{const x=N===t;return e.jsxs("div",{className:"rounded-lg border border-slate-150 dark:border-slate-800 overflow-hidden bg-slate-50/50 dark:bg-slate-950/20",children:[e.jsxs("button",{onClick:()=>_(x?null:t),className:"w-full text-left px-4 py-3 bg-white hover:bg-slate-50 text-xs font-bold text-slate-700 dark:text-slate-300 dark:bg-slate-900 dark:hover:bg-slate-800 flex items-center justify-between transition-colors cursor-pointer",children:[e.jsx("span",{children:m.q}),e.jsx(L,{className:`w-4 h-4 text-slate-400 transition-transform ${x?"rotate-180":""}`})]}),x&&e.jsx("div",{className:"p-4 border-t border-slate-150 dark:border-slate-800 text-xs leading-relaxed text-slate-500 dark:text-slate-400 bg-white dark:bg-slate-900 max-w-4xl",children:m.a})]},t)})})]})]})}export{O as SetupGuideView};
