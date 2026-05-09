/* ScratchFever — frontend */

let allGames = [];
let states = [];
let currentSort = { col: "return_pct", asc: false };
let currentTab = "ev";

// ── Hunt state ────────────────────────────────────────────────────────────────
let currentHuntState = 'MA';

// ── MA Hunt state ─────────────────────────────────────────────────────────────
let allRetailers = [];
let maGames = [];
let selectedGame = null; // { name, price } or null
let maLoaded = false;
let maMap = null;
let maMapVisible = false;
let maLayerControl = null;
let mapReportFilter = "all"; // "all" | "in" | "out" — synced from maInvFilter

// ── AZ Hunt state ─────────────────────────────────────────────────────────────
let allAzRetailers = [];
let azGames = [];
let selectedAzGame = null; // { name, price } or null
let azLoaded = false;
let azMap = null;
let azMapVisible = false;
let azMapReportFilter = "all";

// ── Community inventory ───────────────────────────────────────────────────────
let communityReports = [];
let gameCounts = {};               // {game_name_lower: count} — members only
let retailerCounts = {};           // {retailer_id: count} — members only
let retailerLatestStatus = {};     // {retailer_id: {has_stock, reported_at}} — members only
let _reportStock = true;
let _openProfileId = null;

// ── Auth state ────────────────────────────────────────────────────────────────
let _currentUser = null;  // { email, username, role } or null
let _openModalGame = null;

function getToken() { return localStorage.getItem("sf_token") || ""; }

function authHeaders() {
  const t = getToken();
  return t ? { "Authorization": `Bearer ${t}` } : {};
}

function callerFetch(url, opts = {}) {
  opts.headers = { ...(opts.headers || {}), ...authHeaders() };
  return fetch(url, opts);
}

function protectedFetch(url, opts = {}) {
  opts.headers = { ...(opts.headers || {}), ...authHeaders() };
  return fetch(url, opts);
}

function _setUser(user) {
  _currentUser = user;
  const chip  = document.getElementById("userChip");
  const btn   = document.getElementById("loginBtn");
  const caller = document.getElementById("callerTabBtn");

  if (user) {
    document.getElementById("userEmail").textContent = user.username || user.email;
    const roleEl = document.getElementById("userRole");
    roleEl.textContent = user.role === "admin" ? "Admin" : "Member";
    roleEl.className = "user-chip-role role-" + user.role;
    chip.style.display = "";
    btn.style.display  = "none";
    caller.style.display = user.role === "admin" ? "" : "none";
    const isAdmin = user.role === "admin";
    document.getElementById("scrapeBtn").style.display = isAdmin ? "" : "none";
    document.getElementById("statusBar").style.display = isAdmin ? "" : "none";
    const repBtn = document.getElementById("reportInvBtn");
    if (repBtn) repBtn.style.display = "";
    const azRepBtn = document.getElementById("azReportInvBtn");
    if (azRepBtn) azRepBtn.style.display = "";
    const soonRepBtn = document.getElementById("huntSoonReportBtn");
    if (soonRepBtn) soonRepBtn.style.display = "";
  } else {
    chip.style.display  = "none";
    btn.style.display   = "";
    caller.style.display = "none";
    document.getElementById("scrapeBtn").style.display = "none";
    document.getElementById("statusBar").style.display = "none";
    const repBtn = document.getElementById("reportInvBtn");
    if (repBtn) repBtn.style.display = "none";
    const azRepBtn = document.getElementById("azReportInvBtn");
    if (azRepBtn) azRepBtn.style.display = "none";
    const soonRepBtn = document.getElementById("huntSoonReportBtn");
    if (soonRepBtn) soonRepBtn.style.display = "none";
    // Close any open store profile and clear report badges
    _openProfileId = null;
    document.querySelectorAll(".store-profile-tr").forEach(el => el.remove());
    document.querySelectorAll(".store-profile-open").forEach(el => el.classList.remove("store-profile-open"));
    updateReportBadges();
  }
}

async function restoreSession() {
  const token = getToken();
  if (!token) { _setUser(null); return; }
  try {
    const res = await fetch("/api/auth/me", { headers: authHeaders() });
    if (res.ok) {
      const data = await res.json();
      _setUser({ email: data.email, username: data.username, role: data.role });
      loadCommunityReports();
    } else {
      localStorage.removeItem("sf_token");
      _setUser(null);
    }
  } catch (_) { _setUser(null); }
}

function logout() {
  localStorage.removeItem("sf_token");
  _setUser(null);
  if (currentTab === "caller") switchTab("ev");
  communityReports = [];
  gameCounts = {}; retailerCounts = {}; retailerLatestStatus = {};
  renderTable(); updateReportBadges(); updateLastReportCells();
}

// ── Auth modal ────────────────────────────────────────────────────────────────

function openAuthModal(tab = "login") {
  document.getElementById("authModalOverlay").classList.add("open");
  switchAuthTab(tab);
}

function closeAuthModal() {
  document.getElementById("authModalOverlay").classList.remove("open");
}

function switchAuthTab(tab) {
  document.getElementById("authFormLogin").style.display    = tab === "login"    ? "" : "none";
  document.getElementById("authFormRegister").style.display = tab === "register" ? "" : "none";
  document.getElementById("authTabLogin").classList.toggle("active",    tab === "login");
  document.getElementById("authTabRegister").classList.toggle("active", tab === "register");
  document.getElementById("loginMsg").style.display    = "none";
  document.getElementById("registerMsg").style.display = "none";
}

function togglePw(inputId, btn) {
  const input = document.getElementById(inputId);
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  btn.querySelector(".eye-icon").style.display     = showing ? "" : "none";
  btn.querySelector(".eye-off-icon").style.display = showing ? "none" : "";
  btn.setAttribute("aria-label", showing ? "Show password" : "Hide password");
}

async function submitLogin() {
  const email = document.getElementById("loginEmail").value.trim();
  const pass  = document.getElementById("loginPassword").value;
  const msgEl = document.getElementById("loginMsg");
  if (!email || !pass) { _authMsg(msgEl, "Enter email and password.", "err"); return; }

  try {
    const res  = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password: pass }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Login failed");
    localStorage.setItem("sf_token", data.token);
    _setUser({ email: data.email, username: data.username, role: data.role });
    closeAuthModal();
    loadCommunityReports();
    loadGameCounts(); loadRetailerCounts(); loadRetailerLatest();
  } catch (e) {
    _authMsg(msgEl, e.message, "err");
  }
}

async function submitRegister() {
  const username = document.getElementById("registerUsername").value.trim();
  const email    = document.getElementById("registerEmail").value.trim();
  const pass     = document.getElementById("registerPassword").value;
  const confirm  = document.getElementById("registerConfirm").value;
  const msgEl    = document.getElementById("registerMsg");
  if (!username) { _authMsg(msgEl, "Choose a username.", "err"); return; }
  if (!email || !pass) { _authMsg(msgEl, "Enter email and password.", "err"); return; }
  if (pass !== confirm) { _authMsg(msgEl, "Passwords do not match.", "err"); return; }
  if (pass.length < 8)  { _authMsg(msgEl, "Password must be at least 8 characters.", "err"); return; }

  try {
    const res  = await fetch("/api/auth/register", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, username, password: pass }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Registration failed");
    localStorage.setItem("sf_token", data.token);
    _setUser({ email: data.email, username: data.username, role: data.role });
    closeAuthModal();
    loadCommunityReports();
    loadGameCounts(); loadRetailerCounts(); loadRetailerLatest();
  } catch (e) {
    _authMsg(msgEl, e.message, "err");
  }
}

function _authMsg(el, text, type) {
  el.style.display = "";
  el.className = "caller-msg " + type;
  el.textContent = text;
}

// Keep --header-h CSS var in sync with actual header height (fixes sticky thead)
function syncHeaderHeight() {
  const h = document.querySelector(".site-header")?.offsetHeight || 64;
  document.documentElement.style.setProperty("--header-h", h + "px");
}

// ── Boot ──────────────────────────────────────────────────────────────────────
(async function init() {
  syncHeaderHeight();
  window.addEventListener("resize", syncHeaderHeight);
  await Promise.all([loadStates(), loadGames(), loadMaGames(), loadAzGames(), restoreSession()]);
  await Promise.all([loadCommunityReports(), loadGameCounts(), loadRetailerCounts(), loadRetailerLatest()]);
  loadStatus();
  loadPrizeClaims();
  setInterval(() => { loadStatus(); loadPrizeClaims(); }, 30_000);
})();

async function loadGameCounts() {
  try {
    const res = await protectedFetch("/api/inventory/game-counts");
    if (!res.ok) return;
    const data = await res.json();
    gameCounts = data.counts || {};
    renderTable();
  } catch (_) {}
}

async function loadMaGames() {
  try {
    const res = await fetch("/api/games?state=MA&limit=500&sort_by=return_pct");
    if (!res.ok) return;
    const data = await res.json();
    maGames = data.games || [];
    populateGameFilterSelect();
  } catch (_) {}
}

function populateGameFilterSelect() {
  // data is in maGames; UI is a typeahead, nothing to rebuild
}

function searchGameFilter() {
  const input = document.getElementById("gameFilterInput");
  const dd = document.getElementById("gameFilterDropdown");
  const clear = document.getElementById("gameFilterClear");
  const q = input.value.trim().toLowerCase();

  clear.style.display = q ? "" : "none";

  const matches = q
    ? maGames.filter(g => g.name.toLowerCase().includes(q))
    : maGames.slice(0, 50);

  if (!matches.length) { dd.style.display = "none"; return; }

  dd.innerHTML = matches.map(g => {
    const meta = [g.price != null ? `$${g.price}` : null, g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : null]
      .filter(Boolean).join(" · ");
    const sub = meta ? `<span style="color:var(--text-muted);font-size:.78rem">${escHtml(meta)}</span>` : "";
    return `<div class="store-option" onmousedown="selectGameFilter(${JSON.stringify(g.name).replace(/"/g, '&quot;')})">${escHtml(g.name)} ${sub}</div>`;
  }).join("");
  dd.style.display = "";
}

function selectGameFilter(name) {
  const input = document.getElementById("gameFilterInput");
  const dd = document.getElementById("gameFilterDropdown");
  const clear = document.getElementById("gameFilterClear");
  input.value = name;
  dd.style.display = "none";
  clear.style.display = "";
  const g = maGames.find(g => g.name === name) || { name, price: null };
  selectedGame = { name: g.name, price: g.price ?? null };
  applyGameFilter();
}

function clearGameFilter() {
  document.getElementById("gameFilterInput").value = "";
  document.getElementById("gameFilterDropdown").style.display = "none";
  document.getElementById("gameFilterClear").style.display = "none";
  selectedGame = null;
  applyGameFilter();
}

// hide dropdown when focus leaves
document.addEventListener("click", e => {
  const wrap = document.getElementById("gameFilterInput");
  if (wrap && !wrap.closest(".filter-group").contains(e.target)) {
    document.getElementById("gameFilterDropdown").style.display = "none";
  }
});

async function loadRetailerCounts() {
  try {
    const res = await protectedFetch("/api/inventory/retailer-counts");
    if (!res.ok) return;
    const data = await res.json();
    retailerCounts = data.counts || {};
    updateReportBadges();
  } catch (_) {}
}

async function loadRetailerLatest() {
  try {
    const res = await protectedFetch("/api/inventory/retailer-latest");
    if (!res.ok) return;
    const data = await res.json();
    retailerLatestStatus = data.statuses || {};
    updateLastReportCells();
  } catch (_) {}
}

function buildLatestStatusFromReports() {
  const status = {};
  const activeGame = currentHuntState === 'AZ' ? selectedAzGame : selectedGame;
  const gameFilter = activeGame?.name.toLowerCase();
  for (const rep of communityReports) {
    if (gameFilter && rep.game_name?.toLowerCase() !== gameFilter) continue;
    const rid = rep.retailer_id;
    if (!rid) continue;
    const existing = status[rid];
    if (!existing || parseReportedAt(rep.reported_at) > parseReportedAt(existing.reported_at)) {
      status[rid] = { has_stock: rep.has_stock, reported_at: rep.reported_at };
    }
  }
  retailerLatestStatus = status;
}

function parseReportedAt(str) {
  if (!str) return new Date(0);
  if (str.length >= 19 && str[10] === " ") return new Date(str.replace(" ", "T") + "Z");
  if (str.includes("T") || str.includes("Z")) return new Date(str);
  return new Date(str + "T00:00:00Z");
}

function lastReportCellHtml(rid) {
  const s = retailerLatestStatus[rid];
  if (!s) return `<span style="color:var(--text-muted);font-size:.8rem">—</span>`;
  const icon = s.has_stock ? "✅" : "❌";
  const ago  = timeAgo(parseReportedAt(s.reported_at));
  return `<span style="font-size:.8rem;white-space:nowrap;line-height:1.4">${icon}<br><span style="color:var(--text-muted);font-size:.72rem">${ago}</span></span>`;
}

function updateLastReportCells() {
  document.querySelectorAll("td.last-report-cell[data-rid]").forEach(cell => {
    cell.innerHTML = lastReportCellHtml(cell.dataset.rid);
  });
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadGames() {
  const params = buildParams();
  try {
    const res = await fetch(`/api/games?${params}`);
    const data = await res.json();
    allGames = data.games || [];
    renderTable();
    updateStats();
  } catch (e) {
    document.getElementById("gamesBody").innerHTML =
      `<tr><td colspan="14" class="loading-cell">Failed to load data. Is the server running?</td></tr>`;
  }
}

async function loadStates() {
  try {
    const res = await fetch("/api/states");
    const data = await res.json();
    states = data.states || [];
    const sel = document.getElementById("filterState");
    states.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s.state_code;
      opt.textContent = `${s.state_name} (${s.game_count})`;
      sel.appendChild(opt);
    });
  } catch (_) {}
}

