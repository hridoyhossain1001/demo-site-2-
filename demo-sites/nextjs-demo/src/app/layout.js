import Script from "next/script";
import "./globals.css";

export const metadata = {
  title: "AeroChronos — Next.js Tracking Demo Store",
  description: "A premium gadget store showcasing Buykori AdSync event tracking in Next.js.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <Script id="buykori-adsync-pixel" strategy="afterInteractive">
          {`
            !function(f,b,e,v,n,t,s)
            {if(f.capi)return;n=f.capi=function(){n.callMethod?
            n.callMethod.apply(n,arguments):n.queue.push(arguments)};
            if(!f._capi)f._capi=n;n.push=n;n.loaded=!0;n.version='1.0';
            n.queue=[];t=b.createElement(e);t.async=!0;
            t.src=v;s=b.getElementsByTagName(e)[0];
            s.parentNode.insertBefore(t,s)}(window,document,'script',
            '${process.env.NEXT_PUBLIC_BUYKORI_GATEWAY_URL}/t.js?key=${process.env.NEXT_PUBLIC_BUYKORI_API_KEY}');
          `}
        </Script>
        
        <header>
          <div className="nav-container">
            <a href="/" className="logo">AEROCHRONOS (Next.js)</a>
            <nav className="nav-menu">
              <a href="/" className="nav-link">Shop</a>
              <a href="/checkout" className="nav-link">Checkout</a>
            </nav>
            <div className="cart-icon">
              <a href="/checkout">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="9" cy="21" r="1"></circle>
                  <circle cx="20" cy="21" r="1"></circle>
                  <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"></path>
                </svg>
                <span className="cart-badge" id="cart-count">0</span>
              </a>
            </div>
          </div>
        </header>

        {children}

        <footer>
          <p>&copy; 2026 AeroChronos Next.js Store. All rights reserved.</p>
        </footer>

        {/* Global cart count synchronizer script for simple client-side HTML layout fallback */}
        <Script id="cart-sync" strategy="lazyOnload">
          {`
            function syncCartBadge() {
              try {
                let cart = JSON.parse(localStorage.getItem('cart') || '[]');
                let count = cart.reduce((sum, item) => sum + item.qty, 0);
                let badge = document.getElementById('cart-count');
                if (badge) badge.innerText = count;
              } catch(e) {}
            }
            window.addEventListener('DOMContentLoaded', syncCartBadge);
            setInterval(syncCartBadge, 1000);
          `}
        </Script>
      </body>
    </html>
  );
}
