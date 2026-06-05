import { NextResponse } from "next/server";
import crypto from "crypto";

// Helper function to hash values using SHA-256 (Facebook CAPI / TikTok standard)
function sha256(text) {
  if (!text) return null;
  const cleanText = text.toString().trim().toLowerCase();
  // If it's already a 64-character hex hash, return it directly
  if (/^[a-f0-9]{64}$/.test(cleanText)) {
    return cleanText;
  }
  return crypto.createHash("sha256").update(cleanText).digest("hex");
}

export async function POST(request) {
  try {
    const body = await request.json();
    const { orderId, total, items, customer } = body;

    if (!customer || !customer.email || !customer.phone) {
      return NextResponse.json({ error: "Missing required customer details" }, { status: 400 });
    }

    // 1. Capture Client IP and User Agent from request headers
    const clientUserAgent = request.headers.get("user-agent") || "";
    // Check multiple headers for the real client IP (e.g. Vercel, Cloudflare, Nginx proxies)
    const clientIpAddress =
      request.headers.get("x-forwarded-for")?.split(",")[0].trim() ||
      request.headers.get("x-real-ip") ||
      "127.0.0.1";

    const referer = request.headers.get("referer") || "https://demo-nextjs-shop.vercel.app/checkout";

    // 2. Hash sensitive customer data (PII) on the server side
    const userData = {
      client_ip_address: clientIpAddress,
      client_user_agent: clientUserAgent,
      em: [sha256(customer.email)],
      ph: [sha256(customer.phone)],
      fn: customer.first_name ? [sha256(customer.first_name)] : undefined,
      ln: customer.last_name ? [sha256(customer.last_name)] : undefined,
      ct: customer.city ? [sha256(customer.city)] : undefined,
      st: customer.state ? [sha256(customer.state)] : undefined,
      zp: customer.zip ? [sha256(customer.zip)] : undefined,
      country: customer.country ? [sha256(customer.country)] : [sha256("BD")],
    };

    // 3. Build AdSync Conversion API event payload
    const event = {
      event_name: "Purchase",
      event_time: Math.floor(Date.now() / 1000),
      event_id: orderId, // Identical event_id for browser-server deduplication
      event_source_url: referer,
      action_source: "website",
      user_data: userData,
      custom_data: {
        value: Number(total),
        currency: "BDT",
        order_id: orderId,
        content_ids: items.map((item) => String(item.id)),
        content_type: "product",
        num_items: items.reduce((sum, item) => sum + item.qty, 0),
      },
    };
    // 4. Send Server-to-Server (S2S) POST request to Buykori AdSync Conversion Gateway
    const gatewayUrl = `${process.env.NEXT_PUBLIC_BUYKORI_GATEWAY_URL}/c?key=${process.env.NEXT_PUBLIC_BUYKORI_API_KEY}`;
    console.log(`[S2S Tracking] Sending event for Order ${orderId} to Gateway...`);
    const gatewayResponse = await fetch(gatewayUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ data: [event] }),
    });

    if (!gatewayResponse.ok) {
      const errorText = await gatewayResponse.text();
      console.error(`[S2S Tracking Error] Gateway responded with status ${gatewayResponse.status}: ${errorText}`);
    } else {
      const responseData = await gatewayResponse.json();
      console.log(`[S2S Tracking Success] Gateway response:`, responseData);
    }

    return NextResponse.json({
      success: true,
      message: "Order processed successfully",
      s2s_tracked: gatewayResponse.ok,
    });
  } catch (error) {
    console.error("[Checkout Route API Error] Failed to process S2S checkout:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
