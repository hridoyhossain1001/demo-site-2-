import { NextResponse } from "next/server";
import crypto from "crypto";

function sha256(value) {
  if (!value) return undefined;
  const normalized = String(value).trim().toLowerCase();
  if (/^[a-f0-9]{64}$/.test(normalized)) return normalized;
  return crypto.createHash("sha256").update(normalized).digest("hex");
}

function hashedArray(value) {
  const hashed = sha256(value);
  return hashed ? [hashed] : undefined;
}

export async function POST(request) {
  try {
    const gatewayOrigin = process.env.NEXT_PUBLIC_BUYKORI_GATEWAY_URL;
    const apiKey = process.env.BUYKORI_API_KEY;
    if (!gatewayOrigin || !apiKey) {
      return NextResponse.json({ error: "Tracking is not configured" }, { status: 503 });
    }

    const { eventName, eventId, customData = {}, user = {}, sourceUrl } = await request.json();
    if (!eventName) {
      return NextResponse.json({ error: "Missing event name" }, { status: 400 });
    }

    const requestOrigin = request.headers.get("origin") || request.nextUrl.origin;
    const eventSourceUrl = sourceUrl || request.headers.get("referer") || requestOrigin;
    const event = {
      event_name: eventName,
      event_time: Math.floor(Date.now() / 1000),
      event_id: eventId || `${eventName}-${crypto.randomUUID()}`,
      event_source_url: eventSourceUrl,
      action_source: "website",
      user_data: {
        client_ip_address:
          request.headers.get("x-forwarded-for")?.split(",")[0].trim() ||
          request.headers.get("x-real-ip") ||
          "127.0.0.1",
        client_user_agent: request.headers.get("user-agent") || "",
        em: hashedArray(user.email),
        ph: hashedArray(user.phone),
        fn: hashedArray(user.first_name),
        ln: hashedArray(user.last_name),
        ct: hashedArray(user.city),
        st: hashedArray(user.state),
        zp: hashedArray(user.zip),
        country: hashedArray(user.country),
      },
      custom_data: customData,
    };

    const body = JSON.stringify({ data: [event] });
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const signature = crypto
      .createHmac("sha256", apiKey)
      .update(`${timestamp}.${body}`)
      .digest("hex");

    const response = await fetch(`${gatewayOrigin}/api/v1/events`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
        "X-CAPI-Origin": new URL(eventSourceUrl).origin,
        "X-CAPI-Timestamp": timestamp,
        "X-CAPI-Signature": signature,
      },
      body,
    });
    const responseText = await response.text();

    if (!response.ok) {
      console.error(`[Browser Tracking Error] Gateway ${response.status}: ${responseText}`);
      return NextResponse.json({ error: "Gateway rejected event" }, { status: 502 });
    }

    return NextResponse.json({ success: true, gateway: JSON.parse(responseText) });
  } catch (error) {
    console.error("[Browser Tracking Error]", error);
    return NextResponse.json({ error: "Failed to track event" }, { status: 500 });
  }
}
