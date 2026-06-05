"use client";

import React, { useEffect } from "react";

export default function Home() {
  // Sync the badge on load
  useEffect(() => {
    if (typeof window !== "undefined") {
      let cart = JSON.parse(localStorage.getItem("cart") || "[]");
      let count = cart.reduce((sum, item) => sum + item.qty, 0);
      let badge = document.getElementById("cart-count");
      if (badge) badge.innerText = count;
    }
  }, []);

  const addToCart = (id, name, price, img) => {
    let cart = JSON.parse(localStorage.getItem("cart") || "[]");
    let item = cart.find((i) => i.id === id);

    if (item) {
      item.qty += 1;
    } else {
      cart.push({ id, name, price, img, qty: 1 });
    }

    localStorage.setItem("cart", JSON.stringify(cart));
    
    // Sync the badge
    let count = cart.reduce((sum, item) => sum + item.qty, 0);
    let badge = document.getElementById("cart-count");
    if (badge) badge.innerText = count;

    // Track AddToCart using Buykori AdSync client SDK
    if (window.capi) {
      window.capi("track", "AddToCart", {
        value: price,
        currency: "BDT",
        content_ids: [String(id)],
        content_type: "product",
      });
    }

    alert(`${name} added to cart!`);
  };

  return (
    <main>
      <section className="hero">
        <h1>Elevate Your Tech Lifestyle</h1>
        <p>Explore our curated collection of premium gadgets designed with clean aesthetics and unmatched performance.</p>
      </section>

      <section className="products-grid">
        {/* Product 1 */}
        <div className="product-card">
          <div className="product-img-wrapper">
            <img className="product-img" src="/assets/smart_watch.png" alt="AeroChronos Smartwatch X" />
          </div>
          <div className="product-info">
            <span className="product-category">Smart Wearables</span>
            <h3 className="product-title">
              <a href="/product/1">AeroChronos Smartwatch X</a>
            </h3>
            <p className="product-desc">Minimalist luxury smart wearable featuring glowing micro-neon displays, comprehensive biometrics, and 14-day battery life.</p>
            <div className="product-meta">
              <span className="product-price">15,000 BDT</span>
              <button className="btn" onClick={() => addToCart(1, "AeroChronos Smartwatch X", 15000, "/assets/smart_watch.png")}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="5" x2="12" y2="19"></line>
                  <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
                Add to Cart
              </button>
            </div>
          </div>
        </div>

        {/* Product 2 */}
        <div className="product-card">
          <div className="product-img-wrapper">
            <img className="product-img" src="/assets/headphones.png" alt="AcousticMax Headphones Pro" />
          </div>
          <div className="product-info">
            <span className="product-category">Audio Experience</span>
            <h3 className="product-title">
              <a href="/product/2">AcousticMax Headphones Pro</a>
            </h3>
            <p className="product-desc">Advanced noise-canceling headphones with customized matte finishes, ultra-comfortable acoustics, and deep bass performance.</p>
            <div className="product-meta">
              <span className="product-price">9,500 BDT</span>
              <button className="btn" onClick={() => addToCart(2, "AcousticMax Headphones Pro", 9500, "/assets/headphones.png")}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="12" y1="5" x2="12" y2="19"></line>
                  <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
                Add to Cart
              </button>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