async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    const dot = document.getElementById("statusDot");
    const txt = document.getElementById("statusText");

    if (data.scraper_running) {
      dot.className = "status-dot busy";
      txt.textContent = "Scraping…";
    } else if (data.last_run) {
      dot.className = "status-dot ok";
      const d = new Date(/Z$|[+-]\d{2}:\d{2}$/.test(data.last_run) ? data.last_run : data.last_run + 'Z');
      txt.textContent = `Updated ${timeAgo(d)}`;
    } else {
      dot.className = "status-dot";
      txt.textContent = "No data yet";
    }
    document.getElementById("statGames").textContent = data.total_games.toLocaleString();
    document.getElementById("statStates").textContent = data.states_covered;
  } catch (_) {}
}

function fmtClaimPrize(amount) {
  if (amount >= 1_000_000) return "$" + (amount / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (amount >= 1000) return "$" + (amount / 1000).toFixed(0) + "K";
  return "$" + amount.toLocaleString();
}

function buildClaimItem(c) {
  const prize = fmtClaimPrize(c.prize_amount);
  const when = timeAgo(new Date(c.detected_at));
  const left = c.new_remaining === 0
    ? '<span style="color:var(--red);font-weight:700">GONE</span>'
    : `${c.new_remaining.toLocaleString()} left`;
  const count = c.claimed_count > 1 ? ` ×${c.claimed_count}` : "";
  return `<div class="claim-item">
    <span class="badge badge-state">${escHtml(c.state_code)}</span>
    <span class="claim-game">${escHtml(c.game_name)}</span>
    <span class="claim-prize">${prize} prize claimed${count}</span>
    <span class="claim-remaining">${left}</span>
    <span class="claim-when">${when}</span>
  </div>`;
}

async function loadPrizeClaims() {
  try {
    const res = await fetch("/api/prize-claims?min_prize=9000&limit=6");
    if (!res.ok) return;
    const data = await res.json();
    const banner = document.getElementById("bigwinsBanner");
    const items = document.getElementById("bigwinsBannerItems");
    if (!data.claims || data.claims.length === 0) {
      banner.style.display = "none";
      return;
    }
    banner.style.display = "";
    items.innerHTML = data.claims.map(c => {
      const prize = fmtClaimPrize(c.prize_amount);
      const count = c.claimed_count > 1 ? ` ×${c.claimed_count}` : "";
      return `<span class="bigwins-banner-chip">
        <span class="badge badge-state">${escHtml(c.state_code)}</span>
        <span class="bigwins-chip-game">${escHtml(c.game_name)}</span>
        <span class="bigwins-chip-prize">${prize}${count}</span>
      </span>`;
    }).join("");
  } catch (_) {}
}

let bigwinsLoaded = false;

async function loadBigWins() {
  try {
    const loadingEl = document.getElementById("bigwinsLoading");
    const list = document.getElementById("bigwinsList");
    const countEl = document.getElementById("bigwinsCount");
    if (loadingEl) loadingEl.style.display = "";
    const res = await fetch("/api/prize-claims?min_prize=9000&days=7&limit=500");
    if (!res.ok) return;
    const data = await res.json();
    if (loadingEl) loadingEl.style.display = "none";
    if (!data.claims || data.claims.length === 0) {
      list.innerHTML = '<div style="color:var(--text-muted);padding:2rem 1rem">No big wins in the last 7 days.</div>';
      countEl.textContent = "";
      return;
    }
    countEl.textContent = `${data.claims.length} claim${data.claims.length !== 1 ? "s" : ""}`;
    list.innerHTML = data.claims.map(buildClaimItem).join("");
  } catch (_) {}
}

// ── Filters & sorting ─────────────────────────────────────────────────────────
function buildParams() {
  const state    = document.getElementById("filterState")?.value || "";
  const price    = document.getElementById("filterPrice")?.value || "";
  const minRet   = document.getElementById("filterMinReturn")?.value || "";
  const sortBy   = document.getElementById("sortBy")?.value || "return_pct";
  const p = new URLSearchParams({ sort_by: sortBy, limit: 1000 });
  if (state)  p.set("state", state);
  if (price)  { p.set("min_price", price); p.set("max_price", price); }
  if (minRet) p.set("min_return", minRet);
  return p.toString();
}

async function applyFilters() {
  document.getElementById("gamesBody").innerHTML =
    `<tr><td colspan="14" class="loading-cell">Loading…</td></tr>`;
  await loadGames();
}

function renderTable() {
  const search = document.getElementById("searchInput").value.toLowerCase().trim();
  const showNearSoldOut = document.getElementById("hideSuspicious")?.checked;
  const hideNoData = document.getElementById("hideNoData")?.checked ?? true;
  let games = allGames;

  if (hideNoData) {
    games = games.filter(g => g.tickets_remaining != null);
  }

  if (!showNearSoldOut) {
    games = games.filter(g => {
      if (!g.total_tickets || !g.tickets_remaining) return true;
      const pctLeft = g.tickets_remaining / g.total_tickets;
      return !(pctLeft < 0.05 && g.return_pct >= 100);
    });
  }

  if (search) {
    games = games.filter(g =>
      g.name.toLowerCase().includes(search) ||
      g.state_name.toLowerCase().includes(search) ||
      g.state_code.toLowerCase().includes(search)
    );
  }

  // Client-side secondary sort
  const { col, asc } = currentSort;
  games = [...games].sort((a, b) => {
    const va = a[col] ?? -Infinity;
    const vb = b[col] ?? -Infinity;
    return asc ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  });

  document.getElementById("resultCount").textContent =
    `${games.length.toLocaleString()} games`;

  const tbody = document.getElementById("gamesBody");
  if (!games.length) {
    tbody.innerHTML = `<tr><td colspan="14" class="loading-cell">No games match your filters.</td></tr>`;
    return;
  }

  tbody.innerHTML = games.map((g, i) => gameRow(g, i + 1)).join("");
  updateStats(games);
}

function gameRow(g, rank) {
  const ret = g.return_pct;
  const cls = ret >= 100 ? "ev-positive" : ret >= 90 ? "ev-near" : ret >= 70 ? "ev-mid" : "ev-low";
  const barPct = Math.min(100, (ret / 120) * 100).toFixed(1);

  const retCell = ret != null
    ? `<span class="${cls}">
         <div class="return-bar-wrap">
           <div class="return-bar"><div class="return-bar-fill" style="width:${barPct}%"></div></div>
           ${ret.toFixed(2)}%
         </div>
       </span>`
    : "—";

  const ev     = g.ev != null ? `${g.ev >= 0 ? "+" : ""}$${g.ev.toFixed(2)}` : "—";
  const jackpotOdds = g.jackpot_odds_one_in != null ? `1 in ${fmtNum(Math.round(g.jackpot_odds_one_in))}` : "—";
  const odds   = g.overall_odds_one_in ? `1 in ${fmtNum(g.overall_odds_one_in)}` : "—";
  const left   = g.tickets_remaining != null ? fmtNum(g.tickets_remaining) : "—";
  const topRem = g.top_prize_remaining != null ? fmtNum(g.top_prize_remaining) : "—";
  const pool   = g.prize_pool_remaining != null ? "$" + fmtMoney(g.prize_pool_remaining) : "—";
  const updated = g.scraped_at ? timeAgo(new Date(g.scraped_at + (g.scraped_at.endsWith("Z") ? "" : "Z"))) : "—";

  const reportCount = gameCounts[g.name.toLowerCase()] || 0;
  const reportBadge = reportCount > 0
    ? `<span class="game-report-badge" title="${reportCount} community report${reportCount > 1 ? 's' : ''}">${reportCount} 📍</span>`
    : "";

  const nameEsc = escHtml(g.name).replace(/'/g, "\\'");
  return `<tr onclick="openGame(${g.id})">
    <td style="color:var(--text-muted);font-size:.8rem;font-weight:700">${rank}</td>
    <td><span class="state-pill state-${g.state_code}">${g.state_code}</span></td>
    <td><strong>${escHtml(g.name)}</strong>${reportBadge}</td>
    <td style="color:var(--text-muted);font-size:.8rem;width:60px;max-width:60px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(g.game_id)}</td>
    <td><span class="price-pill">$${g.price}</span></td>
    <td>${retCell}</td>
    <td class="${g.ev >= 0 ? "ev-positive" : ""}">${ev}</td>
    <td style="color:var(--text-muted);font-size:.85rem">${jackpotOdds}</td>
    <td>${g.top_prize != null ? "<strong>$" + fmtMoney(g.top_prize) + "</strong>" : "—"}</td>
    <td>${topRem}</td>
    <td>${odds}</td>
    <td>${left}</td>
    <td>${pool}</td>
    <td style="color:var(--text-muted);font-size:.8rem">${updated}</td>
    <td onclick="event.stopPropagation()">
      <button class="btn-campaign-launch" onclick="launchCampaign('${nameEsc}', ${g.price}, '${escHtml(g.game_id)}')" title="Create calling campaign for this game">📞</button>
    </td>
  </tr>`;
}

// ── Stats update ──────────────────────────────────────────────────────────────
function updateStats(games) {
  const gs = games || allGames;
  const positive = gs.filter(g => g.return_pct >= 100).length;
  const best = gs.reduce((max, g) => Math.max(max, g.return_pct || 0), 0);
  document.getElementById("statPositive").textContent = positive.toLocaleString();
  document.getElementById("statBest").textContent = best > 0 ? best.toFixed(1) + "%" : "—";
}

// ── Column sort ───────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("thead th[data-col]").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.dataset.col;
      if (currentSort.col === col) {
        currentSort.asc = !currentSort.asc;
      } else {
        currentSort.col = col;
        currentSort.asc = false;
      }
      document.querySelectorAll("thead th").forEach(h => h.classList.remove("active"));
      th.classList.add("active");
      th.textContent = th.textContent.replace(/[▲▼]/, "").trim() +
        (currentSort.asc ? " ▲" : " ▼");
      renderTable();
    });
  });
});

// ── Game detail modal ─────────────────────────────────────────────────────────
async function openGame(id) {
  document.getElementById("modalOverlay").classList.add("open");
  document.getElementById("modalContent").innerHTML =
    `<div class="loading-cell">Loading…</div>`;
  try {
    const res = await fetch(`/api/games/${id}`);
    if (!res.ok) throw new Error("Not found");
    const g = await res.json();
    renderModal(g);
  } catch (e) {
    document.getElementById("modalContent").innerHTML =
      `<p style="color:var(--red)">Failed to load game details.</p>`;
  }
}

function renderModal(g) {
  _openModalGame = g;
  const ret = g.return_pct;
  const cls = ret >= 100 ? "ev-positive" : ret >= 90 ? "ev-near" : ret >= 70 ? "ev-mid" : "ev-low";
  const ev = g.ev != null ? `${g.ev >= 0 ? "+" : ""}$${g.ev.toFixed(4)}` : "N/A";

  const prizePoolRemaining = g.prize_pool_left != null
    ? g.prize_pool_left
    : (g.prize_tiers || []).reduce(
        (sum, t) => sum + (t.prize_amount || 0) * (t.prizes_remaining || 0), 0
      );
  const faceValueOutstanding = g.tickets_remaining != null ? g.tickets_remaining * g.price : null;
  const ticketsSold = g.total_tickets != null && g.tickets_remaining != null
    ? g.total_tickets - g.tickets_remaining : null;

  const tierRows = (g.prize_tiers || []).map(t => {
    const rem = t.prizes_remaining != null ? fmtNum(t.prizes_remaining) : "—";
    const tot = t.prizes_total != null ? fmtNum(t.prizes_total) : "—";
    const odds = t.odds_one_in ? `1 in ${fmtNum(t.odds_one_in)}` : "—";
    const prob = t.prizes_remaining != null && g.tickets_remaining
      ? ((t.prizes_remaining / g.tickets_remaining) * 100).toFixed(4) + "%"
      : (t.odds_one_in ? ((1 / t.odds_one_in) * 100).toFixed(4) + "%" : "—");
    return `<tr>
      <td><strong>$${fmtMoney(t.prize_amount)}</strong></td>
      <td>${odds}</td>
      <td>${prob}</td>
      <td>${rem}</td>
      <td>${tot}</td>
    </tr>`;
  }).join("");

  const noSalesData = g.tickets_remaining == null && g.total_tickets == null;

  document.getElementById("modalContent").innerHTML = `
    ${g.image_url ? `<img src="${escHtml(g.image_url)}" alt="${escHtml(g.name)}" class="modal-ticket-img" onerror="this.style.display='none'">` : ""}
    <div class="modal-title">${escHtml(g.name)}</div>
    <div class="modal-state">${g.state_name} • $${g.price} ticket</div>
    ${noSalesData ? `<div style="background:rgba(255,200,0,.12);border:1px solid rgba(255,200,0,.3);border-radius:8px;padding:.6rem .85rem;margin:.75rem 0;font-size:.82rem;color:#c8a800">
      <strong>Limited data</strong> — ${g.state_name} does not publish ticket sales figures, so Est. Tickets Left, Tickets Sold, and EV calculations are based on prize table odds only.
    </div>` : ""}

    <div class="modal-stats">
      <div class="modal-stat">
        <div class="modal-stat-val ${cls}">${ret != null ? ret.toFixed(2) + "%" : "N/A"}</div>
        <div class="modal-stat-lbl">Return %</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-val ${g.ev >= 0 ? "ev-positive" : ""}">${ev}</div>
        <div class="modal-stat-lbl">Net EV per ticket</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-val">$${fmtMoney(g.top_prize)}</div>
        <div class="modal-stat-lbl">Top Prize</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-val">${g.top_prize_remaining != null ? fmtNum(g.top_prize_remaining) : "—"}</div>
        <div class="modal-stat-lbl">Top Prize Remaining</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-val">${g.overall_odds_one_in ? "1 in " + fmtNum(g.overall_odds_one_in) : "—"}</div>
        <div class="modal-stat-lbl">Overall Odds</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-val">${g.tickets_remaining != null ? fmtNum(g.tickets_remaining) : "—"}</div>
        <div class="modal-stat-lbl">Est. Tickets Left</div>
        <div class="modal-stat-note">prizes remaining × overall odds</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-val">${prizePoolRemaining > 0 ? "$" + fmtMoney(prizePoolRemaining) : "—"}</div>
        <div class="modal-stat-lbl">Prize Pool Left</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-val">${faceValueOutstanding != null ? "$" + fmtMoney(faceValueOutstanding) : "—"}</div>
        <div class="modal-stat-lbl">Face Value Outstanding</div>
        <div class="modal-stat-note">est. tickets × ticket price</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-val">${g.total_tickets != null ? fmtNum(g.total_tickets) : "—"}</div>
        <div class="modal-stat-lbl">Total Tickets</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-val">${ticketsSold != null ? fmtNum(ticketsSold) : "—"}</div>
        <div class="modal-stat-lbl">Tickets Sold</div>
        <div class="modal-stat-note">total − est. remaining</div>
      </div>
    </div>

    <h3 style="margin-bottom:.75rem;font-size:1rem">Prize Table</h3>
    ${tierRows ? `
    <table class="prize-table">
      <thead>
        <tr>
          <th>Prize</th>
          <th>Odds</th>
          <th>Current Win Odds</th>
          <th>Remaining</th>
          <th>Total Printed</th>
        </tr>
      </thead>
      <tbody>${tierRows}</tbody>
    </table>` : "<p style='color:var(--text-muted)'>Prize tier data not available.</p>"}

    ${g.detail_url ? `<a class="detail-link" href="${escHtml(g.detail_url)}" target="_blank" rel="noopener">
      View on ${g.state_name} Lottery website ↗
    </a>` : ""}

    <div id="modalCommunityWrapper">${modalCommunitySection(g.name, g.price)}</div>
  `;
}

