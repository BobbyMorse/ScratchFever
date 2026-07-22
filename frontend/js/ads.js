// ── Ads + session unlocks ───────────────────────────────────────────────────
// Mirror of the mobile ad model on the web:
//   - Free users see banner ads on non-Chase list surfaces.
//   - Free users can "watch an ad" to unlock a premium strategy for the
//     session. The unlock is deliberately session-scoped (memory-only) so
//     restarting the browser costs another ad — that's the trade.
//   - Pro users see zero ads and everything unlocked.
//   - The Chase (Most Wanted votes + retailer stock status) stays Pro-only.
//
// Ad rendering uses Ezoic (standalone JavaScript integration). Ezoic gives us
// Google AdX demand without needing our own AdSense approval, and is far more
// tolerant of lottery-adjacent content. When Ezoic is configured (meta
// `sf-ezoic-enabled` set and the sa.min.js loader present) each slot renders an
// Ezoic placeholder; otherwise we render a subtle in-house "Upgrade to remove
// ads" card in the same footprint so the layout is stable across environments.
// This keeps the plumbing shippable today and lights up real ads by flipping
// one meta flag + pasting placeholder IDs once the Ezoic site is integrated.
//
// KEY DIFFERENCE FROM ADSENSE: Ezoic placements are keyed by a UNIQUE numeric
// placeholder ID. AdSense let one slot ID power every placement; Ezoic cannot —
// two ads on the same page need two different IDs. So each slot name maps to a
// POOL of IDs (meta `sf-ezoic-ids-<slot>`, comma-separated) and we hand out a
// distinct ID to each instance of that slot on the page. Ezoic is also an SPA-
// hostile API: an ID shown once must be destroyed before it can be shown again,
// so refreshAllBanners tears down the previous set before showing the new one.
(function () {
  "use strict";

  // ── Session unlocks (in-memory, cleared on tab close) ────────────────────
  const _unlockedStrategies = new Set();
  const _warnedSlots = new Set();

  // Ezoic placeholder IDs currently live on the page. Destroyed and rebuilt on
  // every refresh so re-renders (login/logout, list re-paint) don't leave stale
  // placeholders that block their IDs from being reused.
  let _liveEzoicIds = [];

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

  function _meta(name) {
    const m = document.querySelector(`meta[name="${name}"]`);
    return (m && m.content ? m.content : "").trim();
  }

  // Ezoic is "on" only when ops has flipped the flag AND the loader queue is
  // present. Until then every slot renders the house upsell card.
  function _ezoicEnabled() {
    const flag = _meta("sf-ezoic-enabled").toLowerCase();
    return !!flag && flag !== "0" && flag !== "false";
  }

  function _ez() {
    const e = window.ezstandalone;
    return (e && Array.isArray(e.cmd)) ? e : null;
  }

  // Parse the comma-separated ID pool for a slot. Unknown slots fall back to
  // the `banner` pool so ops can go live by populating a single pool, exactly
  // like the old AdSense `banner` fallback.
  function _ezoicIds(slotName) {
    let raw = _meta(`sf-ezoic-ids-${slotName}`);
    if (!raw && slotName !== "banner") raw = _meta("sf-ezoic-ids-banner");
    return raw
      .split(",")
      .map(s => parseInt(s.trim(), 10))
      .filter(n => Number.isInteger(n) && n > 0);
  }

  // ── Rendering primitives ─────────────────────────────────────────────────
  function _houseAd(container) {
    container.innerHTML = `
      <div class="sf-house-ad" onclick="openPaywallOrLogin()" role="button" tabindex="0">
        <div class="sf-house-ad-body">
          <div class="sf-house-ad-title">Enjoying ScratchFrenzy?</div>
          <div class="sf-house-ad-sub">Go Pro to remove ads and unlock every strategy.</div>
        </div>
        <div class="sf-house-ad-cta">Upgrade →</div>
      </div>`;
  }

  function _ezoicPlaceholder(container, id) {
    container.innerHTML = `<div id="ezoic-pub-ad-placeholder-${id}"></div>`;
  }

  function _warnSlotOnce(slotName, msg) {
    if (_warnedSlots.has(slotName)) return;
    _warnedSlots.add(slotName);
    try { console.warn(msg); } catch (_) {}
  }

  // Render a single banner into `container`. No-ops for Pro. Used by the
  // rewarded modal (a lone dynamic slot). Batch placements go through
  // refreshAllBanners instead. Returns the Ezoic placeholder id it showed (so
  // the caller can destroy it) or null when it rendered the house ad.
  function renderBanner(container, opts) {
    if (!container) return null;
    if (_pro()) { container.innerHTML = ""; container.style.display = "none"; return null; }
    container.style.display = "";
    const slotName = (opts && opts.slot) || "banner";
    const ez = _ez();
    if (_ezoicEnabled() && ez) {
      const ids = _ezoicIds(slotName);
      if (ids.length) {
        const id = ids[0];
        _ezoicPlaceholder(container, id);
        ez.cmd.push(function () { try { ez.showAds(id); } catch (_) {} });
        return id;
      }
      _warnSlotOnce(slotName, `[ads] Ezoic enabled but no placeholder IDs for slot "${slotName}" — house ad rendering. Set <meta name="sf-ezoic-ids-${slotName}">.`);
    }
    _houseAd(container);
    return null;
  }

  // Rebuild every static/inline banner on the page. Tears down the previous
  // Ezoic set first (SPA-safe), then assigns a distinct placeholder id to each
  // slot instance from that slot's pool and shows them in one batched call —
  // Ezoic prefers a single showAds() over many.
  function refreshAllBanners() {
    const containers = Array.from(document.querySelectorAll("[data-sf-ad-slot]"));
    const ez = _ez();

    // Destroy whatever we showed last pass so those IDs are free to reuse.
    if (ez && _liveEzoicIds.length) {
      const toKill = _liveEzoicIds.slice();
      ez.cmd.push(function () { try { ez.destroyPlaceholders.apply(ez, toKill); } catch (_) {} });
      _liveEzoicIds = [];
    }

    if (_pro()) {
      containers.forEach(el => { el.innerHTML = ""; el.style.display = "none"; });
      return;
    }

    const useEzoic = _ezoicEnabled() && !!ez;
    const assigned = [];
    const cursor = {}; // per-slot index into its ID pool

    containers.forEach(el => {
      el.style.display = "";
      const slot = el.getAttribute("data-sf-ad-slot") || "banner";
      if (useEzoic) {
        const ids = _ezoicIds(slot);
        const i = cursor[slot] || 0;
        if (i < ids.length) {
          cursor[slot] = i + 1;
          const id = ids[i];
          _ezoicPlaceholder(el, id);
          assigned.push(id);
          return;
        }
        // Pool exhausted for this slot (more placements than configured IDs) —
        // fall back to the house ad rather than reusing an ID (which Ezoic
        // would refuse to fill). Add more IDs to the pool to cover them.
        _warnSlotOnce(slot, `[ads] Ezoic pool for slot "${slot}" is too small for the number of placements — extra slots show the house ad. Add more IDs to <meta name="sf-ezoic-ids-${slot}">.`);
      }
      _houseAd(el);
    });

    if (useEzoic && assigned.length) {
      _liveEzoicIds = assigned.slice();
      ez.cmd.push(function () { try { ez.showAds.apply(ez, assigned); } catch (_) {} });
    }
  }

  // ── Rewarded flow ────────────────────────────────────────────────────────
  // The web doesn't have a native "rewarded video" primitive, but the exchange
  // is the same: the user watches a short ad, we unlock the feature. We
  // implement it as a modal that (a) renders an Ezoic placeholder inside and
  // (b) counts down for 8s before enabling the "Continue" button. If Ezoic
  // isn't configured, the modal runs the timer over the house-ad message —
  // dev-friendly and still a real friction gate.
  //
  // NOTE: the `rewarded` ID pool must not overlap the pools shown by
  // refreshAllBanners, or Ezoic will refuse the duplicate id while the modal is
  // open. Give it its own dedicated placeholder ID in the dashboard.
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
      const rwId = renderBanner(wrap.querySelector("#sfRwSlot"), { slot: "rewarded" });

      let resolved = false;
      const finish = (earned) => {
        if (resolved) return;
        resolved = true;
        // Tear down the modal's placeholder so its ID is free for next time.
        if (rwId) {
          const ez = _ez();
          if (ez) ez.cmd.push(function () { try { ez.destroyPlaceholders(rwId); } catch (_) {} });
        }
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
