"""
Tracker SDK Generator — ডায়নামিক JavaScript ট্র্যাকার কোড জেনারেট করে।
প্রতি ক্লায়েন্টের API Key embed করে কাস্টমাইজড JS রিটার্ন করে।
"""
import re


def generate_tracker_js(api_key: str, gateway_origin: str, ingest_token: str = "") -> str:
    # Strict regex validation for api_key to prevent injection
    if not api_key or not re.match(r"^[a-zA-Z0-9_\-]+$", api_key):
        raise ValueError("Invalid API Key format")

    # Strict regex validation for gateway_origin to prevent Host Header Injection and XSS
    if not gateway_origin or not re.match(r"^https?://[a-zA-Z0-9.\-]+(?::[0-9]+)?$", gateway_origin):
        raise ValueError("Invalid gateway origin format")

    """
    API Key ও AdSync API URL embed করে মিনিফাইড-স্টাইল JavaScript কোড রিটার্ন করে।
    - _fbc / _fbp cookie capture
    - SHA-256 hashing (browser-native SubtleCrypto)
    - Beacon API with fetch fallback
    - Unique event_id generation
    - SPA (History API) support
    - capi('track', ...) and capi('setUser', ...) global API
    """
    return f"""(function(){{
"use strict";

/* ═══════════════════════════════════════════════════════════════════
   Buykori AdSync Tracker v1.0
   Auto-tracks PageView, captures cookies, hashes PII.
   ═══════════════════════════════════════════════════════════════════ */

var K="{api_key}";
var E="{gateway_origin}/c";
var T="{ingest_token}";
var U={{}};  // user identity store
var Q=[];   // event queue (before DOM ready)
var R=false; // ready flag
var lastPageViewUrl='';
var D=qp('buykori_debug')==='1';
persistMarketing();
persistTtclid();

/* ─── Helpers ──────────────────────────────────────────────────── */

// UUID-like event ID generator
function uid(){{
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,function(c){{
    var r=Math.random()*16|0;
    return(c==='x'?r:(r&0x3|0x8)).toString(16);
  }});
}}

// Cookie reader
function gc(n){{
  var m=document.cookie.match(new RegExp('(?:^|; )'+n+'=([^;]*)'));
  return m?decodeURIComponent(m[1]):null;
}}

// Query param reader
function qp(n){{
  try{{return new URLSearchParams(location.search).get(n)||'';}}
  catch(e){{return '';}}
}}

function persistTtclid(){{
  var id=qp('ttclid');
  if(id)document.cookie='_ttclid='+encodeURIComponent(id)+'; path=/; max-age='+(90*24*60*60)+'; SameSite=Lax';
}}

function persistMarketing(){{
  ['utm_source','utm_medium','utm_campaign','utm_content','utm_term','campaign_source'].forEach(function(k){{
    var v=qp(k);
    if(v)document.cookie='_buykorigw_'+k+'='+encodeURIComponent(v)+'; path=/; max-age='+(30*24*60*60)+'; SameSite=Lax';
  }});
}}

function marketing(){{
  var out={{}};
  ['utm_source','utm_medium','utm_campaign','utm_content','utm_term','campaign_source'].forEach(function(k){{
    out[k]=qp(k)||gc('_buykorigw_'+k)||'';
  }});
  if(!out.campaign_source&&out.utm_source)out.campaign_source=out.utm_source;
  if(!out.utm_source&&(qp('ttclid')||gc('_ttclid'))){{out.utm_source='tiktok';out.campaign_source='tiktok';}}
  if(!out.utm_source&&gc('_fbc')){{out.utm_source='facebook';out.campaign_source='facebook';}}
  return out;
}}

// SHA-256 hash (returns promise)
function sha(s){{
  if(!s)return Promise.resolve(null);
  s=s.trim().toLowerCase();
  if(/^[a-f0-9]{{64}}$/.test(s))return Promise.resolve(s); // already hashed
  var enc=new TextEncoder().encode(s);
  return crypto.subtle.digest('SHA-256',enc).then(function(buf){{
    return Array.from(new Uint8Array(buf)).map(function(b){{
      return b.toString(16).padStart(2,'0');
    }}).join('');
  }});
}}

// Hash multiple values → array
function hashArr(arr){{
  if(!arr||!arr.length)return Promise.resolve(null);
  return Promise.all(arr.map(function(v){{return sha(v);}}));
}}

function debug(message,data){{
  if(D&&typeof console!=='undefined')console.info('[Buykori AdSync] '+message,data||'');
}}

/* ─── Send Event ───────────────────────────────────────────────── */
function send(eventName, customData, userData, eventId){{
  if(eventName==='PageView'){{
    if(lastPageViewUrl===location.href){{
      debug('Suppressed duplicate PageView',location.href);
      return;
    }}
    lastPageViewUrl=location.href;
  }}
  var fbc=gc('_fbc');
  var fbp=gc('_fbp');
  var ttp=gc('_ttp');
  var ttclid=qp('ttclid')||gc('_ttclid');

  // Build user_data
  var ud={{}};
  ud.client_user_agent=navigator.userAgent;
  if(fbc)ud.fbc=fbc;
  if(fbp)ud.fbp=fbp;
  if(ttp)ud.ttp=ttp;
  if(ttclid)ud.ttclid=ttclid;

  // Merge stored user identity
  var mergedUser=Object.assign({{}},U,userData||{{}});

  // Hash PII fields, then send
  var hashJobs=[];
  var piiFields=['em','ph','fn','ln','ct','st','zp','country'];

  piiFields.forEach(function(f){{
    if(mergedUser[f]){{
      var vals=Array.isArray(mergedUser[f])?mergedUser[f]:[mergedUser[f]];
      hashJobs.push(
        hashArr(vals).then(function(hashed){{
          if(hashed)ud[f]=hashed;
        }})
      );
    }}
  }});

  // Copy non-PII fields
  if(mergedUser.external_id){{
    var eid=Array.isArray(mergedUser.external_id)?mergedUser.external_id:[mergedUser.external_id];
    ud.external_id=eid;
  }}

  Promise.all(hashJobs).then(function(){{
    var evt={{
      event_name:eventName,
      event_time:Math.floor(Date.now()/1000),
      event_id:eventId||uid(),
      event_source_url:location.href,
      action_source:'website',
      user_data:ud
    }};

    if(customData){{
      var cd=marketing();
      if(customData.value!=null)cd.value=Number(customData.value);
      if(customData.currency)cd.currency=customData.currency;
      if(customData.content_ids)cd.content_ids=customData.content_ids;
      if(customData.content_type)cd.content_type=customData.content_type;
      if(customData.order_id)cd.order_id=customData.order_id;
      if(customData.num_items!=null)cd.num_items=Number(customData.num_items);
      evt.custom_data=cd;
    }}else{{
      evt.custom_data=marketing();
    }}

    var body=JSON.stringify({{data:[evt]}});
    debug('Sending '+eventName,{{event_id:evt.event_id,source_url:evt.event_source_url}});

    // Prefer sendBeacon (works even on page unload)
    if(navigator.sendBeacon){{
      var blob=new Blob([body],{{type:'application/json'}});
      var ok=navigator.sendBeacon(E+'?key='+K+(T?'&token='+encodeURIComponent(T):''),blob);
      if(ok)debug('Queued with sendBeacon',evt.event_id);
      else fallbackFetch(body,evt);
    }}else{{
      fallbackFetch(body,evt);
    }}
  }}).catch(function(err){{
    // Silently fail — never break client's website
    if(typeof console!=='undefined')console.warn('[CAPI]',err);
  }});
}}

function fallbackFetch(body,evt){{
  fetch(E+'?key='+K+(T?'&token='+encodeURIComponent(T):''),{{
    method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body:body,
    keepalive:true
  }}).then(function(response){{
    debug('Fetch completed',{{event_id:evt&&evt.event_id,status:response.status}});
  }}).catch(function(error){{
    debug('Fetch failed',{{event_id:evt&&evt.event_id,error:String(error)}});
  }});
}}

/* ─── Global API ───────────────────────────────────────────────── */
function capi(cmd){{
  if(cmd==='track'){{
    var evtName=arguments[1];
    var cd=arguments[2]||null;
    var fourth=arguments[3]||null;
    var opts=fourth&&((fourth.eventId||fourth.event_id||fourth.userData||fourth.user_data))?fourth:null;
    var ud=opts?(opts.userData||opts.user_data||null):fourth;
    var eventId=opts?(opts.eventId||opts.event_id||null):null;
    if(!R){{Q.push([evtName,cd,ud,eventId]);return;}}
    send(evtName,cd,ud,eventId);
  }}
  else if(cmd==='setUser'){{
    var data=arguments[1]||{{}};
    // Convenience: 'email' → 'em', 'phone' → 'ph'
    if(data.email){{data.em=[data.email];delete data.email;}}
    if(data.phone){{data.ph=[data.phone];delete data.phone;}}
    if(data.first_name){{data.fn=[data.first_name];delete data.first_name;}}
    if(data.last_name){{data.ln=[data.last_name];delete data.last_name;}}
    if(data.city){{data.ct=[data.city];delete data.city;}}
    if(data.state){{data.st=[data.state];delete data.state;}}
    if(data.zip){{data.zp=[data.zip];delete data.zip;}}
    Object.assign(U,data);
  }}
}}

// Expose globally
window.capi=capi;

/* ─── SPA Support (History API) ────────────────────────────────── */
if(history.pushState && !history.pushState.__isWrapped){{
  var origPush=history.pushState;
  history.pushState=function(){{
    origPush.apply(this,arguments);
    setTimeout(function(){{send('PageView');}},100);
  }};
  history.pushState.__isWrapped=true;
}}
if(history.replaceState && !history.replaceState.__isWrapped){{
  var origReplace=history.replaceState;
  history.replaceState=function(){{
    origReplace.apply(this,arguments);
    setTimeout(function(){{send('PageView');}},100);
  }};
  history.replaceState.__isWrapped=true;
}}
if(!window.__capi_popstate_registered){{
  window.addEventListener('popstate',function(){{
    setTimeout(function(){{send('PageView');}},100);
  }});
  window.__capi_popstate_registered=true;
}}

/* ─── Init ─────────────────────────────────────────────────────── */
function init(){{
  R=true;
  // Fire auto PageView
  send('PageView');
  // Flush queued events
  Q.forEach(function(q){{send(q[0],q[1],q[2],q[3]);}});
  Q=[];
}}

if(document.readyState==='loading'){{
  document.addEventListener('DOMContentLoaded',init);
}}else{{
  init();
}}

}})();"""