function closeModal() {
  document.getElementById("modalOverlay").classList.remove("open");
  _openModalGame = null;
}

function refreshOpenModalCommunity() {
  if (!_openModalGame) return;
  const wrapper = document.getElementById("modalCommunityWrapper");
  if (!wrapper) return;
  wrapper.innerHTML = modalCommunitySection(_openModalGame.name, _openModalGame.price);
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeModal(); closeAuthModal(); closeReportModal(); }
});

// ── Scrape trigger ────────────────────────────────────────────────────────────
async function triggerScrape() {
  const state = document.getElementById("filterState").value || null;
  if (!state) {
    alert("Select a state first — running all states at once takes 1+ hours.");
    return;
  }
  const btn = document.getElementById("scrapeBtn");
  btn.classList.add("busy");
  btn.textContent = "Scraping…";
  btn.disabled = true;
  document.getElementById("cancelScrapeBtn").style.display = "";
  try {
    await fetch(`/api/scrape?state=${state}`, { method: "POST" });
    pollScrapeStatus();
  } catch (e) {
    btn.classList.remove("busy");
    btn.textContent = "↻ Refresh Data";
    btn.disabled = false;
    document.getElementById("cancelScrapeBtn").style.display = "none";
  }
}

async function cancelScrape() {
  await fetch("/api/scrape/cancel", { method: "POST" });
  document.getElementById("cancelScrapeBtn").style.display = "none";
}

async function pollScrapeStatus() {
  const btn = document.getElementById("scrapeBtn");
  const res = await fetch("/api/scrape/status");
  const data = await res.json();
  if (data.running) {
    setTimeout(pollScrapeStatus, 3000);
  } else {
    btn.classList.remove("busy");
    btn.textContent = "↻ Refresh Data";
    btn.disabled = false;
    document.getElementById("cancelScrapeBtn").style.display = "none";
    await loadGames();
    await loadStatus();
    await loadStates();
  }
}

// ── Tab switching ─────────────────────────────────────────────────────────────
function switchTab(name) {
  currentTab = name;
  document.querySelectorAll(".tab-content").forEach(el => el.style.display = "none");
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  document.getElementById(`tab-${name}`).style.display = "";
  document.querySelector(`.tab-btn[data-tab="${name}"]`).classList.add("active");
  if (name === "ma") {
    selectHuntState(currentHuntState);
  }
  if (name === "caller" && !callerLoaded) {
    callerLoaded = true;
    loadCallerData();
    setInterval(() => { if (currentTab === "caller") loadCallerData(); }, 15_000);
  }
  if (name === "bigwins" && !bigwinsLoaded) {
    bigwinsLoaded = true;
    loadBigWins();
  }
}

function selectHuntState(code) {
  currentHuntState = code;
  document.querySelectorAll(".state-item").forEach(el =>
    el.classList.toggle("active", el.dataset.state === code)
  );

  document.getElementById("huntConsoleMA").style.display   = "none";
  document.getElementById("huntConsoleAZ").style.display   = "none";
  document.getElementById("huntConsoleSoon").style.display = "none";

  if (code === "MA") {
    document.getElementById("huntConsoleMA").style.display = "";
    if (!maLoaded) loadMaRetailers();
    if (_currentUser) loadCommunityReports();
  } else if (code === "AZ") {
    document.getElementById("huntConsoleAZ").style.display = "";
    if (!azLoaded) loadAzRetailers();
    if (_currentUser) loadCommunityReports();
  } else {
    document.getElementById("huntConsoleSoon").style.display = "";
    const nameEl = document.querySelector(`.state-item[data-state="${code}"] .state-name`);
    const stateName = nameEl?.textContent || code;
    document.getElementById("huntSoonTitle").textContent = stateName;
    document.getElementById("huntSoonSubtitle").textContent =
      "Top games by expected value. Full retailer hunt coming soon.";
    const repBtn = document.getElementById("huntSoonReportBtn");
    if (repBtn) repBtn.style.display = _currentUser ? "" : "none";
    loadGenericState(code);
  }
}

async function loadGenericState(code) {
  const container = document.getElementById("huntSoonGames");
  container.innerHTML = `<div class="loading-cell" style="padding:2rem;text-align:center">Loading ${code} games…</div>`;
  try {
    const res = await fetch(`/api/games?state=${encodeURIComponent(code)}&sort_by=return_pct&limit=20`);
    if (!res.ok) throw new Error("no data");
    const data = await res.json();
    const games = data.games || [];
    if (!games.length) {
      container.innerHTML = `<div class="hunt-soon-empty">No game data available for ${code} yet. Check back soon.</div>`;
      return;
    }
    container.innerHTML = `
      <table class="hunt-soon-table">
        <thead><tr>
          <th>Game</th><th>Price</th><th>Return %</th><th>Top Prize</th><th>Remaining</th>
        </tr></thead>
        <tbody>${games.map(g => {
          const ret = g.return_pct != null ? g.return_pct.toFixed(1) + "%" : "—";
          const retCls = g.return_pct >= 70 ? "color:var(--green)" : g.return_pct >= 55 ? "color:var(--text-muted)" : "color:var(--red)";
          const top = g.top_prize != null ? "$" + fmtNum(g.top_prize) : "—";
          const rem = g.tickets_remaining != null ? fmtNum(g.tickets_remaining) : "—";
          const price = g.price != null ? "$" + g.price.toFixed(0) : "—";
          return `<tr>
            <td><strong>${escHtml(g.name)}</strong></td>
            <td>${price}</td>
            <td style="${retCls};font-weight:600">${ret}</td>
            <td>${top}</td>
            <td>${rem}</td>
          </tr>`;
        }).join("")}</tbody>
      </table>`;
  } catch (_) {
    container.innerHTML = `<div class="hunt-soon-empty">No game data available for ${code} yet.</div>`;
  }
}

// ── MA Leaflet map ────────────────────────────────────────────────────────────

function getFilteredRows() {
  const q            = (document.getElementById("maSearchInput").value || "").toLowerCase().trim();
  const city         = (document.getElementById("maCityInput").value   || "").toLowerCase().trim();
  const invFilter    = document.getElementById("maInvFilter")?.value  || "";
  const dateFilter   = document.getElementById("maDateFilter")?.value || "";
  const showUnchecked = document.getElementById("maShowUnchecked")?.checked ?? true;

  mapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));

  if (!showUnchecked) rows = rows.filter(r => !!retailerLatestStatus[r.id]);

  if (invFilter) {
    rows = rows.filter(r => {
      const s = retailerLatestStatus[r.id];
      if (invFilter === "in")      return s && s.has_stock;
      if (invFilter === "out")     return s && !s.has_stock;
      if (invFilter === "checked") return !!s;
      return true;
    });
  }

  if (dateFilter) {
    const now = Date.now();
    const cutoffs = { today: 86400000, "7d": 7 * 86400000, "30d": 30 * 86400000 };
    const cutoff  = cutoffs[dateFilter];
    rows = rows.filter(r => {
      const s = retailerLatestStatus[r.id];
      if (!s) return false;
      return (now - parseReportedAt(s.reported_at).getTime()) <= cutoff;
    });
  }

  return rows;
}

function toggleMaMap() {
  const sec = document.getElementById("maMapSection");
  maMapVisible = !maMapVisible;
  sec.style.display = maMapVisible ? "" : "none";
  if (maMapVisible) {
    if (!maMap) initMaMap();
    else maMap.invalidateSize();
    renderMapLayers(getFilteredRows());
  }
}

function initMaMap() {
  maMap = L.map("maMap").setView([42.1, -71.8], 9);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(maMap);
}

function renderMapLayers(retailers) {
  if (!maMap) return;

  maMap.eachLayer(layer => { if (!(layer instanceof L.TileLayer)) maMap.removeLayer(layer); });
  if (maLayerControl) { maLayerControl.remove(); maLayerControl = null; }
  window._inventoryLayer = null;

  updateInventoryMapLayer(retailers);
}

// ── MA Hunt data loading ──────────────────────────────────────────────────────
async function loadMaRetailers() {
  try {
    const res = await fetch("/api/ma/retailers?limit=7000");
    const data = await res.json();
    allRetailers = data.retailers || [];
    maLoaded = true;
    updateMaStats();
    renderMaTable();
  } catch (e) {
    document.getElementById("maTableBody").innerHTML =
      `<tr><td colspan="6" class="loading-cell">Failed to load MA retailers.</td></tr>`;
  }
}

function updateMaStats() {
  document.getElementById("maStatTotal").textContent = allRetailers.length.toLocaleString();
}

function renderMaTable() {
  _openProfileId = null;
  const rows = getFilteredRows();
  const checkedCount = selectedGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedGame.name)}</strong>` : "";
  document.getElementById("maResultCount").innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("maTableBody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
  } else {
    tbody.innerHTML = rows.map((r, i) => maRow(r, i + 1)).join("");
    updateReportBadges();
  }
  if (maMapVisible) renderMapLayers(rows);
}


function downloadMaCsv() {
  let rows = getFilteredRows();

  const cols = ["name","address","city","zipCode","phone","latitude","longitude","games"];
  const header = cols.join(",");
  const csvRows = rows.map(r =>
    cols.map(c => {
      const v = String(r[c] ?? "");
      return v.includes(",") || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v;
    }).join(",")
  );
  const blob = new Blob([header + "\n" + csvRows.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `ma_retailers.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function maRow(r, rank) {
  const addr = encodeURIComponent(`${r.name}, ${r.address}, ${r.city}, MA ${r.zipCode}`);
  const mapsUrl   = `https://www.google.com/maps/search/?api=1&query=${addr}`;
  const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(r.name + ' ' + r.city + ' MA lottery')}`;
  const directionsUrl = (r.latitude && r.longitude)
    ? `https://www.google.com/maps/dir/?api=1&destination=${r.latitude},${r.longitude}`
    : mapsUrl;

  const links = `
    <a class="link-btn link-maps" href="${mapsUrl}" target="_blank" rel="noopener" title="View on Maps">Maps</a>
    <a class="link-btn link-dir"  href="${directionsUrl}" target="_blank" rel="noopener" title="Get Directions">Dir</a>
    <a class="link-btn link-srch" href="${searchUrl}" target="_blank" rel="noopener" title="Google Search">Search</a>`;

  const rid = escHtml(r.id || "");

  return `<tr class="ma-store-row" data-retailer-id="${rid}" onclick="toggleStoreProfile(this)">
    <td style="color:var(--text-muted);font-size:.8rem;font-weight:700">${rank}</td>
    <td><strong>${escHtml(r.name)}</strong><span class="report-count-badge" id="rbadge-${rid}" style="display:none"></span><br><span style="font-size:.78rem;color:var(--text-muted)">${escHtml(r.address)}</span></td>
    <td>${escHtml(r.city)}</td>
    <td>${escHtml(r.zipCode)}</td>
    <td class="last-report-cell" data-rid="${rid}">${lastReportCellHtml(rid)}</td>
    <td class="links-cell" onclick="event.stopPropagation()">${links}</td>
  </tr>`;
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function fmtNum(n) {
  if (n == null) return "—";
  n = parseFloat(n);
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 10_000)    return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function fmtMoney(n) {
  if (n == null) return "0";
  n = parseFloat(n);
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000)     return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function timeAgo(date) {
  const secs = Math.floor((Date.now() - date) / 1000);
  if (secs < 60)   return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtDate(dt) {
  if (!dt) return "";
  const d = new Date(dt.replace(" ", "T") + "Z");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "America/New_York" });
}

