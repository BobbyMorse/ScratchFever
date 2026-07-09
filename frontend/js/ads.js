// ── Ads + session unlocks ───────────────────────────────────────────────────
// Mirror of the mobile ad model on the web:
//   - Free users see banner ads on non-Chase list surfaces.
//   - Free users can "watch an ad" to unlock a premium strategy for the
//     session. The unlock is deliberately session-scoped (memory-only) so
//     restarting the browser costs another ad — that's the trade.
//   - Pro users see zero ads and everything unlocked.
//   - The Chase (Most Wanted votes + retailer stock status) stays Pro-only.
//
// Ad rendering uses Google AdSense when window.SF_ADSENSE_CLIENT is present.
// If it isn't, we render a subtle in-house "Upgrade to remove ads" slot in
// the same footprint so the layout is stable across environments. This lets
// us ship the plumbing today and light up real ads by setting one env-driven
// meta tag once the AdSense account is approved.
(function () {
  "use strict";

  // ── Session unlocks (in-memory, cleared on tab close) ────────────────────
  const _unlockedStrategies = new Set();

  function isStrategyUnlocked(name) {
    return _unlockedStrategies.has(name);
  }

  function unlockStrategy(name) {
    _unlockedStrategies.add(name);
    try {
      document.dispatchEvent(new CustomEvent("sf:strategy-unlocked", { detail: { name } }));
      if (window.posthog && typeof window.posthog.capture === "function") {
        window.posthog.capture("rewarded_unlock", { surface: "strategy", key: name });
      }
    } catch (_) {}
  }

  // ── Ad config ────────────────────────────────────────────────────────────
  function _pro() {
    try { return typeof isPro === "function" ? !!isPro() : false; } catch (_) { return false; }
  }

  function _adsenseClient() {
    // Preferred: <meta name="sf-adsense-client" content="ca-pub-…"> so ops can
    // flip live ads without a JS deploy.
    const meta = document.querySelector('meta[name="sf-adsense-client"]');
    const v = (meta && meta.content ? meta.content : window.SF_ADSENSE_CLIENT || "").trim();
    return v || null;
  }

  function _adsenseSlot(name) {
    const meta = document.querySelector(`meta[name="sf-adsense-slot-${name}"]`);
    const v = (meta && meta.content ? meta.content : "").trim();
    return v || null;
  }

  let _adsenseLoaded = false;
  function _loadAdSense() {
    if (_adsenseLoaded) return;
    const client = _adsenseClient();
    if (!client) return;
    _adsenseLoaded = true;
    const s = document.createElement("script");
    s.async = true;
    s.crossOrigin = "anonymous";
    s.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${encodeURIComponent(client)}`;
    document.head.appendChild(s);
  }

  // Render a banner into `container`. No-ops for Pro; renders AdSense if a
  // client+slot pair is configured, otherwise renders the fallback "upgrade"
  // slot so the surface reserves consistent vertical space.
  function renderBanner(container, opts) {
    if (!container) return;
    if (_pro()) { container.innerHTML = ""; container.style.display = "none"; return; }
    container.style.display = "";
    const slotName = (opts && opts.slot) || "banner";
    const client = _adsenseClient();
    const slot = _adsenseSlot(slotName);
    if (client && slot) {
      _loadAdSense();
      container.innerHTML = `<ins class="adsbygoogle sf-ad-ins"
        style="display:block"
        data-ad-client="${client}"
        data-ad-slot="${slot}"
        data-ad-format="auto"
        data-full-width-responsive="true"></ins>`;
      try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch (_) {}
      return;
    }
    // Fallback house ad — neutral copy, upgrade CTA.
    container.innerHTML = `
      <div class="sf-house-ad" onclick="openPaywallOrLogin()" role="button" tabindex="0">
        <div class="sf-house-ad-body">
          <div class="sf-house-ad-title">Enjoying ScratchFrenzy?</div>
          <div class="sf-house-ad-sub">Go Pro to remove ads and unlock every strategy.</div>
        </div>
        <div class="sf-house-ad-cta">Upgrade →</div>
      </div>`;
  }

  function refreshAllBanners() {
    document.querySelectorAll("[data-sf-ad-slot]").forEach(el => {
      renderBanner(el, { slot: el.getAttribute("data-sf-ad-slot") || "banner" });
    });
  }

  // ── Rewarded flow ────────────────────────────────────────────────────────
  // The web doesn't have AdMob's "rewarded video" primitive, but the exchange
  // is the same: the user watches a short ad, we unlock the feature. We
  // implement it as a modal that (a) tries to render an AdSense unit inside
  // and (b) counts down for 8s before enabling the "Continue" button. If no
  // AdSense creds are configured, the modal just runs the timer over the
  // house-ad message — dev-friendly and still a real friction gate.
  //
  // Returns a Promise<boolean> resolving true if the user completed the ad
  // and false if they closed it early.
  function showRewardedAd(context) {
    return new Promise(resolve => {
      const wrap = document.createElement("div");
      wrap.className = "sf-rw-overlay";
      wrap.innerHTML = `
        <div class="sf-rw-modal" role="dialog" aria-modal="true" aria-label="Sponsored — unlock feature">
          <button class="sf-rw-close" aria-label="Close" title="Close">✕</button>
          <div class="sf-rw-eyebrow">SPONSORED · UNLOCK ${escapeHtml((context && context.label) || "STRATEGY")}</div>
          <div class="sf-rw-slot" id="sfRwSlot"></div>
          <div class="sf-rw-foot">
            <div class="sf-rw-timer" id="sfRwTimer">Watch to unlock — 8s</div>
            <div class="sf-rw-actions">
              <button class="sf-rw-continue" id="sfRwContinue" disabled>Continue</button>
              <button class="sf-rw-upgrade" onclick="openPaywallOrLogin()">Upgrade to Pro — no ads</button>
            </div>
          </div>
        </div>`;
      document.body.appendChild(wrap);
      document.body.classList.add("sf-rw-open");
      renderBanner(wrap.querySelector("#sfRwSlot"), { slot: "rewarded" });

      let resolved = false;
      const finish = (earned) => {
        if (resolved) return;
        resolved = true;
        document.body.classList.remove("sf-rw-open");
        wrap.remove();
        resolve(!!earned);
      };
      wrap.querySelector(".sf-rw-close").addEventListener("click", () => finish(false));
      const contBtn = wrap.querySelector("#sfRwContinue");
      contBtn.addEventListener("click", () => finish(true));
      const timerEl = wrap.querySelector("#sfRwTimer");
      let left = 8;
      const iv = setInterval(() => {
        left -= 1;
        if (left > 0) {
          timerEl.textContent = `Watch to unlock — ${left}s`;
        } else {
          clearInterval(iv);
          timerEl.textContent = "You can continue now.";
          contBtn.disabled = false;
          contBtn.classList.add("ready");
        }
      }, 1000);

      try {
        if (window.posthog && typeof window.posthog.capture === "function") {
          window.posthog.capture("rewarded_ad_shown", { surface: (context && context.surface) || "strategy" });
        }
      } catch (_) {}
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ── Public API on window (no module system on this codebase) ─────────────
  window.SFAds = {
    isStrategyUnlocked,
    unlockStrategy,
    renderBanner,
    refreshAllBanners,
    showRewardedAd,
  };

  // Re-render every banner whenever the pro/free state flips (login, logout,
  // beta redeem, Stripe redirect back). The `sf:user-changed` event is fired
  // from _setUser in app.js.
  document.addEventListener("sf:user-changed", refreshAllBanners);

  // First paint — after DOMContentLoaded so slot containers exist.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", refreshAllBanners);
  } else {
    refreshAllBanners();
  }
})();
