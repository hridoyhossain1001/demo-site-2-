"use client";

import React, { useState, useEffect, useRef } from "react";

export default function Checkout() {
  const [cart, setCart] = useState([]);
  const [formData, setFormData] = useState({
    first_name: "",
    last_name: "",
    email: "",
    phone: "",
    address: "",
    city: "",
    state: "",
    zip: "",
    country: "BD",
  });
  const [loading, setLoading] = useState(false);
  const checkoutStarted = useRef(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const storedCart = JSON.parse(localStorage.getItem("cart") || "[]");
      setCart(storedCart);
    }
  }, []);

  const total = cart.reduce((sum, item) => sum + item.price * item.qty, 0);

  const trackCheckoutStart = () => {
    if (checkoutStarted.current || cart.length === 0 || !window.capi) return;

    const cartKey = cart
      .map((item) => `${item.id}:${item.qty}`)
      .sort()
      .join("|");
    const storageKey = `buykori_checkout_started:${cartKey}`;
    if (sessionStorage.getItem(storageKey)) {
      checkoutStarted.current = true;
      return;
    }

    const checkoutId = crypto.randomUUID();
    sessionStorage.setItem(storageKey, checkoutId);
    checkoutStarted.current = true;
    window.capi(
      "track",
      "InitiateCheckout",
      {
        value: total,
        currency: "BDT",
        content_ids: cart.map((item) => String(item.id)),
        content_type: "product",
        num_items: cart.reduce((sum, item) => sum + item.qty, 0),
      },
      { eventId: `checkout:${checkoutId}` }
    );
  };

  const handleInputChange = (e) => {
    trackCheckoutStart();
    const { id, value } = e.target;
    setFormData((prev) => ({ ...prev, [id]: value }));
  };

  const processPurchase = async (e) => {
    e.preventDefault();
    setLoading(true);

    const orderId = "ORD-NJ-" + Math.floor(100000 + Math.random() * 900000);

    // Prepare purchase payload for our backend API
    const payload = {
      orderId,
      total,
      items: cart,
      customer: formData,
    };

    try {
      // 1. Trigger SERVER-SIDE tracking by calling our Next.js API endpoint
      const response = await fetch("/api/purchase", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error("Failed to process order on server");
      }

      const resData = await response.json();

      // 2. We can also fire browser-side Purchase event with identical event_id (for client-server deduplication)
      if (window.capi) {
        // Feed user traits to pixel
        window.capi("setUser", {
          email: formData.email,
          phone: formData.phone,
          first_name: formData.first_name,
          last_name: formData.last_name,
          city: formData.city,
          state: formData.state,
          zip: formData.zip,
          country: formData.country,
        });

        window.capi(
          "track",
          "Purchase",
          {
            value: total,
            currency: "BDT",
            content_ids: cart.map((item) => String(item.id)),
            content_type: "product",
            order_id: orderId,
            num_items: cart.reduce((sum, item) => sum + item.qty, 0),
          },
          { eventId: orderId }
        );
      }

      // Store order info for success page
      localStorage.setItem(
        "lastOrder",
        JSON.stringify({
          orderId,
          total,
          email: formData.email,
          items: cart,
        })
      );

      // Clear local cart
      localStorage.removeItem("cart");
      
      // Update badge
      let badge = document.getElementById("cart-count");
      if (badge) badge.innerText = "0";

      // Redirect to thank-you
      setTimeout(() => {
        window.location.href = "/thank-you";
      }, 300);
    } catch (err) {
      alert("Order placement failed: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main>
      <div className="checkout-layout">
        {/* Billing Details Form */}
        <div className="checkout-card">
          <h2>Shipping Information</h2>
          <form id="checkout-form" onSubmit={processPurchase}>
            <div className="form-group-row">
              <div className="form-group">
                <label htmlFor="first_name">First Name</label>
                <input type="text" id="first_name" className="form-control" required placeholder="John" value={formData.first_name} onChange={handleInputChange} />
              </div>
              <div className="form-group">
                <label htmlFor="last_name">Last Name</label>
                <input type="text" id="last_name" class="form-control" required placeholder="Doe" value={formData.last_name} onChange={handleInputChange} />
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="email">Email Address</label>
              <input type="email" id="email" className="form-control" required placeholder="john.doe@example.com" value={formData.email} onChange={handleInputChange} />
            </div>

            <div className="form-group">
              <label htmlFor="phone">Phone Number</label>
              <input type="tel" id="phone" className="form-control" required placeholder="01712345678" value={formData.phone} onChange={handleInputChange} />
            </div>

            <div className="form-group">
              <label htmlFor="address">Street Address</label>
              <input type="text" id="address" className="form-control" required placeholder="123 Technology Way" value={formData.address} onChange={handleInputChange} />
            </div>

            <div className="form-group-row">
              <div className="form-group">
                <label htmlFor="city">City</label>
                <input type="text" id="city" className="form-control" required placeholder="Dhaka" value={formData.city} onChange={handleInputChange} />
              </div>
              <div className="form-group">
                <label htmlFor="state">State / Division</label>
                <input type="text" id="state" className="form-control" required placeholder="Dhaka Division" value={formData.state} onChange={handleInputChange} />
              </div>
            </div>

            <div className="form-group-row">
              <div className="form-group">
                <label htmlFor="zip">ZIP / Postal Code</label>
                <input type="text" id="zip" className="form-control" required placeholder="1212" value={formData.zip} onChange={handleInputChange} />
              </div>
              <div className="form-group">
                <label htmlFor="country">Country</label>
                <input type="text" id="country" className="form-control" required placeholder="BD" value={formData.country} onChange={handleInputChange} />
              </div>
            </div>

            <button type="submit" className="btn" style={{ width: "100%", marginTop: "1rem", padding: "1rem" }} disabled={loading || cart.length === 0}>
              {loading ? "Processing Order..." : "Place Order (Hybrid S2S)"}
            </button>
          </form>
        </div>

        {/* Order Summary Box */}
        <div className="checkout-card" style={{ height: "fit-content" }}>
          <h2>Order Summary</h2>
          <div id="cart-items-container">
            {cart.length === 0 ? (
              <p style={{ color: "var(--text-secondary)", textAlign: "center" }}>Your cart is empty.</p>
            ) : (
              cart.map((item) => (
                <div key={item.id} style={{ display: "flex", gap: "1rem", alignItems: "center", marginBottom: "1.2rem", borderBottom: "1px solid var(--border-color)", paddingBottom: "1rem" }}>
                  <img src={item.img} style={{ width: "60px", height: "60px", objectFit: "cover", borderRadius: "8px" }} alt={item.name} />
                  <div style={{ flexGrow: 1 }}>
                    <h4 style={{ fontSize: "1rem", fontWeight: 600 }}>{item.name}</h4>
                    <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem" }}>
                      Qty: {item.qty} &times; {item.price.toLocaleString()} BDT
                    </p>
                  </div>
                  <span style={{ fontWeight: 600 }}>{(item.price * item.qty).toLocaleString()} BDT</span>
                </div>
              ))
            )}
          </div>

          <div style={{ marginTop: "2rem" }}>
            <div className="summary-item">
              <span>Subtotal</span>
              <span>{total.toLocaleString()} BDT</span>
            </div>
            <div className="summary-item">
              <span>Shipping</span>
              <span>Free</span>
            </div>
            <div className="summary-total">
              <span>Total</span>
              <span>{total.toLocaleString()} BDT</span>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