// ── Launch campaign from EV table ─────────────────────────────────────────────
function launchCampaign(name, price, gameId) {
  switchTab("caller");
  document.getElementById("cfGameName").value   = name;
  document.getElementById("cfGamePrice").value  = price;
  document.getElementById("cfGameNumber").value = gameId;
  document.getElementById("cfGameName").focus();
  document.getElementById("cfCreateBtn").scrollIntoView({ behavior: "smooth", block: "center" });
}

// ── Call Agent ────────────────────────────────────────────────────────────────
let callerLoaded = false;
let _callerCampaigns = [];
let _callerHits = [];

async function loadCallerData() {
  try {
    const [campRes, hitsRes, statusRes] = await Promise.all([
      callerFetch("/api/caller/campaigns"),
      callerFetch("/api/caller/hits"),
      callerFetch("/api/caller/status"),
    ]);
    const campData   = await campRes.json();
    const hitsData   = await hitsRes.json();
    const statusData = await statusRes.json();

    _callerCampaigns = campData.campaigns || [];
    _callerHits      = hitsData.hits || [];

    const totalCalls = _callerCampaigns.reduce((s, c) => s + (c.calls_made || 0), 0);
    const totalHits  = _callerCampaigns.reduce((s, c) => s + (c.hits_found || 0), 0);

    document.getElementById("callerStatHits").textContent      = totalHits.toLocaleString();
    document.getElementById("callerStatCalls").textContent     = totalCalls.toLocaleString();
    document.getElementById("callerStatFlight").textContent    = statusData.calls_in_flight ?? 0;
    document.getElementById("callerStatCampaigns").textContent = _callerCampaigns.length;

    const backendEl = document.getElementById("callerBackendBadge");
    if (backendEl) {
      const b = statusData.backend || "unknown";
      const label = b === "bland" ? "Bland AI" : b === "twilio" ? "Twilio" : "Not Configured";
      const cls   = b === "bland" ? "badge-status-active" : b === "twilio" ? "badge-status-paused" : "badge-status-idle";
      backendEl.textContent = label;
      backendEl.className   = `badge ${cls}`;
    }

    renderCallerCampaigns();
    renderCallerHits();
  } catch (e) {
    document.getElementById("callerCampaignsList").innerHTML =
      `<div class="loading-cell">Failed to load caller data. Is the server running?</div>`;
  }
}

function renderCallerCampaigns() {
  const el = document.getElementById("callerCampaignsList");
  if (!_callerCampaigns.length) {
    el.innerHTML = `<div class="loading-cell">No campaigns yet — create one above.</div>`;
    return;
  }
  el.innerHTML = _callerCampaigns.map(callerCampaignCard).join("");
}

function callerCampaignCard(c) {
  const isActive   = c.status === "active";
  const statusCls  = isActive ? "badge-status-active" : "badge-status-paused";
  const statusLabel = isActive ? "Active" : "Paused";

  const toggle = isActive
    ? `<button class="btn btn-campaign-pause" onclick="pauseCampaign(${c.id})">⏸ Pause</button>`
    : `<button class="btn btn-campaign-start" onclick="startCampaign(${c.id})">▶ Start</button>`;

  return `
    <div class="campaign-card ${c.hits_found > 0 ? "has-hits" : ""}" onclick="openCampaignDetail(${c.id})" style="cursor:pointer" title="Click to view campaign details">
      <div style="flex:1;min-width:160px">
        <div class="campaign-name">${escHtml(c.game_name)}</div>
        <div class="campaign-meta">
          ${c.game_price ? `$${c.game_price} · ` : ""}
          ${c.game_number ? `Game #${c.game_number} · ` : ""}
          Max ${c.max_stores} stores
        </div>
      </div>
      <div class="campaign-stat">
        <div class="campaign-stat-val">${(c.calls_made || 0).toLocaleString()}</div>
        <div class="campaign-stat-lbl">Calls</div>
      </div>
      <div class="campaign-stat">
        <div class="campaign-stat-val hit-val">${(c.hits_found || 0).toLocaleString()}</div>
        <div class="campaign-stat-lbl">Hits</div>
      </div>
      <span class="badge ${statusCls}" style="align-self:center">${statusLabel}</span>
      <span onclick="event.stopPropagation()">${toggle}</span>
      ${c.hits_found > 0 ? `<span onclick="event.stopPropagation()"><button class="btn" onclick="scrollToHits()" style="font-size:.78rem;padding:.3rem .8rem">View Hits ↓</button></span>` : ""}
      <span onclick="event.stopPropagation()"><button class="btn btn-campaign-delete" onclick="deleteCampaign(${c.id}, '${escHtml(c.game_name).replace(/'/g,"\\'")}')">🗑 Delete</button></span>
    </div>`;
}

function renderCallerHits() {
  const section = document.getElementById("callerHitsSection");
  const tbody   = document.getElementById("callerHitsBody");
  const countEl = document.getElementById("callerHitsCount");

  if (!_callerHits.length) {
    section.style.display = "none";
    return;
  }

  section.style.display = "";
  countEl.textContent = `${_callerHits.length.toLocaleString()} positive ${_callerHits.length === 1 ? "hit" : "hits"}`;

  tbody.innerHTML = _callerHits.map(h => {
    const conf = h.confidence != null ? parseFloat(h.confidence) : null;
    const confCls = conf == null ? "conf-low" : conf >= 0.8 ? "conf-high" : conf >= 0.5 ? "conf-mid" : "conf-low";
    const confTxt = conf != null ? (conf * 100).toFixed(0) + "%" : "—";

    const canOrder = h.can_order === 1 ? `<span style="color:var(--green)">Yes</span>`
                   : h.can_order === 0 ? `<span style="color:var(--red)">No</span>` : "—";

    const called = h.called_at
      ? timeAgo(new Date(h.called_at + (h.called_at.endsWith("Z") ? "" : "Z")))
      : "—";

    const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
      (h.name || "") + ", " + (h.address || "") + ", " + (h.city || "") + ", MA"
    )}`;

    const hasTranscript = h.transcript && h.transcript.trim().length > 0;
    const transcriptBtn = hasTranscript
      ? `<button class="btn btn-transcript" onclick="toggleTranscript(${h.queue_id})" title="View call transcript">📋</button>`
      : `<span style="color:var(--text-muted);font-size:.75rem">—</span>`;

    const transcriptRow = hasTranscript ? `
    <tr id="transcript-row-${h.queue_id}" class="transcript-row" style="display:none">
      <td colspan="10">
        <div class="transcript-box"><pre>${escHtml(h.transcript || "")}</pre></div>
      </td>
    </tr>` : "";

    return `<tr id="hit-row-${h.queue_id}">
      <td><strong><a href="${mapsUrl}" target="_blank" rel="noopener" style="color:var(--text);text-decoration:none">${escHtml(h.name || "")}</a></strong></td>
      <td>${escHtml(h.city || "")}</td>
      <td>${escHtml(h.phone || "")}</td>
      <td>${escHtml(h.game_name || "")}</td>
      <td><span class="${confCls}">${confTxt}</span></td>
      <td>${canOrder}</td>
      <td style="max-width:200px;font-size:.82rem;color:var(--text-muted)">${escHtml((h.notes || "").slice(0, 80))}</td>
      <td style="color:var(--text-muted);font-size:.8rem">${called}</td>
      <td>${transcriptBtn}</td>
      <td>
        <button class="btn btn-no-inv" onclick="markNoInventory(${h.id}, ${h.queue_id})" title="Mark store as no longer having this ticket">❌ No Stock</button>
        <button class="btn btn-dnc" onclick="markDNC(${h.queue_id})" title="Do not call this store again">🚫 DNC</button>
      </td>
    </tr>${transcriptRow}`;
  }).join("");
}

function scrollToHits() {
  document.getElementById("callerHitsSection")?.scrollIntoView({ behavior: "smooth" });
}

function toggleTranscript(queueId) {
  const row = document.getElementById(`transcript-row-${queueId}`);
  if (!row) return;
  const btn = document.querySelector(`#hit-row-${queueId} .btn-transcript`);
  const hidden = row.style.display === "none";
  row.style.display = hidden ? "" : "none";
  if (btn) btn.textContent = hidden ? "📋 ▲" : "📋";
}

