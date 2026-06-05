"use client";

import React, { useEffect, useState } from "react";

export default function ThankYou() {
  const [order, setOrder] = useState(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const lastOrder = JSON.parse(localStorage.getItem("lastOrder") || "null");
      setOrder(lastOrder);
      
      // Keep badge at zero
      let badge = document.getElementById("cart-count");
      if (badge) badge.innerText = "0";
    }
  }, []);

  return (
    <main>
      <div className="success-container">
        <div className="success-icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
            <polyline points="22 4 12 14.01 9 11.01"></polyline>
          </svg>
        </div>

        <h1 className="success-title">Order Confirmed!</h1>
        <p className="success-message">Thank you for your purchase. Your order has been received and is being processed (Server-Side S2S tracked).</p>

        <div className="order-details-box" id="order-receipt">
          {order ? (
            <>
              <div className="order-detail-row">
                <span>Order ID:</span>
                <span style={{ fontWeight: 600, color: "var(--accent-cyan)" }}>{order.orderId}</span>
              </div>
              <div className="order-detail-row">
                <span>Email Address:</span>
                <span>{order.email}</span>
              </div>
              <div className="order-detail-row">
                <span>Items Purchased:</span>
                <span>{order.items.reduce((sum, item) => sum + item.qty, 0)} items</span>
              </div>
              <div className="order-detail-row" style={{ marginTop: "1rem", borderTop: "1px dashed var(--border-color)", paddingTop: "1rem" }}>
                <span>Grand Total:</span>
                <span style={{ color: "white", fontSize: "1.2rem" }}>{order.total.toLocaleString()} BDT</span>
              </div>
            </>
          ) : (
            <p style={{ textAlign: "center", color: "var(--text-secondary)" }}>No active order details found.</p>
          )}
        </div>

        <a href="/" className="btn" style={{ padding: "0.9rem 2.5rem" }}>
          Continue Shopping
        </a>
      </div>
    </main>
  );
}
