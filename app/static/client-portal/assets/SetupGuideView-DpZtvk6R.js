import{ax as E,U,at as e,G as j,a2 as y,m as w,p as B,C as c,n as l,c as L}from"./vendor-bundle-Znbnv-r4.js";/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */const $=[{q:"How does Buykori keep tracking when ad blockers are active?",a:"Buykori sends important store events from your WordPress server instead of relying only on browser scripts. This helps Meta, TikTok, and Google receive purchase data even when a visitor uses an ad blocker."},{q:"Why are my events showing as 'Retrying' or 'Failed'?",a:"This usually means a platform key, pixel ID, or account setting needs attention. Open the event details, check the platform response, then update the matching settings if needed."},{q:"How does Buykori prevent double-counted events?",a:"Buykori sends the same event name and event ID through both browser and server tracking. Ad platforms use that match to count one real customer action instead of two."},{q:"What does match quality mean?",a:"Match quality shows how well an ad platform can connect an event to the right customer. Email, phone, browser, and location signals can improve reporting and ad optimization."}];function W({faqExpanded:k,setFaqExpanded:N,copiedStates:n,handleCopy:r,setActivePage:_,api_key:S,public_key:C,pluginReleaseInfo:i}){const[o,d]=E.useState("wordpress");U.useEffect(()=>{const a=t=>{const s=t.detail;if(s?.pageId!=="setup-guide")return;const T=s.sectionId==="setup-shopify"?"shopify":s.sectionId==="setup-custom"?"custom":"wordpress";d(T),window.requestAnimationFrame(()=>{document.getElementById(s.sectionId)?.scrollIntoView({behavior:"smooth",block:"start"})})};return window.addEventListener("buykori:page-section",a),()=>window.removeEventListener("buykori:page-section",a)},[]);const m=S?.trim()||"",u=C?.trim()||"",x=u.length>0,p=(()=>{const{protocol:a,hostname:t,origin:s}=window.location;return t==="client.buykori.app"||t==="buykori.app"||t==="www.buykori.app"?"https://api.buykori.app":t.startsWith("client.")?`${a}//${t.replace(/^client\./,"api.")}`:s})(),h=`${p}/api/v1`,P=`${p}/c`,I=`${h}/plugin/download`,A=i?.package_size?Math.round(i.package_size/1024):0,b=`// Buykori AdSync Custom Pixel Tracking Code
// Place this code in Shopify Settings > Customer Events > Custom Pixels

const API_KEY = "${u||"YOUR_PUBLIC_TRACKER_KEY"}";
const API_URL = "${P}";

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
});`,g=`<script src="${p}/t.js?key=${u||"YOUR_PUBLIC_TRACKER_KEY"}" defer><\/script>`,f=`// 1. Identify User (before firing events, e.g. on checkout, login, or registration)
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
});

// Fire only after meaningful checkout intent, not on checkout page load.
const checkoutId = crypto.randomUUID();
capi('track', 'InitiateCheckout', {
  value: 1450,
  currency: 'BDT',
  content_ids: ['prod_99'],
  content_type: 'product'
}, { eventId: \`checkout:\${checkoutId}\` });

// Use the exact same eventId for browser Purchase and server Purchase.
capi('track', 'Purchase', {
  value: 1450,
  currency: 'BDT',
  order_id: 'order_78891'
}, { eventId: 'order_78891' });

// Add ?buykori_debug=1 to the page URL to inspect SDK activity.`,v=`// Server-to-Server Conversions API (e.g. Node.js / Laravel / Python)
// POST ${h}/events
// Header: X-API-Key: ${m||"YOUR_API_KEY"}
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
}`;return e.jsxs("div",{className:"space-y-6",children:[e.jsxs("div",{className:"flex border-b border-slate-200  bg-white  rounded-xl p-1.5 shadow-sm",children:[e.jsxs("button",{onClick:()=>d("wordpress"),className:`flex items-center justify-center gap-2 flex-1 py-2.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${o==="wordpress"?"bg-indigo-600 text-white shadow-sm":"text-slate-500 hover:text-slate-800   hover:bg-slate-50 "}`,children:[e.jsx(j,{className:"w-4 h-4"}),e.jsx("span",{children:"WordPress / WooCommerce"})]}),e.jsxs("button",{onClick:()=>d("shopify"),className:`flex items-center justify-center gap-2 flex-1 py-2.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${o==="shopify"?"bg-indigo-600 text-white shadow-sm":"text-slate-500 hover:text-slate-800   hover:bg-slate-50 "}`,children:[e.jsx(y,{className:"w-4 h-4"}),e.jsx("span",{children:"Shopify Store"})]}),e.jsxs("button",{onClick:()=>d("custom"),className:`flex items-center justify-center gap-2 flex-1 py-2.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${o==="custom"?"bg-indigo-600 text-white shadow-sm":"text-slate-500 hover:text-slate-800   hover:bg-slate-50 "}`,children:[e.jsx(w,{className:"w-4 h-4"}),e.jsx("span",{children:"Custom Website"})]})]}),o==="wordpress"&&e.jsxs("div",{id:"setup-wordpress",className:"scroll-mt-24 rounded-xl border border-slate-200 bg-white p-6 shadow-sm   animate-fadeIn",children:[e.jsxs("div",{className:"mb-6",children:[e.jsxs("h2",{className:"font-bold text-slate-800 text-base uppercase tracking-wider  flex items-center gap-2",children:[e.jsx(j,{className:"w-5 h-5 text-indigo-500"}),"WooCommerce Tracking Setup"]}),e.jsx("p",{className:"text-xs text-slate-400  mt-1",children:"Set up tracking on your WordPress site in under 5 minutes."})]}),e.jsxs("div",{className:"space-y-8 relative before:absolute before:left-4 before:top-2 before:bottom-2 before:w-0.5 before:bg-slate-100 ",children:[e.jsxs("div",{className:"flex gap-4 relative",children:[e.jsx("div",{className:"w-8 h-8 rounded-full bg-indigo-100  border-2 border-white  flex items-center justify-center text-xs font-bold text-indigo-700  shadow-sm shrink-0",children:"1"}),e.jsxs("div",{className:"space-y-2 flex-1",children:[e.jsx("h3",{className:"font-bold text-slate-800 text-sm ",children:"Download and Install WordPress Helper Plugin"}),e.jsxs("p",{className:"text-xs text-slate-500  max-w-3xl leading-relaxed",children:["Download the pre-configured plugin, then go to ",e.jsx("b",{children:"WordPress Admin > Plugins > Add New > Upload Plugin"}),". Upload the ZIP and activate it."]}),e.jsxs("a",{href:I,className:"inline-flex items-center gap-2 px-3 py-1.5 rounded text-xs font-semibold border transition-colors bg-indigo-600 text-white border-indigo-700 hover:bg-indigo-700","aria-disabled":!1,children:[e.jsx(B,{className:"w-3.5 h-3.5"}),"Download Plugin ZIP"]}),i&&e.jsxs("p",{className:"text-[11px] text-slate-500 ",children:["Latest release v",i.version," / tested up to WordPress ",i.tested," / ",A," KB"]})]})]}),e.jsxs("div",{className:"flex gap-4 relative",children:[e.jsx("div",{className:"w-8 h-8 rounded-full bg-indigo-100  border-2 border-white  flex items-center justify-center text-xs font-bold text-indigo-700  shadow-sm shrink-0",children:"2"}),e.jsxs("div",{className:"space-y-2 flex-1",children:[e.jsx("h3",{className:"font-bold text-slate-800 text-sm ",children:"Connect Buykori Account"}),e.jsxs("p",{className:"text-xs text-slate-500  max-w-3xl leading-relaxed",children:["Open ",e.jsx("b",{children:"Buykori AdSync"})," settings inside WordPress and click ",e.jsx("b",{children:"Connect Buykori Account"}),". Login, approve the site, and the plugin will save its server configuration automatically."]})]})]}),e.jsxs("div",{className:"flex gap-4 relative",children:[e.jsx("div",{className:"w-8 h-8 rounded-full bg-indigo-100  border-2 border-white  flex items-center justify-center text-xs font-bold text-indigo-700  shadow-sm shrink-0",children:"3"}),e.jsxs("div",{className:"space-y-2 flex-1",children:[e.jsx("h3",{className:"font-bold text-slate-800 text-sm ",children:"Run WordPress Connection Test"}),e.jsxs("p",{className:"text-xs text-slate-500  max-w-3xl leading-relaxed",children:["After authorization, use the plugin's ",e.jsx("b",{children:"Test Connection"})," button to confirm everything is connected."]})]})]}),e.jsxs("div",{className:"flex gap-4 relative",children:[e.jsx("div",{className:"w-8 h-8 rounded-full bg-indigo-100  border-2 border-white  flex items-center justify-center text-xs font-bold text-indigo-700  shadow-sm shrink-0",children:"4"}),e.jsxs("div",{className:"space-y-2 flex-1",children:[e.jsx("h3",{className:"font-bold text-slate-800 text-sm ",children:"Send a Test Event"}),e.jsx("p",{className:"text-xs text-slate-500  max-w-3xl leading-relaxed",children:"Send a test event to make sure everything is working."}),e.jsx("button",{onClick:()=>_("campaign-builder"),className:"px-3 py-1.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200/50 rounded text-xs font-semibold shrink-0 cursor-pointer    ",children:"Go to Campaign Helper"})]})]})]})]}),o==="shopify"&&e.jsxs("div",{id:"setup-shopify",className:"scroll-mt-24 rounded-xl border border-slate-200 bg-white p-6 shadow-sm   animate-fadeIn space-y-6",children:[e.jsxs("div",{children:[e.jsxs("h2",{className:"font-bold text-slate-800 text-base uppercase tracking-wider  flex items-center gap-2",children:[e.jsx(y,{className:"w-5 h-5 text-indigo-500"}),"Shopify Tracking Setup"]}),e.jsx("p",{className:"text-xs text-slate-400  mt-1",children:"Set up Shopify browser events and order webhooks."})]}),e.jsxs("div",{className:"space-y-3",children:[e.jsxs("div",{className:"flex items-center gap-2",children:[e.jsx("span",{className:"flex items-center justify-center w-6 h-6 rounded-full bg-indigo-100  text-xs font-bold text-indigo-700 ",children:"1"}),e.jsx("h3",{className:"font-bold text-slate-800 text-sm ",children:"Step 1: Install Custom Pixel"})]}),e.jsxs("p",{className:"text-xs text-slate-500  leading-relaxed max-w-4xl",children:["Navigate to ",e.jsx("b",{children:"Shopify Admin > Settings > Customer Events"}),". Click ",e.jsx("b",{children:"Add custom pixel"}),", give it a name (e.g., ",e.jsx("code",{children:"Buykori AdSync"}),"), and paste the following tracking script inside the editor block:"]}),e.jsxs("div",{className:"relative rounded-lg overflow-hidden border border-slate-200 ",children:[e.jsxs("div",{className:"bg-slate-50  px-4 py-2 border-b border-slate-200  flex items-center justify-between text-xs text-slate-500",children:[e.jsx("span",{children:"Shopify Custom Pixel JavaScript"}),e.jsxs("button",{onClick:()=>r(b,"shopify_px"),className:"flex items-center gap-1 hover:text-indigo-600  cursor-pointer",children:[n.shopify_px?e.jsx(c,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(l,{className:"w-3.5 h-3.5"}),e.jsx("span",{children:n.shopify_px?"Copied":"Copy"})]})]}),e.jsx("pre",{tabIndex:0,"aria-label":"Shopify custom pixel JavaScript",className:"p-4 bg-slate-50  text-xs font-mono overflow-x-auto max-h-72 text-slate-700 outline-none focus:ring-2 focus:ring-indigo-400",children:e.jsx("code",{children:b})})]}),!x&&e.jsx("p",{className:"text-xs text-amber-700  max-w-4xl leading-relaxed",children:"Public tracker key has not loaded for this account. Refresh the portal before installing the Shopify pixel."})]}),e.jsxs("div",{className:"space-y-3 pt-2",children:[e.jsxs("div",{className:"flex items-center gap-2",children:[e.jsx("span",{className:"flex items-center justify-center w-6 h-6 rounded-full bg-indigo-100  text-xs font-bold text-indigo-700 ",children:"2"}),e.jsx("h3",{className:"font-bold text-slate-800 text-sm ",children:"Step 2: Set Up Shopify Webhooks"})]}),e.jsxs("p",{className:"text-xs text-slate-500  leading-relaxed max-w-4xl",children:["To capture ",e.jsx("b",{children:"Purchase"})," events reliably, send Shopify order creation alerts to Buykori:"]}),e.jsxs("div",{className:"bg-slate-50  rounded-xl p-4 border border-slate-200/60  space-y-3 text-xs",children:[e.jsxs("ul",{className:"list-disc pl-5 space-y-2 text-slate-600 ",children:[e.jsxs("li",{children:["Go to ",e.jsx("b",{children:"Shopify Admin > Settings > Notifications"}),", scroll down to the ",e.jsx("b",{children:"Webhooks"})," section."]}),e.jsxs("li",{children:["Click ",e.jsx("b",{children:"Create webhook"}),"."]}),e.jsxs("li",{children:["Choose Event: ",e.jsx("b",{children:"Order creation"})," (or ",e.jsx("code",{children:"orders/create"}),")."]}),e.jsxs("li",{children:["Format: ",e.jsx("b",{children:"JSON"}),"."]}),e.jsx("li",{children:"Paste the URL below inside the webhook destination endpoint:"})]}),e.jsxs("div",{className:"flex items-center gap-2 bg-slate-100  p-2 border border-slate-200  rounded font-mono text-xs text-slate-800  max-w-xl",children:[e.jsx("code",{className:"truncate",children:`${h}/webhook/shopify?key=${m||"YOUR_API_KEY"}`}),e.jsx("button",{onClick:()=>r(`${h}/webhook/shopify?key=${m||"YOUR_API_KEY"}`,"sh_wh_url"),className:"text-slate-400 hover:text-indigo-600 ml-auto shrink-0 cursor-pointer",title:"Copy Shopify Webhook URL",children:n.sh_wh_url?e.jsx(c,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(l,{className:"w-3.5 h-3.5"})})]})]})]})]}),o==="custom"&&e.jsxs("div",{id:"setup-custom",className:"scroll-mt-24 rounded-xl border border-slate-200 bg-white p-6 shadow-sm   animate-fadeIn space-y-6",children:[e.jsxs("div",{children:[e.jsxs("h2",{className:"font-bold text-slate-800 text-base uppercase tracking-wider  flex items-center gap-2",children:[e.jsx(w,{className:"w-5 h-5 text-indigo-500"}),"Custom Website Tracking Setup"]}),e.jsx("p",{className:"text-xs text-slate-400  mt-1",children:"Add Buykori tracking to React, Next.js, Laravel, or any custom website."})]}),e.jsxs("div",{className:"space-y-3",children:[e.jsxs("div",{className:"flex items-center gap-2",children:[e.jsx("span",{className:"flex items-center justify-center w-6 h-6 rounded-full bg-indigo-100  text-xs font-bold text-indigo-700 ",children:"1"}),e.jsx("h3",{className:"font-bold text-slate-800 text-sm ",children:"1. Add Browser Tracking"})]}),e.jsxs("p",{className:"text-xs text-slate-500  leading-relaxed max-w-4xl",children:["Paste the script below inside your website's main layout or ",e.jsx("code",{children:"<head>"})," block to start tracking page views:"]}),e.jsxs("div",{className:"flex items-center gap-2 bg-slate-50  p-2.5 border border-slate-200  rounded font-mono text-xs text-slate-800 ",children:[e.jsx("code",{className:"truncate",children:g}),e.jsx("button",{onClick:()=>x&&r(g,"c_script"),disabled:!x,className:"text-slate-400 hover:text-indigo-600 ml-auto shrink-0 cursor-pointer",title:"Copy Script Tag",children:n.c_script?e.jsx(c,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(l,{className:"w-3.5 h-3.5"})})]}),!x&&e.jsx("p",{className:"text-xs text-amber-700  max-w-4xl leading-relaxed",children:"Public tracker key has not loaded for this account. Refresh the portal before copying the browser script."}),e.jsxs("p",{className:"text-xs text-slate-500  leading-relaxed max-w-4xl pt-1",children:["To send custom events or identify customers, call the ",e.jsx("code",{children:"capi()"})," function:"]}),e.jsxs("div",{className:"relative rounded-lg overflow-hidden border border-slate-200 ",children:[e.jsxs("div",{className:"bg-slate-50  px-4 py-2 border-b border-slate-200  flex items-center justify-between text-xs text-slate-500",children:[e.jsx("span",{children:"Browser Tracking Example"}),e.jsxs("button",{onClick:()=>r(f,"custom_capi"),className:"flex items-center gap-1 hover:text-indigo-600  cursor-pointer",children:[n.custom_capi?e.jsx(c,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(l,{className:"w-3.5 h-3.5"}),e.jsx("span",{children:"Copy"})]})]}),e.jsx("pre",{tabIndex:0,"aria-label":"Browser tracking JavaScript example",className:"p-4 bg-slate-50  text-xs font-mono overflow-x-auto text-slate-700 outline-none focus:ring-2 focus:ring-indigo-400",children:e.jsx("code",{children:f})})]})]}),e.jsxs("div",{className:"space-y-3 pt-2",children:[e.jsxs("div",{className:"flex items-center gap-2",children:[e.jsx("span",{className:"flex items-center justify-center w-6 h-6 rounded-full bg-indigo-100  text-xs font-bold text-indigo-700 ",children:"2"}),e.jsx("h3",{className:"font-bold text-slate-800 text-sm ",children:"2. Send Events From Your Server"})]}),e.jsx("p",{className:"text-xs text-slate-500  leading-relaxed max-w-4xl",children:"Send checkout completions, subscriptions, or leads directly from your server using your API key:"}),e.jsxs("div",{className:"relative rounded-lg overflow-hidden border border-slate-200 ",children:[e.jsxs("div",{className:"bg-slate-50  px-4 py-2 border-b border-slate-200  flex items-center justify-between text-xs text-slate-500",children:[e.jsx("span",{children:"Server Event Example"}),e.jsxs("button",{onClick:()=>r(v,"custom_backend"),className:"flex items-center gap-1 hover:text-indigo-600  cursor-pointer",children:[n.custom_backend?e.jsx(c,{className:"w-3.5 h-3.5 text-emerald-500"}):e.jsx(l,{className:"w-3.5 h-3.5"}),e.jsx("span",{children:"Copy"})]})]}),e.jsx("pre",{tabIndex:0,"aria-label":"Server event cURL example",className:"p-4 bg-slate-50  text-xs font-mono overflow-x-auto text-slate-700 outline-none focus:ring-2 focus:ring-indigo-400",children:e.jsx("code",{children:v})})]})]})]}),e.jsxs("div",{className:"rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-4  ",children:[e.jsxs("div",{children:[e.jsx("h2",{className:"font-bold text-slate-800 text-sm uppercase tracking-wide ",children:"FAQ & Troubleshooting"}),e.jsx("p",{className:"text-xs text-slate-400 ",children:"Common questions and solutions"})]}),e.jsx("div",{className:"space-y-3 pt-2",children:$.map((a,t)=>{const s=k===t;return e.jsxs("div",{className:"rounded-lg border border-slate-150  overflow-hidden bg-slate-50/50 ",children:[e.jsxs("button",{onClick:()=>N(s?null:t),className:"w-full text-left px-4 py-3 bg-white hover:bg-slate-50 text-xs font-bold text-slate-700    flex items-center justify-between transition-colors cursor-pointer",children:[e.jsx("span",{children:a.q}),e.jsx(L,{className:`w-4 h-4 text-slate-400 transition-transform ${s?"rotate-180":""}`})]}),s&&e.jsx("div",{className:"p-4 border-t border-slate-150  text-xs leading-relaxed text-slate-500  bg-white  max-w-4xl",children:a.a})]},t)})})]})]})}export{W as SetupGuideView};
