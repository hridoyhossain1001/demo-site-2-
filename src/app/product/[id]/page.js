"use client";

import React, { useState, useEffect } from "react";
import { useParams } from "next/navigation";

const products = {
  1: {
    id: 1,
    name: "AeroChronos Smartwatch X",
    price: 15000,
    category: "Smart Wearables",
    desc: "Minimalist luxury smart wearable featuring glowing micro-neon displays, comprehensive biometrics, and 14-day battery life. Engineered for elite athletic performance and timeless elegance.",
    img: "/assets/smart_watch.png",
  },
  2: {
    id: 2,
    name: "AcousticMax Headphones Pro",
    price: 9500,
    category: "Audio Experience",
    desc: "Advanced noise-canceling headphones with customized matte finishes, ultra-comfortable acoustics, and deep bass performance. Features spatial audio driver tuning for studio-quality immersion.",
    img: "/assets/headphones.png",
  },
};

export default function ProductDetail() {
  const params = useParams();
  const productId = parseInt(params.id) || 1;
  const product = products[productId];

  const [qty, setQty] = useState(1);

  useEffect(() => {
    if (product && typeof window !== "undefined") {
      // Fire ViewContent event
      if (window.capi) {
        window.capi("track", "ViewContent", {
          value: product.price,
          currency: "BDT",
          content_ids: [String(product.id)],
          content_type: "product",
          content_name: product.name,
        });
      }
    }
  }, [product]);

  if (!product) {
    return (
      <main>
        <p style={{ textAlign: "center", fontSize: "1.2rem", marginTop: "4rem" }}>Product not found.</p>
      </main>
    );
  }

  const changeQty = (amount) => {
    const newVal = qty + amount;
    if (newVal >= 1) {
      setQty(newVal);
    }
  };

  const triggerAddToCart = () => {
    let cart = JSON.parse(localStorage.getItem("cart") || "[]");
    let item = cart.find((i) => i.id === product.id);

    if (item) {
      item.qty += qty;
    } else {
      cart.push({
        id: product.id,
        name: product.name,
        price: product.price,
        img: product.img,
        qty: qty,
      });
    }

    localStorage.setItem("cart", JSON.stringify(cart));
    
    // Sync badgcount
    let count = cart.reduce((sum, item) => sum + item.qty, 0);
    let badge = document.getElementById("cart-count");
    if (badge) badge.innerText = count;

    // Track AddToCart event
    if (window.capi) {
      window.capi("track", "AddToCart", {
        value: product.price * qty,
        currency: "BDT",
        content_ids: [String(product.id)],
        content_type: "product",
        num_items: qty,
      });
    }

    alert(`${qty} ${product.name}(s) added to cart!`);
  };

  return (
    <main>
      <div className="product-detail-container">
        <div className="detail-img-card">
          <img src={product.img} alt={product.name} />
        </div>
        <div className="detail-info">
          <span className="product-category">{product.category}</span>
          <h1 className="detail-title">{product.name}</h1>
          <span className="detail-price">{product.price.toLocaleString()} BDT</span>
          <p className="detail-desc">{product.desc}</p>

          <div className="detail-actions">
            <div className="quantity-selector">
              <button className="qty-btn" onClick={() => changeQty(-1)}>-</button>
              <input type="text" className="qty-input" value={qty} readOnly />
              <button class="qty-btn" onClick={() => changeQty(1)}>+</button>
            </div>
            <button className="btn" style={{ flexGrow: 1 }} onClick={triggerAddToCart}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="9" cy="21" r="1"></circle>
                <circle cx="20" cy="21" r="1"></circle>
                <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"></path>
              </svg>
              Add to Cart
            </button>
          </div>
        </div>
      </div>
    </main>
  );
}