async function createCallerCampaign() {
  const name   = document.getElementById("cfGameName").value.trim();
  const number = document.getElementById("cfGameNumber").value.trim();
  const price  = parseFloat(document.getElementById("cfGamePrice").value) || 0;
  const max    = parseInt(document.getElementById("cfMaxStores").value) || 200;
  const btn    = document.getElementById("cfCreateBtn");

  if (!name) {
    showCallerMsg("Game name is required.", "err");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Creating…";
  showCallerMsg("", "");

  try {
    const res = await callerFetch("/api/caller/campaigns", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game_name: name, game_number: number, game_price: price,
                             max_stores: max, call_backend: document.getElementById("cfBackend").value }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed");
    showCallerMsg(
      `Campaign #${data.campaign.id} created — ${data.queue_loaded} stores queued. Hit Start to begin calling.`,
      "ok"
    );
    document.getElementById("cfGameName").value  = "";
    document.getElementById("cfGameNumber").value = "";
    document.getElementById("cfGamePrice").value  = "";
    await loadCallerData();
  } catch (e) {
    showCallerMsg(e.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "+ Create Campaign";
  }
}

async function sendTestCall() {
  const phone  = document.getElementById("cfTestPhone").value.trim();
  const name   = document.getElementById("cfGameName").value.trim() || "Test Game";
  const number = document.getElementById("cfGameNumber").value.trim();
  const price  = parseFloat(document.getElementById("cfGamePrice").value) || 0;
  const btn    = document.getElementById("cfTestBtn");

  if (!phone) { showCallerMsg("Enter a phone number for the test call.", "err"); return; }

  btn.disabled = true;
  btn.textContent = "Calling…";
  showCallerMsg("", "");

  try {
    const res  = await callerFetch("/api/caller/test-call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone, game_name: name, game_number: number, game_price: price }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed");
    showCallerMsg(`Test call placed — you should receive a call shortly! SID: ${data.call_sid}`, "ok");
  } catch (e) {
    showCallerMsg(`Test call failed: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "📞 Send Test Call";
  }
}

function showCallerMsg(text, type) {
  const el = document.getElementById("cfMessage");
  el.style.display = text ? "" : "none";
  el.className = `caller-msg ${type}`;
  el.textContent = text;
}

async function startCampaign(id) {
  try {
    const res = await callerFetch(`/api/caller/campaigns/${id}/start`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed");
    await loadCallerData();
  } catch (e) {
    alert(`Could not start campaign: ${e.message}`);
  }
}

async function pauseCampaign(id) {
  try {
    const res = await callerFetch(`/api/caller/campaigns/${id}/pause`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed");
    await loadCallerData();
  } catch (e) {
    alert(`Could not pause campaign: ${e.message}`);
  }
}

async function deleteCampaign(id, name) {
  if (!confirm(`Delete campaign for "${name}"? This cannot be undone.`)) return;
  try {
    const res = await callerFetch(`/api/caller/campaigns/${id}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed");
    await loadCallerData();
  } catch (e) {
    alert(`Could not delete campaign: ${e.message}`);
  }
}

async function markNoInventory(resultId, queueId) {
  if (!confirm("Mark this store as no longer having the ticket? It will be removed from the hits list.")) return;
  try {
    const res = await callerFetch(`/api/caller/results/${resultId}/no_inventory`, { method: "POST" });
    if (!res.ok) throw new Error("Failed");
    _callerHits = _callerHits.filter(h => h.id !== resultId);
    renderCallerHits();
    loadCallerData();
  } catch (e) {
    alert("Could not mark no inventory.");
  }
}

async function markDNC(queueId) {
  try {
    const res = await callerFetch(`/api/caller/queue/${queueId}/dnc`, { method: "POST" });
    if (!res.ok) throw new Error("Failed");
    const row = document.getElementById(`hit-row-${queueId}`);
    if (row) {
      row.style.opacity = "0.35";
      const btn = row.querySelector(".btn-dnc");
      if (btn) { btn.textContent = "DNC'd"; btn.disabled = true; }
    }
  } catch (e) {
    alert("Could not mark DNC.");
  }
}

// ── Campaign detail view ───────────────────────────────────────────────────────
let _currentCampaignId = null;
let _detailQueueOffset = 0;
let _detailQueue = [];
let _detailMap = null;
let _detailMapFilter = "all";
let _allRetailers = [];
let _allRetailersSearch = "";
let _allRetailersFilter = "all";
const DETAIL_PAGE_SIZE = 100;

async function openCampaignDetail(id) {
  _currentCampaignId = id;
  _detailQueueOffset = 0;
  _detailQueue = [];
  _allRetailers = [];

  document.getElementById("callerFormSection").style.display        = "none";
  document.getElementById("callerCampaignsSection").style.display   = "none";
  document.getElementById("callerHitsSection").style.display        = "none";
  const detailEl = document.getElementById("callerDetailView");
  detailEl.style.display = "";
  detailEl.innerHTML     = `<div class="loading-cell" style="padding:2rem">Loading campaign…</div>`;

  try {
    const [campRes, queueRes] = await Promise.all([
      callerFetch(`/api/caller/campaigns/${id}`),
      callerFetch(`/api/caller/campaigns/${id}/queue?limit=9999&offset=0`),
    ]);
    if (!campRes.ok) throw new Error(`Campaign fetch failed: ${campRes.status}`);
    if (!queueRes.ok) throw new Error(`Queue fetch failed (${queueRes.status}) — try restarting the server`);
    const campData  = await campRes.json();
    const queueData = await queueRes.json();
    _detailQueue = queueData.queue || [];
    renderCampaignDetail(campData, _detailQueue);
    loadAllRetailers(id);
  } catch (e) {
    detailEl.innerHTML = `<div class="loading-cell" style="padding:2rem">Failed to load: ${escHtml(e.message)}</div>`;
  }
}

function closeCampaignDetail() {
  if (_detailMap) { _detailMap.remove(); _detailMap = null; }
  _currentCampaignId = null;
  document.getElementById("callerDetailView").style.display        = "none";
  document.getElementById("callerFormSection").style.display       = "";
  document.getElementById("callerCampaignsSection").style.display  = "";
  loadCallerData();
}

function renderCampaignDetail(campData, queue) {
  const c       = campData.campaign;
  const stats   = campData.queue_stats   || {};
  const results = campData.recent_results || [];

  const pending  = stats["pending"]  || 0;
  const calling  = stats["calling"]  || 0;
  const done     = stats["done"]     || 0;
  const dnc      = stats["dnc"]      || 0;
  const failed   = stats["failed"]   || 0;

  const statusCls   = c.status === "active" ? "badge-status-active" : "badge-status-paused";
  const statusLabel = c.status === "active" ? "Active" : "Paused";
  const hasMore     = queue.length >= DETAIL_PAGE_SIZE;

  document.getElementById("callerDetailView").innerHTML = `
    <div class="detail-header">
      <button class="btn btn-back" onclick="closeCampaignDetail()">← Back</button>
      <div class="detail-title-block">
        <span class="detail-game-name">${escHtml(c.game_name)}</span>
        <span class="badge ${statusCls}" style="margin-left:.6rem">${statusLabel}</span>
      </div>
      <div class="detail-meta">
        ${c.game_price ? `$${c.game_price} ticket` : ""}
        ${c.game_number ? ` · Game #${c.game_number}` : ""}
      </div>
    </div>

    <div id="detailMapContainer" class="detail-map-top" style="display:none"></div>
    <div id="mapFilterBar" class="map-filter-bar" style="display:none">
      <span style="font-size:.78rem;color:var(--text-muted);font-weight:600">MAP:</span>
      <button class="map-filter-btn active" data-filter="all"    onclick="setMapFilter('all')">All</button>
      <button class="map-filter-btn" data-filter="pending"  onclick="setMapFilter('pending')"><span style="color:#00e5ff">●</span> In Queue</button>
      <button class="map-filter-btn" data-filter="no_stock" onclick="setMapFilter('no_stock')"><span style="color:#ff4444">●</span> No Stock</button>
      <button class="map-filter-btn" data-filter="hit"      onclick="setMapFilter('hit')"><span style="color:#00ff88">●</span> Has It</button>
      <button class="map-filter-btn" data-filter="unchecked" onclick="setMapFilter('unchecked')"><span style="color:#555">●</span> Unchecked</button>
    </div>

    <div class="detail-stats-row">
      <div class="detail-stat-card">
        <div class="detail-stat-val">${(c.calls_made || 0).toLocaleString()}</div>
        <div class="detail-stat-lbl">Calls Made</div>
      </div>
      <div class="detail-stat-card detail-stat-green">
        <div class="detail-stat-val">${(c.hits_found || 0).toLocaleString()}</div>
        <div class="detail-stat-lbl">Hits Found</div>
      </div>
      <div class="detail-stat-card">
        <div class="detail-stat-val">${pending.toLocaleString()}</div>
        <div class="detail-stat-lbl">Pending</div>
      </div>
      <div class="detail-stat-card">
        <div class="detail-stat-val">${done.toLocaleString()}</div>
        <div class="detail-stat-lbl">Checked</div>
      </div>
      <div class="detail-stat-card">
        <div class="detail-stat-val">${(dnc + failed).toLocaleString()}</div>
        <div class="detail-stat-lbl">DNC / Failed</div>
      </div>
    </div>

    ${results.length > 0 ? `
    <section class="table-section">
      <div class="table-meta"><span>Recent Call Results</span></div>
      <div class="detail-results-list">
        ${results.map(renderResultCard).join("")}
      </div>
    </section>` : ""}

    <section class="table-section" style="margin-top:1rem">
      <div class="table-meta">
        <span>All Stores <span style="color:var(--text-muted);font-size:.82rem">— 6,909 MA retailers · highest scored first</span></span>
        <button class="btn" onclick="refreshDetail()" style="font-size:.78rem;padding:.3rem .8rem">↻ Refresh</button>
      </div>
      <div class="all-retailers-controls" id="allRetailersControls">
        <input id="allRetSearch" class="detail-search" type="text" placeholder="Search name or city…"
          oninput="filterAllRetailers()" />
        <select id="allRetFilter" class="detail-search" onchange="filterAllRetailers()">
          <option value="all">All Statuses</option>
          <option value="unchecked">Unchecked</option>
          <option value="pending">In Queue</option>
          <option value="hit">✅ Has It</option>
          <option value="no_stock">❌ No Stock</option>
          <option value="dnc">DNC</option>
        </select>
      </div>
      <div id="allRetailersBody"><div class="loading-cell" style="padding:1rem">Loading stores…</div></div>
    </section>

    <!-- Check modal -->
    <div id="checkModal" class="check-modal-overlay" style="display:none" onclick="if(event.target===this)closeCheckModal()">
      <div class="check-modal">
        <div class="check-modal-title" id="checkModalTitle">Mark Store</div>
        <div style="font-size:.85rem;color:var(--text-muted);margin-bottom:1rem" id="checkModalStore"></div>
        <label style="font-size:.8rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em">Notes (optional)</label>
        <textarea id="checkModalNotes" class="check-modal-notes" placeholder="e.g. Spoke to manager, they're sold out…" rows="3"></textarea>
        <div style="font-size:.75rem;color:var(--text-muted);margin-top:.35rem">Visit date: ${new Date().toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'})}</div>
        <div style="display:flex;gap:.75rem;margin-top:1.25rem">
          <button id="checkModalConfirm" class="btn btn-campaign-start" style="flex:1" onclick="confirmCheck()">Confirm</button>
          <button class="btn" style="flex:0 0 auto" onclick="closeCheckModal()">Cancel</button>
        </div>
      </div>
    </div>
  `;
}

function filterDetailQueue() {
  const q = (document.getElementById("queueSearch")?.value || "").toLowerCase();
  const s = document.getElementById("queueStatusFilter")?.value || "";
  let filtered = _detailQueue;
  if (q) filtered = filtered.filter(r => r.name.toLowerCase().includes(q) || (r.city || "").toLowerCase().includes(q));
  if (s) filtered = filtered.filter(r => r.status === s);
  document.getElementById("detailQueueBody").innerHTML = renderQueueRows(filtered, 0);
}

function initDetailMap(queue) {
  const mapEl = document.getElementById("detailMapContainer");
  if (!mapEl) return;
  const withCoords = queue.filter(q => q.lat && q.lng);
  if (!withCoords.length) return;

  mapEl.style.display = "";
  if (_detailMap) { _detailMap.remove(); _detailMap = null; }
  _detailMap = L.map("detailMapContainer").setView([42.3, -71.8], 8);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
    maxZoom: 18,
  }).addTo(_detailMap);

  withCoords.forEach(q => {
    const color = q.status === "pending" ? "#00e5ff"
                : q.status === "done"    ? "#888"
                : q.status === "dnc"     ? "#ff4444"
                : "#aaa";
    const marker = L.circleMarker([q.lat, q.lng], {
      radius: 7, color, fillColor: color, fillOpacity: 0.85, weight: 1.5,
    }).addTo(_detailMap);
    marker.bindPopup(`<strong>${escHtml(q.name)}</strong><br>${escHtml(q.city || "")}
      ${q.status === "pending" ? `<br><br><button onclick="markQueueNoInventory(${q.id})" style="font-size:.8rem;cursor:pointer">❌ Mark No Stock</button>` : ""}`
    );
  });
}

async function loadAllRetailers(campaignId) {
  const btn  = document.getElementById("loadAllBtn");
  const body = document.getElementById("allRetailersBody");
  if (body) body.innerHTML = `<div class="loading-cell" style="padding:.75rem">Loading stores…</div>`;
  if (btn)  btn.style.display = "none";

  try {
    const res = await callerFetch(`/api/caller/campaigns/${campaignId}/retailers`);
    if (!res.ok) throw new Error("Failed to load");
    const data = await res.json();
    _allRetailers = data.retailers || [];
    updateDetailMapAllRetailers(_allRetailers);
    renderAllRetailers();
  } catch (e) {
    if (body) body.innerHTML = `<div style="color:var(--red);padding:.5rem">Failed to load stores.</div>`;
  }
}

function updateDetailMapAllRetailers(retailers) {
  if (!_detailMap) {
    const mapEl = document.getElementById("detailMapContainer");
    if (!mapEl) return;
    const withCoords = retailers.filter(r => r.lat && r.lng).slice(0, 500);
    if (!withCoords.length) return;
    mapEl.style.display = "";
    _detailMap = L.map("detailMapContainer").setView([42.3, -71.8], 8);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors", maxZoom: 18,
    }).addTo(_detailMap);
  } else {
    // Clear existing markers
    _detailMap.eachLayer(l => { if (l instanceof L.CircleMarker) _detailMap.removeLayer(l); });
  }

  const mapBar = document.getElementById("mapFilterBar");
  if (mapBar) mapBar.style.display = "flex";

  let withCoords = retailers.filter(r => r.lat && r.lng);
  if (_detailMapFilter && _detailMapFilter !== "all") {
    withCoords = withCoords.filter(r => r.campaign_status === _detailMapFilter);
  }
  withCoords.forEach(r => {
    const cs = r.campaign_status;
    const color = cs === "hit"       ? "#00ff88"
                : cs === "no_stock"  ? "#ff4444"
                : cs === "pending"   ? "#00e5ff"
                : cs === "done"      ? "#888"
                : "#555";
    const marker = L.circleMarker([r.lat, r.lng], {
      radius: cs === "unchecked" ? 5 : 7,
      color, fillColor: color, fillOpacity: cs === "unchecked" ? 0.35 : 0.85, weight: 1.5,
    }).addTo(_detailMap);
    const canMark = !["hit", "no_stock"].includes(cs);
    marker.bindPopup(`<strong>${escHtml(r.name)}</strong><br>${escHtml(r.city || "")}
      ${canMark ? `<br><br>
        <button onclick="manualCheckRetailer('${r.id}',${_currentCampaignId},'${escHtml(r.name)}','${escHtml(r.address||"")}','${escHtml(r.city||"")}','${escHtml(r.phone||"")}',${r.lat||"null"},${r.lng||"null"},0)" style="font-size:.8rem;cursor:pointer;margin-right:.3rem">❌ No Stock</button>
        <button onclick="manualCheckRetailer('${r.id}',${_currentCampaignId},'${escHtml(r.name)}','${escHtml(r.address||"")}','${escHtml(r.city||"")}','${escHtml(r.phone||"")}',${r.lat||"null"},${r.lng||"null"},1)" style="font-size:.8rem;cursor:pointer">✅ Has It</button>
      ` : `<br><span style="font-size:.8rem;color:#aaa">${cs === "hit" ? "✅ Has Ticket" : `❌ No Stock${r.checked_at ? ` <span style="color:#888;font-size:.75rem">(${fmtDate(r.checked_at)})</span>` : ""}`}</span>`}
    `);
  });
}

function filterAllRetailers() {
  _allRetailersSearch = (document.getElementById("allRetSearch")?.value || "").toLowerCase();
  _allRetailersFilter = document.getElementById("allRetFilter")?.value || "all";
  renderAllRetailers();
}

function renderAllRetailers() {
  const body = document.getElementById("allRetailersBody");
  if (!body) return;

  let list = _allRetailers;
  if (_allRetailersSearch) {
    list = list.filter(r => r.name.toLowerCase().includes(_allRetailersSearch)
                         || (r.city || "").toLowerCase().includes(_allRetailersSearch));
  }
  if (_allRetailersFilter !== "all") {
    list = list.filter(r => r.campaign_status === _allRetailersFilter);
  }

  const showing = list.slice(0, 150);
  const statusBadge = (cs, checkedAt) => ({
    hit:       `<span class="q-status" style="background:#e6fff0;color:var(--green);border:1px solid var(--green)">✅ Has It</span>`,
    no_stock:  `<span class="q-status q-dnc">❌ No Stock${checkedAt ? `<br><span style="font-weight:normal;font-size:.72rem;color:#aaa">${fmtDate(checkedAt)}</span>` : ""}</span>`,
    pending:   `<span class="q-status q-pending">In Queue</span>`,
    calling:   `<span class="q-status q-calling">Calling…</span>`,
    done:      `<span class="q-status q-done">Done</span>`,
    dnc:       `<span class="q-status q-dnc">DNC</span>`,
    unchecked: `<span class="q-status" style="color:var(--text-muted);background:none">—</span>`,
  }[cs] || `<span class="q-status">${escHtml(cs)}</span>`);

  const canMark = (cs) => !["hit", "no_stock", "calling"].includes(cs);

  body.innerHTML = `
    <div style="font-size:.8rem;color:var(--text-muted);margin-bottom:.5rem">
      Showing ${showing.length.toLocaleString()} of ${list.length.toLocaleString()} stores
      ${list.length < _allRetailers.length ? `(${_allRetailers.length.toLocaleString()} total)` : ""}
    </div>
    <div class="table-scroll">
      <table>
        <thead><tr>
          <th style="width:2.5rem">#</th>
          <th>Store</th><th>City</th><th>Phone</th>
          <th>Status</th><th style="text-align:center">Tries</th><th></th>
        </tr></thead>
        <tbody>
          ${showing.map((r, i) => {
            const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent((r.name||"") + " " + (r.city||"") + " MA")}`;
            const rid = r.id.replace(/'/g, "\\'");
            const rname = escHtml(r.name).replace(/'/g, "\\'");
            const raddr = escHtml(r.address||"").replace(/'/g, "\\'");
            const rcity = escHtml(r.city||"").replace(/'/g, "\\'");
            const rphone = escHtml(r.phone||"").replace(/'/g, "\\'");
            const actionBtn = canMark(r.campaign_status)
              ? `<button class="btn btn-no-inv" onclick="openCheckModal('${rid}',${_currentCampaignId},'${rname}','${raddr}','${rcity}','${rphone}',${r.lat||"null"},${r.lng||"null"},0)">❌ No Stock</button>
                 <button class="btn btn-has-it" onclick="openCheckModal('${rid}',${_currentCampaignId},'${rname}','${raddr}','${rcity}','${rphone}',${r.lat||"null"},${r.lng||"null"},1)">✅ Has It</button>`
              : "";
            return `<tr id="allret-${r.id}">
              <td style="color:var(--text-muted);font-size:.78rem">${i + 1}</td>
              <td><a href="${mapsUrl}" target="_blank" rel="noopener" style="color:var(--text);text-decoration:none"><strong>${escHtml(r.name)}</strong></a></td>
              <td>${escHtml(r.city||"")}</td>
              <td style="font-size:.78rem;color:var(--text-muted)">${escHtml(r.phone||"—")}</td>
              <td id="allret-status-${r.id}">${statusBadge(r.campaign_status, r.campaign_status === "no_stock" ? r.checked_at : null)}</td>
              <td style="text-align:center;color:var(--text-muted);font-size:.82rem">${r.attempts || 0}</td>
              <td id="allret-actions-${r.id}">${actionBtn}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
    </div>
    ${list.length > 150 ? `<div style="text-align:center;padding:.75rem;color:var(--text-muted);font-size:.82rem">Showing first 150 — search or filter to narrow results</div>` : ""}
  `;
}

// ── Check modal ────────────────────────────────────────────────────────────────
let _checkPending = null;

function openCheckModal(retailerId, campaignId, name, address, city, phone, lat, lng, hasInventory) {
  _checkPending = { retailerId, campaignId, name, address, city, phone, lat, lng, hasInventory };
  const title = hasInventory ? "✅ Mark as Has Ticket" : "❌ Mark as No Stock";
  document.getElementById("checkModalTitle").textContent = title;
  document.getElementById("checkModalStore").textContent = `${name} — ${city}`;
  document.getElementById("checkModalNotes").value = "";
  document.getElementById("checkModalConfirm").style.background = hasInventory ? "var(--green)" : "var(--red)";
  document.getElementById("checkModal").style.display = "flex";
  setTimeout(() => document.getElementById("checkModalNotes").focus(), 50);
}

function closeCheckModal() {
  document.getElementById("checkModal").style.display = "none";
  _checkPending = null;
}

async function confirmCheck() {
  if (!_checkPending) return;
  const { retailerId, campaignId, name, address, city, phone, lat, lng, hasInventory } = _checkPending;
  const notes = document.getElementById("checkModalNotes").value.trim();
  closeCheckModal();
  await manualCheckRetailer(retailerId, campaignId, name, address, city, phone, lat, lng, hasInventory, notes);
}

// ── Map filter ─────────────────────────────────────────────────────────────────
function setMapFilter(filter) {
  _detailMapFilter = filter;
  document.querySelectorAll(".map-filter-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.filter === filter);
  });
  if (_allRetailers.length > 0) updateDetailMapAllRetailers(_allRetailers);
}

function refreshDetailMap() {
  if (_allRetailers.length > 0) {
    updateDetailMapAllRetailers(_allRetailers);
  } else {
    initDetailMap(_detailQueue);
  }
}

async function manualCheckRetailer(retailerId, campaignId, name, address, city, phone, lat, lng, hasInventory, notes = "") {
  try {
    const res = await callerFetch(`/api/caller/campaigns/${campaignId}/manual_check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        retailer_id: retailerId, name, address, city, phone,
        lat: lat || null, lng: lng || null,
        has_inventory: hasInventory, notes,
      }),
    });
    if (!res.ok) throw new Error("Failed");
    const r = _allRetailers.find(x => x.id === retailerId);
    if (r) {
      r.campaign_status = hasInventory ? "hit" : "no_stock";
      r.has_inventory = hasInventory;
    }
    renderAllRetailers();
    refreshDetailMap();
  } catch (e) {
    alert("Could not save check.");
  }
}

async function markQueueNoInventory(queueId) {
  if (!confirm("Mark this store as no inventory? It will be removed from the pending queue.")) return;
  try {
    const res = await callerFetch(`/api/caller/queue/${queueId}/no_inventory`, { method: "POST" });
    if (!res.ok) throw new Error("Failed");
    // Update queue entry
    const entry = _detailQueue.find(q => q.id === queueId);
    if (entry) entry.status = "done";
    // Sync into _allRetailers if loaded
    const retailerEntry = _allRetailers.find(r => r.queue_id === queueId);
    if (retailerEntry) {
      retailerEntry.campaign_status = "no_stock";
      retailerEntry.has_inventory = 0;
    }
    filterDetailQueue();
    refreshDetailMap();
    if (_allRetailers.length > 0) renderAllRetailers();
  } catch (e) {
    alert("Could not mark no inventory.");
  }
}

function renderQueueRows(queue, startOffset) {
  if (!queue || !queue.length) return `<tr><td colspan="7" class="loading-cell">No stores in queue.</td></tr>`;
  return queue.map((q, i) => {
    const statusHtml = {
      pending:   `<span class="q-status q-pending">Pending</span>`,
      calling:   `<span class="q-status q-calling">Calling…</span>`,
      done:      `<span class="q-status q-done">Done</span>`,
      dnc:       `<span class="q-status q-dnc">DNC</span>`,
      failed:    `<span class="q-status q-failed">Failed</span>`,
      voicemail: `<span class="q-status q-failed">Voicemail</span>`,
    }[q.status] || `<span class="q-status">${escHtml(q.status)}</span>`;

    const noStockBtn = (q.status === "pending" || q.status === "failed" || q.status === "voicemail")
      ? `<button class="btn btn-no-inv" onclick="markQueueNoInventory(${q.id})" title="I checked — they don't have it">❌ No Stock</button>`
      : "";

    return `<tr id="qrow-${q.id}">
      <td style="color:var(--text-muted);font-size:.82rem">${startOffset + i + 1}</td>
      <td><strong>${escHtml(q.name)}</strong></td>
      <td>${escHtml(q.city || "—")}</td>
      <td style="font-size:.8rem;color:var(--text-muted)">${escHtml(q.phone || "—")}</td>
      <td>${statusHtml}</td>
      <td style="text-align:center;color:var(--text-muted)">${q.attempts || 0}</td>
      <td>${noStockBtn}</td>
    </tr>`;
  }).join("");
}

function renderResultCard(r) {
  const hasGame = r.has_game === 1 ? "✅ Has Ticket"
               : r.has_game === 0 ? "❌ No Stock"
               : "❓ Unknown";
  const cardCls = r.has_game === 1 ? "result-hit" : r.has_game === 0 ? "result-miss" : "";
  const conf = r.confidence != null ? `${(parseFloat(r.confidence) * 100).toFixed(0)}% conf` : "";
  const called = r.called_at
    ? timeAgo(new Date(r.called_at + (r.called_at.endsWith("Z") ? "" : "Z")))
    : "—";

  return `
  <div class="result-card ${cardCls}">
    <div class="result-card-header">
      <div>
        <strong>${escHtml(r.name || "Unknown Store")}</strong>
        <span style="color:var(--text-muted);font-size:.82rem;margin-left:.5rem">${escHtml(r.city || "")}${r.phone ? " · " + escHtml(r.phone) : ""}</span>
      </div>
      <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">
        <span class="result-outcome ${r.has_game === 1 ? "outcome-hit" : r.has_game === 0 ? "outcome-miss" : ""}">${hasGame}</span>
        ${conf ? `<span style="color:var(--text-muted);font-size:.8rem">${conf}</span>` : ""}
        <span style="color:var(--text-muted);font-size:.8rem">${called}</span>
        ${r.transcript ? `<button class="btn btn-transcript" onclick="toggleResultTranscript(${r.id})">📋 Transcript</button>` : ""}
      </div>
    </div>
    ${r.notes ? `<div class="result-notes">${escHtml(r.notes)}</div>` : ""}
    ${r.transcript ? `
    <div id="result-transcript-${r.id}" class="result-transcript" style="display:none">
      ${renderTranscriptBubbles(r.transcript)}
    </div>` : ""}
  </div>`;
}

function renderTranscriptBubbles(transcript) {
  if (!transcript || !transcript.trim()) return "";
  const lines = transcript.split("\n").filter(l => l.trim());
  if (!lines.length) return `<pre style="font-size:.78rem;white-space:pre-wrap">${escHtml(transcript)}</pre>`;

  return lines.map(line => {
    const aiMatch  = line.match(/^(ai|agent|bot|bland|automated|assistant):\s*(.*)/i);
    const humMatch = line.match(/^(human|user|store|person|customer|caller):\s*(.*)/i);
    if (aiMatch) {
      return `<div class="bubble bubble-ai"><span class="bubble-label">AI</span>${escHtml(aiMatch[2])}</div>`;
    } else if (humMatch) {
      return `<div class="bubble bubble-human"><span class="bubble-label">Store</span>${escHtml(humMatch[2])}</div>`;
    }
    return `<div class="bubble bubble-neutral">${escHtml(line)}</div>`;
  }).join("");
}

function toggleResultTranscript(id) {
  const el = document.getElementById(`result-transcript-${id}`);
  if (!el) return;
  el.style.display = el.style.display === "none" ? "" : "none";
}

async function loadMoreQueue() {
  _detailQueueOffset += DETAIL_PAGE_SIZE;
  try {
    const res  = await callerFetch(`/api/caller/campaigns/${_currentCampaignId}/queue?limit=${DETAIL_PAGE_SIZE}&offset=${_detailQueueOffset}`);
    const data = await res.json();
    const newRows = data.queue || [];
    _detailQueue = _detailQueue.concat(newRows);
    const tbody = document.getElementById("detailQueueBody");
    if (tbody) tbody.insertAdjacentHTML("beforeend", renderQueueRows(newRows, _detailQueueOffset));
  } catch (e) {
    alert("Failed to load more stores.");
  }
}

async function refreshDetail() {
  if (_currentCampaignId) openCampaignDetail(_currentCampaignId);
}

async function expandQueue(campaignId) {
  const btn = document.getElementById("expandQueueBtn");
  if (btn) { btn.disabled = true; btn.textContent = "Adding stores…"; }
  try {
    const res  = await callerFetch(`/api/caller/campaigns/${campaignId}/expand`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed");
    await openCampaignDetail(campaignId);
  } catch (e) {
    alert(`Could not expand queue: ${e.message}`);
    if (btn) { btn.disabled = false; btn.textContent = "+ Load All Stores"; }
  }
}

// ── Inventory Report Modal ────────────────────────────────────────────────────

function openReportModal() {
  if (!_currentUser) { openAuthModal("login"); return; }
  document.getElementById("reportModalOverlay").classList.add("open");
  document.getElementById("reportStoreSearch").value = "";
  document.getElementById("reportStoreDropdown").style.display = "none";
  document.getElementById("reportSelectedStore").style.display = "none";
  document.getElementById("reportRetailerId").value = "";
  document.getElementById("reportRetailerName").value = "";
  document.getElementById("reportRetailerCity").value = "";
  document.getElementById("reportRetailerLat").value = "";
  document.getElementById("reportRetailerLng").value = "";
  document.getElementById("reportGameName").value = "";
  document.getElementById("reportGameDropdown").style.display = "none";
  document.getElementById("reportLinkedGameId").value = "";
  document.getElementById("reportGamePrice").value = "";
  document.getElementById("reportNotes").value = "";
  document.getElementById("reportDate").value = new Date().toLocaleDateString("en-CA");
  document.getElementById("reportMsg").style.display = "none";
  setReportStock(true);
}

function closeReportModal() {
  document.getElementById("reportModalOverlay").classList.remove("open");
}

function setReportStock(inStock) {
  _reportStock = inStock;
  document.getElementById("reportBtnIn").className  = "btn stock-btn" + (inStock ? " stock-btn-active" : "");
  document.getElementById("reportBtnOut").className = "btn stock-btn" + (!inStock ? " stock-btn-active" : "");
}

function searchReportGames() {
  const q = document.getElementById("reportGameName").value.trim().toLowerCase();
  const dd = document.getElementById("reportGameDropdown");
  document.getElementById("reportLinkedGameId").value = "";
  if (!q || q.length < 2) { dd.style.display = "none"; return; }
  const matches = allGames.filter(g => g.name.toLowerCase().includes(q)).slice(0, 8);
  if (!matches.length) { dd.style.display = "none"; return; }
  dd.innerHTML = matches.map(g =>
    `<div class="store-option" onclick="selectReportGame(${g.id}, ${JSON.stringify(g.name).replace(/"/g, '&quot;')}, ${g.price != null ? g.price : 'null'})">${escHtml(g.name)} <span style="color:var(--text-muted);font-size:.78rem">$${g.price} · ${g.state_code}</span></div>`
  ).join("");
  dd.style.display = "";
}

function selectReportGame(id, name, price) {
  document.getElementById("reportGameName").value = name;
  document.getElementById("reportLinkedGameId").value = id;
  if (price != null) document.getElementById("reportGamePrice").value = price;
  document.getElementById("reportGameDropdown").style.display = "none";
}

let _reportSearchTimer = null;
function searchReportStores() {
  clearTimeout(_reportSearchTimer);
  _reportSearchTimer = setTimeout(async () => {
    const q = document.getElementById("reportStoreSearch").value.trim();
    const dd = document.getElementById("reportStoreDropdown");
    if (!q || q.length < 2) { dd.style.display = "none"; return; }
    try {
      const endpoint = currentHuntState === 'AZ' ? '/api/az/retailers' : '/api/ma/retailers';
      const res = await fetch(`${endpoint}?search=${encodeURIComponent(q)}&limit=12`);
      const data = await res.json();
      if (!data.retailers?.length) { dd.style.display = "none"; return; }
      dd.innerHTML = data.retailers.map(r =>
        `<div class="store-option" onclick='selectReportStore(${JSON.stringify(r).replace(/'/g, "&#39;")})'>${escHtml(r.name)} <span style="color:var(--text-muted);font-size:.78rem">${escHtml(r.city || "")}</span></div>`
      ).join("");
      dd.style.display = "";
    } catch (_) {}
  }, 220);
}

function selectReportStore(r) {
  document.getElementById("reportRetailerId").value    = r.id;
  document.getElementById("reportRetailerName").value  = r.name || "";
  document.getElementById("reportRetailerCity").value  = r.city || "";
  document.getElementById("reportRetailerLat").value   = r.latitude  || r.lat  || "";
  document.getElementById("reportRetailerLng").value   = r.longitude || r.lng  || "";
  document.getElementById("reportStoreSearch").value   = "";
  document.getElementById("reportStoreDropdown").style.display = "none";
  const sel = document.getElementById("reportSelectedStore");
  sel.textContent  = `${r.name} — ${r.city || ""}`;
  sel.style.display = "";
}

async function submitInventoryReport() {
  const retailerId = document.getElementById("reportRetailerId").value;
  const gameName   = document.getElementById("reportGameName").value.trim();
  const msgEl      = document.getElementById("reportMsg");

  if (!retailerId) {
    msgEl.style.display = ""; msgEl.className = "caller-msg err";
    msgEl.textContent = "Please search for and select a store."; return;
  }
  if (!gameName) {
    msgEl.style.display = ""; msgEl.className = "caller-msg err";
    msgEl.textContent = "Please enter a game name."; return;
  }

  const btn = document.getElementById("reportSubmitBtn");
  btn.disabled = true; btn.textContent = "Submitting…";

  try {
    const body = {
      retailer_id:   retailerId,
      retailer_name: document.getElementById("reportRetailerName").value,
      retailer_city: document.getElementById("reportRetailerCity").value,
      lat:           parseFloat(document.getElementById("reportRetailerLat").value) || null,
      lng:           parseFloat(document.getElementById("reportRetailerLng").value) || null,
      game_name:     gameName,
      game_price:    parseFloat(document.getElementById("reportGamePrice").value)   || null,
      has_stock:     _reportStock,
      notes:         document.getElementById("reportNotes").value.trim(),
      reported_at:   document.getElementById("reportDate").value || null,
    };
    const res = await protectedFetch("/api/inventory/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed");
    msgEl.style.display = ""; msgEl.className = "caller-msg ok";
    msgEl.textContent = "✅ Report submitted! Thanks for contributing.";
    setTimeout(closeReportModal, 1800);
    await loadCommunityReports();
  } catch (e) {
    msgEl.style.display = ""; msgEl.className = "caller-msg err";
    msgEl.textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = "Submit Report";
  }
}

async function loadCommunityReports() {
  if (!_currentUser) return;
  try {
    const res  = await protectedFetch("/api/inventory/reports?limit=500");
    if (!res.ok) return;
    const data = await res.json();
    communityReports = data.reports || [];
    buildLatestStatusFromReports();
    updateReportBadges();
    renderMaTable();
    renderAzTable();
    refreshOpenProfile();
    refreshOpenModalCommunity();
    if (currentHuntState === 'AZ') updateAzInventoryMapLayer();
    else updateInventoryMapLayer();
  } catch (_) {}
}

// ── Store profile (inline expand) ─────────────────────────────────────────────

function toggleStoreProfile(tr) {
  const rid = tr.dataset.retailerId;

  // Remove any currently open profile
  document.querySelectorAll(".store-profile-tr").forEach(el => el.remove());
  document.querySelectorAll(".store-profile-open").forEach(el => el.classList.remove("store-profile-open"));

  if (_openProfileId === rid) {
    _openProfileId = null;
    return;
  }

  _openProfileId = rid;
  tr.classList.add("store-profile-open");
  const profileTr = document.createElement("tr");
  profileTr.className = "store-profile-tr";
  profileTr.innerHTML = `<td colspan="6">${storeProfileHtml(rid)}</td>`;
  tr.insertAdjacentElement("afterend", profileTr);
}

function storeProfileHtml(retailerId) {
  if (!_currentUser) {
    const cnt = retailerCounts[retailerId] || 0;
    const cntMsg = cnt > 0
      ? `<strong>${cnt} community report${cnt > 1 ? "s" : ""}</strong> for this store — log in to view.`
      : "Community reports are <strong>members only</strong>.";
    return `<div class="store-profile-panel">
      <div class="profile-login-gate">
        <span>🔒 ${cntMsg}</span>
        <button class="btn btn-login" onclick="openAuthModal('login')">Log In</button>
        <button class="btn btn-register" onclick="openAuthModal('register')">Join Free</button>
      </div>
    </div>`;
  }

  const reports = communityReports.filter(r => r.retailer_id === retailerId);

  let reportsHtml;
  if (!reports.length) {
    reportsHtml = `<div class="profile-no-reports">No community reports yet for this store. Be the first!</div>`;
  } else {
    reportsHtml = `<div class="profile-reports-list">` + reports.map(r => {
      const stock = r.has_stock
        ? '<span style="color:var(--green);font-weight:600">✅ In Stock</span>'
        : '<span style="color:var(--red)">❌ Out of Stock</span>';
      const who = r.source === "caller" ? "📞 Call" : (r.reporter_username ? `@${escHtml(r.reporter_username)}` : "👤");
      const time = r.reported_at
        ? timeAgo(new Date(r.reported_at + (r.reported_at.endsWith("Z") ? "" : "Z")))
        : "—";
      const price = r.game_price ? ` <span style="color:var(--text-muted);font-size:.78rem">$${r.game_price}</span>` : "";
      return `<div class="profile-report-item">
        <span class="profile-report-game">${escHtml(r.game_name || "")}${price}</span>
        <span>${stock}</span>
        <span class="profile-report-meta">${who} · ${time}${r.notes ? ` · <em>${escHtml(r.notes)}</em>` : ""}</span>
      </div>`;
    }).join("") + `</div>`;
  }

  return `<div class="store-profile-panel">
    <div class="profile-header">
      <span class="profile-title">Community Reports</span>
      <button class="btn btn-report profile-add-btn" onclick="openReportModalForStore('${escHtml(retailerId)}')">+ Add Report</button>
    </div>
    ${reportsHtml}
  </div>`;
}

function normalizeGameName(name) {
  return (name || "").toLowerCase().replace(/[^a-z0-9\s]/g, "").replace(/\s+/g, " ").trim();
}

function modalCommunitySection(gameName, gamePrice) {
  const count = gameCounts[gameName.toLowerCase()] || 0;
  if (!count && !_currentUser) return "";

  if (!_currentUser) {
    return `<div class="modal-community-section">
      <div class="modal-community-title">📍 Community Reports</div>
      <div class="modal-community-gate">
        <span>${count} member report${count > 1 ? "s" : ""} for this game.</span>
        <button class="btn btn-login" onclick="closeModal();openAuthModal('login')" style="font-size:.78rem;padding:.3rem .75rem">Log In to See</button>
        <button class="btn btn-register" onclick="closeModal();openAuthModal('register')" style="font-size:.78rem;padding:.3rem .75rem">Join Free</button>
      </div>
    </div>`;
  }

  const normGame = normalizeGameName(gameName);
  const reports = communityReports.filter(r => normalizeGameName(r.game_name) === normGame);
  const addBtn = `<button class="btn btn-report" onclick="openReportModalForGame(${JSON.stringify(gameName)},${gamePrice != null ? gamePrice : "null"})" style="font-size:.78rem;padding:.3rem .75rem">+ Add Report</button>`;

  if (!reports.length) {
    return `<div class="modal-community-section">
      <div class="modal-community-title" style="display:flex;align-items:center;justify-content:space-between">
        <span>📍 Community Reports</span>${addBtn}
      </div>
      <div class="profile-no-reports">No member reports yet for this game in MA.</div>
    </div>`;
  }

  const items = reports.map(r => {
    const stock = r.has_stock
      ? '<span style="color:var(--green);font-weight:600">✅ In Stock</span>'
      : '<span style="color:var(--red)">❌ Out of Stock</span>';
    const who = r.source === "caller" ? "📞 Call" : (r.reporter_username ? `@${escHtml(r.reporter_username)}` : "👤");
    const time = r.reported_at
      ? timeAgo(new Date(r.reported_at + (r.reported_at.endsWith("Z") ? "" : "Z")))
      : "—";
    const mapsUrl = r.lat && r.lng
      ? `https://www.google.com/maps/search/?api=1&query=${r.lat},${r.lng}`
      : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent((r.retailer_name || "") + ", " + (r.retailer_city || "") + ", MA")}`;
    return `<div class="profile-report-item">
      <span class="profile-report-game"><a href="${mapsUrl}" target="_blank" rel="noopener" class="report-location-link">${escHtml(r.retailer_name || "")}</a> <span style="color:var(--text-muted);font-weight:400;font-size:.77rem">${escHtml(r.retailer_city || "")}</span></span>
      <span>${stock}</span>
      <span class="profile-report-meta">${who} · ${time}${r.notes ? ` · <em>${escHtml(r.notes)}</em>` : ""}</span>
    </div>`;
  }).join("");

  return `<div class="modal-community-section">
    <div class="modal-community-title" style="display:flex;align-items:center;justify-content:space-between">
      <span>📍 Community Reports <span style="color:var(--text-muted);font-weight:400;font-size:.82rem">(MA)</span></span>${addBtn}
    </div>
    <div class="profile-reports-list">${items}</div>
  </div>`;
}

function openReportModalForStore(retailerId) {
  const r = allRetailers.find(ret => String(ret.id) === String(retailerId))
         || allAzRetailers.find(ret => String(ret.id) === String(retailerId));
  openReportModal();
  if (r) setTimeout(() => selectReportStore(r), 0);
}

function openReportModalForGame(gameName, gamePrice) {
  openReportModal();
  setTimeout(() => {
    document.getElementById("reportGameName").value = gameName;
    if (gamePrice != null) document.getElementById("reportGamePrice").value = gamePrice;
  }, 0);
}

function updateReportBadges() {
  // Hide all badges first
  document.querySelectorAll(".report-count-badge").forEach(el => { el.style.display = "none"; el.textContent = ""; });

  // Build counts: prefer detailed communityReports (logged-in), fall back to public retailerCounts
  const counts = {};
  if (_currentUser && communityReports.length) {
    for (const rep of communityReports) {
      counts[rep.retailer_id] = (counts[rep.retailer_id] || 0) + 1;
    }
  } else {
    Object.assign(counts, retailerCounts);
  }

  for (const [rid, count] of Object.entries(counts)) {
    const el = document.getElementById("rbadge-" + rid);
    if (el) { el.textContent = count + " reports"; el.style.display = ""; }
  }
}

function refreshOpenProfile() {
  if (!_openProfileId) return;
  const profileTr = document.querySelector(".store-profile-tr");
  if (!profileTr) return;
  const td = profileTr.querySelector("td");
  if (td) td.innerHTML = storeProfileHtml(_openProfileId);
}

// ── Game-centric filter ───────────────────────────────────────────────────────

function changeGameFilter() {
  // kept for legacy calls; real UI handled by selectGameFilter / clearGameFilter
  applyGameFilter();
}

function applyGameFilter() {
  const th = document.getElementById("maLastReportTh");
  if (th) th.textContent = selectedGame ? `${selectedGame.name}` : "Last Report";

  buildLatestStatusFromReports();

  // Stat cards
  if (selectedGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) {
      s.has_stock ? inCount++ : outCount++;
    }
    document.getElementById("maStatInStockCard").style.display = "";
    document.getElementById("maStatOutCard").style.display = "";
    document.getElementById("maStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("maStatOut").textContent = outCount.toLocaleString();
  } else {
    document.getElementById("maStatInStockCard").style.display = "none";
    document.getElementById("maStatOutCard").style.display = "none";
  }

  renderMaTable();
  if (maMapVisible) renderMapLayers(getFilteredRows());
}


function updateInventoryMapLayer(visibleRetailers) {
  if (!maMap) return;

  if (window._inventoryLayer) { maMap.removeLayer(window._inventoryLayer); window._inventoryLayer = null; }

  // Report dots — filtered by game and stock status
  let reports = communityReports.filter(r => r.lat && r.lng);
  if (selectedGame) reports = reports.filter(r => r.game_name?.toLowerCase() === selectedGame.name.toLowerCase());
  if (mapReportFilter === "in")  reports = reports.filter(r =>  r.has_stock);
  if (mapReportFilter === "out") reports = reports.filter(r => !r.has_stock);

  if (reports.length) {
    const markers = reports.map(r => {
      const color = r.has_stock ? "#00cc44" : "#cc2200";
      const icon  = L.divIcon({
        className: "",
        html: `<div style="width:10px;height:10px;border-radius:50%;background:${color};border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,.3)"></div>`,
        iconSize: [10, 10], iconAnchor: [5, 5],
      });
      const time = r.reported_at
        ? timeAgo(new Date(r.reported_at + (r.reported_at.endsWith("Z") ? "" : "Z")))
        : "";
      return L.marker([r.lat, r.lng], { icon }).bindPopup(
        `<b>${escHtml(r.retailer_name || "")}</b><br>` +
        `${escHtml(r.game_name || "")}${r.game_price ? " $" + r.game_price : ""}<br>` +
        `${r.has_stock ? "✅ In Stock" : "❌ Out of Stock"}<br>` +
        `<span style="color:#888;font-size:.8rem">${escHtml(r.source === "caller" ? "📞 Call verification" : "👤 Community report")} · ${time}</span>`
      );
    });
    window._inventoryLayer = L.layerGroup(markers).addTo(maMap);
  }

}

// ══════════════════════════════════════════════════════════════════════════════
// AZ HUNT
// ══════════════════════════════════════════════════════════════════════════════

async function loadAzGames() {
  try {
    const res = await fetch("/api/games?state=AZ&limit=500&sort_by=return_pct");
    if (!res.ok) return;
    const data = await res.json();
    azGames = data.games || [];
  } catch (_) {}
}

function searchAzGameFilter() {
  const input = document.getElementById("azGameFilterInput");
  const dd    = document.getElementById("azGameFilterDropdown");
  const clear = document.getElementById("azGameFilterClear");
  if (!input) return;
  const q = input.value.trim().toLowerCase();
  clear.style.display = q ? "" : "none";
  const matches = q ? azGames.filter(g => g.name.toLowerCase().includes(q)) : azGames.slice(0, 50);
  if (!matches.length) { dd.style.display = "none"; return; }
  dd.innerHTML = matches.map(g => {
    const meta = [g.price != null ? `$${g.price}` : null, g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : null]
      .filter(Boolean).join(" · ");
    const sub = meta ? `<span style="color:var(--text-muted);font-size:.78rem">${escHtml(meta)}</span>` : "";
    return `<div class="store-option" onmousedown="selectAzGameFilter(${JSON.stringify(g.name).replace(/"/g, '&quot;')})">${escHtml(g.name)} ${sub}</div>`;
  }).join("");
  dd.style.display = "";
}

function selectAzGameFilter(name) {
  const input = document.getElementById("azGameFilterInput");
  const dd    = document.getElementById("azGameFilterDropdown");
  const clear = document.getElementById("azGameFilterClear");
  input.value = name;
  dd.style.display = "none";
  clear.style.display = "";
  const g = azGames.find(g => g.name === name) || { name, price: null };
  selectedAzGame = { name: g.name, price: g.price ?? null };
  applyAzGameFilter();
}

function clearAzGameFilter() {
  document.getElementById("azGameFilterInput").value = "";
  document.getElementById("azGameFilterDropdown").style.display = "none";
  document.getElementById("azGameFilterClear").style.display = "none";
  selectedAzGame = null;
  applyAzGameFilter();
}

function applyAzGameFilter() {
  const th = document.getElementById("azLastReportTh");
  if (th) th.textContent = selectedAzGame ? selectedAzGame.name : "Last Report";

  buildLatestStatusFromReports();

  if (selectedAzGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) {
      s.has_stock ? inCount++ : outCount++;
    }
    document.getElementById("azStatInStockCard").style.display = "";
    document.getElementById("azStatOutCard").style.display = "";
    document.getElementById("azStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("azStatOut").textContent = outCount.toLocaleString();
  } else {
    document.getElementById("azStatInStockCard").style.display = "none";
    document.getElementById("azStatOutCard").style.display = "none";
  }

  renderAzTable();
  if (azMapVisible) renderAzMapLayers(getAzFilteredRows());
}

// ── AZ data loading ───────────────────────────────────────────────────────────

async function loadAzRetailers() {
  try {
    const res = await fetch("/api/az/retailers?limit=7000");
    const data = await res.json();
    allAzRetailers = data.retailers || [];
    azLoaded = true;
    updateAzStats();
    renderAzTable();
  } catch (e) {
    const tbody = document.getElementById("azTableBody");
    if (tbody) tbody.innerHTML =
      `<tr><td colspan="6" class="loading-cell">No AZ retailer data yet. Run fetch_az_retailers.py to populate.</td></tr>`;
  }
}

function updateAzStats() {
  const el = document.getElementById("azStatTotal");
  if (el) el.textContent = allAzRetailers.length.toLocaleString();
}

function getAzFilteredRows() {
  const q             = (document.getElementById("azSearchInput")?.value || "").toLowerCase().trim();
  const city          = (document.getElementById("azCityInput")?.value   || "").toLowerCase().trim();
  const invFilter     = document.getElementById("azInvFilter")?.value  || "";
  const dateFilter    = document.getElementById("azDateFilter")?.value || "";
  const showUnchecked = document.getElementById("azShowUnchecked")?.checked ?? true;

  azMapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allAzRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));

  if (!showUnchecked) rows = rows.filter(r => !!retailerLatestStatus[r.id]);

  if (invFilter) {
    rows = rows.filter(r => {
      const s = retailerLatestStatus[r.id];
      if (invFilter === "in")      return s && s.has_stock;
      if (invFilter === "out")     return s && !s.has_stock;
      if (invFilter === "checked") return !!s;
      return true;
    });
  }

  if (dateFilter) {
    const now = Date.now();
    const cutoffs = { today: 86400000, "7d": 7 * 86400000, "30d": 30 * 86400000 };
    const cutoff  = cutoffs[dateFilter];
    rows = rows.filter(r => {
      const s = retailerLatestStatus[r.id];
      if (!s) return false;
      return (now - parseReportedAt(s.reported_at).getTime()) <= cutoff;
    });
  }

  return rows;
}

function renderAzTable() {
  if (!azLoaded) return;
  _openProfileId = null;
  const rows = getAzFilteredRows();
  const checkedCount = selectedAzGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null
    ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedAzGame.name)}</strong>`
    : "";
  const countEl = document.getElementById("azResultCount");
  if (countEl) countEl.innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("azTableBody");
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
  } else {
    tbody.innerHTML = rows.map((r, i) => azRow(r, i + 1)).join("");
    updateReportBadges();
  }
  if (azMapVisible) renderAzMapLayers(rows);
}

function azRow(r, rank) {
  const addr = encodeURIComponent(`${r.name}, ${r.address}, ${r.city}, AZ ${r.zipCode}`);
  const mapsUrl       = `https://www.google.com/maps/search/?api=1&query=${addr}`;
  const searchUrl     = `https://www.google.com/search?q=${encodeURIComponent(r.name + ' ' + r.city + ' AZ lottery')}`;
  const directionsUrl = (r.latitude && r.longitude)
    ? `https://www.google.com/maps/dir/?api=1&destination=${r.latitude},${r.longitude}`
    : mapsUrl;

  const links = `
    <a class="link-btn link-maps" href="${mapsUrl}" target="_blank" rel="noopener" title="View on Maps">Maps</a>
    <a class="link-btn link-dir"  href="${directionsUrl}" target="_blank" rel="noopener" title="Get Directions">Dir</a>
    <a class="link-btn link-srch" href="${searchUrl}" target="_blank" rel="noopener" title="Google Search">Search</a>`;

  const rid = escHtml(r.id || "");

  return `<tr class="ma-store-row" data-retailer-id="${rid}" onclick="toggleStoreProfile(this)">
    <td style="color:var(--text-muted);font-size:.8rem;font-weight:700">${rank}</td>
    <td><strong>${escHtml(r.name)}</strong><span class="report-count-badge" id="rbadge-${rid}" style="display:none"></span><br><span style="font-size:.78rem;color:var(--text-muted)">${escHtml(r.address)}</span></td>
    <td>${escHtml(r.city)}</td>
    <td>${escHtml(r.zipCode)}</td>
    <td class="last-report-cell" data-rid="${rid}">${lastReportCellHtml(rid)}</td>
    <td class="links-cell" onclick="event.stopPropagation()">${links}</td>
  </tr>`;
}

function downloadAzCsv() {
  const rows = getAzFilteredRows();
  const cols = ["name","address","city","zipCode","phone","latitude","longitude"];
  const header = cols.join(",");
  const csvRows = rows.map(r =>
    cols.map(c => {
      const v = String(r[c] ?? "");
      return v.includes(",") || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v;
    }).join(",")
  );
  const blob = new Blob([header + "\n" + csvRows.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "az_retailers.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── AZ Leaflet map ────────────────────────────────────────────────────────────

function toggleAzMap() {
  const sec = document.getElementById("azMapSection");
  azMapVisible = !azMapVisible;
  sec.style.display = azMapVisible ? "" : "none";
  if (azMapVisible) {
    if (!azMap) initAzMap();
    else azMap.invalidateSize();
    renderAzMapLayers(getAzFilteredRows());
  }
}

function initAzMap() {
  azMap = L.map("azMap").setView([34.05, -111.09], 7);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(azMap);
}

function renderAzMapLayers(retailers) {
  if (!azMap) return;
  azMap.eachLayer(layer => { if (!(layer instanceof L.TileLayer)) azMap.removeLayer(layer); });
  window._azInventoryLayer = null;
  updateAzInventoryMapLayer(retailers);
}

function updateAzInventoryMapLayer(visibleRetailers) {
  if (!azMap) return;

  if (window._azInventoryLayer) {
    azMap.removeLayer(window._azInventoryLayer);
    window._azInventoryLayer = null;
  }

  const retailers = visibleRetailers || getAzFilteredRows();

  const retailerMarkers = retailers
    .filter(r => r.latitude && r.longitude)
    .map(r => {
      const status = retailerLatestStatus[r.id];
      const color = status
        ? (status.has_stock ? "#00cc44" : "#cc2200")
        : "#4a9eff";
      const icon = L.divIcon({
        className: "",
        html: `<div style="width:8px;height:8px;border-radius:50%;background:${color};border:1.5px solid white;box-shadow:0 1px 3px rgba(0,0,0,.3)"></div>`,
        iconSize: [8, 8], iconAnchor: [4, 4],
      });
      const statusTxt = status
        ? (status.has_stock ? "✅ In Stock" : "❌ Out of Stock")
        : "Not yet checked";
      return L.marker([parseFloat(r.latitude), parseFloat(r.longitude)], { icon })
        .bindPopup(`<b>${escHtml(r.name)}</b><br>${escHtml(r.city || "")} ${escHtml(r.zipCode || "")}<br>${statusTxt}`);
    });

  // Community report dots filtered to AZ retailers
  const azIds = new Set(allAzRetailers.map(r => String(r.id)));
  let reports = communityReports.filter(r => r.lat && r.lng && azIds.has(String(r.retailer_id)));
  if (selectedAzGame) reports = reports.filter(r => r.game_name?.toLowerCase() === selectedAzGame.name.toLowerCase());
  if (azMapReportFilter === "in")  reports = reports.filter(r =>  r.has_stock);
  if (azMapReportFilter === "out") reports = reports.filter(r => !r.has_stock);

  const reportMarkers = reports.map(r => {
    const color = r.has_stock ? "#00cc44" : "#cc2200";
    const icon  = L.divIcon({
      className: "",
      html: `<div style="width:10px;height:10px;border-radius:50%;background:${color};border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,.3)"></div>`,
      iconSize: [10, 10], iconAnchor: [5, 5],
    });
    const time = r.reported_at
      ? timeAgo(new Date(r.reported_at + (r.reported_at.endsWith("Z") ? "" : "Z")))
      : "";
    return L.marker([r.lat, r.lng], { icon }).bindPopup(
      `<b>${escHtml(r.retailer_name || "")}</b><br>` +
      `${escHtml(r.game_name || "")}${r.game_price ? " $" + r.game_price : ""}<br>` +
      `${r.has_stock ? "✅ In Stock" : "❌ Out of Stock"}<br>` +
      `<span style="color:#888;font-size:.8rem">${escHtml(r.source === "caller" ? "📞 Call" : "👤 Community")} · ${time}</span>`
    );
  });

  const allMarkers = [...retailerMarkers, ...reportMarkers];
  if (allMarkers.length) {
    window._azInventoryLayer = L.layerGroup(allMarkers).addTo(azMap);
  }
}
