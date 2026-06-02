/* ScratchFever — frontend */

// States excluded from EV rankings — no reliable ticket-count data published by state.
// IL: publishes remaining prizes only, no total tickets or per-game odds.
const EV_EXCLUDED_STATES = new Set(["IL"]);

const STATE_LOTTERY_URLS = {
  AR: "https://www.myarkansaslottery.com/games/instant-games",
  AZ: "https://www.arizonalottery.com/scratchers/",
  CA: "https://www.calottery.com/scratch",
  CO: "https://coloradolottery.com/en/games/scratch/",
  CT: "https://www.ctlottery.org/instant-games",
  DC: "https://dclottery.com/games/scratchoffs",
  DE: "https://www.delottery.com/Instant-Games",
  FL: "https://www.flalottery.com/scratch-off-games",
  GA: "https://www.galottery.com/en-us/games/scratchers.html",
  IA: "https://www.ialottery.com/Games/ScratchGames.aspx",
  ID: "https://www.idaholottery.com/games/scratch/",
  IL: "https://www.illinoislottery.com/illinois-lottery/scratch-offs.html",
  IN: "https://www.hoosierlottery.com/games/scratch-offs",
  KS: "https://www.kslottery.com/games/instants",
  KY: "https://www.kylottery.com/apps/game_pages/scratch_offs.html",
  LA: "https://louisianalottery.com/scratch-offs",
  MA: "https://www.masslottery.com/games/instant-tickets",
  MD: "https://www.mdlottery.com/games/scratch-offs/",
  ME: "https://www.mainelottery.com/games/instant.html",
  MI: "https://www.michiganlottery.com/games/instant-games",
  MN: "https://www.mnlottery.com/games/scratch_games/",
  MO: "https://www.molottery.com/s/scratchers-list.do",
  MS: "https://www.mslottery.com/scratchoffs.html",
  MT: "https://montanalottery.com/scratch-games/",
  NC: "https://www.nclottery.com/scratch",
  NE: "https://www.nelottery.com/lotteryApp/scratch-off",
  NH: "https://www.nhlottery.com/games/scratch-tickets",
  NJ: "https://www.njlottery.com/en-us/games/scratchoffs.html",
  NM: "https://www.nmlottery.com/games/scratch/",
  NY: "https://nylottery.ny.gov/scratch-off-games",
  OH: "https://www.ohiolottery.com/games/scratch-offs",
  OR: "https://www.oregonlottery.org/games/scratch-its/",
  PA: "https://www.palottery.pa.gov/Scratch-Offs/Currently-On-Sale.aspx",
  RI: "https://www.rilot.com/en-us/scratch/games.html",
  SC: "https://www.sceducationlottery.com/games/scratch-offs.aspx",
  SD: "https://www.sdlottery.org/games/scratch-tickets/",
  TN: "https://www.tnlottery.com/scratch-offs",
  TX: "https://www.txlottery.org/export/sites/lottery/Games/Scratch_Offs/",
  VA: "https://www.valottery.com/games/scratch",
  VT: "https://www.vtlottery.com/games/instant-games/",
  WA: "https://www.walottery.com/Scratch/",
  WI: "https://www.wilottery.com/games/scratch/",
  WV: "https://www.wvlottery.com/games/scratch-offs/",
};

let allGames = [];
let allGamesUnfiltered = [];
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

// ── RI Hunt state ─────────────────────────────────────────────────────────────
let allRiRetailers = [];
let riGames = [];
let selectedRiGame = null; // { name, price } or null
let riLoaded = false;
let riMap = null;
let riMapVisible = false;
let riMapReportFilter = "all";

// ── FL Hunt state ─────────────────────────────────────────────────────────────
let allFlRetailers = [];
let flGames = [];
let selectedFlGame = null;
let flLoaded = false;
let flMap = null;
let flMapVisible = false;
let flMapReportFilter = "all";

// ── GA Hunt state ─────────────────────────────────────────────────────────────
let allGaRetailers = [];
let gaGames = [];
let selectedGaGame = null;
let gaLoaded = false;
let gaMap = null;
let gaMapVisible = false;
let gaMapReportFilter = "all";

// ── NY Hunt state ─────────────────────────────────────────────────────────────
let allNyRetailers = [];
let nyGames = [];
let selectedNyGame = null;
let nyLoaded = false;
let nyMap = null;
let nyMapVisible = false;
let nyMapReportFilter = "all";

// ── VA Hunt state ─────────────────────────────────────────────────────────────
let allVaRetailers = [];
let vaGames = [];
let selectedVaGame = null;
let vaLoaded = false;
let vaMap = null;
let vaMapVisible = false;
let vaMapReportFilter = "all";

// ── DC Hunt state ─────────────────────────────────────────────────────────────
let allDcRetailers = [];
let dcGames = [];
let selectedDcGame = null;
let dcLoaded = false;
let dcMap = null;
let dcMapVisible = false;
let dcMapReportFilter = "all";

// ── VT Hunt state ─────────────────────────────────────────────────────────────
let allVtRetailers = [];
let vtGames = [];
let selectedVtGame = null;
let vtLoaded = false;
let vtMap = null;
let vtMapVisible = false;
let vtMapReportFilter = "all";

// ── Render generation counters (cancel in-flight RAF renders on new render) ───
let maRenderGen = 0;
let azRenderGen = 0;
let riRenderGen = 0;
let flRenderGen = 0;
let gaRenderGen = 0;
let nyRenderGen = 0;
let vaRenderGen = 0;
let dcRenderGen = 0;
let vtRenderGen = 0;
let communityReportsLastFetch = 0;

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

// ── User preferences ──────────────────────────────────────────────────────────
const _PREFS_KEY = "sf_prefs";
const _prefsDefaults = { defaultHuntState: "MA", evDefaultState: "" };
let _prefs = { ..._prefsDefaults };

function loadPrefs() {
  try {
    const raw = localStorage.getItem(_PREFS_KEY);
    if (raw) _prefs = { ..._prefsDefaults, ...JSON.parse(raw) };
  } catch (_) {}
  currentHuntState = _prefs.defaultHuntState || "MA";
}

function onSettingChange(key, value) {
  _prefs[key] = value;
  localStorage.setItem(_PREFS_KEY, JSON.stringify(_prefs));
  if (key === "defaultHuntState") {
    currentHuntState = value;
  } else if (key === "evDefaultState") {
    const sel = document.getElementById("filterState");
    if (sel) { sel.value = value; loadGames(); }
  }
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
  const btn        = document.getElementById("loginBtn");
  const accountBtn = document.getElementById("accountTabBtn");
  const caller     = document.getElementById("callerTabBtn");
  const proCta     = document.getElementById("sidebarProCta");

  if (user) {
    document.getElementById("userDisplayName").textContent = user.username || user.email.split("@")[0];
    btn.style.display        = "none";
    accountBtn.style.display = "";
    if (proCta) proCta.style.display = "none";
    const isAdmin = user.role === "admin";
    caller.style.display = isAdmin ? "" : "none";
    const dataStatusBtn = document.getElementById("dataStatusBtn");
    if (dataStatusBtn) dataStatusBtn.style.display = isAdmin ? "" : "none";
    document.getElementById("playsTabBtn").style.display = "";
    document.getElementById("playsLoginNudge").style.display = "none";
    document.getElementById("scrapeBtn").style.display = isAdmin ? "" : "none";
    const thCampaign = document.getElementById("thCampaign");
    if (thCampaign) thCampaign.style.display = isAdmin ? "" : "none";
    const myStoreLink = document.getElementById("myStoreLink");
    if (myStoreLink) myStoreLink.style.display = (user.role === "retailer" || user.role === "admin") ? "" : "none";
  } else {
    btn.style.display        = "";
    accountBtn.style.display = "none";
    caller.style.display     = "none";
    const dataStatusBtn = document.getElementById("dataStatusBtn");
    if (dataStatusBtn) dataStatusBtn.style.display = "none";
    if (proCta) proCta.style.display = "";
    document.getElementById("playsTabBtn").style.display = "none";
    document.getElementById("playsLoginNudge").style.display = "";
    const msl = document.getElementById("myStoreLink");
    if (msl) msl.style.display = "none";
    document.getElementById("scrapeBtn").style.display = "none";
    const thCampaign = document.getElementById("thCampaign");
    if (thCampaign) thCampaign.style.display = "none";
    if (currentTab === "account") switchTab("ev");
    _openProfileId = null;
    document.querySelectorAll(".store-profile-tr").forEach(el => el.remove());
    document.querySelectorAll(".store-profile-open").forEach(el => el.classList.remove("store-profile-open"));
    updateReportBadges();
  }
}

function populateAccountTab() {
  if (!_currentUser) return;
  document.getElementById("accountDisplayName").textContent = _currentUser.username || "—";
  document.getElementById("accountEmailFull").textContent = _currentUser.email || "—";
  const roleEl = document.getElementById("accountRoleBadge");
  const roleLabel = _currentUser.role === "admin" ? "Admin" : _currentUser.role === "retailer" ? "Retailer" : "Member";
  roleEl.textContent = roleLabel;
  roleEl.className = "user-chip-role role-" + _currentUser.role;
}

async function restoreSession() {
  const token = getToken();
  if (!token) { _setUser(null); return; }
  try {
    const res = await fetch("/api/auth/me", { headers: authHeaders() });
    if (res.ok) {
      const data = await res.json();
      _setUser({ email: data.email, username: data.username, role: data.role });
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
  loadPrefs();
  syncHeaderHeight();
  window.addEventListener("resize", syncHeaderHeight);
  await Promise.all([loadStates(), loadAllGamesUnfiltered(), restoreSession()]);
  if (_prefs.evDefaultState) {
    const sel = document.getElementById("filterState");
    if (sel) { sel.value = _prefs.evDefaultState; loadGames(); }
  }
  await Promise.all([loadCommunityReports(), loadGameCounts(), loadRetailerCounts(), loadRetailerLatest()]);
  loadStatus();
  loadPrizeClaims();
  setInterval(() => { loadStatus(); loadPrizeClaims(); }, 30_000);
  setInterval(() => { if (_currentUser && currentTab === "ma") loadCommunityReports(); }, 60_000);

  // Deep-link: /?store=<id>&state=<code> — open that store's profile inline.
  // Used by the retailer dashboard's "View public page" link.
  openStoreFromUrl();
})();

async function openStoreFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const storeId = params.get("store");
  const stateCode = (params.get("state") || "MA").toUpperCase();
  if (!storeId) return;

  // Switch to the Hunt tab (where retailer rows live) and target the right state.
  try { switchTab("ma"); } catch (_) {}
  try { selectHuntState(stateCode); } catch (_) {}

  // Wait until that state's retailer array is populated (load is async).
  const stateArrays = {
    MA: () => allRetailers,
    AZ: () => allAzRetailers, RI: () => allRiRetailers,
    FL: () => allFlRetailers, GA: () => allGaRetailers,
    NY: () => allNyRetailers, VA: () => allVaRetailers,
    DC: () => allDcRetailers, VT: () => allVtRetailers,
  };
  const getArr = stateArrays[stateCode]
    || (typeof GEN_STATES !== "undefined" && GEN_STATES[stateCode] ? () => (allGenRetailers[stateCode] || []) : null)
    || stateArrays.MA;
  const deadline = Date.now() + 15_000;  // 15s cap so we never spin forever
  while (Date.now() < deadline) {
    const arr = getArr();
    if (arr && arr.length && arr.some(r => String(r.id) === String(storeId))) break;
    await new Promise(r => setTimeout(r, 250));
  }

  // Render is also async (lazy table). Try a couple times.
  for (let i = 0; i < 8; i++) {
    if (typeof openStoreInventoryFromMap === "function") {
      try { openStoreInventoryFromMap(storeId); } catch (_) {}
    }
    // openStoreInventoryFromMap calls toggleStoreProfile which flips _openProfileId
    if (typeof _openProfileId !== "undefined" && _openProfileId === String(storeId)) return;
    await new Promise(r => setTimeout(r, 300));
  }
}

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
    populateCallerStateSelect();
  } catch (_) {}
}

function populateGameFilterSelect() {
  // data is in maGames; UI is a typeahead, nothing to rebuild
}

const STATE_LABELS = {
  MA:"Massachusetts", AZ:"Arizona", RI:"Rhode Island", FL:"Florida",
  GA:"Georgia", NY:"New York", VA:"Virginia", DC:"Washington DC",
  VT:"Vermont", CT:"Connecticut", NJ:"New Jersey", MI:"Michigan",
  KS:"Kansas", DE:"Delaware", WY:"Wyoming", PA:"Pennsylvania",
};

function populateCallerStateSelect() {
  const sel = document.getElementById("cfStateSelect");
  if (!sel) return;
  const prev = sel.value;
  const source = allGamesUnfiltered.length ? allGamesUnfiltered : maGames;
  const codes = [...new Set(source.map(g => g.state_code).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">— State —</option>';
  codes.forEach(code => {
    const opt = document.createElement("option");
    opt.value = code;
    opt.textContent = STATE_LABELS[code] ? `${STATE_LABELS[code]} (${code})` : code;
    if (code === prev) opt.selected = true;
    sel.appendChild(opt);
  });
  populateCallerGameSelect(sel.value);
  renderTicketsPicker();
}

function populateCallerGameSelect(stateCode) {
  const sel = document.getElementById("cfGameSelect");
  if (!sel) return;
  const prev = sel.value;
  const games = stateCode
    ? allGamesUnfiltered.filter(g => g.state_code === stateCode)
    : [];
  sel.innerHTML = stateCode
    ? '<option value="">— Select a game —</option>'
    : '<option value="">— Pick a state first —</option>';
  games.forEach(g => {
    const opt = document.createElement("option");
    opt.value = g.game_id || "";
    opt.dataset.name  = g.name;
    opt.dataset.price = g.price ?? "";
    opt.textContent   = `${g.name}${g.price != null ? ` ($${g.price})` : ""}`;
    if (g.game_id === prev) opt.selected = true;
    sel.appendChild(opt);
  });
}

function onCallerStateSelect() {
  const state = document.getElementById("cfStateSelect").value;
  _selectedTickets = new Set();
  const searchEl = document.getElementById("cfTicketsSearch");
  if (searchEl) searchEl.value = "";
  renderTicketsPicker();
  populateTestRetailerSelect(state);
  loadStoreCandidates();
}

let _selectedTickets = new Set();

function _currentStateGames() {
  const state = document.getElementById("cfStateSelect")?.value || "";
  if (!state) return [];
  return allGamesUnfiltered.filter(g => g.state_code === state);
}

function renderTicketsPicker() {
  const listEl   = document.getElementById("cfTicketsList");
  const countEl  = document.getElementById("cfTicketsCount");
  if (!listEl) return;

  const state = document.getElementById("cfStateSelect")?.value || "";
  if (!state) {
    listEl.innerHTML = `<div class="cf-tickets-empty">— Pick a state first —</div>`;
    if (countEl) countEl.textContent = "No tickets selected";
    return;
  }

  const search = (document.getElementById("cfTicketsSearch")?.value || "").trim().toLowerCase();
  const games  = _currentStateGames();
  const matches = search ? games.filter(g => (g.name || "").toLowerCase().includes(search)) : games;

  if (!matches.length) {
    listEl.innerHTML = `<div class="cf-tickets-empty">No games match.</div>`;
  } else {
    // Selected first, then the rest
    const selected   = matches.filter(g => _selectedTickets.has(g.name));
    const unselected = matches.filter(g => !_selectedTickets.has(g.name));
    const ordered    = [...selected, ...unselected];
    listEl.innerHTML = ordered.map(g => {
      const checked = _selectedTickets.has(g.name) ? "checked" : "";
      const priceStr = g.price != null ? `$${g.price}` : "";
      return `<label class="cf-ticket-row">
        <input type="checkbox" data-name="${escHtml(g.name)}" ${checked} onchange="toggleTicket(this)" />
        <span>${escHtml(g.name)}</span>
        <span class="cf-ticket-price">${priceStr}</span>
      </label>`;
    }).join("");
  }

  if (countEl) {
    const n = _selectedTickets.size;
    countEl.textContent = n === 0
      ? "No tickets selected"
      : `${n} ticket${n === 1 ? "" : "s"} selected`;
  }
}

function toggleTicket(input) {
  const name = input.dataset.name;
  if (input.checked) _selectedTickets.add(name);
  else _selectedTickets.delete(name);
  const countEl = document.getElementById("cfTicketsCount");
  if (countEl) {
    const n = _selectedTickets.size;
    countEl.textContent = n === 0 ? "No tickets selected" : `${n} ticket${n === 1 ? "" : "s"} selected`;
  }
}

function getSelectedTickets() {
  const games = _currentStateGames();
  const out = [];
  _selectedTickets.forEach(name => {
    const g = games.find(x => x.name === name);
    out.push({ name, price: g && g.price != null ? g.price : null });
  });
  return out;
}

async function populateTestRetailerSelect(state) {
  const sel = document.getElementById("cfTestAsRetailer");
  if (!sel) return;
  sel.innerHTML = `<option value="">— Generic test (no specific store) —</option>`;
  if (!state) return;
  try {
    const res = await callerFetch(`/api/vapi/retailers?state=${encodeURIComponent(state)}&limit=200&only_with_phone=false`);
    if (!res.ok) return;
    const data = await res.json();
    (data.retailers || []).forEach(r => {
      const opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = `${r.name}${r.city ? ' · ' + r.city : ''}`;
      sel.appendChild(opt);
    });
  } catch (_) {}
}

function onCallerGameSelect() {
  const sel = document.getElementById("cfGameSelect");
  const opt = sel.options[sel.selectedIndex];
  if (opt && opt.value) {
    const price = opt.dataset.price;
    if (price !== "") document.getElementById("cfGamePrice").value = price;
  }
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

// hide any open .store-dropdown when the click is outside its filter-group
document.addEventListener("click", e => {
  document.querySelectorAll(".store-dropdown").forEach(dd => {
    if (dd.style.display === "none") return;
    const group = dd.closest(".filter-group");
    if (group && !group.contains(e.target)) dd.style.display = "none";
  });
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

async function loadRetailerLatest(gameName) {
  try {
    const url = gameName
      ? `/api/inventory/retailer-latest?game_name=${encodeURIComponent(gameName)}`
      : "/api/inventory/retailer-latest";
    const res = await protectedFetch(url);
    if (!res.ok) return;
    const data = await res.json();
    retailerLatestStatus = data.statuses || {};
    updateLastReportCells();
    _refreshStatCounts();
    if (currentHuntState === "AZ") renderAzTable();
    else renderMaTable();
  } catch (_) {}
}

function _refreshStatCounts() {
  if (currentHuntState === "MA" && selectedGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) {
      s.has_stock ? inCount++ : outCount++;
    }
    document.getElementById("maStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("maStatOut").textContent = outCount.toLocaleString();
    if (maMapVisible) renderMapLayers(getFilteredRows());
  } else if (currentHuntState === "AZ" && selectedAzGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) {
      s.has_stock ? inCount++ : outCount++;
    }
    document.getElementById("azStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("azStatOut").textContent = outCount.toLocaleString();
    if (azMapVisible) renderAzMapLayers(getAzFilteredRows());
  }
}

function buildLatestStatusFromReports() {
  const status = {};
  const activeGame = currentHuntState === 'AZ' ? selectedAzGame
    : currentHuntState === 'RI' ? selectedRiGame
    : currentHuntState === 'FL' ? selectedFlGame
    : currentHuntState === 'GA' ? selectedGaGame
    : currentHuntState === 'NY' ? selectedNyGame
    : currentHuntState === 'VA' ? selectedVaGame
    : currentHuntState === 'DC' ? selectedDcGame
    : currentHuntState === 'VT' ? selectedVtGame
    : (typeof GEN_STATES !== 'undefined' && GEN_STATES[currentHuntState]) ? selectedGenGame
    : selectedGame;
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
  const iso = str.includes("T") ? str : str.replace(" ", "T");
  if (/[+\-]\d{2}:\d{2}$|Z$/.test(iso)) return new Date(iso);
  return new Date(iso.length <= 10 ? iso + "T00:00:00Z" : iso + "Z");
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
async function loadAllGamesUnfiltered() {
  try {
    const res = await fetch("/api/games?sort_by=return_pct&limit=5000");
    if (!res.ok) return;
    const data = await res.json();
    const raw = data.games || [];
    allGamesUnfiltered = raw;
    maGames = raw.filter(g => g.state_code === "MA");
    populateCallerStateSelect();
    azGames = raw.filter(g => g.state_code === "AZ");
    riGames = raw.filter(g => g.state_code === "RI");
    flGames = raw.filter(g => g.state_code === "FL");
    gaGames = raw.filter(g => g.state_code === "GA");
    nyGames = raw.filter(g => g.state_code === "NY");
    vaGames = raw.filter(g => g.state_code === "VA");
    dcGames = raw.filter(g => g.state_code === "DC");
    vtGames = raw.filter(g => g.state_code === "VT");
    if (typeof GEN_STATES !== "undefined") {
      for (const code of Object.keys(GEN_STATES)) {
        genGames[code] = raw.filter(g => g.state_code === code);
      }
    }
    allGames = applyClientFilters(raw);
    renderTable();
    populateGameFilterSelect();
    if (document.getElementById("plState")) {
      initPlStateSelect();
      onPlStateChange();
    }
  } catch (_) {}
}

function loadGames() {
  allGames = applyClientFilters(allGamesUnfiltered);
  const tbody = document.getElementById("gamesBody");
  if (tbody) tbody.innerHTML = `<tr><td colspan="15" class="loading-cell">Loading…</td></tr>`;
  requestAnimationFrame(renderTable);
}

function applyClientFilters(games) {
  const state  = document.getElementById("filterState")?.value || "";
  const price  = document.getElementById("filterPrice")?.value || "";
  const minRet = document.getElementById("filterMinReturn")?.value || "";
  let result = games.filter(g => !EV_EXCLUDED_STATES.has(g.state_code));
  if (state)  result = result.filter(g => g.state_code === state);
  if (price)  { const p = Number(price); result = result.filter(g => g.price === p); }
  if (minRet) result = result.filter(g => (g.return_pct || 0) >= Number(minRet));
  return result;
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
    const res = await fetch("/api/status/states");
    if (!res.ok) return;
    const data = await res.json();
    const bar = document.getElementById("statusBar");
    const dot = document.getElementById("statusDot");
    const txt = document.getElementById("statusText");
    if (!bar || !dot || !txt) return;
    bar.style.display = "";
    if (data.last_run) {
      dot.className = "status-dot ok";
      txt.textContent = `Updated ${timeAgo(_parseTs(data.last_run))}`;
    } else if (data.scraper_running) {
      dot.className = "status-dot ok";
      txt.textContent = "Fetching data…";
    } else {
      dot.className = "status-dot";
      txt.textContent = "No data yet";
    }
  } catch (_) {}
}

function _parseTs(ts) {
  if (!ts) return null;
  return new Date(/Z$|[+-]\d{2}:\d{2}$/.test(ts) ? ts : ts + "Z");
}

let _dsStates = null;
let _dsSortCol = null;
let _dsSortDir = 1;
let _dsFilterStatus = null;
let _dsActiveCode = null;
let _dsRetailerRunning = {};  // state_code -> bool

function dsToggleSort(col) {
  if (_dsSortCol === col) {
    _dsSortDir = -_dsSortDir;
  } else {
    _dsSortCol = col;
    _dsSortDir = 1;
  }
  _renderDsGrid();
}

function dsSetFilter(status) {
  _dsFilterStatus = _dsFilterStatus === status ? null : status;
  _renderDsGrid();
}

function _dsSortValue(s, col) {
  const STATUS_ORDER = {ok: 0, warn: 1, error: 2, never: 3};
  switch (col) {
    case 'status':    return STATUS_ORDER[s.status] ?? 4;
    case 'name':      return (s.state_name || '').toLowerCase();
    case 'scraped':   return s.last_scrape_at ? new Date(s.last_scrape_at).getTime() : 0;
    case 'games':     return s.games_in_db ?? 0;
    case 'ev':        return s.ev_pct ?? -1;
    case 'img':       return s.image_pct ?? -1;
    case 'avgret':    return s.avg_return ?? 0;
    case 'prizes':    return s.prizes_pct ?? -1;
    case 'winners':   return s.winners_count ?? -1;
    case 'retailers': return s.retailer_last_scraped ? new Date(s.retailer_last_scraped).getTime() : 0;
    default:          return 0;
  }
}

function _renderDsGrid() {
  const tbody = document.getElementById("dsGrid");
  if (!tbody || !_dsStates) return;

  let rows = [..._dsStates];

  if (_dsFilterStatus) {
    rows = rows.filter(s =>
      _dsFilterStatus === 'never'
        ? (s.status !== 'ok' && s.status !== 'warn' && s.status !== 'error')
        : s.status === _dsFilterStatus
    );
  }

  if (_dsSortCol) {
    rows.sort((a, b) => {
      const av = _dsSortValue(a, _dsSortCol);
      const bv = _dsSortValue(b, _dsSortCol);
      if (av < bv) return -_dsSortDir;
      if (av > bv) return _dsSortDir;
      return 0;
    });
  }

  document.querySelectorAll('.ds-table thead th[data-col]').forEach(th => {
    th.classList.remove('ds-th-asc', 'ds-th-desc');
    if (th.dataset.col === _dsSortCol) {
      th.classList.add(_dsSortDir === 1 ? 'ds-th-asc' : 'ds-th-desc');
    }
  });

  document.querySelectorAll('.ds-chip[data-filter]').forEach(chip => {
    chip.classList.toggle('ds-chip-active', chip.dataset.filter === _dsFilterStatus);
  });

  function _pctBar(pct) {
    if (!pct && pct !== 0) return `<span class="ds-muted">—</span>`;
    const cls = pct >= 90 ? "ds-pct-hi" : pct >= 50 ? "ds-pct-mid" : "ds-pct-lo";
    return `<span class="ds-pct ${cls}">${pct}%</span>`;
  }
  function _retCell(s) {
    if (!s.has_retailer_scraper) return `<span class="ds-muted">—</span>`;
    const running = _dsRetailerRunning[s.state_code];
    const btn = `<button class="ds-rescrape-btn${running ? ' ds-rescrape-running' : ''}" onclick="dsRescrapeRetailers('${s.state_code}')" title="Re-scrape retailer data">${running ? '…' : '↺'}</button>`;
    if (!s.retailer_last_scraped) return `<span class="ds-pct-lo">Never</span> ${btn}`;
    const d = _parseTs(s.retailer_last_scraped);
    const ageDays = d ? Math.floor((Date.now() - d) / 86400000) : 999;
    const cls = ageDays > 35 ? "ds-pct-lo" : "ds-pct-hi";
    return `<span class="${cls}">${timeAgo(d)}</span> ${btn}`;
  }

  const activeCode = _dsActiveCode;
  const html = rows.map(s => {
    const isActive = s.state_code === activeCode;
    const dotCls = isActive ? "ds-sdot busy"
                 : s.status === "ok" ? "ds-sdot ok"
                 : s.status === "error" ? "ds-sdot err"
                 : s.status === "warn" ? "ds-sdot warn"
                 : "ds-sdot never";
    const dotTitle = s.status === "error" && s.last_scrape_error
      ? `Last scrape failed: ${s.last_scrape_error}`
      : s.status === "error" ? "Last scrape failed"
      : s.status === "ok" ? "OK"
      : s.status === "warn" ? "Stale" : "Never run";
    const d = _parseTs(s.last_scrape_at);
    const when = isActive ? `<span style="color:var(--yellow);font-weight:600">Scraping now…</span>`
               : d ? timeAgo(d) : `<span class="ds-muted">—</span>`;
    const errLine = "";
    const games = s.games_in_db > 0 ? s.games_in_db.toLocaleString() : `<span class="ds-muted">—</span>`;
    const avgRet = s.avg_return
      ? `<span class="${s.avg_return >= 100 ? 'ds-pct-hi' : s.avg_return >= 80 ? 'ds-pct-mid' : 'ds-pct-lo'}">${s.avg_return}%</span>`
      : `<span class="ds-muted">—</span>`;
    const winCell = (() => {
      if (s.winners_count > 0) {
        const geoCls = s.winners_geocoded_pct >= 80 ? 'ds-pct-hi'
                     : s.winners_geocoded_pct >= 40 ? 'ds-pct-mid'
                     : 'ds-pct-lo';
        const detailTag = s.winners_has_retailer
          ? `<span class="ds-detail-tag ds-detail-store" title="Wins include the specific retailer where the ticket was sold">store</span>`
          : `<span class="ds-detail-tag ds-detail-city" title="Wins only have the winner's home city (no retailer info)">city</span>`;
        return `<span class="${geoCls}" title="${s.winners_geocoded_pct}% geocoded · latest ${s.winners_latest || '—'}">${s.winners_count.toLocaleString()}</span> ${detailTag}`;
      }
      if (s.has_winners_scraper) return `<span class="ds-pct-lo">Pending</span>`;
      return `<span class="ds-muted">—</span>`;
    })();
    return `<tr class="ds-state-row${isActive ? " ds-state-active" : ""}">
      <td><span class="${dotCls}" title="${dotTitle.replace(/"/g, '&quot;')}"></span></td>
      <td><span class="ds-state-code">${s.state_code}</span> <span class="ds-state-name">${s.state_name}</span>${errLine}</td>
      <td class="ds-state-when">${when}</td>
      <td class="ds-col-num">${games}</td>
      <td class="ds-col-num">${_pctBar(s.ev_pct)}</td>
      <td class="ds-col-num">${_pctBar(s.image_pct)}</td>
      <td class="ds-col-num">${avgRet}</td>
      <td class="ds-col-num">${_pctBar(s.prizes_pct)}</td>
      <td class="ds-col-num">${winCell}</td>
      <td class="ds-col-num">${_retCell(s)}</td>
    </tr>`;
  }).join("");

  tbody.innerHTML = html || `<tr><td colspan="10" class="ds-loading ds-muted">No states match filter.</td></tr>`;
}

async function loadStateHealth() {
  const tbody = document.getElementById("dsGrid");
  const banner = document.getElementById("dsScraperStatus");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="10" class="ds-loading">Loading…</td></tr>`;
  try {
    const res = await fetch("/api/status/states");
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();

    // scraper running banner
    if (banner) {
      banner.style.display = data.scraper_running ? "" : "none";
      const cur = document.getElementById("dsCurrentState");
      if (cur) {
        cur.textContent = data.current_state
          ? `Scraping ${data.current_state.name} (${data.current_state.code})…`
          : "Scraper running…";
      }
    }

    // update header status bar too
    const dot = document.getElementById("statusDot");
    const txt = document.getElementById("statusText");
    if (dot && txt) {
      if (data.last_run) {
        dot.className = "status-dot ok";
        txt.textContent = `Updated ${timeAgo(_parseTs(data.last_run))}`;
      } else if (data.scraper_running) {
        dot.className = "status-dot ok";
        txt.textContent = "Fetching data…";
      } else {
        dot.className = "status-dot";
        txt.textContent = "No data yet";
      }
    }

    // summary chips
    let ok = 0, warn = 0, err = 0, never = 0;
    data.states.forEach(s => {
      if (s.status === "ok") ok++;
      else if (s.status === "warn") warn++;
      else if (s.status === "error") err++;
      else never++;
    });
    const setN = (id, n) => { const el = document.getElementById(id); if (el) el.textContent = n; };
    setN("dsOkN", ok); setN("dsWarnN", warn); setN("dsErrN", err); setN("dsNeverN", never);

    const sub = document.getElementById("dsLastUpdated");
    if (sub) sub.textContent = `${data.states.length} states · refreshed just now`;

    _dsStates = data.states;
    _dsActiveCode = data.current_state && data.scraper_running ? data.current_state.code : null;
    _renderDsGrid();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="9" class="ds-loading" style="color:var(--red)">Failed to load: ${e.message}</td></tr>`;
  }
}

async function dsRescrapeRetailers(stateCode) {
  if (_dsRetailerRunning[stateCode]) return;
  _dsRetailerRunning[stateCode] = true;
  _renderDsGrid();
  try {
    const res = await callerFetch(`/api/admin/scrape/retailers/${stateCode}`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Failed to start retailer scrape for ${stateCode}: ${err.detail || res.status}`);
      _dsRetailerRunning[stateCode] = false;
      _renderDsGrid();
      return;
    }
    // Poll for completion
    const poll = setInterval(async () => {
      try {
        const sr = await callerFetch(`/api/admin/scrape/retailers/${stateCode}/status`);
        const d = await sr.json();
        if (!d.running) {
          clearInterval(poll);
          _dsRetailerRunning[stateCode] = false;
          await loadStateHealth();
        }
      } catch (_) {}
    }, 3000);
  } catch (e) {
    _dsRetailerRunning[stateCode] = false;
    _renderDsGrid();
  }
}

function fmtClaimPrize(amount) {
  if (amount >= 1_000_000) return "$" + (amount / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (amount >= 1000) return "$" + (amount / 1000).toFixed(0) + "K";
  return "$" + amount.toLocaleString();
}

function buildClaimItem(c) {
  const prize = fmtClaimPrize(c.prize_amount);
  const when = timeAgo(parseReportedAt(c.detected_at));
  const left = c.new_remaining === 0
    ? '<span style="color:var(--red);font-weight:700">GONE</span>'
    : `${c.new_remaining.toLocaleString()} left`;
  const count = c.claimed_count > 1 ? ` ×${c.claimed_count}` : "";
  const clickable = c.game_db_id != null;
  return `<div class="claim-item${clickable ? ' claim-item-link' : ''}"${clickable ? ` onclick="openGame(${c.game_db_id})"` : ''}>
    <span class="badge badge-state">${escHtml(c.state_code)}</span>
    <span class="claim-game">${escHtml(c.game_name)}</span>
    <span class="claim-prize">${prize} prize claimed${count}</span>
    <span class="claim-remaining">${left}</span>
    <span class="claim-when">${when}</span>
  </div>`;
}

async function loadPrizeClaims() {
  try {
    const res = await fetch("/api/prize-claims?min_prize=10000&limit=30");
    if (!res.ok) return;
    const data = await res.json();
    const banner = document.getElementById("bigwinsBanner");
    const items = document.getElementById("bigwinsBannerItems");
    if (!data.claims || data.claims.length === 0) {
      banner.style.display = "none";
      return;
    }
    banner.style.display = "";
    // Shuffle so states are interleaved rather than grouped
    const claims = [...data.claims].sort(() => Math.random() - 0.5);
    const chips = claims.map(c => {
      const prize = fmtClaimPrize(c.prize_amount);
      const count = c.claimed_count > 1 ? ` ×${c.claimed_count}` : "";
      const clickable = c.game_db_id != null;
      return `<span class="bigwins-banner-chip${clickable ? ' bigwins-banner-chip-link' : ''}"${clickable ? ` onclick="openGame(${c.game_db_id})"` : ''}>
        <span class="badge badge-state">${escHtml(c.state_code)}</span>
        <span class="bigwins-chip-game">${escHtml(c.game_name)}</span>
        <span class="bigwins-chip-prize">${prize}${count}</span>
      </span>`;
    }).join('<span class="bigwins-banner-chip" style="opacity:.35">•</span>');
    // Duplicate for seamless loop
    items.innerHTML = `<span class="bigwins-ticker-track">${chips}<span class="bigwins-banner-chip" style="opacity:.35">•</span>${chips}<span class="bigwins-banner-chip" style="opacity:.35">•</span></span>`;

    if (data.fetched_at) {
      const fetchedAt = new Date(data.fetched_at);
      const freshnessEl = document.getElementById("bigwinsFreshness");
      if (freshnessEl) {
        const update = () => {
          const secs = Math.floor((Date.now() - fetchedAt) / 1000);
          let label;
          if (secs < 60) label = `Updated ${secs}s ago`;
          else if (secs < 3600) label = `Updated ${Math.floor(secs / 60)}m ago`;
          else label = `Updated ${Math.floor(secs / 3600)}h ago`;
          freshnessEl.textContent = label;
        };
        update();
        freshnessEl.style.display = "";
        setInterval(update, 30000);
      }
    }
  } catch (_) {}
}

let bigwinsLoaded = false;
let allBigWins = [];
let allBigWinsStates = [];         // states present in the prize_claims list view
let allBigWinsDays = null;         // days range the loaded list set covers
let allBigWinsLoading = null;      // in-flight promise to dedupe concurrent loads
let bigwinsView = "list";          // "list" | "map"
// Map view uses pre-aggregated location groups from /api/reported-wins/map.
// We no longer hold the per-win list — the server does the heavy lifting so
// state-flooding can't silently truncate small states off the map.
let bigwinsMapGroups = [];         // [{lat, lng, state_code, is_home, win_count, total_prize, games, top_wins, ...}]
let bigwinsMapStates = [];         // states present in the loaded set (for the dropdown)
let bigwinsMapGameCounts = [];     // [{name, count}] precomputed for the dropdown
let bigwinsMapTotalWins = 0;
let bigwinsMapTotalPrize = 0;
let bigwinsMapKey = null;          // cache key: stringified (days, min_prize)
let bigwinsMapLoading = null;
let bigwinsMap = null;
let bigwinsMapMarkers = null;

async function loadBigWins() {
  const days = parseInt(document.getElementById("bigwinsRangeFilter")?.value || "30", 10);
  if (allBigWinsDays === days && allBigWins.length) return;
  if (allBigWinsLoading) return allBigWinsLoading;
  const loadingEl = document.getElementById("bigwinsLoading");
  const list = document.getElementById("bigwinsList");
  if (loadingEl) loadingEl.style.display = "";
  allBigWinsLoading = (async () => {
    try {
      const res = await fetch(`/api/prize-claims?min_prize=10000&days=${days}&limit=100000`);
      if (!res.ok) {
        if (list) list.innerHTML = '<div style="color:var(--text-muted);padding:2rem 1rem">Failed to load wins. Try refreshing.</div>';
        return;
      }
      const data = await res.json();
      allBigWins = data.claims || [];
      allBigWinsStates = [...new Set(allBigWins.map(c => c.state_code))].sort();
      allBigWinsDays = days;
      rebuildBigWinsStateDropdown();
      rebuildBigWinsGameDropdown();
      filterBigWins();
    } catch (e) {
      if (list) list.innerHTML = '<div style="color:var(--text-muted);padding:2rem 1rem">Failed to load wins. Try refreshing.</div>';
      console.error("loadBigWins:", e);
    } finally {
      if (loadingEl) loadingEl.style.display = "none";
      allBigWinsLoading = null;
    }
  })();
  return allBigWinsLoading;
}

function bigwinsRangeLabel() {
  const days = parseInt(document.getElementById("bigwinsRangeFilter")?.value || "30", 10);
  if (days >= 7300) return "all time";
  if (days >= 365) {
    const yrs = Math.round(days / 365);
    return `last ${yrs} year${yrs > 1 ? "s" : ""}`;
  }
  return `last ${days} days`;
}

function filterBigWins() {
  if (bigwinsView === "map") {
    // Min-prize is a server-side filter (changes which wins get aggregated),
    // so it requires a refetch. State and game are client-side filters over
    // the aggregated groups, so they only re-render.
    loadBigWinsMap().then(() => renderBigWinsMap());
    return;
  }
  const state = document.getElementById("bigwinsStateFilter")?.value || "";
  const game = document.getElementById("bigwinsGameFilter")?.value || "";
  const minPrize = parseFloat(document.getElementById("bigwinsPrizeFilter")?.value || "0") || 0;
  const list = document.getElementById("bigwinsList");
  const countEl = document.getElementById("bigwinsCount");
  let filtered = allBigWins;
  if (state) filtered = filtered.filter(c => c.state_code === state);
  if (game)  filtered = filtered.filter(c => (c.game_name || "").trim() === game);
  if (minPrize) filtered = filtered.filter(c => (c.prize_amount || 0) >= minPrize);
  if (filtered.length === 0) {
    const range = bigwinsRangeLabel();
    const prizeLabel = minPrize ? `${fmtClaimPrize(minPrize)}+` : "";
    const scope = [state, game, prizeLabel].filter(Boolean).join(" · ");
    list.innerHTML = `<div style="color:var(--text-muted);padding:2rem 1rem">No big wins${scope ? ` for ${escHtml(scope)}` : ""} in the ${range}.</div>`;
    countEl.textContent = "";
    return;
  }
  countEl.textContent = `${filtered.length} claim${filtered.length !== 1 ? "s" : ""}`;
  list.innerHTML = filtered.map(buildClaimItem).join("");
}

function setBigWinsView(view) {
  bigwinsView = view;
  const listBtn = document.getElementById("bigwinsViewListBtn");
  const mapBtn = document.getElementById("bigwinsViewMapBtn");
  const listEl = document.getElementById("bigwinsList");
  const mapWrap = document.getElementById("bigwinsMapWrap");
  const rangeSel = document.getElementById("bigwinsRangeFilter");
  const gameSel = document.getElementById("bigwinsGameFilter");
  if (view === "map") {
    listBtn?.classList.remove("is-active");
    mapBtn?.classList.add("is-active");
    listBtn?.setAttribute("aria-selected", "false");
    mapBtn?.setAttribute("aria-selected", "true");
    if (listEl) listEl.style.display = "none";
    if (mapWrap) mapWrap.style.display = "";
    if (rangeSel) rangeSel.style.display = "";
    if (gameSel) gameSel.style.display = "";
    // Map's purpose is the multi-year distribution. If the user is on a
    // short window (the list's default), bump to 3 years so smaller states
    // aren't invisible.
    if (rangeSel && parseInt(rangeSel.value, 10) < 365) {
      rangeSel.value = "1095";
    }
    rebuildBigWinsStateDropdown();
    loadBigWinsMap().then(() => renderBigWinsMap());
  } else {
    mapBtn?.classList.remove("is-active");
    listBtn?.classList.add("is-active");
    mapBtn?.setAttribute("aria-selected", "false");
    listBtn?.setAttribute("aria-selected", "true");
    if (mapWrap) mapWrap.style.display = "none";
    if (listEl) listEl.style.display = "";
    if (rangeSel) rangeSel.style.display = "";
    if (gameSel) gameSel.style.display = "";
    rebuildBigWinsStateDropdown();
    rebuildBigWinsGameDropdown();
    loadBigWins().then(() => filterBigWins());
  }
}

function rebuildBigWinsStateDropdown() {
  const sel = document.getElementById("bigwinsStateFilter");
  if (!sel) return;
  const prev = sel.value;
  const source = bigwinsView === "map" ? bigwinsMapStates : allBigWinsStates;
  sel.innerHTML = '<option value="">All States</option>' +
    source.map(sc => `<option value="${sc}">${sc}</option>`).join("");
  if (source.includes(prev)) sel.value = prev;
}

function rebuildBigWinsGameDropdown() {
  const sel = document.getElementById("bigwinsGameFilter");
  if (!sel) return;
  const prev = sel.value;
  let sorted;
  if (bigwinsView === "map") {
    sorted = bigwinsMapGameCounts.map(gc => [gc.name, gc.count]);
  } else {
    const counts = new Map();
    for (const c of allBigWins) {
      const key = (c.game_name || "").trim() || "(unknown)";
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }
  sel.innerHTML = '<option value="">All Tickets</option>' +
    sorted.map(([name, n]) => `<option value="${escAttr(name)}">${escHtml(name)} (${n})</option>`).join("");
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
}

function escAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function onBigWinsRangeChange() {
  if (bigwinsView === "map") {
    bigwinsMapKey = null;
    bigwinsMapGroups = [];
    loadBigWinsMap().then(() => renderBigWinsMap());
  } else {
    allBigWinsDays = null;
    allBigWins = [];
    loadBigWins();
  }
}

async function loadBigWinsMap() {
  const days = parseInt(document.getElementById("bigwinsRangeFilter")?.value || "1095", 10);
  const minPrize = parseFloat(document.getElementById("bigwinsPrizeFilter")?.value || "0") || 10000;
  // min_prize gates server-side aggregation, so it's part of the cache key.
  // state and game filters apply client-side off the loaded groups.
  const effectiveMin = Math.max(minPrize, 10000);
  const key = `${days}|${effectiveMin}`;
  if (bigwinsMapKey === key && bigwinsMapGroups.length) return;
  if (bigwinsMapLoading) return bigwinsMapLoading;
  const statsEl = document.getElementById("bigwinsMapStats");
  if (statsEl) statsEl.textContent = "Loading…";
  bigwinsMapLoading = (async () => {
    try {
      const url = `/api/reported-wins/map?days=${days}&min_prize=${effectiveMin}`;
      const res = await fetch(url);
      if (!res.ok) { bigwinsMapKey = key; return; }
      const data = await res.json();
      bigwinsMapGroups = data.groups || [];
      bigwinsMapStates = data.states_with_data || [];
      bigwinsMapGameCounts = data.game_counts || [];
      bigwinsMapTotalWins = data.total_wins || 0;
      bigwinsMapTotalPrize = data.total_prize || 0;
      bigwinsMapKey = key;
      rebuildBigWinsStateDropdown();
      rebuildBigWinsGameDropdown();
    } catch (e) {
      console.error("loadBigWinsMap:", e);
      bigwinsMapKey = key;
    } finally {
      bigwinsMapLoading = null;
    }
  })();
  return bigwinsMapLoading;
}

function initBigWinsMap() {
  if (bigwinsMap) return;
  bigwinsMap = L.map("bigwinsMap", { preferCanvas: true }).setView([39.5, -96.0], 4);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(bigwinsMap);
  bigwinsMapMarkers = L.layerGroup().addTo(bigwinsMap);
  setupMapAutoResize(bigwinsMap);
}

function renderBigWinsMap() {
  initBigWinsMap();
  // Leaflet sometimes needs a kick if container was display:none when init ran
  setTimeout(() => bigwinsMap && bigwinsMap.invalidateSize(), 50);
  bigwinsMapMarkers.clearLayers();

  const state = document.getElementById("bigwinsStateFilter")?.value || "";
  const game = document.getElementById("bigwinsGameFilter")?.value || "";
  const minPrize = parseFloat(document.getElementById("bigwinsPrizeFilter")?.value || "0") || 0;

  // Server already aggregated per location and applied days+min_prize. State
  // and game filters narrow the visible groups; no client-side regrouping.
  let groups = bigwinsMapGroups;
  if (state) groups = groups.filter(g => g.state_code === state);
  if (game)  groups = groups.filter(g => g.games && (g.games[game] || 0) > 0);

  const empty = document.getElementById("bigwinsMapEmpty");
  const stats = document.getElementById("bigwinsMapStats");
  const countEl = document.getElementById("bigwinsCount");

  if (!groups.length) {
    if (empty) {
      empty.style.display = "";
      const prizeLabel = minPrize ? `${fmtClaimPrize(minPrize)}+` : "";
      const filterDesc = [state, game, prizeLabel].filter(Boolean).join(" · ");
      empty.textContent = filterDesc
        ? `No mapped wins for ${filterDesc} in this time range.`
        : "No mapped wins yet for this time range.";
    }
    if (stats) stats.textContent = "";
    if (countEl) countEl.textContent = "";
    return;
  }
  if (empty) empty.style.display = "none";

  // Recompute headline counts from the visible slice.
  let visibleWins = 0;
  let visiblePrize = 0;
  for (const g of groups) {
    if (game) {
      const c = (g.games && g.games[game]) || 0;
      visibleWins += c;
      // We don't store per-game prize sums on the group — approximate using
      // the proportional share. Header label only, not used for plotting.
      visiblePrize += g.win_count > 0 ? g.total_prize * (c / g.win_count) : 0;
    } else {
      visibleWins += g.win_count;
      visiblePrize += g.total_prize;
    }
  }

  const days = parseInt(document.getElementById("bigwinsRangeFilter")?.value || "1095", 10);
  const rangeLabel = days >= 365 ? `last ${Math.round(days / 365)} year${days >= 730 ? "s" : ""}` : `last ${days} days`;
  if (stats) {
    const stateSeg = state ? ` · ${state}` : "";
    const gameSeg = game ? ` · ${escHtml(game)}` : "";
    const prizeSeg = minPrize ? ` · ${fmtClaimPrize(minPrize)}+` : "";
    stats.innerHTML = `<strong>${visibleWins.toLocaleString()}</strong> wins across <strong>${groups.length.toLocaleString()}</strong> retailers · <strong>${fmtClaimPrize(visiblePrize)}</strong> total prizes · ${rangeLabel}${stateSeg}${gameSeg}${prizeSeg}`;
  }
  if (countEl) countEl.textContent = `${visibleWins.toLocaleString()} mapped win${visibleWins !== 1 ? "s" : ""}`;

  const bounds = [];
  for (const g of groups) {
    const radius = Math.max(7, Math.min(28, Math.sqrt(g.total_prize) / 30));
    // Distinct color for winner-home pins (city centroid, not a specific store).
    const color = g.is_home
      ? (g.win_count > 1 ? "#3b82f6" : "#60a5fa")
      : (g.win_count > 1 ? "#e85d04" : "#f48c06");
    const stroke = g.is_home ? "#1e40af" : "#7a2a00";
    const marker = L.circleMarker([g.lat, g.lng], {
      radius,
      fillColor: color,
      color: stroke,
      weight: 1.5,
      opacity: 0.9,
      fillOpacity: 0.7,
    });
    marker.bindPopup(buildBigWinsPopup(g), { maxWidth: 280 });
    marker.addTo(bigwinsMapMarkers);
    bounds.push([g.lat, g.lng]);
  }
  if (bounds.length) {
    bigwinsMap.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
  }
}

function buildBigWinsPopup(g) {
  const tops = (g.top_wins || []).slice().sort((a, b) => (b.prize_amount || 0) - (a.prize_amount || 0));
  const items = tops.map(w => {
    const prize = fmtClaimPrize(w.prize_amount);
    const game = escHtml((w.source_game_name || "").trim() || "(unknown game)");
    const gameHtml = w.game_db_id != null
      ? `<a class="bp-game-link" onclick="openGame(${w.game_db_id});return false;">${game}</a>`
      : game;
    const date = w.claim_date ? `<span class="bp-date"> · ${escHtml(w.claim_date)}</span>` : "";
    return `<li><span class="bp-prize">${prize}</span> · ${gameHtml}${date}</li>`;
  }).join("");
  const winCount = g.win_count || tops.length;
  const remaining = winCount - tops.length;
  const moreNote = remaining > 0 ? `<li class="bp-date">…and ${remaining.toLocaleString()} more</li>` : "";
  const headerLine = g.is_home
    ? `<div class="bp-retailer">Winners from ${escHtml(g.winner_city || g.retailer_city || "this area")}</div>
       <div class="bp-city">${escHtml(g.state_code || "")} · ${winCount.toLocaleString()} win${winCount !== 1 ? "s" : ""} · home-city pin</div>`
    : `<div class="bp-retailer">${escHtml(g.retailer_name || "Unknown retailer")}</div>
       <div class="bp-city">${escHtml(g.retailer_city || "")}${g.state_code ? ", " + escHtml(g.state_code) : ""} · ${winCount.toLocaleString()} win${winCount !== 1 ? "s" : ""}</div>`;
  return `<div class="bigwins-popup">
    ${headerLine}
    <ul>${items}${moreNote}</ul>
  </div>`;
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
  const sortBy = document.getElementById("sortBy")?.value || "return_pct";
  currentSort.col = sortBy;
  currentSort.asc = sortBy === "name" || sortBy === "price";
  document.querySelectorAll("thead th[data-col]").forEach(h => {
    h.textContent = h.textContent.replace(/[▲▼]/, "").trim();
    h.classList.remove("active");
  });
  const matchingTh = document.querySelector(`thead th[data-col="${sortBy}"]`);
  if (matchingTh) {
    matchingTh.classList.add("active");
    matchingTh.textContent = matchingTh.textContent + (currentSort.asc ? " ▲" : " ▼");
  }
  loadGames();
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
      if (g.tickets_remaining == null) return true;
      return g.tickets_remaining >= 30000;
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
    const aNul = a[col] == null;
    const bNul = b[col] == null;
    if (aNul && bNul) return 0;
    if (aNul) return 1;
    if (bNul) return -1;
    return asc ? (a[col] > b[col] ? 1 : -1) : (a[col] < b[col] ? 1 : -1);
  });

  document.getElementById("resultCount").textContent =
    `${games.length.toLocaleString()} games`;

  const tbody = document.getElementById("gamesBody");
  if (!games.length) {
    tbody.innerHTML = `<tr><td colspan="16" class="loading-cell">No games match your filters.</td></tr>`;
    return;
  }

  const CHUNK = 60;
  tbody.innerHTML = games.slice(0, CHUNK).map((g, i) => gameRow(g, i + 1)).join("");
  updateStats(games);

  if (games.length > CHUNK) {
    let offset = CHUNK;
    function appendChunk() {
      const end = Math.min(offset + CHUNK, games.length);
      const html = games.slice(offset, end).map((g, i) => gameRow(g, offset + i + 1)).join("");
      const tmp = document.createElement("tbody");
      tmp.innerHTML = html;
      while (tmp.firstChild) tbody.appendChild(tmp.firstChild);
      offset = end;
      if (offset < games.length) requestAnimationFrame(appendChunk);
    }
    requestAnimationFrame(appendChunk);
  }
}

function gameRow(g, rank) {
  const ret = g.return_pct;
  const cls = ret >= 100 ? "ev-positive" : ret >= 90 ? "ev-near" : ret >= 70 ? "ev-mid" : "ev-low";
  const barPct = Math.min(100, (ret / 120) * 100).toFixed(1);

  const approxMark = g.ev_approximate ? "~" : "";
  const retCell = ret != null
    ? `<span class="${cls}" title="${g.ev_approximate ? "Estimated — top prize depletion rate used as proxy for overall ticket sales" : ""}">
         <div class="return-bar-wrap">
           <div class="return-bar"><div class="return-bar-fill" style="width:${barPct}%"></div></div>
           ${approxMark}${ret.toFixed(2)}%
         </div>
       </span>`
    : "—";

  const ev     = g.ev != null ? `${approxMark}${g.ev >= 0 ? "+" : ""}$${g.ev.toFixed(2)}` : "—";
  const jackpotOdds = g.jackpot_odds_one_in != null ? `1 in ${fmtNum(Math.round(g.jackpot_odds_one_in))}` : "—";
  const odds   = g.overall_odds_one_in ? `1 in ${fmtNum(g.overall_odds_one_in)}` : "—";
  const left   = g.tickets_remaining != null ? fmtNum(g.tickets_remaining) : "—";
  const topRem = g.top_prize_remaining != null ? fmtNum(g.top_prize_remaining) : "—";
  const pool   = g.prize_pool_remaining != null ? "$" + fmtMoney(g.prize_pool_remaining) : "—";
  const updated = g.scraped_at ? timeAgo(parseReportedAt(g.scraped_at)) : "—";
  const updatedFull = g.scraped_at ? fmtDate(g.scraped_at) : "";

  let startedCell = `<span style="color:var(--text-muted)">—</span>`;
  if (g.start_date) {
    const sd = parseReportedAt(g.start_date);
    const days = Math.floor((Date.now() - sd) / 86400000);
    let label;
    if (days < 0) label = "soon";
    else if (days < 30) label = `${days}d ago`;
    else if (days < 365) label = `${Math.round(days / 30)} mo ago`;
    else label = `${(days / 365).toFixed(1)} yr ago`;
    const isNew = days >= 0 && days < 60;
    startedCell = `<span title="${fmtDate(g.start_date)}" style="font-size:.8rem;${isNew ? 'color:var(--green);font-weight:700' : 'color:var(--text-muted)'}">${isNew ? '🆕 ' : ''}${label}</span>`;
  }

  const reportCount = gameCounts[g.name.toLowerCase()] || 0;
  const reportBadge = reportCount > 0
    ? `<span class="game-report-badge" title="In stock at ${reportCount} member-reported location${reportCount > 1 ? 's' : ''}">${reportCount} 📍</span>`
    : "";

  const nameEsc = escHtml(g.name).replace(/'/g, "\\'");
  const isAdmin = _currentUser && _currentUser.role === "admin";
  const campaignCell = isAdmin
    ? `<td onclick="event.stopPropagation()">
      <button class="btn-campaign-launch" onclick="launchCampaign('${nameEsc}', ${g.price}, '${escHtml(g.game_id)}', '${escHtml(g.state_code || "")}')" title="Create calling campaign for this game">📞</button>
    </td>`
    : "";
  return `<tr onclick="openGame(${g.id})">
    <td style="color:var(--text-muted);font-size:.8rem;font-weight:700">${rank}</td>
    <td><span class="state-pill state-${g.state_code}">${g.state_code}</span></td>
    <td><span class="game-name"><strong>${escHtml(g.name)}</strong>${reportBadge}</span></td>
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
    <td>${startedCell}</td>
    <td style="color:var(--text-muted);font-size:.8rem" title="${updatedFull}">${updated}</td>
    ${campaignCell}
  </tr>`;
}

// ── Stats update ──────────────────────────────────────────────────────────────
function updateStats(games) {
  const gs = games || allGames;
  const positive = gs.filter(g => g.return_pct >= 100).length;
  const best = gs.reduce((max, g) => Math.max(max, g.return_pct || 0), 0);
  const statesCount = new Set(gs.map(g => g.state_code)).size;
  document.getElementById("statGames").textContent = gs.length.toLocaleString();
  document.getElementById("statStates").textContent = statesCount;
  document.getElementById("statPositive").textContent = positive.toLocaleString();
  document.getElementById("statBest").textContent = best > 0 ? best.toFixed(1) + "%" : "—";
}

// ── Column sort ───────────────────────────────────────────────────────────────
// Columns where ascending is the natural first-click direction (alphabetical names,
// lower-is-better odds, lower price).
const ASC_DEFAULT_COLS = new Set([
  "state_code", "name", "game_id", "price",
  "jackpot_odds_one_in", "overall_odds_one_in",
]);

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("thead th[data-col]").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.dataset.col;
      if (currentSort.col === col) {
        currentSort.asc = !currentSort.asc;
      } else {
        currentSort.col = col;
        currentSort.asc = ASC_DEFAULT_COLS.has(col);
      }
      document.querySelectorAll("thead th").forEach(h => h.classList.remove("active"));
      th.classList.add("active");
      th.textContent = th.textContent.replace(/[▲▼]/, "").trim() +
        (currentSort.asc ? " ▲" : " ▼");
      requestAnimationFrame(renderTable);
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
  const ev = g.ev != null ? `${g.ev_approximate ? "~" : ""}${g.ev >= 0 ? "+" : ""}$${g.ev.toFixed(4)}` : "N/A";

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
      ? `1 in ${fmtNum(g.tickets_remaining / t.prizes_remaining)}`
      : (t.odds_one_in ? `1 in ${fmtNum(t.odds_one_in)}` : "—");
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
    ${CHASE_HANDLERS[g.state_code] ? `<div class="modal-chase-link-row"><a class="modal-chase-link" href="javascript:void(0)" onclick="viewGameInChase(${escHtml(JSON.stringify(g.name))},${escHtml(JSON.stringify(g.state_code || ""))})">Find this ticket in The Chase →</a></div>` : ""}
    ${g.scraped_at ? `<div style="font-size:.78rem;color:var(--text-muted);margin:.25rem 0 .5rem;letter-spacing:.01em">State lottery data as of ${fmtDate(g.scraped_at)} &nbsp;·&nbsp; ${timeAgo(parseReportedAt(g.scraped_at))}</div>` : ""}
    ${noSalesData ? `<div style="background:rgba(255,200,0,.12);border:1px solid rgba(255,200,0,.3);border-radius:8px;padding:.6rem .85rem;margin:.75rem 0;font-size:.82rem;color:#c8a800">
      <strong>Limited data</strong> — ${g.state_name} does not publish ticket sales figures, so Est. Tickets Left, Tickets Sold, and EV calculations are based on prize table odds only.
    </div>` : ""}
    ${g.ev_approximate ? `<div style="background:rgba(255,160,0,.1);border:1px solid rgba(255,160,0,.3);border-radius:8px;padding:.6rem .85rem;margin:.75rem 0;font-size:.82rem;color:#c87800">
      <strong>Estimated EV</strong> — ${g.state_code === "ME"
        ? `${g.state_name} publishes the total unclaimed prize pool and percent unsold, but only the top prize tiers per game. EV is computed from the aggregate unclaimed dollar value divided by remaining tickets; small-prize tiers are not enumerated individually.`
        : `${g.state_name} only publishes top-prize remaining counts. The top-prize depletion rate is extrapolated to estimate remaining counts for every prize tier, total ticket sales, and EV. Real EV may differ.`}
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
        <div class="modal-stat-val">${ticketsSold != null && ticketsSold > 0 ? fmtNum(ticketsSold) : "—"}</div>
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
          <th>Original Odds</th>
          <th>Current Odds</th>
          <th>Remaining</th>
          <th>Total Printed</th>
        </tr>
      </thead>
      <tbody>${tierRows}</tbody>
    </table>` : "<p style='color:var(--text-muted)'>Prize tier data not available.</p>"}

    ${(g.detail_url || STATE_LOTTERY_URLS[g.state_code]) ? `<a class="detail-link" href="${escHtml(g.detail_url || STATE_LOTTERY_URLS[g.state_code])}" target="_blank" rel="noopener">
      View on ${g.state_name} Lottery website ↗
    </a>` : ""}

    <div id="modalCommunityWrapper">${modalCommunitySection(g.name, g.price, g.state_code, g.state_name)}</div>
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
  wrapper.innerHTML = modalCommunitySection(_openModalGame.name, _openModalGame.price, _openModalGame.state_code, _openModalGame.state_name);
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeModal(); closeAuthModal(); closeReportModal(); closeGameNotes(); }
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
    await fetch(`/api/scrape?state=${state}`, { method: "POST", headers: authHeaders() });
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
    await loadAllGamesUnfiltered();
    await loadStatus();
    await loadStates();
  }
}

// ── Tab switching ─────────────────────────────────────────────────────────────
function switchTab(name) {
  if (name === "health" && !(_currentUser && _currentUser.role === "admin")) {
    name = "ev";
  }
  currentTab = name;
  document.querySelectorAll(".tab-content").forEach(el => el.style.display = "none");
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  document.getElementById(`tab-${name}`).style.display = "";
  document.querySelector(`.tab-btn[data-tab="${name}"]`).classList.add("active");
  if (name === "ma") {
    selectHuntState(currentHuntState);
  }
  if (name === "settings") {
    populateSettingsTab();
  }
  if (name === "caller") {
    populateCallerStateSelect();
    if (!callerLoaded) {
      callerLoaded = true;
      loadCallerData();
      setInterval(() => { if (currentTab === "caller") loadCallerData(); }, 15_000);
    }
  }
  if (name === "bigwins" && !bigwinsLoaded) {
    bigwinsLoaded = true;
    loadBigWins();
  }
  if (name === "health") {
    loadStateHealth();
    const isAdmin = _currentUser && _currentUser.role === "admin";
  }
  if (name === "plays") {
    if (_currentUser) loadPlays();
    else document.getElementById("playsLoginNudge").style.display = "";
  }
  if (name === "account") {
    populateAccountTab();
  }
}

function toggleStateDropdown() {
  const panel = document.getElementById("stateDropdownPanel");
  if (!panel) return;
  panel.style.display = panel.style.display === "none" ? "" : "none";
}

function closeStateDropdown() {
  const panel = document.getElementById("stateDropdownPanel");
  if (panel) panel.style.display = "none";
}

document.addEventListener("click", function(e) {
  const wrap = document.getElementById("stateDropdownWrap");
  if (wrap && !wrap.contains(e.target)) closeStateDropdown();
});

function selectHuntState(code) {
  currentHuntState = code;
  document.querySelectorAll(".state-dd-item").forEach(el =>
    el.classList.toggle("active", el.dataset.state === code)
  );
  const nameEl = document.querySelector(`.state-dd-item[data-state="${code}"] .state-dd-item-name`);
  if (nameEl) {
    document.getElementById("stateDropdownLabel").textContent = nameEl.textContent;
    document.getElementById("stateDropdownAbbr").textContent = code;
  }
  closeStateDropdown();

  document.getElementById("huntConsoleMA").style.display   = "none";
  document.getElementById("huntConsoleAZ").style.display   = "none";
  document.getElementById("huntConsoleRI").style.display   = "none";
  document.getElementById("huntConsoleFL").style.display   = "none";
  document.getElementById("huntConsoleGA").style.display   = "none";
  document.getElementById("huntConsoleNY").style.display   = "none";
  document.getElementById("huntConsoleVA").style.display   = "none";
  document.getElementById("huntConsoleDC").style.display   = "none";
  document.getElementById("huntConsoleVT").style.display   = "none";
  document.getElementById("huntConsoleGen").style.display  = "none";
  document.getElementById("huntConsoleSoon").style.display = "none";

  if (code === "MA") {
    document.getElementById("huntConsoleMA").style.display = "";
    if (!maLoaded) loadMaRetailers();
    if (_currentUser && Date.now() - communityReportsLastFetch > 30_000) loadCommunityReports();
  } else if (code === "AZ") {
    document.getElementById("huntConsoleAZ").style.display = "";
    if (!azLoaded) loadAzRetailers();
    if (_currentUser && Date.now() - communityReportsLastFetch > 30_000) loadCommunityReports();
  } else if (code === "RI") {
    document.getElementById("huntConsoleRI").style.display = "";
    if (!riLoaded) loadRiRetailers();
    if (_currentUser && Date.now() - communityReportsLastFetch > 30_000) loadCommunityReports();
  } else if (code === "FL") {
    document.getElementById("huntConsoleFL").style.display = "";
    if (!flLoaded) loadFlRetailers();
    if (_currentUser && Date.now() - communityReportsLastFetch > 30_000) loadCommunityReports();
  } else if (code === "GA") {
    document.getElementById("huntConsoleGA").style.display = "";
    if (!gaLoaded) loadGaRetailers();
    if (_currentUser && Date.now() - communityReportsLastFetch > 30_000) loadCommunityReports();
  } else if (code === "NY") {
    document.getElementById("huntConsoleNY").style.display = "";
    if (!nyLoaded) loadNyRetailers();
    if (_currentUser && Date.now() - communityReportsLastFetch > 30_000) loadCommunityReports();
  } else if (code === "VA") {
    document.getElementById("huntConsoleVA").style.display = "";
    if (!vaLoaded) loadVaRetailers();
    if (_currentUser && Date.now() - communityReportsLastFetch > 30_000) loadCommunityReports();
  } else if (code === "DC") {
    document.getElementById("huntConsoleDC").style.display = "";
    if (!dcLoaded) loadDcRetailers();
    if (_currentUser && Date.now() - communityReportsLastFetch > 30_000) loadCommunityReports();
  } else if (code === "VT") {
    document.getElementById("huntConsoleVT").style.display = "";
    if (!vtLoaded) loadVtRetailers();
    if (_currentUser && Date.now() - communityReportsLastFetch > 30_000) loadCommunityReports();
  } else if (GEN_STATES[code]) {
    document.getElementById("huntConsoleGen").style.display = "";
    loadGenRetailers(code);
    if (_currentUser && Date.now() - communityReportsLastFetch > 30_000) loadCommunityReports();
  } else {
    document.getElementById("huntConsoleSoon").style.display = "";
    const soonNameEl = document.querySelector(`.state-dd-item[data-state="${code}"] .state-dd-item-name`);
    const stateName = soonNameEl?.textContent || code;
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

  mapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));


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

// ── Shared map-cluster helpers ────────────────────────────────────────────────
// Marker clustering, lazy popups, and per-map debouncing for all state maps.
function _dotIcon(color, size) {
  const s = size || 10;
  return L.divIcon({
    className: "sf-dot",
    html: `<span style="display:block;width:${s}px;height:${s}px;border-radius:50%;background:${color};border:1.5px solid #fff;box-shadow:0 0 2px rgba(0,0,0,.45)"></span>`,
    iconSize: [s, s],
    iconAnchor: [s / 2, s / 2],
  });
}

function _popupHtmlRetailer(r, status) {
  const statusTxt = status ? (status.has_stock ? "✅ In Stock" : "❌ Out of Stock") : "Not yet checked";
  const mapsUrl = `https://www.google.com/maps/dir/?api=1&destination=${r.latitude},${r.longitude}`;
  const rid = r.id || r.retailer_id || "";
  return `<b>${escHtml(r.name)}</b><br>${escHtml(r.city || "")} ${escHtml(r.zipCode || "")}<br>${statusTxt}<br>` +
    `<a href="${mapsUrl}" target="_blank" rel="noopener" style="font-size:.85rem">📍 Directions</a> · ` +
    `<a href="javascript:void(0)" onclick="openStoreInventoryFromMap('${rid}'); return false;" style="font-size:.85rem">📋 Inventory</a>`;
}

function _popupHtmlReport(r) {
  const time = r.reported_at ? timeAgo(parseReportedAt(r.reported_at)) : "";
  const mapsUrl = `https://www.google.com/maps/dir/?api=1&destination=${r.lat},${r.lng}`;
  const rid = r.id || r.retailer_id || "";
  return `<b>${escHtml(r.retailer_name || "")}</b><br>` +
    `${escHtml(r.game_name || "")}${r.game_price ? " $" + r.game_price : ""}<br>` +
    `${r.has_stock ? "✅ In Stock" : "❌ Out of Stock"}<br>` +
    `<span style="color:#888;font-size:.8rem">${escHtml(r.source === "caller" ? "📞 Call" : "👤 Community")} · ${time}</span><br>` +
    `<a href="${mapsUrl}" target="_blank" rel="noopener" style="font-size:.85rem">📍 Directions</a> · ` +
    `<a href="javascript:void(0)" onclick="openStoreInventoryFromMap('${rid}'); return false;" style="font-size:.85rem">📋 Inventory</a>`;
}

// Build (or rebuild) a marker-cluster layer of retailers + community reports on `map`.
// Replaces any prior layer stored at window[layerKey].
function renderInventoryCluster(map, layerKey, opts) {
  if (!map) return;
  const o = opts || {};
  const retailers = o.retailers || [];
  const allReports = o.reports || [];
  const scopeIds = o.scopeIds || null;
  const selectedGame = o.selectedGame || null;
  const reportFilter = o.reportFilter || "all";

  // Remove previous layer if present.
  const prev = window[layerKey];
  if (prev) { map.removeLayer(prev); window[layerKey] = null; }

  const cluster = L.markerClusterGroup({
    chunkedLoading: true,
    chunkInterval: 150,
    chunkDelay: 40,
    maxClusterRadius: 55,
    showCoverageOnHover: false,
    spiderfyOnMaxZoom: true,
    disableClusteringAtZoom: 16,
    // Color clusters by inventory status, not by count. Green when any child
    // has in-stock inventory marked; otherwise neutral grey.
    iconCreateFunction: (c) => {
      const children = c.getAllChildMarkers();
      let hasInStock = false;
      for (const m of children) {
        if (m.options.sfStockState === "in") { hasInStock = true; break; }
      }
      const inner = hasInStock ? "rgba(0,204,68,1)" : "rgba(150,150,150,0.9)";
      const count = children.length;
      return L.divIcon({
        html: `<div style="background:${inner}"><span>${count}</span></div>`,
        className: `sf-cluster ${hasInStock ? "sf-cluster-instock" : "sf-cluster-neutral"}`,
        iconSize: L.point(40, 40),
      });
    },
  });

  const batch = [];
  const renderedRetailerIds = new Set();

  for (const r of retailers) {
    if (!r.latitude || !r.longitude) continue;
    const lat = parseFloat(r.latitude), lng = parseFloat(r.longitude);
    if (!isFinite(lat) || !isFinite(lng)) continue;
    const status = retailerLatestStatus[r.id];
    const color = status ? (status.has_stock ? "#00cc44" : "#cc2200") : "#4a9eff";
    const stockState = status ? (status.has_stock ? "in" : "out") : "unchecked";
    const m = L.marker([lat, lng], { icon: _dotIcon(color, 10), sfStockState: stockState });
    // Lazy popup: HTML is only built when the marker is clicked.
    m.bindPopup(() => _popupHtmlRetailer(r, status));
    batch.push(m);
    if (r.id != null) renderedRetailerIds.add(String(r.id));
  }

  let reports = allReports.filter(r => r.lat && r.lng);
  if (scopeIds) reports = reports.filter(r => scopeIds.has(String(r.retailer_id)));
  if (selectedGame) reports = reports.filter(r => r.game_name && r.game_name.toLowerCase() === selectedGame.name.toLowerCase());
  if (reportFilter === "in")  reports = reports.filter(r =>  r.has_stock);
  if (reportFilter === "out") reports = reports.filter(r => !r.has_stock);
  // Don't double-count: a report at an already-rendered retailer would inflate the cluster badge.
  reports = reports.filter(r => !renderedRetailerIds.has(String(r.retailer_id)));

  for (const r of reports) {
    const color = r.has_stock ? "#00cc44" : "#cc2200";
    const m = L.marker([r.lat, r.lng], { icon: _dotIcon(color, 12), sfStockState: r.has_stock ? "in" : "out" });
    m.bindPopup(() => _popupHtmlReport(r));
    batch.push(m);
  }

  if (batch.length) {
    cluster.addLayers(batch);
    cluster.addTo(map);
    window[layerKey] = cluster;
  }
}

// Reacts to any container size change (window resize, sidebar toggle, tab switch)
// by calling invalidateSize. Without this, Leaflet's internal pixel coords go stale
// and tiles render as misaligned chunks when you zoom or pan.
function setupMapAutoResize(map) {
  if (!map || !map._container || map._autoResizeAttached) return;
  map._autoResizeAttached = true;
  let raf = 0;
  const kick = () => {
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => { raf = 0; if (map._container) map.invalidateSize(); });
  };
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(kick);
    ro.observe(map._container);
    map._autoResizeObserver = ro;
  }
  window.addEventListener("resize", kick);
  map._autoResizeKick = kick;
}

// Per-key debouncer: collapses repeated calls (e.g. typing in a search box) into one render.
const _mapRenderTimers = {};
function debounceMapRender(key, fn, ms) {
  if (_mapRenderTimers[key]) clearTimeout(_mapRenderTimers[key]);
  _mapRenderTimers[key] = setTimeout(() => {
    _mapRenderTimers[key] = null;
    fn();
  }, ms == null ? 180 : ms);
}

function toggleMaMap() {
  const sec = document.getElementById("maMapSection");
  maMapVisible = !maMapVisible;
  sec.style.display = maMapVisible ? "" : "none";
  if (maMapVisible) {
    if (!maMap) initMaMap();
    setTimeout(() => maMap && maMap.invalidateSize(), 50);
    renderMapLayers(getFilteredRows());
  }
}

function initMaMap() {
  maMap = L.map("maMap", { preferCanvas: true }).setView([42.1, -71.8], 9);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(maMap);
  setupMapAutoResize(maMap);
}

function renderMapLayers(retailers) {
  if (!maMap) return;
  if (maLayerControl) { maLayerControl.remove(); maLayerControl = null; }
  debounceMapRender("ma", () => updateInventoryMapLayer(retailers), 180);
}

// ── Lazy row rendering (shared by every state hunt table) ────────────────────
// Renders an initial chunk, then lazy-appends more as the user scrolls toward
// the bottom of the table's scroll container. Keeps the DOM small instead of
// materializing 30k rows up front — that was the source of the laggy scroll.
function lazyRenderRows({ tbody, rows, rowFn, getStaleFlag, chunk = 200, cols = 6 }) {
  if (tbody._lazyIO) { tbody._lazyIO.disconnect(); tbody._lazyIO = null; }
  tbody._lazyFlush = null;
  const initial = Math.min(chunk, rows.length);
  tbody.innerHTML = rows.slice(0, initial).map((r, i) => rowFn(r, i + 1)).join("");
  updateReportBadges();
  if (initial >= rows.length) return;
  const sentinel = document.createElement("tr");
  sentinel.className = "lazy-sentinel";
  sentinel.innerHTML = `<td colspan="${cols}" style="height:1px;padding:0;border:0"></td>`;
  tbody.appendChild(sentinel);
  const scrollRoot = tbody.closest(".table-scroll") || null;
  let offset = initial;
  const io = new IntersectionObserver((entries) => {
    if (getStaleFlag && getStaleFlag()) { io.disconnect(); tbody._lazyIO = null; return; }
    if (!entries.some(e => e.isIntersecting)) return;
    if (offset >= rows.length) { io.disconnect(); sentinel.remove(); tbody._lazyIO = null; return; }
    const end = Math.min(offset + chunk, rows.length);
    const tmp = document.createElement("tbody");
    tmp.innerHTML = rows.slice(offset, end).map((r, i) => rowFn(r, offset + i + 1)).join("");
    while (tmp.firstChild) tbody.insertBefore(tmp.firstChild, sentinel);
    offset = end;
    updateReportBadges();
    if (offset >= rows.length) { io.disconnect(); sentinel.remove(); tbody._lazyIO = null; }
  }, { root: scrollRoot, rootMargin: "600px 0px" });
  io.observe(sentinel);
  tbody._lazyIO = io;
  // Synchronously render any rows still pending — used when an off-screen row needs to exist
  // in the DOM right now (e.g. opening a store profile from a map popup).
  tbody._lazyFlush = () => {
    if (offset >= rows.length) return;
    if (getStaleFlag && getStaleFlag()) return;
    const tmp = document.createElement("tbody");
    tmp.innerHTML = rows.slice(offset, rows.length).map((r, i) => rowFn(r, offset + i + 1)).join("");
    while (tmp.firstChild) tbody.insertBefore(tmp.firstChild, sentinel);
    offset = rows.length;
    updateReportBadges();
    io.disconnect();
    sentinel.remove();
    tbody._lazyIO = null;
    tbody._lazyFlush = null;
  };
}

// ── MA Hunt data loading ──────────────────────────────────────────────────────
async function loadMaRetailers() {
  try {
    const res = await fetch("/api/ma/retailers?limit=30000");
    const data = await res.json();
    allRetailers = data.retailers || [];
    maLoaded = true;
    updateMaStats();
    renderMaTable();
    if (!maMapVisible) toggleMaMap();
  } catch (e) {
    document.getElementById("maTableBody").innerHTML =
      `<tr><td colspan="6" class="loading-cell">Failed to load MA retailers.</td></tr>`;
  }
}

function updateMaStats() {
  document.getElementById("maStatTotal").textContent = allRetailers.length.toLocaleString();
}

function renderMaTable() {
  const myGen = ++maRenderGen;
  _openProfileId = null;
  const rows = getFilteredRows();
  const checkedCount = selectedGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedGame.name)}</strong>` : "";
  document.getElementById("maResultCount").innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("maTableBody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
    return;
  }
  if (maMapVisible) renderMapLayers(rows);
  lazyRenderRows({
    tbody,
    rows,
    rowFn: maRow,
    getStaleFlag: () => myGen !== maRenderGen,
  });
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
    <td><strong>${escHtml(r.name)}</strong><br><span style="font-size:.78rem;color:var(--text-muted)">${escHtml(r.address)}</span><span class="report-count-badge" id="rbadge-${rid}" style="display:none"></span></td>
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
  const d = parseReportedAt(dt);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "America/New_York" });
}

// ── Launch campaign from EV table ─────────────────────────────────────────────
function launchCampaign(name, price, _gameId, stateCode) {
  switchTab("caller");
  if (stateCode) {
    const stateSel = document.getElementById("cfStateSelect");
    if (stateSel && stateSel.value !== stateCode) {
      stateSel.value = stateCode;
      onCallerStateSelect();
    }
  }
  if (name) {
    _selectedTickets.add(name);
    renderTicketsPicker();
  }
  const btn = document.getElementById("cfCreateBtn");
  if (btn) btn.scrollIntoView({ behavior: "smooth", block: "center" });
}

// ── Call Agent ────────────────────────────────────────────────────────────────
let callerLoaded = false;
let _callerCampaigns = [];
let _callerHits = [];

async function loadCallerData() {
  try {
    const [statsRes, recentRes, configRes, diagRes] = await Promise.all([
      callerFetch("/api/vapi/stats"),
      callerFetch("/api/vapi/recent?limit=100"),
      callerFetch("/api/vapi/config"),
      callerFetch("/api/vapi/diagnostics"),
    ]);
    const stats  = await statsRes.json();
    const recent = await recentRes.json();
    const config = await configRes.json();
    const diag   = await diagRes.json();

    document.getElementById("callerStatHits").textContent      = (stats.hits || 0).toLocaleString();
    document.getElementById("callerStatCalls").textContent     = (stats.total_calls || 0).toLocaleString();
    document.getElementById("callerStatFlight").textContent    = (stats.in_flight || 0).toLocaleString();
    document.getElementById("callerStatCampaigns").textContent = (stats.calls_today || 0).toLocaleString();
    const vmEl = document.getElementById("callerStatVoicemail");
    if (vmEl) vmEl.textContent = (stats.voicemails || 0).toLocaleString();
    const vmTodayEl = document.getElementById("callerStatVoicemailToday");
    if (vmTodayEl) vmTodayEl.textContent = `${(stats.voicemails_today || 0).toLocaleString()} today`;

    const backendEl = document.getElementById("callerBackendBadge");
    if (backendEl) {
      if (config.configured) {
        backendEl.textContent = "VAPI Ready";
        backendEl.className = "badge badge-status-active";
      } else {
        backendEl.textContent = "Not Configured";
        backendEl.className = "badge badge-status-idle";
      }
    }

    const banner = document.getElementById("callerConfigBanner");
    if (banner) {
      if (!config.configured) {
        const missing = [];
        if (!config.has_private_key)  missing.push("<code>VAPI_PRIVATE_KEY</code>");
        if (!config.has_assistant_id) missing.push("<code>VAPI_ASSISTANT_ID</code>");
        if (!config.has_phone_number) missing.push("<code>VAPI_PHONE_NUMBER_ID</code>");
        banner.innerHTML = `VAPI is not configured — set ${missing.join(", ")} in your environment to enable live calls. Preview still works.`;
        banner.style.background = "rgba(245,158,11,0.12)";
        banner.style.color      = "#92400e";
        banner.style.border     = "1px solid rgba(245,158,11,0.35)";
        banner.style.display    = "block";
      } else if (diag && diag.stuck_in_flight >= 2 && !diag.last_webhook_received_at) {
        banner.innerHTML =
          `<strong>⚠ VAPI webhook isn't reaching this server.</strong> ` +
          `${diag.stuck_in_flight} calls are stuck "In flight" because we never received an end-of-call report. ` +
          `<br><br>In your VAPI dashboard → Assistant → <strong>Server URL</strong>, set:<br>` +
          `<code style="font-size:.78rem">${escHtml(diag.expected_webhook_url || '')}</code>` +
          `<br><br>And under <strong>Server Messages</strong>, enable <code>end-of-call-report</code>.` +
          (diag.webhook_secret_configured ? `<br>Also set the assistant's webhook header <code>X-VAPI-Secret</code> to match <code>VAPI_WEBHOOK_SECRET</code>.` : "");
        banner.style.background = "rgba(239,68,68,0.10)";
        banner.style.color      = "#991b1b";
        banner.style.border     = "1px solid rgba(239,68,68,0.4)";
        banner.style.display    = "block";
      } else if (diag && diag.last_webhook_received_at) {
        const ago = timeAgo ? timeAgo(diag.last_webhook_received_at) : diag.last_webhook_received_at;
        banner.innerHTML = `✓ Webhook healthy — last end-of-call report received ${ago}. <span style="color:var(--text-muted);font-size:.78rem">URL: <code>${escHtml(diag.expected_webhook_url || '')}</code></span>`;
        banner.style.background = "rgba(34,197,94,0.10)";
        banner.style.color      = "#166534";
        banner.style.border     = "1px solid rgba(34,197,94,0.35)";
        banner.style.display    = "block";
      } else {
        banner.style.display = "none";
      }
    }

    _callerRecent = recent.calls || [];
    renderCallerRecent();
  } catch (e) {
    const body = document.getElementById("callerRecentBody");
    if (body) body.innerHTML = `<tr><td colspan="9" class="loading-cell">Failed to load caller data. Is the server running?</td></tr>`;
  }
}

let _callerRecent = [];

function renderCallerRecent() {
  const tbody = document.getElementById("callerRecentBody");
  if (!tbody) return;
  if (!_callerRecent.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="loading-cell">No calls yet — start a dispatch above.</td></tr>`;
    return;
  }
  tbody.innerHTML = _callerRecent.map(c => {
    const resultHtml = renderResultCell(c);
    const conf = c.confidence != null ? `${Math.round(parseFloat(c.confidence) * 100)}%` : "—";
    const dur  = c.duration_sec != null ? `${Math.round(parseFloat(c.duration_sec))}s` : "—";
    const when = c.ended_at || c.received_at || "";
    const whenShort = when ? when.slice(0, 16).replace("T", " ") : "—";
    const hasDetail = !!(c.summary || c.transcript || (c.per_ticket_results && c.per_ticket_results.length));
    const summaryShort = c.summary ? escHtml(c.summary) : "—";
    const summaryCell = hasDetail
      ? `<span class="cf-summary-link" onclick="openCallDetail(${c.id})" title="Click to read transcript">${summaryShort}</span>`
      : summaryShort;
    return `<tr>
      <td><span style="white-space:nowrap">${whenShort}</span></td>
      <td><strong>${escHtml(c.retailer_name) || "<span style='color:var(--text-muted)'>(unknown)</span>"}</strong></td>
      <td>${escHtml(c.retailer_city) || "—"}</td>
      <td>${escHtml(c.game_name) || "—"}</td>
      <td>${resultHtml}</td>
      <td>${conf}</td>
      <td>${dur}</td>
      <td><span style="color:var(--text-muted);font-size:.78rem">${escHtml(c.ended_reason || "—")}</span></td>
      <td><span style="display:inline-block;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle">${summaryCell}</span></td>
    </tr>`;
  }).join("");
}

function openCallDetail(callId) {
  const c = (_callerRecent || []).find(x => x.id === callId);
  if (!c) return;

  const when = c.ended_at || c.received_at || "";
  const whenStr = when ? when.replace("T", " ").slice(0, 19) : "—";
  const dur  = c.duration_sec != null ? `${Math.round(parseFloat(c.duration_sec))}s` : "—";
  const conf = c.confidence != null ? `${Math.round(parseFloat(c.confidence) * 100)}%` : "—";

  const ticketsHtml = renderResultDetail(c);
  const perTicketBlock = (Array.isArray(c.per_ticket_results) && c.per_ticket_results.length)
    ? `<div class="call-detail-section">
         <div class="call-detail-section-label">Per-ticket results</div>
         <div>${ticketsHtml}</div>
       </div>`
    : "";

  const summaryBlock = c.summary
    ? `<div class="call-detail-section">
         <div class="call-detail-section-label">Summary</div>
         <div class="call-detail-summary">${escHtml(c.summary)}</div>
       </div>`
    : "";

  const transcriptBlock = c.transcript
    ? `<div class="call-detail-section">
         <div class="call-detail-section-label">Transcript</div>
         <div class="call-detail-transcript">${formatTranscript(c.transcript)}</div>
       </div>`
    : `<div class="call-detail-section">
         <div class="call-detail-section-label">Transcript</div>
         <div style="font-size:.85rem;color:var(--text-muted)">No transcript saved for this call.</div>
       </div>`;

  const retailer = c.retailer_name || "(unknown retailer)";
  const meta = [
    whenStr,
    c.retailer_city || null,
    c.state_code || null,
    `Duration ${dur}`,
    `Confidence ${conf}`,
    c.ended_reason ? `Ended: ${c.ended_reason}` : null,
  ].filter(Boolean).map(escHtml).join(" · ");

  document.getElementById("callDetailBody").innerHTML = `
    <div class="call-detail-header">
      <div class="call-detail-title">${escHtml(retailer)}</div>
      <div class="call-detail-meta">${meta}</div>
      <div style="font-size:.85rem;margin-top:.2rem"><strong>Asked about:</strong> ${escHtml(c.game_name || "—")}</div>
    </div>
    ${perTicketBlock}
    ${summaryBlock}
    ${transcriptBlock}
  `;
  document.getElementById("callDetailModal").classList.add("show");
}

function closeCallDetail(ev) {
  if (ev && ev.target && ev.target.id !== "callDetailModal" && !ev.target.classList.contains("call-detail-close")) return;
  document.getElementById("callDetailModal").classList.remove("show");
}

function formatTranscript(text) {
  if (!text) return "";
  // VAPI delivers transcripts like:  "User: hello\nAI: hi there\nUser: ..."
  return String(text).split(/\r?\n/).map(line => {
    const m = line.match(/^(AI|Assistant|User|Customer|Bot)\s*:\s*(.*)$/i);
    if (!m) return escHtml(line);
    const isAi = /ai|assistant|bot/i.test(m[1]);
    const cls  = isAi ? "speaker-ai" : "speaker-user";
    return `<span class="${cls}">${escHtml(m[1])}:</span> ${escHtml(m[2])}`;
  }).join("\n");
}

function renderResultCell(c) {
  // Compact ratio for the table row: "2/3 in stock", color-coded.
  if (c.is_voicemail) {
    return `<span class="badge badge-status-paused" title="Reached a voicemail greeting — no inventory data captured">📭 Voicemail</span>`;
  }
  if (c.ended_at == null && c.has_game == null && !(c.per_ticket_results && c.per_ticket_results.length)) {
    return `<span class="badge badge-status-paused">In flight</span>`;
  }
  if (Array.isArray(c.per_ticket_results) && c.per_ticket_results.length) {
    const total = c.per_ticket_results.length;
    const yes = c.per_ticket_results.filter(t => t && t.has_game === true).length;
    let cls;
    if (yes === total)   cls = "badge-green";          // all in stock
    else if (yes === 0)  cls = "badge-status-idle";    // none
    else                 cls = "badge-status-paused";  // partial (highlight)
    return `<span class="badge ${cls}" title="${yes} of ${total} tickets in stock">${yes}/${total} in stock</span>`;
  }
  if (c.has_game === true)  return `<span class="badge badge-green">1/1 in stock</span>`;
  if (c.has_game === false) return `<span class="badge badge-status-idle">0/1 in stock</span>`;
  return `<span class="badge badge-status-idle">—</span>`;
}

function renderResultDetail(c) {
  // Per-ticket pills for the detail modal — always shows the breakdown.
  if (!Array.isArray(c.per_ticket_results) || !c.per_ticket_results.length) {
    return renderResultCell(c);
  }
  const pills = c.per_ticket_results.map(t => {
    const name = (t && t.name) ? String(t.name) : "?";
    const has = t && t.has_game;
    const cls = has === true ? "yes" : has === false ? "no" : "unk";
    const mark = has === true ? "✓ Has" : has === false ? "✗ Out" : "— Unknown";
    const conf = t && t.confidence != null ? ` ${Math.round(parseFloat(t.confidence) * 100)}%` : "";
    const titleParts = [name];
    if (t && t.notes) titleParts.push(String(t.notes));
    return `<span class="cf-ticket-pill ${cls}" title="${escHtml(titleParts.join(' — '))}"><span class="cf-pill-name">${escHtml(name)}</span><span class="cf-pill-mark">${mark}${conf}</span></span>`;
  }).join("");
  return `<div class="cf-ticket-result">${pills}</div>`;
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
          Max ${c.max_stores} stores ·
          ${c.call_backend === "twilio_ivr" ? "Twilio IVR" : "Bland AI"}
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
      ? timeAgo(parseReportedAt(h.called_at))
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

// ── Store picker ──────────────────────────────────────────────────────────────
let _storeCandidates = [];        // [{external_id, name, city, phone, score, latitude, longitude, last_called_at, last_talked, called_within_window, inventory_updated}, ...]
let _selectedStores  = new Set(); // set of external_id
let _storesView      = "list";    // "list" | "map"
let _storesMap       = null;      // Leaflet map instance (lazy)
let _storesCluster   = null;      // L.markerClusterGroup
let _storesMarkers   = new Map(); // external_id → L.Marker
let _showSelectedOnly = false;    // when true, list+map show only checked stores

function _updateMapCoverageNote(shown, totalFiltered, missingCoords) {
  const countEl = document.getElementById("cfStoresCount");
  if (!countEl) return;
  // Append to whatever updateStoresCount sets so it doesn't clobber.
  const sel = _selectedStores.size;
  const base = sel === 0 ? "No stores selected" : `${sel} store${sel === 1 ? "" : "s"} selected`;
  const cov = missingCoords > 0
    ? ` · map: ${shown.toLocaleString()} of ${totalFiltered.toLocaleString()} (${missingCoords.toLocaleString()} missing coords)`
    : ` · map: ${shown.toLocaleString()}`;
  countEl.textContent = base + cov;
}

function toggleShowSelectedOnly() {
  _showSelectedOnly = !_showSelectedOnly;
  const btn = document.getElementById("cfShowSelectedBtn");
  if (btn) {
    btn.textContent = _showSelectedOnly ? "Show all" : "Show selected";
    btn.classList.toggle("cf-view-active", _showSelectedOnly);
  }
  renderStoresPicker();
  if (_storesView === "map" && _storesMap) renderStoresMap();
}

async function loadStoreCandidates() {
  const state = document.getElementById("cfStateSelect").value;
  const listEl = document.getElementById("cfStoresList");
  const countEl = document.getElementById("cfStoresCount");
  if (!state) {
    _storeCandidates = [];
    _selectedStores = new Set();
    if (listEl) listEl.innerHTML = `<div class="cf-tickets-empty">— Pick a state first —</div>`;
    if (countEl) countEl.textContent = "No stores selected";
    return;
  }
  if (listEl) listEl.innerHTML = `<div class="cf-tickets-empty">Loading stores…</div>`;
  const cooldownDays = parseInt(document.getElementById("cfCooldownDays").value);
  const cooldownHrs = (isNaN(cooldownDays) ? 7 : cooldownDays) * 24;
  try {
    const res = await callerFetch(`/api/vapi/candidates?state=${encodeURIComponent(state)}&cooldown_hours=${cooldownHrs}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _storeCandidates = data.candidates || [];
    _selectedStores  = new Set();
    // Start from zero — user adds via list checkboxes, map clicks, or "Select top N".
    renderStoresPicker();
    // If map was already initialized (e.g. user switches state while in map view),
    // rebuild markers for the new state. Otherwise it builds on first switch.
    if (_storesView === "map" && _storesMap) renderStoresMap();
  } catch (e) {
    if (listEl) listEl.innerHTML = `<div class="cf-tickets-empty" style="color:var(--danger)">Failed to load stores: ${escHtml(e.message)}</div>`;
  }
}

function autoSelectStores() {
  // On fresh state load, default-select top 100 (subject to skip toggle).
  const skipCalled = document.getElementById("cfSkipCalled")?.checked !== false;
  const topN = parseInt(document.getElementById("cfSelectTopN")?.value) || 100;
  _selectedStores = new Set();
  let picked = 0;
  for (const c of _storeCandidates) {
    if (picked >= topN) break;
    if (skipCalled && (c.last_called_at || c.called_within_window)) continue;
    _selectedStores.add(c.external_id);
    picked++;
  }
}

function selectTopNStores() {
  if (!_storeCandidates.length) return;
  autoSelectStores();
  refreshStoresViews();
}

function selectNoStores() {
  _selectedStores = new Set();
  refreshStoresViews();
}

function onSkipCalledToggle() {
  // Toggle is cosmetic + affects bulk "Select top N"; doesn't touch manual picks.
  refreshStoresViews();
}

function refreshStoresViews() {
  renderStoresPicker();
  // Refresh marker icons in place so map reflects selection changes.
  if (_storesMap && _storesMarkers.size) {
    _storeCandidates.forEach(c => {
      const m = _storesMarkers.get(c.external_id);
      if (m) m.setIcon(_storeMarkerIcon(c));
    });
  }
}

function onCooldownChange() {
  // Cooldown defines the "within window" badge — re-fetch annotations.
  if (document.getElementById("cfStateSelect").value) loadStoreCandidates();
}

function toggleStore(input) {
  const id = input.dataset.id;
  if (input.checked) _selectedStores.add(id);
  else _selectedStores.delete(id);
  updateStoresCount();
}

function updateStoresCount() {
  const countEl = document.getElementById("cfStoresCount");
  if (!countEl) return;
  const n = _selectedStores.size;
  countEl.textContent = n === 0 ? "No stores selected" : `${n} store${n === 1 ? "" : "s"} selected`;
}

function renderStoresPicker() {
  const listEl = document.getElementById("cfStoresList");
  if (!listEl) return;
  if (!_storeCandidates.length) {
    listEl.innerHTML = `<div class="cf-tickets-empty">No callable stores for this state.</div>`;
    updateStoresCount();
    return;
  }
  const skipCalled = document.getElementById("cfSkipCalled")?.checked !== false;
  const search = (document.getElementById("cfStoresSearch")?.value || "").trim().toLowerCase();
  const cooldownDays = parseInt(document.getElementById("cfCooldownDays").value) || 7;

  let rows = _storeCandidates;
  if (_showSelectedOnly) {
    rows = rows.filter(c => _selectedStores.has(c.external_id));
  }
  if (search) {
    rows = rows.filter(c => (c.name || "").toLowerCase().includes(search) || (c.city || "").toLowerCase().includes(search));
  }
  // Sink already-called rows when the skip toggle is on (still visible, just last).
  if (skipCalled && !_showSelectedOnly) {
    const fresh = rows.filter(c => !c.last_called_at && !c.called_within_window);
    const called = rows.filter(c =>  c.last_called_at ||  c.called_within_window);
    rows = [...fresh, ...called];
  }

  if (!rows.length) {
    listEl.innerHTML = `<div class="cf-tickets-empty">No stores match.</div>`;
    updateStoresCount();
    return;
  }

  listEl.innerHTML = rows.map(c => {
    const checked = _selectedStores.has(c.external_id) ? "checked" : "";
    const scoreBadge = c.score != null
      ? `<span class="badge" style="background:rgba(99,102,241,0.12);color:#4338ca;font-size:.7rem">${Math.round(c.score)}</span>`
      : "";
    const calledEverBadge = c.last_called_at
      ? `<span class="badge" style="background:rgba(245,158,11,0.15);color:#92400e;font-size:.7rem" title="Last AI-called ${c.last_called_at.slice(0,10)}${c.last_talked ? ' · had real conversation' : ''}">Called ${_relativeDays(c.last_called_at)}</span>`
      : `<span class="badge" style="background:rgba(148,163,184,0.18);color:#475569;font-size:.7rem">Never called</span>`;
    const inWindowBadge = c.called_within_window
      ? `<span class="badge" style="background:rgba(239,68,68,0.12);color:#991b1b;font-size:.7rem" title="AI-called within the ${cooldownDays}-day recall window">Within ${cooldownDays}d</span>`
      : "";
    const invBadge = c.inventory_updated
      ? `<span class="badge" style="background:rgba(34,197,94,0.15);color:#166534;font-size:.7rem" title="A prior VAPI call wrote inventory_reports for this store">Inventory ✓</span>`
      : "";
    const phoneShort = c.phone ? String(c.phone).replace(/[^0-9]/g, "").slice(-10).replace(/(\d{3})(\d{3})(\d{4})/, "$1-$2-$3") : "—";
    return `<label class="cf-ticket-row" style="display:grid;grid-template-columns:auto 1fr auto;gap:.55rem;align-items:center;padding:.35rem .55rem">
      <input type="checkbox" data-id="${escHtml(c.external_id)}" ${checked} onchange="toggleStore(this)" />
      <div style="min-width:0">
        <div style="font-weight:600;font-size:.84rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escHtml(c.name || "(unnamed)")}</div>
        <div style="font-size:.72rem;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escHtml(c.city || "—")} · ${phoneShort}</div>
      </div>
      <div style="display:flex;gap:.3rem;align-items:center;flex-wrap:wrap;justify-content:flex-end">
        ${scoreBadge} ${calledEverBadge} ${inWindowBadge} ${invBadge}
      </div>
    </label>`;
  }).join("");
  updateStoresCount();
}

function _relativeDays(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (isNaN(then)) return iso.slice(0, 10);
  const days = Math.floor((Date.now() - then) / (1000 * 60 * 60 * 24));
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

// ── Stores map view ──────────────────────────────────────────────────────────
function setStoresView(mode) {
  _storesView = mode;
  const listEl = document.getElementById("cfStoresList");
  const mapEl  = document.getElementById("cfStoresMap");
  const legendEl = document.getElementById("cfStoresMapLegend");
  const listBtn = document.getElementById("cfViewListBtn");
  const mapBtn  = document.getElementById("cfViewMapBtn");
  const regionBtn = document.getElementById("cfRegionSelectBtn");
  if (mode === "map") {
    listEl.style.display = "none";
    mapEl.style.display  = "block";
    if (legendEl) legendEl.style.display = "flex";
    if (regionBtn) regionBtn.style.display = "";
    listBtn.classList.remove("cf-view-active");
    mapBtn.classList.add("cf-view-active");
    renderStoresMap();
  } else {
    listEl.style.display = "";
    mapEl.style.display  = "none";
    if (legendEl) legendEl.style.display = "none";
    if (regionBtn) regionBtn.style.display = "none";
    if (_regionSelectActive) toggleRegionSelect();
    mapBtn.classList.remove("cf-view-active");
    listBtn.classList.add("cf-view-active");
  }
}

// ── Region select on the stores map ──────────────────────────────────────────
let _regionSelectActive = false;
let _regionRect = null;
let _regionStart = null;

function toggleRegionSelect() {
  if (!_storesMap) return;
  _regionSelectActive = !_regionSelectActive;
  const btn = document.getElementById("cfRegionSelectBtn");
  const mapEl = document.getElementById("cfStoresMap");
  if (_regionSelectActive) {
    if (btn) { btn.textContent = "Cancel region"; btn.classList.add("cf-view-active"); }
    _storesMap.dragging.disable();
    _storesMap.boxZoom.disable();
    _storesMap.doubleClickZoom.disable();
    let overlay = document.getElementById("cfRegionOverlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "cfRegionOverlay";
      overlay.style.cssText = "position:absolute;inset:0;cursor:crosshair;z-index:900;background:transparent";
      mapEl.appendChild(overlay);
    }
    overlay.style.display = "";
    overlay.addEventListener("mousedown", _regionMouseDown);
  } else {
    if (btn) { btn.textContent = "Select region"; btn.classList.remove("cf-view-active"); }
    _storesMap.dragging.enable();
    _storesMap.boxZoom.enable();
    _storesMap.doubleClickZoom.enable();
    const overlay = document.getElementById("cfRegionOverlay");
    if (overlay) {
      overlay.removeEventListener("mousedown", _regionMouseDown);
      overlay.remove();
    }
    document.removeEventListener("mousemove", _regionMouseMove);
    document.removeEventListener("mouseup", _regionMouseUp);
    if (_regionRect) { _storesMap.removeLayer(_regionRect); _regionRect = null; }
    _regionStart = null;
  }
}

function _regionPointToLatLng(clientX, clientY) {
  const mapEl = document.getElementById("cfStoresMap");
  const rect = mapEl.getBoundingClientRect();
  return _storesMap.containerPointToLatLng([clientX - rect.left, clientY - rect.top]);
}

function _regionMouseDown(e) {
  e.preventDefault();
  _regionStart = _regionPointToLatLng(e.clientX, e.clientY);
  if (_regionRect) _storesMap.removeLayer(_regionRect);
  _regionRect = L.rectangle([_regionStart, _regionStart], {
    color: "#22c55e", weight: 2, fillColor: "#22c55e", fillOpacity: 0.12, interactive: false,
  }).addTo(_storesMap);
  document.addEventListener("mousemove", _regionMouseMove);
  document.addEventListener("mouseup", _regionMouseUp);
}

function _regionMouseMove(e) {
  if (!_regionStart || !_regionRect) return;
  _regionRect.setBounds([_regionStart, _regionPointToLatLng(e.clientX, e.clientY)]);
}

function _regionMouseUp(e) {
  document.removeEventListener("mousemove", _regionMouseMove);
  document.removeEventListener("mouseup", _regionMouseUp);
  if (!_regionStart) { if (_regionSelectActive) toggleRegionSelect(); return; }
  const end = _regionPointToLatLng(e.clientX, e.clientY);
  const bounds = L.latLngBounds(_regionStart, end);
  let added = 0;
  _storeCandidates.forEach(c => {
    const lat = parseFloat(c.latitude);
    const lng = parseFloat(c.longitude);
    if (!isFinite(lat) || !isFinite(lng)) return;
    if (!bounds.contains([lat, lng])) return;
    if (_selectedStores.has(c.external_id)) return;
    _selectedStores.add(c.external_id);
    added++;
    const m = _storesMarkers.get(c.external_id);
    if (m) m.setIcon(_storeMarkerIcon(c));
  });
  _regionStart = null;
  if (_regionRect) { _storesMap.removeLayer(_regionRect); _regionRect = null; }
  toggleRegionSelect(); // exit region mode
  updateStoresCount();
  if (_storesView === "list") renderStoresPicker();
  if (added > 0 && typeof showToast === "function") showToast(`Added ${added} store${added === 1 ? "" : "s"} to call list`);
}

function _storeMarkerColor(c) {
  if (_selectedStores.has(c.external_id)) return "#22c55e"; // green
  if (c.called_within_window)              return "#ef4444"; // red
  if (c.last_called_at)                    return "#f59e0b"; // amber
  if (c.inventory_updated)                 return "#6366f1"; // indigo
  return "#94a3b8";                                          // slate
}

function _storeMarkerIcon(c) {
  const color = _storeMarkerColor(c);
  const selectedCls = _selectedStores.has(c.external_id) ? "selected" : "";
  return L.divIcon({
    className: "",
    iconSize: [16, 16],
    iconAnchor: [8, 8],
    html: `<div class="cf-store-marker ${selectedCls}" style="width:16px;height:16px;background:${color}"></div>`,
  });
}

function renderStoresMap() {
  const mapEl = document.getElementById("cfStoresMap");
  if (!mapEl) return;
  if (!_storesMap) {
    _storesMap = L.map(mapEl, { preferCanvas: true }).setView([39.5, -96.0], 4);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    }).addTo(_storesMap);
    _storesCluster = L.markerClusterGroup({
      chunkedLoading: true,
      spiderfyOnMaxZoom: true,
      showCoverageOnHover: false,
    });
    _storesMap.addLayer(_storesCluster);
  } else {
    _storesCluster.clearLayers();
    _storesMarkers.clear();
  }

  const search = (document.getElementById("cfStoresSearch")?.value || "").trim().toLowerCase();
  const matchesFilters = c =>
    (!_showSelectedOnly || _selectedStores.has(c.external_id)) &&
    (!search || (c.name || "").toLowerCase().includes(search) || (c.city || "").toLowerCase().includes(search));
  const filtered = _storeCandidates.filter(matchesFilters);
  const withCoords = filtered.filter(c => c.latitude != null && c.longitude != null);
  const missingCoords = filtered.length - withCoords.length;
  _updateMapCoverageNote(withCoords.length, filtered.length, missingCoords);

  if (!withCoords.length) {
    setTimeout(() => _storesMap.invalidateSize(), 50);
    return;
  }

  const bounds = [];
  withCoords.forEach(c => {
    const lat = parseFloat(c.latitude);
    const lng = parseFloat(c.longitude);
    if (!isFinite(lat) || !isFinite(lng)) return;
    bounds.push([lat, lng]);
    const m = L.marker([lat, lng], { icon: _storeMarkerIcon(c) });
    const phoneShort = c.phone ? String(c.phone).replace(/[^0-9]/g, "").slice(-10).replace(/(\d{3})(\d{3})(\d{4})/, "$1-$2-$3") : "—";
    const calledLine = c.last_called_at
      ? `Called ${_relativeDays(c.last_called_at)}${c.called_within_window ? ' (within window)' : ''}`
      : 'Never called';
    const invLine = c.inventory_updated ? '<br>Inventory ✓ updated previously' : '';
    const scoreLine = c.score != null ? `<br>Score ${Math.round(c.score)}` : '';
    m.bindPopup(
      `<div style="font-size:.85rem">
         <div style="font-weight:700">${escHtml(c.name || '(unnamed)')}</div>
         <div style="color:#666;font-size:.78rem">${escHtml(c.city || '—')} · ${phoneShort}${scoreLine}</div>
         <div style="font-size:.78rem;margin-top:.3rem">${calledLine}${invLine}</div>
         <div style="margin-top:.45rem">
           <button onclick="toggleStoreById('${escHtml(c.external_id)}')" style="font-size:.78rem;padding:.25rem .55rem;border:1px solid #ccc;border-radius:4px;cursor:pointer;background:#fff">
             ${_selectedStores.has(c.external_id) ? 'Remove from call list' : 'Add to call list'}
           </button>
         </div>
       </div>`
    );
    m.on("click", () => toggleStoreById(c.external_id));
    _storesCluster.addLayer(m);
    _storesMarkers.set(c.external_id, m);
  });

  if (bounds.length) {
    try { _storesMap.fitBounds(bounds, { padding: [20, 20], maxZoom: 12 }); } catch (_) {}
  }
  setTimeout(() => _storesMap && _storesMap.invalidateSize(), 50);
}

function toggleStoreById(id) {
  if (_selectedStores.has(id)) _selectedStores.delete(id);
  else _selectedStores.add(id);
  // Refresh just the affected marker icon.
  const m = _storesMarkers.get(id);
  const c = _storeCandidates.find(x => x.external_id === id);
  if (m && c) {
    m.setIcon(_storeMarkerIcon(c));
    // Refresh open popup label if open.
    if (m.getPopup() && m.isPopupOpen()) {
      const popup = m.getPopup();
      const html = popup.getContent().replace(
        /(Remove from call list|Add to call list)/,
        _selectedStores.has(id) ? 'Remove from call list' : 'Add to call list'
      );
      popup.setContent(html);
    }
  }
  updateStoresCount();
  // Keep list in sync if visible later.
  if (_storesView === "list") renderStoresPicker();
}

async function dispatchSelectedStores(dryRun) {
  const state    = document.getElementById("cfStateSelect").value;
  const tickets  = getSelectedTickets();
  const btn      = dryRun ? document.getElementById("cfDryRunBtn") : document.getElementById("cfCreateBtn");
  const origLabel = btn.textContent;

  if (!state)            { showCallerMsg("Select a state first.", "err"); return; }
  if (!tickets.length)   { showCallerMsg("Pick at least one ticket.", "err"); return; }
  if (!_selectedStores.size) { showCallerMsg("Pick at least one store to call.", "err"); return; }

  // Preserve the on-screen order of _storeCandidates (already score-sorted by backend).
  const orderedIds = _storeCandidates
    .filter(c => _selectedStores.has(c.external_id))
    .map(c => c.external_id);

  const ticketsLabel = tickets.map(t => `${t.name}${t.price != null ? ` ($${t.price})` : ""}`).join(", ");
  if (!dryRun && !confirm(`Dispatch ${orderedIds.length} VAPI calls in ${state} asking about:\n\n${ticketsLabel}`)) return;

  btn.disabled = true;
  btn.textContent = dryRun ? "Previewing…" : "Dispatching…";
  showCallerMsg("", "");

  try {
    const res = await callerFetch("/api/vapi/dispatch_selected", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        state,
        tickets,
        selected_external_ids: orderedIds,
        dry_run: !!dryRun,
      }),
    });
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error(_formatApiError(data, res.status));
    if (dryRun) {
      const preview = (data.preview || []).slice(0, 5)
        .map(p => `• ${escHtml(p.name)} (${escHtml(p.city || '—')})`)
        .join("<br>");
      showCallerMsg(
        `Preview — would call <strong>${data.would_call}</strong> of ${data.selected} selected stores in ${state}.` +
        (data.missing_ids && data.missing_ids.length ? ` ⚠ ${data.missing_ids.length} stale ID(s).` : "") +
        (preview ? `<br><br>${preview}` : ""),
        "ok"
      );
    } else {
      showCallerMsg(
        `Dispatched <strong>${data.dispatched}</strong> calls · ` +
        `<strong>${data.failed || 0}</strong> failed` +
        (data.skipped && data.skipped.length ? ` · <strong>${data.skipped.length}</strong> with bad phone` : "") +
        ". Watch the table below for results as VAPI completes calls.",
        "ok"
      );
      await loadCallerData();
      // Refresh candidates so newly-dispatched stores get their "Called" badge.
      await loadStoreCandidates();
    }
  } catch (e) {
    showCallerMsg(e.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = origLabel;
  }
}

function _formatApiError(data, status) {
  if (!data) return `Server error (${status})`;
  const d = data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d.map(e => {
      const loc = Array.isArray(e.loc) ? e.loc.filter(x => x !== "body").join(".") : "";
      return loc ? `${loc}: ${e.msg || JSON.stringify(e)}` : (e.msg || JSON.stringify(e));
    }).join("; ");
  }
  if (d && typeof d === "object") return JSON.stringify(d);
  return data.message || `Server error (${status})`;
}

async function sendTestCall() {
  const phone   = document.getElementById("cfTestPhone").value.trim();
  const tickets = getSelectedTickets();
  const btn     = document.getElementById("cfTestBtn");

  if (!phone)          { showCallerMsg("Enter a phone number for the test call.", "err"); return; }
  if (!tickets.length) { showCallerMsg("Pick at least one ticket above first.", "err"); return; }

  btn.disabled = true;
  btn.textContent = "Calling…";
  showCallerMsg("", "");

  const asRetailerVal = document.getElementById("cfTestAsRetailer")?.value || "";
  const asRetailerId  = asRetailerVal ? parseInt(asRetailerVal) : null;

  try {
    const res  = await callerFetch("/api/vapi/test_call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone,
        tickets,
        as_retailer_id: asRetailerId,
      }),
    });
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error(_formatApiError(data, res.status));
    const asLabel = data.simulated_store && data.simulated_store !== "Test Call"
      ? ` (assistant will think it's calling <strong>${escHtml(data.simulated_store)}${data.simulated_city ? ' in ' + escHtml(data.simulated_city) : ''}</strong>)`
      : "";
    showCallerMsg(
      `Test call placed${asLabel} — your phone should ring shortly. VAPI call id: <code>${escHtml(data.call_id || '—')}</code>`,
      "ok"
    );
    setTimeout(loadCallerData, 1500);
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
  el.innerHTML = text;
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
  _detailMap = L.map("detailMapContainer", { preferCanvas: true }).setView([42.3, -71.8], 8);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
    maxZoom: 18,
  }).addTo(_detailMap);
  setupMapAutoResize(_detailMap);

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
    _detailMap = L.map("detailMapContainer", { preferCanvas: true }).setView([42.3, -71.8], 8);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap contributors", maxZoom: 18,
    }).addTo(_detailMap);
    setupMapAutoResize(_detailMap);
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
    ? timeAgo(parseReportedAt(r.called_at))
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
  const matches = allGamesUnfiltered.filter(g => g.name.toLowerCase().includes(q)).slice(0, 8);
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
      const stateEndpoints = { MA: '/api/ma/retailers', AZ: '/api/az/retailers', RI: '/api/ri/retailers', FL: '/api/fl/retailers', GA: '/api/ga/retailers', NY: '/api/ny/retailers' };
      const endpoint = stateEndpoints[currentHuntState] || '/api/ma/retailers';
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
  communityReportsLastFetch = Date.now();
  try {
    const res  = await protectedFetch("/api/inventory/reports?limit=500");
    if (!res.ok) return;
    const data = await res.json();
    communityReports = data.reports || [];
    // Build local status immediately for rendering, then refresh from full DB
    buildLatestStatusFromReports();
    const activeGame = currentHuntState === 'AZ' ? selectedAzGame
      : currentHuntState === 'RI' ? selectedRiGame
      : currentHuntState === 'FL' ? selectedFlGame
      : currentHuntState === 'GA' ? selectedGaGame
      : currentHuntState === 'NY' ? selectedNyGame
      : currentHuntState === 'VA' ? selectedVaGame
      : currentHuntState === 'DC' ? selectedDcGame
      : currentHuntState === 'VT' ? selectedVtGame
      : (typeof GEN_STATES !== 'undefined' && GEN_STATES[currentHuntState]) ? selectedGenGame
      : selectedGame;
    loadRetailerLatest(activeGame?.name);
    updateReportBadges();
    // If the user has an inventory panel open, avoid the full table re-renders —
    // they reset _openProfileId and tear down the panel mid-edit. Cell-level
    // updates + a profile refresh keep the page stable.
    if (_openProfileId) {
      updateLastReportCells();
      refreshOpenProfile();
    } else {
      renderMaTable();
      renderAzTable();
      renderRiTable();
      renderFlTable();
      renderGaTable();
      renderNyTable();
      renderVaTable();
      renderDcTable();
      renderVtTable();
      if (typeof renderGenTable === "function" && currentGenState) renderGenTable();
    }
    refreshOpenModalCommunity();
    if (currentHuntState === 'AZ') updateAzInventoryMapLayer();
    else if (currentHuntState === 'RI') updateRiInventoryMapLayer();
    else if (currentHuntState === 'FL') updateFlInventoryMapLayer();
    else if (currentHuntState === 'GA') updateGaInventoryMapLayer();
    else if (currentHuntState === 'NY') updateNyInventoryMapLayer();
    else if (currentHuntState === 'VA') updateVaInventoryMapLayer();
    else if (currentHuntState === 'DC') updateDcInventoryMapLayer();
    else if (currentHuntState === 'VT') updateVtInventoryMapLayer();
    else if (typeof GEN_STATES !== "undefined" && GEN_STATES[currentHuntState]) updateGenInventoryMapLayer();
    else updateInventoryMapLayer();
  } catch (_) {}
}

// ── Store profile (inline expand) ─────────────────────────────────────────────

function goToStoreFromModal(retailerId) {
  closeModal();
  const tr = document.querySelector(`tr[data-retailer-id="${CSS.escape(String(retailerId))}"]`);
  if (tr) {
    tr.scrollIntoView({ behavior: 'smooth', block: 'center' });
    toggleStoreProfile(tr);
  }
}

function openStoreInventoryFromMap(retailerId) {
  let tr = document.querySelector(`tr[data-retailer-id="${CSS.escape(String(retailerId))}"]`);
  if (!tr) {
    // Row hasn't been lazy-rendered yet — flush the state's table so it exists in the DOM.
    const state = getRetailerState(retailerId);
    const tbodyId = state ? `${state.toLowerCase()}TableBody` : null;
    const tbody = tbodyId ? document.getElementById(tbodyId) : null;
    if (tbody && typeof tbody._lazyFlush === 'function') tbody._lazyFlush();
    tr = document.querySelector(`tr[data-retailer-id="${CSS.escape(String(retailerId))}"]`);
    if (!tr) return;
  }
  if (_openProfileId !== String(retailerId)) toggleStoreProfile(tr);
  tr.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

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

// ── Per-retailer inventory helpers ────────────────────────────────────────────

function getRetailerState(retailerId) {
  if (allRetailers.some(r => String(r.id) === String(retailerId))) return 'MA';
  if (allAzRetailers.some(r => String(r.id) === String(retailerId))) return 'AZ';
  if (allRiRetailers.some(r => String(r.id) === String(retailerId))) return 'RI';
  if (allFlRetailers.some(r => String(r.id) === String(retailerId))) return 'FL';
  if (allGaRetailers.some(r => String(r.id) === String(retailerId))) return 'GA';
  if (allNyRetailers.some(r => String(r.id) === String(retailerId))) return 'NY';
  if (allVaRetailers.some(r => String(r.id) === String(retailerId))) return 'VA';
  if (allDcRetailers.some(r => String(r.id) === String(retailerId))) return 'DC';
  if (allVtRetailers.some(r => String(r.id) === String(retailerId))) return 'VT';
  if (typeof GEN_STATES !== "undefined") {
    for (const code of Object.keys(GEN_STATES)) {
      const arr = allGenRetailers[code];
      if (arr && arr.some(r => String(r.id) === String(retailerId))) return code;
    }
  }
  return null;
}

function getGamesForRetailer(retailerId) {
  const state = getRetailerState(retailerId);
  if (state === 'MA') return maGames;
  if (state === 'AZ') return azGames;
  if (state === 'RI') return riGames;
  if (state === 'FL') return flGames;
  if (state === 'GA') return gaGames;
  if (state === 'NY') return nyGames;
  if (state === 'VA') return vaGames;
  if (state === 'DC') return dcGames;
  if (state === 'VT') return vtGames;
  if (state && typeof GEN_STATES !== "undefined" && GEN_STATES[state]) {
    return genGames[state] || [];
  }
  return [];
}

function getPerGameStatuses(retailerId) {
  const result = {};
  const sorted = [...communityReports]
    .filter(r => r.retailer_id === retailerId)
    .sort((a, b) => parseReportedAt(b.reported_at) - parseReportedAt(a.reported_at));
  for (const r of sorted) {
    const key = (r.game_name || '').toLowerCase();
    if (!result[key]) {
      result[key] = {
        has_stock: r.has_stock,
        reported_at: r.reported_at,
        reporter_username: r.reporter_username,
        notes: r.notes,
        game_name: r.game_name,
      };
    }
  }
  return result;
}

function _findRetailerAcrossStates(retailerId) {
  const buckets = [
    [allRetailers, 'MA'], [allAzRetailers, 'AZ'], [allRiRetailers, 'RI'],
    [allFlRetailers, 'FL'], [allGaRetailers, 'GA'], [allNyRetailers, 'NY'],
    [allVaRetailers, 'VA'], [allDcRetailers, 'DC'], [allVtRetailers, 'VT'],
  ];
  if (typeof GEN_STATES !== "undefined") {
    for (const code of Object.keys(GEN_STATES)) {
      const arr = allGenRetailers[code];
      if (arr) buckets.push([arr, code]);
    }
  }
  for (const [arr, code] of buckets) {
    if (!arr) continue;
    const r = arr.find(x => String(x.id) === String(retailerId));
    if (r) return { retailer: r, state_code: code };
  }
  return { retailer: null, state_code: getRetailerState(retailerId) };
}

function _claimUrl(retailerId) {
  const { retailer, state_code } = _findRetailerAcrossStates(retailerId);
  const qs = new URLSearchParams({
    retailer_id: String(retailerId),
    state_code:  state_code || '',
    store_name:  retailer?.name || '',
    city:        retailer?.city || '',
    zip:         retailer?.zip_code || retailer?.zip || '',
    phone:       retailer?.phone || '',
  });
  return `/claim?${qs.toString()}`;
}

function storeProfileHtml(retailerId) {
  const games = getGamesForRetailer(retailerId);
  const perGameStatuses = getPerGameStatuses(retailerId);

  const inCount  = Object.values(perGameStatuses).filter(s => s.has_stock === true).length;
  const outCount = Object.values(perGameStatuses).filter(s => s.has_stock === false).length;

  const loginBanner = !_currentUser
    ? `<div class="inv-login-banner">
        <span>🔒 Log in to see reports and update inventory</span>
        <button class="btn btn-login" style="font-size:.74rem;padding:.22rem .6rem" onclick="openAuthModal('login')">Log In</button>
        <button class="btn" style="font-size:.74rem;padding:.22rem .6rem" onclick="openAuthModal('register')">Join Free</button>
      </div>`
    : '';

  const rid = escHtml(retailerId);

  // Async-loaded owner info (fresh-pack banner, hours, description, etc.).
  // Filled by loadOwnerProfile(rid) after the panel is rendered.
  const ownerMount = `<div class="store-owner-mount" data-rid="${rid}"></div>`;

  // "Claim this store" CTA — visible to everyone; the click target itself
  // gates on auth. Cheap, high-value for owners discovering the product.
  const claimCta = `<div class="store-claim-cta">
    <span class="store-claim-cta-icon">🏪</span>
    <div class="store-claim-cta-body">
      <div class="store-claim-cta-title">Own this store?</div>
      <div class="store-claim-cta-sub">Claim your free dashboard to manage inventory, post promotions, and drive foot traffic.</div>
    </div>
    <a class="btn btn-claim" href="${_claimUrl(retailerId)}">Claim this store</a>
  </div>`;

  const gameRows = games.map(g => {
    const key = (g.name || '').toLowerCase();
    const status = perGameStatuses[key] ?? null;
    const hs = status?.has_stock ?? null;
    const accentClass = hs === true ? 'acc-in' : hs === false ? 'acc-out' : 'acc-none';
    const inClass  = hs === true  ? ' is-in'  : '';
    const outClass = hs === false ? ' is-out' : '';
    const filterVal = hs === true ? 'in' : hs === false ? 'out' : 'not_set';
    const gName  = escHtml(g.name);
    const gPrice = g.price != null ? g.price : '';
    const meta   = `$${g.price ?? '?'} · ${g.return_pct != null ? g.return_pct.toFixed(1) + '% EV' : '—'}`;
    const updLine = status
      ? `<div class="inv-upd">Updated ${timeAgo(parseReportedAt(status.reported_at))}${status.reporter_username ? ` · @${escHtml(status.reporter_username)}` : ''}${status.notes ? ` · <em>${escHtml(status.notes)}</em>` : ''}</div>`
      : '';
    return `<div class="inv-game-row" data-game-status="${filterVal}">
      <div class="inv-accent ${accentClass}"></div>
      <div class="inv-game-body">
        <div class="inv-game-top">
          <div class="inv-game-info">
            <span class="inv-game-name">${escHtml(g.name)}</span>
            <span class="inv-game-meta">${escHtml(meta)}</span>
          </div>
          <div class="inv-btn-grp">
            <button class="inv-btn${inClass}" data-rid="${rid}" data-game="${gName}" data-price="${gPrice}" data-stock="true"  onclick="toggleGameInvBtn(this)">In Stock</button>
            <button class="inv-btn${outClass}" data-rid="${rid}" data-game="${gName}" data-price="${gPrice}" data-stock="false" onclick="toggleGameInvBtn(this)">Out</button>
            <button class="inv-btn"            data-rid="${rid}" data-game="${gName}" data-price="${gPrice}"                   onclick="openGameNotesBtn(this)">Notes</button>
          </div>
        </div>
        ${updLine}
      </div>
    </div>`;
  }).join('');

  const summaryHtml = (inCount || outCount)
    ? `<span style="color:var(--mint)">●</span> ${inCount} in &ensp;<span style="color:var(--red)">●</span> ${outCount} out`
    : `No reports yet`;

  // Defer the owner-profile fetch so the panel paints instantly.
  setTimeout(() => loadOwnerProfile(retailerId), 0);

  return `<div class="inv-panel">
    ${ownerMount}
    <div class="inv-panel-hd">
      <span class="inv-panel-title">Update Inventory</span>
      <span class="inv-summary">${summaryHtml}</span>
    </div>
    ${loginBanner}
    <div class="inv-filter-row">
      <button class="inv-filter-tab active" onclick="setInvPanelFilter(this,'all')">All</button>
      <button class="inv-filter-tab" onclick="setInvPanelFilter(this,'in')">In Stock</button>
      <button class="inv-filter-tab" onclick="setInvPanelFilter(this,'out')">Out of Stock</button>
      <button class="inv-filter-tab" onclick="setInvPanelFilter(this,'not_set')">Not Set</button>
    </div>
    <div class="inv-game-list">
      ${games.length ? gameRows : '<div class="inv-no-games">No games tracked for this state yet.</div>'}
    </div>
    ${claimCta}
  </div>`;
}

async function loadOwnerProfile(retailerId) {
  const mount = document.querySelector(`.store-owner-mount[data-rid="${CSS.escape(String(retailerId))}"]`);
  if (!mount || mount.dataset.loaded === '1') return;
  mount.dataset.loaded = '1';

  let profile = null;
  let posts = [];
  try {
    const [pRes, postsRes] = await Promise.all([
      fetch(`/api/public/retailer/${encodeURIComponent(retailerId)}/profile`),
      fetch(`/api/public/retailer/${encodeURIComponent(retailerId)}/posts?limit=3`),
    ]);
    if (pRes.ok) profile = (await pRes.json()).profile;
    if (postsRes.ok) posts = (await postsRes.json()).posts || [];
  } catch (_) { return; }

  if (!profile && !posts.length) {
    // Hide the empty CTA — if there's neither, we hide the claim block too
    // by hiding the parent CTA section.
    mount.remove();
    return;
  }

  const verifiedBadge = profile?.verified
    ? `<span class="store-verified-pill">✓ Verified by owner</span>` : '';
  const bannerHtml = profile?.banner_text
    ? `<div class="store-fresh-banner">
         <span class="store-fresh-banner-tag">FRESH</span>
         <span>${escHtml(profile.banner_text)}</span>
       </div>` : '';
  const metaBits = [];
  if (profile?.hours_text) metaBits.push(`<span>🕐 ${escHtml(profile.hours_text)}</span>`);
  if (profile?.phone)      metaBits.push(`<span>📞 ${escHtml(profile.phone)}</span>`);
  if (profile?.website)    metaBits.push(`<a href="${escHtml(profile.website)}" target="_blank" rel="noopener">🌐 Visit website</a>`);
  const metaHtml = metaBits.length
    ? `<div class="store-owner-meta">${metaBits.join('')}</div>` : '';
  const descHtml = profile?.description
    ? `<div class="store-owner-desc">${escHtml(profile.description)}</div>` : '';
  const photoHtml = profile?.photo_url
    ? `<img class="store-owner-photo" src="${escHtml(profile.photo_url)}" alt="" loading="lazy" />` : '';
  const postsHtml = posts.length
    ? `<div class="store-owner-posts">
         <div class="store-owner-posts-title">Latest from the store</div>
         ${posts.map(p => `
           <div class="store-owner-post">
             <div class="store-owner-post-title">${escHtml(p.title)}</div>
             ${p.body ? `<div class="store-owner-post-body">${escHtml(p.body)}</div>` : ''}
             <div class="store-owner-post-meta">${timeAgo(new Date(p.created_at))}</div>
           </div>`).join('')}
       </div>` : '';

  if (!profile && posts.length) {
    mount.innerHTML = `<div class="store-owner-card">${postsHtml}</div>`;
    return;
  }

  const storePageLink = `<a class="store-full-page-link" href="/store/${encodeURIComponent(retailerId)}" target="_blank">
    Open full store page <span style="font-size:.85em">↗</span>
  </a>`;

  mount.innerHTML = `<div class="store-owner-card">
    ${bannerHtml}
    <div class="store-owner-card-hd">
      ${photoHtml}
      <div class="store-owner-card-body">
        <div class="store-owner-card-title">From the store ${verifiedBadge}</div>
        ${descHtml}
        ${metaHtml}
      </div>
    </div>
    ${postsHtml}
    <div class="store-full-page-row">${storePageLink}</div>
  </div>`;
}

function setInvPanelFilter(btn, filter) {
  const panel = btn.closest('.inv-panel');
  if (!panel) return;
  panel.querySelectorAll('.inv-filter-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  panel.querySelectorAll('.inv-game-row').forEach(row => {
    row.style.display = (filter === 'all' || row.dataset.gameStatus === filter) ? '' : 'none';
  });
}

function toggleGameInvBtn(btn) {
  const rid     = btn.dataset.rid;
  const game    = btn.dataset.game;
  const price   = btn.dataset.price !== '' ? parseFloat(btn.dataset.price) : null;
  const hasStock = btn.dataset.stock === 'true';
  toggleGameInv(rid, game, price, hasStock, null);
}

async function toggleGameInv(retailerId, gameName, gamePrice, hasStock, notes) {
  if (!_currentUser) { openAuthModal('login'); return; }

  const ret = allRetailers.find(r => String(r.id) === String(retailerId))
           || allAzRetailers.find(r => String(r.id) === String(retailerId))
           || allRiRetailers.find(r => String(r.id) === String(retailerId));

  const now = new Date().toISOString();
  const newReport = {
    id: Date.now(),
    retailer_id: retailerId,
    retailer_name: ret?.name || '',
    retailer_city: ret?.city || '',
    lat: ret?.latitude || null,
    lng: ret?.longitude || null,
    game_name: gameName,
    game_price: gamePrice,
    has_stock: hasStock,
    reporter_username: _currentUser.username,
    notes: notes || null,
    reported_at: now,
    source: 'community',
  };

  const gameKey = (gameName || '').toLowerCase();
  const prevReports = communityReports;
  communityReports = [
    newReport,
    ...communityReports.filter(r =>
      !(r.retailer_id === retailerId && (r.game_name || '').toLowerCase() === gameKey)
    ),
  ];

  buildLatestStatusFromReports();
  refreshOpenProfile();
  updateLastReportCells();
  updateReportBadges();

  try {
    const res = await protectedFetch('/api/inventory/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        retailer_id:   retailerId,
        retailer_name: ret?.name || '',
        retailer_city: ret?.city || '',
        lat:           ret?.latitude || null,
        lng:           ret?.longitude || null,
        game_name:     gameName,
        game_price:    gamePrice,
        has_stock:     hasStock,
        notes:         notes || null,
      }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        if (j.detail) {
          if (typeof j.detail === 'string') {
            detail = j.detail;
          } else if (Array.isArray(j.detail)) {
            detail = j.detail.map(e => e.msg || JSON.stringify(e)).join('; ');
          } else {
            detail = JSON.stringify(j.detail);
          }
        }
      } catch {}
      throw new Error(detail);
    }
  } catch (err) {
    communityReports = prevReports;
    buildLatestStatusFromReports();
    refreshOpenProfile();
    updateLastReportCells();
    updateReportBadges();
    console.error('Inventory report failed:', err);
    showToast(`Could not save: ${err.message}`, "err", 6000);
  }
}

let _invNotesRid   = null;
let _invNotesGame  = null;
let _invNotesPrice = null;
let _invNotesStock = null;

function openGameNotesBtn(btn) {
  const rid   = btn.dataset.rid;
  const game  = btn.dataset.game;
  const price = btn.dataset.price !== '' ? parseFloat(btn.dataset.price) : null;
  openGameNotes(rid, game, price);
}

function openGameNotes(retailerId, gameName, gamePrice) {
  if (!_currentUser) { openAuthModal('login'); return; }
  _invNotesRid   = retailerId;
  _invNotesGame  = gameName;
  _invNotesPrice = gamePrice;
  const status = getPerGameStatuses(retailerId)[(gameName || '').toLowerCase()] ?? null;
  _invNotesStock = status?.has_stock ?? null;
  setInvNotesStock(_invNotesStock);
  document.getElementById('invNotesGameName').textContent = gameName;
  document.getElementById('invNotesText').value = status?.notes || '';
  document.getElementById('invNotesOverlay').classList.add('open');
}

function closeGameNotes() {
  document.getElementById('invNotesOverlay').classList.remove('open');
  _invNotesRid = _invNotesGame = _invNotesPrice = _invNotesStock = null;
}

function setInvNotesStock(v) {
  _invNotesStock = v;
  document.getElementById('invNotesBtnIn').className  = 'inv-notes-stock-btn' + (v === true  ? ' is-in'  : '');
  document.getElementById('invNotesBtnOut').className = 'inv-notes-stock-btn' + (v === false ? ' is-out' : '');
}

async function submitGameNotes() {
  if (_invNotesStock === null) return;
  const rid   = _invNotesRid;
  const game  = _invNotesGame;
  const price = _invNotesPrice;
  const stock = _invNotesStock;
  const notes = document.getElementById('invNotesText').value.trim() || null;
  const btn   = document.getElementById('invNotesSubmitBtn');
  btn.disabled = true; btn.textContent = 'Submitting…';
  closeGameNotes();
  await toggleGameInv(rid, game, price, stock, notes);
  btn.disabled = false; btn.textContent = 'Submit';
}

function normalizeGameName(name) {
  return (name || "").toLowerCase().replace(/[^a-z0-9\s]/g, "").replace(/\s+/g, " ").trim();
}

const CHASE_HANDLERS = {
  MA: { select: "selectGameFilter",   inv: "maInvFilter", render: "renderMaTable" },
  AZ: { select: "selectAzGameFilter", inv: "azInvFilter", render: "renderAzTable" },
  RI: { select: "selectRiGameFilter", inv: "riInvFilter", render: "renderRiTable" },
  FL: { select: "selectFlGameFilter", inv: "flInvFilter", render: "renderFlTable" },
  GA: { select: "selectGaGameFilter", inv: "gaInvFilter", render: "renderGaTable" },
  NY: { select: "selectNyGameFilter", inv: "nyInvFilter", render: "renderNyTable" },
  VA: { select: "selectVaGameFilter", inv: "vaInvFilter", render: "renderVaTable" },
  DC: { select: "selectDcGameFilter", inv: "dcInvFilter", render: "renderDcTable" },
  VT: { select: "selectVtGameFilter", inv: "vtInvFilter", render: "renderVtTable" },
};

function viewGameInChase(gameName, stateCode) {
  closeModal();
  const code = (stateCode || "MA").toUpperCase();
  switchTab("ma");
  selectHuntState(code);
  const h = CHASE_HANDLERS[code];
  if (!h) return;
  setTimeout(() => {
    try {
      const invEl = document.getElementById(h.inv);
      if (invEl) invEl.value = "in";
      const selectFn = window[h.select];
      if (typeof selectFn === "function") selectFn(gameName);
      const renderFn = window[h.render];
      if (typeof renderFn === "function") renderFn();
    } catch (e) {}
  }, 60);
}

function modalCommunitySection(gameName, gamePrice, stateCode, stateName) {
  const count = gameCounts[gameName.toLowerCase()] || 0;
  if (!count && !_currentUser) return "";

  if (!_currentUser) {
    return `<div class="modal-community-section">
      <div class="modal-community-title">📍 Inventory</div>
      <div class="modal-community-gate">
        <span>In stock at ${count} member-reported location${count > 1 ? "s" : ""}.</span>
        <button class="btn btn-login" onclick="closeModal();openAuthModal('login')" style="font-size:.78rem;padding:.3rem .75rem">Log In to See</button>
        <button class="btn btn-register" onclick="closeModal();openAuthModal('register')" style="font-size:.78rem;padding:.3rem .75rem">Join Free</button>
      </div>
    </div>`;
  }

  const normGame = normalizeGameName(gameName);
  const allReports = communityReports.filter(r => normalizeGameName(r.game_name) === normGame);
  const addBtn = `<button class="btn btn-report" onclick="openReportModalForGame(${JSON.stringify(gameName)},${gamePrice != null ? gamePrice : "null"})" style="font-size:.78rem;padding:.3rem .75rem">+ Add Report</button>`;
  const chaseHref = `onclick="viewGameInChase(${escHtml(JSON.stringify(gameName))},${escHtml(JSON.stringify(stateCode || ""))})"`;

  // --- Retailer-confirmed section (summary only) ---
  const latestByRetailer = {};
  for (const r of allReports) {
    if (r.source !== "retailer") continue;
    const existing = latestByRetailer[r.retailer_id];
    if (!existing || new Date(r.reported_at) > new Date(existing.reported_at)) {
      latestByRetailer[r.retailer_id] = r;
    }
  }
  const retailerConfirmed = Object.values(latestByRetailer);

  let retailerSection = "";
  if (retailerConfirmed.length) {
    if (!_currentUser) {
      retailerSection = `<div class="modal-retailer-section">
        <div class="modal-community-title" style="margin-bottom:.55rem">🏪 Retailer-Confirmed</div>
        <div class="modal-community-gate">
          <span>${retailerConfirmed.length} retailer report${retailerConfirmed.length > 1 ? "s" : ""} for this game.</span>
          <button class="btn btn-login" onclick="closeModal();openAuthModal('login')" style="font-size:.78rem;padding:.3rem .75rem">Log In to See</button>
          <button class="btn btn-register" onclick="closeModal();openAuthModal('register')" style="font-size:.78rem;padding:.3rem .75rem">Join Free</button>
        </div>
      </div>`;
    } else {
      const rIn  = retailerConfirmed.filter(r => r.has_stock).length;
      const rOut = retailerConfirmed.length - rIn;
      const summary = `<span style="color:var(--text-muted);font-weight:400;font-size:.82rem">${retailerConfirmed.length} store${retailerConfirmed.length > 1 ? "s" : ""} · <span style="color:var(--green);font-weight:600">${rIn} in</span> · <span style="color:var(--red);font-weight:600">${rOut} out</span></span>`;
      retailerSection = `<div class="modal-retailer-section">
        <div class="modal-community-title" style="margin-bottom:.55rem;display:flex;align-items:center;justify-content:space-between;gap:.5rem;flex-wrap:wrap">
          <span>🏪 Retailer-Confirmed</span>${summary}
        </div>
        <button class="modal-view-all-chase" ${chaseHref}>View ${retailerConfirmed.length} retailer-confirmed store${retailerConfirmed.length > 1 ? "s" : ""} in The Chase →</button>
      </div>`;
    }
  }

  // --- Member inventory section (summary only) ---
  const reports = allReports.filter(r => r.source !== "retailer");

  if (!reports.length && !retailerSection) {
    return `<div class="modal-community-section">
      <div class="modal-community-title" style="display:flex;align-items:center;justify-content:space-between">
        <span>📍 Inventory</span>${addBtn}
      </div>
      <div class="profile-no-reports">No inventory data yet for this game in ${stateCode || "your state"}.</div>
    </div>`;
  }

  const cIn  = reports.filter(r => r.has_stock).length;
  const cOut = reports.length - cIn;
  const cSummary = reports.length
    ? `<span style="color:var(--text-muted);font-weight:400;font-size:.82rem">${reports.length} store${reports.length > 1 ? "s" : ""} · <span style="color:var(--green);font-weight:600">${cIn} in</span> · <span style="color:var(--red);font-weight:600">${cOut} out</span></span>`
    : "";

  const communitySection = reports.length ? `<div class="modal-community-section">
    <div class="modal-community-title" style="display:flex;align-items:center;justify-content:space-between;gap:.5rem;flex-wrap:wrap">
      <span>📍 Inventory <span style="color:var(--text-muted);font-weight:400;font-size:.82rem">(${stateCode || ""})</span></span>
      <span style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">${cSummary}${addBtn}</span>
    </div>
    <button class="modal-view-all-chase" ${chaseHref}>View ${reports.length} store${reports.length > 1 ? "s" : ""} in The Chase →</button>
  </div>` : `<div class="modal-community-section">
    <div class="modal-community-title" style="display:flex;align-items:center;justify-content:space-between">
      <span>📍 Inventory</span>${addBtn}
    </div>
    <div class="profile-no-reports">No inventory data yet for this game in ${stateCode || "your state"}.</div>
  </div>`;

  return retailerSection + communitySection;
}

function openReportModalForStore(retailerId) {
  const r = allRetailers.find(ret => String(ret.id) === String(retailerId))
         || allAzRetailers.find(ret => String(ret.id) === String(retailerId))
         || allRiRetailers.find(ret => String(ret.id) === String(retailerId));
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
  if (!td) return;

  // Preserve filter-tab selection and game-list scroll position across re-render
  const prevPanel = td.querySelector(".inv-panel");
  const activeTab = prevPanel?.querySelector(".inv-filter-tab.active");
  const prevFilter = activeTab
    ? (activeTab.textContent.trim().toLowerCase() === "in stock"     ? "in"
      : activeTab.textContent.trim().toLowerCase() === "out of stock" ? "out"
      : activeTab.textContent.trim().toLowerCase() === "not set"      ? "not_set"
      : "all")
    : "all";
  const prevScroll = prevPanel?.querySelector(".inv-game-list")?.scrollTop || 0;

  td.innerHTML = storeProfileHtml(_openProfileId);

  if (prevFilter !== "all") {
    const newPanel = td.querySelector(".inv-panel");
    const tabs = newPanel?.querySelectorAll(".inv-filter-tab");
    if (tabs) {
      const map = { all: 0, in: 1, out: 2, not_set: 3 };
      const idx = map[prevFilter];
      if (idx != null && tabs[idx]) setInvPanelFilter(tabs[idx], prevFilter);
    }
  }
  const newList = td.querySelector(".inv-game-list");
  if (newList && prevScroll) newList.scrollTop = prevScroll;
}

// ── Non-blocking toast ────────────────────────────────────────────────────────
function showToast(msg, type = "info", ms = 4500) {
  let stack = document.getElementById("sfToastStack");
  if (!stack) {
    stack = document.createElement("div");
    stack.id = "sfToastStack";
    stack.className = "sf-toast-stack";
    document.body.appendChild(stack);
  }
  const icon = type === "err" ? "⚠️" : type === "ok" ? "✅" : "ℹ️";
  const toast = document.createElement("div");
  toast.className = `sf-toast ${type}`;
  toast.innerHTML = `<span class="sf-toast-icon">${icon}</span><span class="sf-toast-body"></span><button class="sf-toast-close" aria-label="Dismiss">✕</button>`;
  toast.querySelector(".sf-toast-body").textContent = msg;
  const dismiss = () => {
    toast.classList.add("fade-out");
    setTimeout(() => toast.remove(), 220);
  };
  toast.querySelector(".sf-toast-close").onclick = dismiss;
  stack.appendChild(toast);
  setTimeout(dismiss, ms);
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

  // Stat cards — show local counts immediately, API call below will correct them
  if (selectedGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) {
      s.has_stock ? inCount++ : outCount++;
    }
    document.getElementById("maStatInStockCard").style.display = "";
    document.getElementById("maStatOutCard").style.display = "";
    document.getElementById("maStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("maStatOut").textContent = outCount.toLocaleString();
    loadRetailerLatest(selectedGame.name);
  } else {
    document.getElementById("maStatInStockCard").style.display = "none";
    document.getElementById("maStatOutCard").style.display = "none";
    loadRetailerLatest();
  }

  renderMaTable();
  if (maMapVisible) renderMapLayers(getFilteredRows());
}


function updateInventoryMapLayer(visibleRetailers) {
  renderInventoryCluster(maMap, "_inventoryLayer", {
    retailers: visibleRetailers || getFilteredRows(),
    reports: communityReports,
    selectedGame: selectedGame,
    reportFilter: mapReportFilter,
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// RI HUNT
// ══════════════════════════════════════════════════════════════════════════════

async function loadRiRetailers() {
  try {
    const res = await fetch("/api/ri/retailers?limit=30000");
    const data = await res.json();
    allRiRetailers = data.retailers || [];
    riLoaded = true;
    updateRiStats();
    renderRiTable();
    if (!riMapVisible) toggleRiMap();
  } catch (e) {
    const tbody = document.getElementById("riTableBody");
    if (tbody) tbody.innerHTML =
      `<tr><td colspan="6" class="loading-cell">Failed to load RI retailers.</td></tr>`;
  }
}

function updateRiStats() {
  const el = document.getElementById("riStatTotal");
  if (el) el.textContent = allRiRetailers.length.toLocaleString();
}

function getRiFilteredRows() {
  const q             = (document.getElementById("riSearchInput")?.value || "").toLowerCase().trim();
  const city          = (document.getElementById("riCityInput")?.value   || "").toLowerCase().trim();
  const invFilter     = document.getElementById("riInvFilter")?.value  || "";
  const dateFilter    = document.getElementById("riDateFilter")?.value || "";

  riMapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allRiRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));


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

function renderRiTable() {
  if (!riLoaded) return;
  const myGen = ++riRenderGen;
  _openProfileId = null;
  const rows = getRiFilteredRows();
  const checkedCount = selectedRiGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null
    ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedRiGame.name)}</strong>`
    : "";
  const countEl = document.getElementById("riResultCount");
  if (countEl) countEl.innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("riTableBody");
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
    return;
  }
  if (riMapVisible) renderRiMapLayers(rows);
  lazyRenderRows({
    tbody,
    rows,
    rowFn: riRow,
    getStaleFlag: () => myGen !== riRenderGen,
  });
}

function riRow(r, rank) {
  const addr = encodeURIComponent(`${r.name}, ${r.address}, ${r.city}, RI ${r.zipCode}`);
  const mapsUrl       = `https://www.google.com/maps/search/?api=1&query=${addr}`;
  const searchUrl     = `https://www.google.com/search?q=${encodeURIComponent(r.name + ' ' + r.city + ' RI lottery')}`;
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
    <td><strong>${escHtml(r.name)}</strong><br><span style="font-size:.78rem;color:var(--text-muted)">${escHtml(r.address)}</span><span class="report-count-badge" id="rbadge-${rid}" style="display:none"></span></td>
    <td>${escHtml(r.city)}</td>
    <td>${escHtml(r.zipCode)}</td>
    <td class="last-report-cell" data-rid="${rid}">${lastReportCellHtml(rid)}</td>
    <td class="links-cell" onclick="event.stopPropagation()">${links}</td>
  </tr>`;
}

function downloadRiCsv() {
  const rows = getRiFilteredRows();
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
  a.download = "ri_retailers.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── RI Leaflet map ────────────────────────────────────────────────────────────

function toggleRiMap() {
  const sec = document.getElementById("riMapSection");
  riMapVisible = !riMapVisible;
  sec.style.display = riMapVisible ? "" : "none";
  if (riMapVisible) {
    if (!riMap) initRiMap();
    setTimeout(() => riMap && riMap.invalidateSize(), 50);
    renderRiMapLayers(getRiFilteredRows());
  }
}

function initRiMap() {
  riMap = L.map("riMap", { preferCanvas: true }).setView([41.7, -71.5], 11);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(riMap);
  setupMapAutoResize(riMap);
}

function renderRiMapLayers(retailers) {
  if (!riMap) return;
  debounceMapRender("ri", () => updateRiInventoryMapLayer(retailers), 180);
}

function updateRiInventoryMapLayer(visibleRetailers) {
  renderInventoryCluster(riMap, "_riInventoryLayer", {
    retailers: visibleRetailers || getRiFilteredRows(),
    reports: communityReports,
    scopeIds: new Set(allRiRetailers.map(r => String(r.id))),
    selectedGame: selectedRiGame,
    reportFilter: riMapReportFilter,
  });
}

// ── RI game filter ────────────────────────────────────────────────────────────

function searchRiGameFilter() {
  const input = document.getElementById("riGameFilterInput");
  const dd    = document.getElementById("riGameFilterDropdown");
  const clear = document.getElementById("riGameFilterClear");
  if (!input) return;
  const q = input.value.trim().toLowerCase();
  clear.style.display = q ? "" : "none";
  const matches = q ? riGames.filter(g => g.name.toLowerCase().includes(q)) : riGames.slice(0, 50);
  if (!matches.length) { dd.style.display = "none"; return; }
  dd.innerHTML = matches.map(g => {
    const meta = [g.price != null ? `$${g.price}` : null, g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : null]
      .filter(Boolean).join(" · ");
    const sub = meta ? `<span style="color:var(--text-muted);font-size:.78rem">${escHtml(meta)}</span>` : "";
    return `<div class="store-option" onmousedown="selectRiGameFilter(${JSON.stringify(g.name).replace(/"/g, '&quot;')})">${escHtml(g.name)} ${sub}</div>`;
  }).join("");
  dd.style.display = "";
}

function selectRiGameFilter(name) {
  const input = document.getElementById("riGameFilterInput");
  const dd    = document.getElementById("riGameFilterDropdown");
  const clear = document.getElementById("riGameFilterClear");
  input.value = name;
  dd.style.display = "none";
  clear.style.display = "";
  const g = riGames.find(g => g.name === name) || { name, price: null };
  selectedRiGame = { name: g.name, price: g.price ?? null };
  applyRiGameFilter();
}

function clearRiGameFilter() {
  document.getElementById("riGameFilterInput").value = "";
  document.getElementById("riGameFilterDropdown").style.display = "none";
  document.getElementById("riGameFilterClear").style.display = "none";
  selectedRiGame = null;
  applyRiGameFilter();
}

function applyRiGameFilter() {
  const th = document.getElementById("riLastReportTh");
  if (th) th.textContent = selectedRiGame ? selectedRiGame.name : "Last Report";

  buildLatestStatusFromReports();

  if (selectedRiGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) {
      s.has_stock ? inCount++ : outCount++;
    }
    document.getElementById("riStatInStockCard").style.display = "";
    document.getElementById("riStatOutCard").style.display = "";
    document.getElementById("riStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("riStatOut").textContent = outCount.toLocaleString();
    loadRetailerLatest(selectedRiGame.name);
  } else {
    document.getElementById("riStatInStockCard").style.display = "none";
    document.getElementById("riStatOutCard").style.display = "none";
    loadRetailerLatest();
  }

  renderRiTable();
  if (riMapVisible) renderRiMapLayers(getRiFilteredRows());
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
    loadRetailerLatest(selectedAzGame.name);
  } else {
    document.getElementById("azStatInStockCard").style.display = "none";
    document.getElementById("azStatOutCard").style.display = "none";
    loadRetailerLatest();
  }

  renderAzTable();
  if (azMapVisible) renderAzMapLayers(getAzFilteredRows());
}

// ── AZ data loading ───────────────────────────────────────────────────────────

async function loadAzRetailers() {
  try {
    const res = await fetch("/api/az/retailers?limit=30000");
    const data = await res.json();
    allAzRetailers = data.retailers || [];
    azLoaded = true;
    updateAzStats();
    renderAzTable();
    if (!azMapVisible) toggleAzMap();
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

  azMapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allAzRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));


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
  const myGen = ++azRenderGen;
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
    return;
  }
  if (azMapVisible) renderAzMapLayers(rows);
  lazyRenderRows({
    tbody,
    rows,
    rowFn: azRow,
    getStaleFlag: () => myGen !== azRenderGen,
  });
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
    <td><strong>${escHtml(r.name)}</strong><br><span style="font-size:.78rem;color:var(--text-muted)">${escHtml(r.address)}</span><span class="report-count-badge" id="rbadge-${rid}" style="display:none"></span></td>
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
    setTimeout(() => azMap && azMap.invalidateSize(), 50);
    renderAzMapLayers(getAzFilteredRows());
  }
}

function initAzMap() {
  azMap = L.map("azMap", { preferCanvas: true }).setView([34.05, -111.09], 7);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(azMap);
  setupMapAutoResize(azMap);
}

function renderAzMapLayers(retailers) {
  if (!azMap) return;
  debounceMapRender("az", () => updateAzInventoryMapLayer(retailers), 180);
}

function updateAzInventoryMapLayer(visibleRetailers) {
  renderInventoryCluster(azMap, "_azInventoryLayer", {
    retailers: visibleRetailers || getAzFilteredRows(),
    reports: communityReports,
    scopeIds: new Set(allAzRetailers.map(r => String(r.id))),
    selectedGame: selectedAzGame,
    reportFilter: azMapReportFilter,
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// FL HUNT
// ══════════════════════════════════════════════════════════════════════════════

async function loadFlRetailers() {
  try {
    const res = await fetch("/api/fl/retailers?limit=30000");
    const data = await res.json();
    allFlRetailers = data.retailers || [];
    flLoaded = true;
    const el = document.getElementById("flStatTotal");
    if (el) el.textContent = allFlRetailers.length.toLocaleString();
    renderFlTable();
    if (!flMapVisible) toggleFlMap();
  } catch (e) {
    const tbody = document.getElementById("flTableBody");
    if (tbody) tbody.innerHTML =
      `<tr><td colspan="6" class="loading-cell">Failed to load FL retailers.</td></tr>`;
  }
}

function getFlFilteredRows() {
  const q             = (document.getElementById("flSearchInput")?.value || "").toLowerCase().trim();
  const city          = (document.getElementById("flCityInput")?.value   || "").toLowerCase().trim();
  const invFilter     = document.getElementById("flInvFilter")?.value  || "";
  const dateFilter    = document.getElementById("flDateFilter")?.value || "";

  flMapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allFlRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));
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

function renderFlTable() {
  if (!flLoaded) return;
  const myGen = ++flRenderGen;
  _openProfileId = null;
  const rows = getFlFilteredRows();
  const checkedCount = selectedFlGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null
    ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedFlGame.name)}</strong>`
    : "";
  const countEl = document.getElementById("flResultCount");
  if (countEl) countEl.innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("flTableBody");
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
    return;
  }
  if (flMapVisible) renderFlMapLayers(rows);
  lazyRenderRows({
    tbody,
    rows,
    rowFn: (r, rank) => _stateRow(r, rank, "FL"),
    getStaleFlag: () => myGen !== flRenderGen,
  });
}

function downloadFlCsv() {
  const rows = getFlFilteredRows();
  const cols = ["name","address","city","zipCode","phone","latitude","longitude"];
  const blob = new Blob([cols.join(",") + "\n" + rows.map(r =>
    cols.map(c => { const v = String(r[c] ?? ""); return v.includes(",") || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v; }).join(",")
  ).join("\n")], { type: "text/csv" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "fl_retailers.csv"; a.click(); URL.revokeObjectURL(a.href);
}

function toggleFlMap() {
  const sec = document.getElementById("flMapSection");
  flMapVisible = !flMapVisible;
  sec.style.display = flMapVisible ? "" : "none";
  if (flMapVisible) {
    if (!flMap) initFlMap();
    setTimeout(() => flMap && flMap.invalidateSize(), 50);
    renderFlMapLayers(getFlFilteredRows());
  }
}

function initFlMap() {
  flMap = L.map("flMap", { preferCanvas: true }).setView([27.8, -81.7], 7);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors", maxZoom: 19,
  }).addTo(flMap);
  setupMapAutoResize(flMap);
}

function renderFlMapLayers(retailers) {
  if (!flMap) return;
  debounceMapRender("fl", () => updateFlInventoryMapLayer(retailers), 180);
}

function updateFlInventoryMapLayer(visibleRetailers) {
  renderInventoryCluster(flMap, "_flInventoryLayer", {
    retailers: visibleRetailers || getFlFilteredRows(),
    reports: communityReports,
    scopeIds: new Set(allFlRetailers.map(r => String(r.id))),
    selectedGame: selectedFlGame,
    reportFilter: flMapReportFilter,
  });
}

function searchFlGameFilter() {
  const input = document.getElementById("flGameFilterInput");
  const dd    = document.getElementById("flGameFilterDropdown");
  const clear = document.getElementById("flGameFilterClear");
  if (!input) return;
  const q = input.value.trim().toLowerCase();
  clear.style.display = q ? "" : "none";
  const matches = q ? flGames.filter(g => g.name.toLowerCase().includes(q)) : flGames.slice(0, 50);
  if (!matches.length) { dd.style.display = "none"; return; }
  dd.innerHTML = matches.map(g => {
    const meta = [g.price != null ? `$${g.price}` : null, g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : null].filter(Boolean).join(" · ");
    const sub = meta ? `<span style="color:var(--text-muted);font-size:.78rem">${escHtml(meta)}</span>` : "";
    return `<div class="store-option" onmousedown="selectFlGameFilter(${JSON.stringify(g.name).replace(/"/g, '&quot;')})">${escHtml(g.name)} ${sub}</div>`;
  }).join("");
  dd.style.display = "";
}

function selectFlGameFilter(name) {
  const input = document.getElementById("flGameFilterInput");
  const dd    = document.getElementById("flGameFilterDropdown");
  const clear = document.getElementById("flGameFilterClear");
  input.value = name; dd.style.display = "none"; clear.style.display = "";
  const g = flGames.find(g => g.name === name) || { name, price: null };
  selectedFlGame = { name: g.name, price: g.price ?? null };
  applyFlGameFilter();
}

function clearFlGameFilter() {
  document.getElementById("flGameFilterInput").value = "";
  document.getElementById("flGameFilterDropdown").style.display = "none";
  document.getElementById("flGameFilterClear").style.display = "none";
  selectedFlGame = null;
  applyFlGameFilter();
}

function applyFlGameFilter() {
  const th = document.getElementById("flLastReportTh");
  if (th) th.textContent = selectedFlGame ? selectedFlGame.name : "Last Report";
  buildLatestStatusFromReports();
  if (selectedFlGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) { s.has_stock ? inCount++ : outCount++; }
    document.getElementById("flStatInStockCard").style.display = "";
    document.getElementById("flStatOutCard").style.display = "";
    document.getElementById("flStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("flStatOut").textContent = outCount.toLocaleString();
    loadRetailerLatest(selectedFlGame.name);
  } else {
    document.getElementById("flStatInStockCard").style.display = "none";
    document.getElementById("flStatOutCard").style.display = "none";
    loadRetailerLatest();
  }
  renderFlTable();
  if (flMapVisible) renderFlMapLayers(getFlFilteredRows());
}

// ══════════════════════════════════════════════════════════════════════════════
// GA HUNT
// ══════════════════════════════════════════════════════════════════════════════

async function loadGaRetailers() {
  try {
    const res = await fetch("/api/ga/retailers?limit=30000");
    const data = await res.json();
    allGaRetailers = data.retailers || [];
    gaLoaded = true;
    const el = document.getElementById("gaStatTotal");
    if (el) el.textContent = allGaRetailers.length.toLocaleString();
    renderGaTable();
    if (!gaMapVisible) toggleGaMap();
  } catch (e) {
    const tbody = document.getElementById("gaTableBody");
    if (tbody) tbody.innerHTML =
      `<tr><td colspan="6" class="loading-cell">Failed to load GA retailers.</td></tr>`;
  }
}

function getGaFilteredRows() {
  const q             = (document.getElementById("gaSearchInput")?.value || "").toLowerCase().trim();
  const city          = (document.getElementById("gaCityInput")?.value   || "").toLowerCase().trim();
  const invFilter     = document.getElementById("gaInvFilter")?.value  || "";
  const dateFilter    = document.getElementById("gaDateFilter")?.value || "";

  gaMapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allGaRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));
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

function renderGaTable() {
  if (!gaLoaded) return;
  const myGen = ++gaRenderGen;
  _openProfileId = null;
  const rows = getGaFilteredRows();
  const checkedCount = selectedGaGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null
    ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedGaGame.name)}</strong>`
    : "";
  const countEl = document.getElementById("gaResultCount");
  if (countEl) countEl.innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("gaTableBody");
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
    return;
  }
  if (gaMapVisible) renderGaMapLayers(rows);
  lazyRenderRows({
    tbody,
    rows,
    rowFn: (r, rank) => _stateRow(r, rank, "GA"),
    getStaleFlag: () => myGen !== gaRenderGen,
  });
}

function downloadGaCsv() {
  const rows = getGaFilteredRows();
  const cols = ["name","address","city","zipCode","phone","latitude","longitude"];
  const blob = new Blob([cols.join(",") + "\n" + rows.map(r =>
    cols.map(c => { const v = String(r[c] ?? ""); return v.includes(",") || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v; }).join(",")
  ).join("\n")], { type: "text/csv" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "ga_retailers.csv"; a.click(); URL.revokeObjectURL(a.href);
}

function toggleGaMap() {
  const sec = document.getElementById("gaMapSection");
  gaMapVisible = !gaMapVisible;
  sec.style.display = gaMapVisible ? "" : "none";
  if (gaMapVisible) {
    if (!gaMap) initGaMap();
    setTimeout(() => gaMap && gaMap.invalidateSize(), 50);
    renderGaMapLayers(getGaFilteredRows());
  }
}

function initGaMap() {
  gaMap = L.map("gaMap", { preferCanvas: true }).setView([32.7, -83.5], 7);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors", maxZoom: 19,
  }).addTo(gaMap);
  setupMapAutoResize(gaMap);
}

function renderGaMapLayers(retailers) {
  if (!gaMap) return;
  debounceMapRender("ga", () => updateGaInventoryMapLayer(retailers), 180);
}

function updateGaInventoryMapLayer(visibleRetailers) {
  renderInventoryCluster(gaMap, "_gaInventoryLayer", {
    retailers: visibleRetailers || getGaFilteredRows(),
    reports: communityReports,
    scopeIds: new Set(allGaRetailers.map(r => String(r.id))),
    selectedGame: selectedGaGame,
    reportFilter: gaMapReportFilter,
  });
}

function searchGaGameFilter() {
  const input = document.getElementById("gaGameFilterInput");
  const dd    = document.getElementById("gaGameFilterDropdown");
  const clear = document.getElementById("gaGameFilterClear");
  if (!input) return;
  const q = input.value.trim().toLowerCase();
  clear.style.display = q ? "" : "none";
  const matches = q ? gaGames.filter(g => g.name.toLowerCase().includes(q)) : gaGames.slice(0, 50);
  if (!matches.length) { dd.style.display = "none"; return; }
  dd.innerHTML = matches.map(g => {
    const meta = [g.price != null ? `$${g.price}` : null, g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : null].filter(Boolean).join(" · ");
    const sub = meta ? `<span style="color:var(--text-muted);font-size:.78rem">${escHtml(meta)}</span>` : "";
    return `<div class="store-option" onmousedown="selectGaGameFilter(${JSON.stringify(g.name).replace(/"/g, '&quot;')})">${escHtml(g.name)} ${sub}</div>`;
  }).join("");
  dd.style.display = "";
}

function selectGaGameFilter(name) {
  const input = document.getElementById("gaGameFilterInput");
  const dd    = document.getElementById("gaGameFilterDropdown");
  const clear = document.getElementById("gaGameFilterClear");
  input.value = name; dd.style.display = "none"; clear.style.display = "";
  const g = gaGames.find(g => g.name === name) || { name, price: null };
  selectedGaGame = { name: g.name, price: g.price ?? null };
  applyGaGameFilter();
}

function clearGaGameFilter() {
  document.getElementById("gaGameFilterInput").value = "";
  document.getElementById("gaGameFilterDropdown").style.display = "none";
  document.getElementById("gaGameFilterClear").style.display = "none";
  selectedGaGame = null;
  applyGaGameFilter();
}

function applyGaGameFilter() {
  const th = document.getElementById("gaLastReportTh");
  if (th) th.textContent = selectedGaGame ? selectedGaGame.name : "Last Report";
  buildLatestStatusFromReports();
  if (selectedGaGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) { s.has_stock ? inCount++ : outCount++; }
    document.getElementById("gaStatInStockCard").style.display = "";
    document.getElementById("gaStatOutCard").style.display = "";
    document.getElementById("gaStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("gaStatOut").textContent = outCount.toLocaleString();
    loadRetailerLatest(selectedGaGame.name);
  } else {
    document.getElementById("gaStatInStockCard").style.display = "none";
    document.getElementById("gaStatOutCard").style.display = "none";
    loadRetailerLatest();
  }
  renderGaTable();
  if (gaMapVisible) renderGaMapLayers(getGaFilteredRows());
}

// ══════════════════════════════════════════════════════════════════════════════
// NY HUNT
// ══════════════════════════════════════════════════════════════════════════════

async function loadNyRetailers() {
  try {
    const res = await fetch("/api/ny/retailers?limit=30000");
    const data = await res.json();
    allNyRetailers = data.retailers || [];
    nyLoaded = true;
    const el = document.getElementById("nyStatTotal");
    if (el) el.textContent = allNyRetailers.length.toLocaleString();
    renderNyTable();
    if (!nyMapVisible) toggleNyMap();
  } catch (e) {
    const tbody = document.getElementById("nyTableBody");
    if (tbody) tbody.innerHTML =
      `<tr><td colspan="6" class="loading-cell">Failed to load NY retailers.</td></tr>`;
  }
}

function getNyFilteredRows() {
  const q             = (document.getElementById("nySearchInput")?.value || "").toLowerCase().trim();
  const city          = (document.getElementById("nyCityInput")?.value   || "").toLowerCase().trim();
  const invFilter     = document.getElementById("nyInvFilter")?.value  || "";
  const dateFilter    = document.getElementById("nyDateFilter")?.value || "";

  nyMapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allNyRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));
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

function renderNyTable() {
  if (!nyLoaded) return;
  const myGen = ++nyRenderGen;
  _openProfileId = null;
  const rows = getNyFilteredRows();
  const checkedCount = selectedNyGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null
    ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedNyGame.name)}</strong>`
    : "";
  const countEl = document.getElementById("nyResultCount");
  if (countEl) countEl.innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("nyTableBody");
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
    return;
  }
  if (nyMapVisible) renderNyMapLayers(rows);
  lazyRenderRows({
    tbody,
    rows,
    rowFn: (r, rank) => _stateRow(r, rank, "NY"),
    getStaleFlag: () => myGen !== nyRenderGen,
  });
}

function downloadNyCsv() {
  const rows = getNyFilteredRows();
  const cols = ["name","address","city","zipCode","phone","latitude","longitude"];
  const blob = new Blob([cols.join(",") + "\n" + rows.map(r =>
    cols.map(c => { const v = String(r[c] ?? ""); return v.includes(",") || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v; }).join(",")
  ).join("\n")], { type: "text/csv" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "ny_retailers.csv"; a.click(); URL.revokeObjectURL(a.href);
}

function toggleNyMap() {
  const sec = document.getElementById("nyMapSection");
  nyMapVisible = !nyMapVisible;
  sec.style.display = nyMapVisible ? "" : "none";
  if (nyMapVisible) {
    if (!nyMap) initNyMap();
    setTimeout(() => nyMap && nyMap.invalidateSize(), 50);
    renderNyMapLayers(getNyFilteredRows());
  }
}

function initNyMap() {
  nyMap = L.map("nyMap", { preferCanvas: true }).setView([42.9, -75.8], 7);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors", maxZoom: 19,
  }).addTo(nyMap);
  setupMapAutoResize(nyMap);
}

function renderNyMapLayers(retailers) {
  if (!nyMap) return;
  debounceMapRender("ny", () => updateNyInventoryMapLayer(retailers), 180);
}

function updateNyInventoryMapLayer(visibleRetailers) {
  renderInventoryCluster(nyMap, "_nyInventoryLayer", {
    retailers: visibleRetailers || getNyFilteredRows(),
    reports: communityReports,
    scopeIds: new Set(allNyRetailers.map(r => String(r.id))),
    selectedGame: selectedNyGame,
    reportFilter: nyMapReportFilter,
  });
}

function searchNyGameFilter() {
  const input = document.getElementById("nyGameFilterInput");
  const dd    = document.getElementById("nyGameFilterDropdown");
  const clear = document.getElementById("nyGameFilterClear");
  if (!input) return;
  const q = input.value.trim().toLowerCase();
  clear.style.display = q ? "" : "none";
  const matches = q ? nyGames.filter(g => g.name.toLowerCase().includes(q)) : nyGames.slice(0, 50);
  if (!matches.length) { dd.style.display = "none"; return; }
  dd.innerHTML = matches.map(g => {
    const meta = [g.price != null ? `$${g.price}` : null, g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : null].filter(Boolean).join(" · ");
    const sub = meta ? `<span style="color:var(--text-muted);font-size:.78rem">${escHtml(meta)}</span>` : "";
    return `<div class="store-option" onmousedown="selectNyGameFilter(${JSON.stringify(g.name).replace(/"/g, '&quot;')})">${escHtml(g.name)} ${sub}</div>`;
  }).join("");
  dd.style.display = "";
}

function selectNyGameFilter(name) {
  const input = document.getElementById("nyGameFilterInput");
  const dd    = document.getElementById("nyGameFilterDropdown");
  const clear = document.getElementById("nyGameFilterClear");
  input.value = name; dd.style.display = "none"; clear.style.display = "";
  const g = nyGames.find(g => g.name === name) || { name, price: null };
  selectedNyGame = { name: g.name, price: g.price ?? null };
  applyNyGameFilter();
}

function clearNyGameFilter() {
  document.getElementById("nyGameFilterInput").value = "";
  document.getElementById("nyGameFilterDropdown").style.display = "none";
  document.getElementById("nyGameFilterClear").style.display = "none";
  selectedNyGame = null;
  applyNyGameFilter();
}

function applyNyGameFilter() {
  const th = document.getElementById("nyLastReportTh");
  if (th) th.textContent = selectedNyGame ? selectedNyGame.name : "Last Report";
  buildLatestStatusFromReports();
  if (selectedNyGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) { s.has_stock ? inCount++ : outCount++; }
    document.getElementById("nyStatInStockCard").style.display = "";
    document.getElementById("nyStatOutCard").style.display = "";
    document.getElementById("nyStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("nyStatOut").textContent = outCount.toLocaleString();
    loadRetailerLatest(selectedNyGame.name);
  } else {
    document.getElementById("nyStatInStockCard").style.display = "none";
    document.getElementById("nyStatOutCard").style.display = "none";
    loadRetailerLatest();
  }
  renderNyTable();
  if (nyMapVisible) renderNyMapLayers(getNyFilteredRows());
}

// ══════════════════════════════════════════════════════════════════════════════
// VA HUNT
// ══════════════════════════════════════════════════════════════════════════════

async function loadVaRetailers() {
  try {
    const res = await fetch("/api/va/retailers?limit=30000");
    const data = await res.json();
    allVaRetailers = data.retailers || [];
    vaLoaded = true;
    const el = document.getElementById("vaStatTotal");
    if (el) el.textContent = allVaRetailers.length.toLocaleString();
    renderVaTable();
    if (!vaMapVisible) toggleVaMap();
  } catch (e) {
    const tbody = document.getElementById("vaTableBody");
    if (tbody) tbody.innerHTML =
      `<tr><td colspan="6" class="loading-cell">Failed to load VA retailers.</td></tr>`;
  }
}

function getVaFilteredRows() {
  const q             = (document.getElementById("vaSearchInput")?.value || "").toLowerCase().trim();
  const city          = (document.getElementById("vaCityInput")?.value   || "").toLowerCase().trim();
  const invFilter     = document.getElementById("vaInvFilter")?.value  || "";
  const dateFilter    = document.getElementById("vaDateFilter")?.value || "";

  vaMapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allVaRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));
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

function renderVaTable() {
  if (!vaLoaded) return;
  const myGen = ++vaRenderGen;
  _openProfileId = null;
  const rows = getVaFilteredRows();
  const checkedCount = selectedVaGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null
    ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedVaGame.name)}</strong>`
    : "";
  const countEl = document.getElementById("vaResultCount");
  if (countEl) countEl.innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("vaTableBody");
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
    return;
  }
  if (vaMapVisible) renderVaMapLayers(rows);
  lazyRenderRows({
    tbody,
    rows,
    rowFn: (r, rank) => _stateRow(r, rank, "VA"),
    getStaleFlag: () => myGen !== vaRenderGen,
  });
}

function downloadVaCsv() {
  const rows = getVaFilteredRows();
  const cols = ["name","address","city","zipCode","phone","latitude","longitude"];
  const blob = new Blob([cols.join(",") + "\n" + rows.map(r =>
    cols.map(c => { const v = String(r[c] ?? ""); return v.includes(",") || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v; }).join(",")
  ).join("\n")], { type: "text/csv" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "va_retailers.csv"; a.click(); URL.revokeObjectURL(a.href);
}

function toggleVaMap() {
  const sec = document.getElementById("vaMapSection");
  vaMapVisible = !vaMapVisible;
  sec.style.display = vaMapVisible ? "" : "none";
  if (vaMapVisible) {
    if (!vaMap) initVaMap();
    setTimeout(() => vaMap && vaMap.invalidateSize(), 50);
    renderVaMapLayers(getVaFilteredRows());
  }
}

function initVaMap() {
  vaMap = L.map("vaMap", { preferCanvas: true }).setView([37.5, -79.5], 7);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors", maxZoom: 19,
  }).addTo(vaMap);
  setupMapAutoResize(vaMap);
}

function renderVaMapLayers(retailers) {
  if (!vaMap) return;
  debounceMapRender("va", () => updateVaInventoryMapLayer(retailers), 180);
}

function updateVaInventoryMapLayer(visibleRetailers) {
  renderInventoryCluster(vaMap, "_vaInventoryLayer", {
    retailers: visibleRetailers || getVaFilteredRows(),
    reports: communityReports,
    scopeIds: new Set(allVaRetailers.map(r => String(r.id))),
    selectedGame: selectedVaGame,
    reportFilter: vaMapReportFilter,
  });
}

function searchVaGameFilter() {
  const input = document.getElementById("vaGameFilterInput");
  const dd    = document.getElementById("vaGameFilterDropdown");
  const clear = document.getElementById("vaGameFilterClear");
  if (!input) return;
  const q = input.value.trim().toLowerCase();
  clear.style.display = q ? "" : "none";
  const matches = q ? vaGames.filter(g => g.name.toLowerCase().includes(q)) : vaGames.slice(0, 50);
  if (!matches.length) { dd.style.display = "none"; return; }
  dd.innerHTML = matches.map(g => {
    const meta = [g.price != null ? `$${g.price}` : null, g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : null].filter(Boolean).join(" · ");
    const sub = meta ? `<span style="color:var(--text-muted);font-size:.78rem">${escHtml(meta)}</span>` : "";
    return `<div class="store-option" onmousedown="selectVaGameFilter(${JSON.stringify(g.name).replace(/"/g, '&quot;')})">${escHtml(g.name)} ${sub}</div>`;
  }).join("");
  dd.style.display = "";
}

function selectVaGameFilter(name) {
  const input = document.getElementById("vaGameFilterInput");
  const dd    = document.getElementById("vaGameFilterDropdown");
  const clear = document.getElementById("vaGameFilterClear");
  input.value = name; dd.style.display = "none"; clear.style.display = "";
  const g = vaGames.find(g => g.name === name) || { name, price: null };
  selectedVaGame = { name: g.name, price: g.price ?? null };
  applyVaGameFilter();
}

function clearVaGameFilter() {
  document.getElementById("vaGameFilterInput").value = "";
  document.getElementById("vaGameFilterDropdown").style.display = "none";
  document.getElementById("vaGameFilterClear").style.display = "none";
  selectedVaGame = null;
  applyVaGameFilter();
}

function applyVaGameFilter() {
  const th = document.getElementById("vaLastReportTh");
  if (th) th.textContent = selectedVaGame ? selectedVaGame.name : "Last Report";
  buildLatestStatusFromReports();
  if (selectedVaGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) { s.has_stock ? inCount++ : outCount++; }
    document.getElementById("vaStatInStockCard").style.display = "";
    document.getElementById("vaStatOutCard").style.display = "";
    document.getElementById("vaStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("vaStatOut").textContent = outCount.toLocaleString();
    loadRetailerLatest(selectedVaGame.name);
  } else {
    document.getElementById("vaStatInStockCard").style.display = "none";
    document.getElementById("vaStatOutCard").style.display = "none";
    loadRetailerLatest();
  }
  renderVaTable();
  if (vaMapVisible) renderVaMapLayers(getVaFilteredRows());
}

// ══════════════════════════════════════════════════════════════════════════════
// DC HUNT
// ══════════════════════════════════════════════════════════════════════════════

async function loadDcRetailers() {
  try {
    const res = await fetch("/api/dc/retailers?limit=30000");
    const data = await res.json();
    allDcRetailers = data.retailers || [];
    dcLoaded = true;
    const el = document.getElementById("dcStatTotal");
    if (el) el.textContent = allDcRetailers.length.toLocaleString();
    renderDcTable();
    if (!dcMapVisible) toggleDcMap();
  } catch (e) {
    const tbody = document.getElementById("dcTableBody");
    if (tbody) tbody.innerHTML =
      `<tr><td colspan="6" class="loading-cell">Failed to load DC retailers.</td></tr>`;
  }
}

function getDcFilteredRows() {
  const q             = (document.getElementById("dcSearchInput")?.value || "").toLowerCase().trim();
  const city          = (document.getElementById("dcCityInput")?.value   || "").toLowerCase().trim();
  const invFilter     = document.getElementById("dcInvFilter")?.value  || "";
  const dateFilter    = document.getElementById("dcDateFilter")?.value || "";

  dcMapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allDcRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));
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

function renderDcTable() {
  if (!dcLoaded) return;
  const myGen = ++dcRenderGen;
  _openProfileId = null;
  const rows = getDcFilteredRows();
  const checkedCount = selectedDcGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null
    ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedDcGame.name)}</strong>`
    : "";
  const countEl = document.getElementById("dcResultCount");
  if (countEl) countEl.innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("dcTableBody");
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
    return;
  }
  if (dcMapVisible) renderDcMapLayers(rows);
  lazyRenderRows({
    tbody,
    rows,
    rowFn: (r, rank) => _stateRow(r, rank, "DC"),
    getStaleFlag: () => myGen !== dcRenderGen,
  });
}

function downloadDcCsv() {
  const rows = getDcFilteredRows();
  const cols = ["name","address","city","zipCode","phone","latitude","longitude"];
  const blob = new Blob([cols.join(",") + "\n" + rows.map(r =>
    cols.map(c => { const v = String(r[c] ?? ""); return v.includes(",") || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v; }).join(",")
  ).join("\n")], { type: "text/csv" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "dc_retailers.csv"; a.click(); URL.revokeObjectURL(a.href);
}

function toggleDcMap() {
  const sec = document.getElementById("dcMapSection");
  dcMapVisible = !dcMapVisible;
  sec.style.display = dcMapVisible ? "" : "none";
  if (dcMapVisible) {
    if (!dcMap) initDcMap();
    setTimeout(() => dcMap && dcMap.invalidateSize(), 50);
    renderDcMapLayers(getDcFilteredRows());
  }
}

function initDcMap() {
  dcMap = L.map("dcMap", { preferCanvas: true }).setView([38.9, -77.03], 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors", maxZoom: 19,
  }).addTo(dcMap);
  setupMapAutoResize(dcMap);
}

function renderDcMapLayers(retailers) {
  if (!dcMap) return;
  debounceMapRender("dc", () => updateDcInventoryMapLayer(retailers), 180);
}

function updateDcInventoryMapLayer(visibleRetailers) {
  renderInventoryCluster(dcMap, "_dcInventoryLayer", {
    retailers: visibleRetailers || getDcFilteredRows(),
    reports: communityReports,
    scopeIds: new Set(allDcRetailers.map(r => String(r.id))),
    selectedGame: selectedDcGame,
    reportFilter: dcMapReportFilter,
  });
}

function searchDcGameFilter() {
  const input = document.getElementById("dcGameFilterInput");
  const dd    = document.getElementById("dcGameFilterDropdown");
  const clear = document.getElementById("dcGameFilterClear");
  if (!input) return;
  const q = input.value.trim().toLowerCase();
  clear.style.display = q ? "" : "none";
  const matches = q ? dcGames.filter(g => g.name.toLowerCase().includes(q)) : dcGames.slice(0, 50);
  if (!matches.length) { dd.style.display = "none"; return; }
  dd.innerHTML = matches.map(g => {
    const meta = [g.price != null ? `$${g.price}` : null, g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : null].filter(Boolean).join(" · ");
    const sub = meta ? `<span style="color:var(--text-muted);font-size:.78rem">${escHtml(meta)}</span>` : "";
    return `<div class="store-option" onmousedown="selectDcGameFilter(${JSON.stringify(g.name).replace(/"/g, '&quot;')})">${escHtml(g.name)} ${sub}</div>`;
  }).join("");
  dd.style.display = "";
}

function selectDcGameFilter(name) {
  const input = document.getElementById("dcGameFilterInput");
  const dd    = document.getElementById("dcGameFilterDropdown");
  const clear = document.getElementById("dcGameFilterClear");
  input.value = name; dd.style.display = "none"; clear.style.display = "";
  const g = dcGames.find(g => g.name === name) || { name, price: null };
  selectedDcGame = { name: g.name, price: g.price ?? null };
  applyDcGameFilter();
}

function clearDcGameFilter() {
  document.getElementById("dcGameFilterInput").value = "";
  document.getElementById("dcGameFilterDropdown").style.display = "none";
  document.getElementById("dcGameFilterClear").style.display = "none";
  selectedDcGame = null;
  applyDcGameFilter();
}

function applyDcGameFilter() {
  const th = document.getElementById("dcLastReportTh");
  if (th) th.textContent = selectedDcGame ? selectedDcGame.name : "Last Report";
  buildLatestStatusFromReports();
  if (selectedDcGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) { s.has_stock ? inCount++ : outCount++; }
    document.getElementById("dcStatInStockCard").style.display = "";
    document.getElementById("dcStatOutCard").style.display = "";
    document.getElementById("dcStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("dcStatOut").textContent = outCount.toLocaleString();
    loadRetailerLatest(selectedDcGame.name);
  } else {
    document.getElementById("dcStatInStockCard").style.display = "none";
    document.getElementById("dcStatOutCard").style.display = "none";
    loadRetailerLatest();
  }
  renderDcTable();
  if (dcMapVisible) renderDcMapLayers(getDcFilteredRows());
}

// ══════════════════════════════════════════════════════════════════════════════
// VT HUNT
// ══════════════════════════════════════════════════════════════════════════════

async function loadVtRetailers() {
  try {
    const res = await fetch("/api/vt/retailers?limit=30000");
    const data = await res.json();
    allVtRetailers = data.retailers || [];
    vtLoaded = true;
    const el = document.getElementById("vtStatTotal");
    if (el) el.textContent = allVtRetailers.length.toLocaleString();
    renderVtTable();
    if (vtMapVisible) renderVtMapLayers(getVtFilteredRows());
    if (!vtMapVisible) toggleVtMap();
  } catch (e) {
    const tbody = document.getElementById("vtTableBody");
    if (tbody) tbody.innerHTML =
      `<tr><td colspan="6" class="loading-cell">Failed to load VT retailers.</td></tr>`;
  }
}

function getVtFilteredRows() {
  const q             = (document.getElementById("vtSearchInput")?.value || "").toLowerCase().trim();
  const city          = (document.getElementById("vtCityInput")?.value   || "").toLowerCase().trim();
  const invFilter     = document.getElementById("vtInvFilter")?.value  || "";
  const dateFilter    = document.getElementById("vtDateFilter")?.value || "";

  vtMapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = allVtRetailers;
  if (q)    rows = rows.filter(r => r.name.toLowerCase().includes(q));
  if (city) rows = rows.filter(r => r.city.toLowerCase().includes(city));
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

function renderVtTable() {
  if (!vtLoaded) return;
  const myGen = ++vtRenderGen;
  _openProfileId = null;
  const rows = getVtFilteredRows();
  const checkedCount = selectedVtGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null
    ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedVtGame.name)}</strong>`
    : "";
  const countEl = document.getElementById("vtResultCount");
  if (countEl) countEl.innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("vtTableBody");
  if (!tbody) return;
  if (vtMapVisible) renderVtMapLayers(rows);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
    return;
  }
  lazyRenderRows({
    tbody,
    rows,
    rowFn: (r, rank) => _stateRow(r, rank, "VT"),
    getStaleFlag: () => myGen !== vtRenderGen,
  });
}

function downloadVtCsv() {
  const rows = getVtFilteredRows();
  const cols = ["name","address","city","zipCode","phone","latitude","longitude"];
  const blob = new Blob([cols.join(",") + "\n" + rows.map(r =>
    cols.map(c => { const v = String(r[c] ?? ""); return v.includes(",") || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v; }).join(",")
  ).join("\n")], { type: "text/csv" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "vt_retailers.csv"; a.click(); URL.revokeObjectURL(a.href);
}

function toggleVtMap() {
  const sec = document.getElementById("vtMapSection");
  vtMapVisible = !vtMapVisible;
  sec.style.display = vtMapVisible ? "" : "none";
  if (vtMapVisible) {
    if (!vtMap) initVtMap();
    setTimeout(() => vtMap && vtMap.invalidateSize(), 50);
    renderVtMapLayers(getVtFilteredRows());
  }
}

function initVtMap() {
  vtMap = L.map("vtMap", { preferCanvas: true }).setView([44.0, -72.7], 8);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors", maxZoom: 19,
  }).addTo(vtMap);
  setupMapAutoResize(vtMap);
}

function renderVtMapLayers(retailers) {
  if (!vtMap) return;
  debounceMapRender("vt", () => updateVtInventoryMapLayer(retailers), 180);
}

function updateVtInventoryMapLayer(visibleRetailers) {
  renderInventoryCluster(vtMap, "_vtInventoryLayer", {
    retailers: visibleRetailers || getVtFilteredRows(),
    reports: communityReports,
    scopeIds: new Set(allVtRetailers.map(r => String(r.id))),
    selectedGame: selectedVtGame,
    reportFilter: vtMapReportFilter,
  });
}

function searchVtGameFilter() {
  const input = document.getElementById("vtGameFilterInput");
  const dd    = document.getElementById("vtGameFilterDropdown");
  const clear = document.getElementById("vtGameFilterClear");
  if (!input) return;
  const q = input.value.trim().toLowerCase();
  clear.style.display = q ? "" : "none";
  const matches = q ? vtGames.filter(g => g.name.toLowerCase().includes(q)) : vtGames.slice(0, 50);
  if (!matches.length) { dd.style.display = "none"; return; }
  dd.innerHTML = matches.map(g => {
    const meta = [g.price != null ? `$${g.price}` : null, g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : null].filter(Boolean).join(" · ");
    const sub = meta ? `<span style="color:var(--text-muted);font-size:.78rem">${escHtml(meta)}</span>` : "";
    return `<div class="store-option" onmousedown="selectVtGameFilter(${JSON.stringify(g.name).replace(/"/g, '&quot;')})">${escHtml(g.name)} ${sub}</div>`;
  }).join("");
  dd.style.display = "";
}

function selectVtGameFilter(name) {
  const input = document.getElementById("vtGameFilterInput");
  const dd    = document.getElementById("vtGameFilterDropdown");
  const clear = document.getElementById("vtGameFilterClear");
  input.value = name; dd.style.display = "none"; clear.style.display = "";
  const g = vtGames.find(g => g.name === name) || { name, price: null };
  selectedVtGame = { name: g.name, price: g.price ?? null };
  applyVtGameFilter();
}

function clearVtGameFilter() {
  document.getElementById("vtGameFilterInput").value = "";
  document.getElementById("vtGameFilterDropdown").style.display = "none";
  document.getElementById("vtGameFilterClear").style.display = "none";
  selectedVtGame = null;
  applyVtGameFilter();
}

function applyVtGameFilter() {
  const th = document.getElementById("vtLastReportTh");
  if (th) th.textContent = selectedVtGame ? selectedVtGame.name : "Last Report";
  buildLatestStatusFromReports();
  if (selectedVtGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) { s.has_stock ? inCount++ : outCount++; }
    document.getElementById("vtStatInStockCard").style.display = "";
    document.getElementById("vtStatOutCard").style.display = "";
    document.getElementById("vtStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("vtStatOut").textContent = outCount.toLocaleString();
    loadRetailerLatest(selectedVtGame.name);
  } else {
    document.getElementById("vtStatInStockCard").style.display = "none";
    document.getElementById("vtStatOutCard").style.display = "none";
    loadRetailerLatest();
  }
  renderVtTable();
  if (vtMapVisible) renderVtMapLayers(getVtFilteredRows());
}

// ══════════════════════════════════════════════════════════════════════════════
// GENERIC LIVE-STATE HUNT (CO, CT, ME, MI, NJ, OR, SC, WA)
// One reusable console driven by `currentGenState`. Data sourced from
// /api/state/{code}/retailers (backed by the state_retailers table).
// ══════════════════════════════════════════════════════════════════════════════

const GEN_STATES = {
  AR: { name: "Arkansas",       center: [34.8, -92.5],  zoom: 7 },
  CA: { name: "California",     center: [37.2, -119.5], zoom: 6 },
  CO: { name: "Colorado",       center: [39.0, -105.5], zoom: 7 },
  CT: { name: "Connecticut",    center: [41.6, -72.7],  zoom: 9 },
  ID: { name: "Idaho",          center: [44.5, -114.5], zoom: 6 },
  IN: { name: "Indiana",        center: [39.9, -86.3],  zoom: 7 },
  KS: { name: "Kansas",         center: [38.5, -98.5],  zoom: 7 },
  KY: { name: "Kentucky",       center: [37.5, -85.0],  zoom: 7 },
  LA: { name: "Louisiana",      center: [31.0, -92.0],  zoom: 7 },
  MD: { name: "Maryland",       center: [39.0, -76.8],  zoom: 8 },
  ME: { name: "Maine",          center: [45.3, -69.0],  zoom: 7 },
  MI: { name: "Michigan",       center: [44.3, -85.6],  zoom: 7 },
  MO: { name: "Missouri",       center: [38.5, -92.5],  zoom: 7 },
  MS: { name: "Mississippi",    center: [32.8, -89.5],  zoom: 7 },
  NC: { name: "North Carolina", center: [35.5, -79.5],  zoom: 7 },
  NE: { name: "Nebraska",       center: [41.5, -99.5],  zoom: 7 },
  NH: { name: "New Hampshire",  center: [43.9, -71.6],  zoom: 8 },
  NJ: { name: "New Jersey",     center: [40.2, -74.7],  zoom: 8 },
  OH: { name: "Ohio",           center: [40.3, -82.7],  zoom: 7 },
  OK: { name: "Oklahoma",       center: [35.5, -97.5],  zoom: 7 },
  OR: { name: "Oregon",         center: [43.9, -120.5], zoom: 7 },
  PA: { name: "Pennsylvania",   center: [40.9, -77.5],  zoom: 7 },
  SC: { name: "South Carolina", center: [33.8, -81.0],  zoom: 8 },
  TX: { name: "Texas",          center: [31.5, -99.0],  zoom: 6 },
  WA: { name: "Washington",     center: [47.4, -120.7], zoom: 7 },
  WI: { name: "Wisconsin",      center: [44.5, -89.5],  zoom: 7 },
};

let allGenRetailers = {};   // { CODE: [...] }
let genGames        = {};   // { CODE: [...] }
let genLoaded       = {};   // { CODE: true }
let selectedGenGame = null;
let currentGenState = null;
let genMap          = null;
let genMapVisible   = false;
let genMapReportFilter = "all";
let genRenderGen    = 0;

function _currentGenList() { return currentGenState ? (allGenRetailers[currentGenState] || []) : []; }
function _currentGenGames() { return currentGenState ? (genGames[currentGenState] || []) : []; }

async function loadGenRetailers(code) {
  currentGenState = code;
  selectedGenGame = null;
  if (genMapVisible && genMap) {
    const cfg = GEN_STATES[code];
    if (cfg) genMap.setView(cfg.center, cfg.zoom);
  }
  // Reset filter inputs when switching states
  ["genGameFilterInput", "genSearchInput", "genCityInput"].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = "";
  });
  ["genInvFilter", "genDateFilter"].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = "";
  });
  const clr = document.getElementById("genGameFilterClear"); if (clr) clr.style.display = "none";
  const inStockCard = document.getElementById("genStatInStockCard"); if (inStockCard) inStockCard.style.display = "none";
  const outCard = document.getElementById("genStatOutCard"); if (outCard) outCard.style.display = "none";

  if (genLoaded[code]) {
    const totalEl = document.getElementById("genStatTotal");
    if (totalEl) totalEl.textContent = (allGenRetailers[code] || []).length.toLocaleString();
    _syncGenMapButton();
    renderGenTable();
    _autoShowGenMap();
    return;
  }
  try {
    const totalEl = document.getElementById("genStatTotal");
    if (totalEl) totalEl.textContent = "—";
    const tbody = document.getElementById("genTableBody");
    if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">Loading ${GEN_STATES[code]?.name || code} retailers…</td></tr>`;
    const res = await fetch(`/api/state/${encodeURIComponent(code)}/retailers?limit=30000`);
    const data = await res.json();
    allGenRetailers[code] = data.retailers || [];
    genLoaded[code] = true;
    if (totalEl) totalEl.textContent = allGenRetailers[code].length.toLocaleString();
    _syncGenMapButton();
    renderGenTable();
    _autoShowGenMap();
  } catch (e) {
    const tbody = document.getElementById("genTableBody");
    if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">Failed to load retailers.</td></tr>`;
  }
}

// Hide the Map button when the current state has no geo-coded retailers
// (scraper missing or broken — CT today). Avoids opening an empty map.
function _syncGenMapButton() {
  const btn = document.getElementById("genViewMapBtn");
  if (!btn) return;
  const list = _currentGenList();
  const hasGeo = list.some(r => r.latitude != null && r.longitude != null);
  btn.style.display = hasGeo ? "" : "none";
  if (!hasGeo && genMapVisible) {
    const sec = document.getElementById("genMapSection");
    if (sec) sec.style.display = "none";
    genMapVisible = false;
  }
}

function _autoShowGenMap() {
  if (genMapVisible) return;
  const hasGeo = _currentGenList().some(r => r.latitude != null && r.longitude != null);
  if (hasGeo) toggleGenMap();
}

function getGenFilteredRows() {
  if (!currentGenState) return [];
  const q          = (document.getElementById("genSearchInput")?.value || "").toLowerCase().trim();
  const city       = (document.getElementById("genCityInput")?.value   || "").toLowerCase().trim();
  const invFilter  = document.getElementById("genInvFilter")?.value  || "";
  const dateFilter = document.getElementById("genDateFilter")?.value || "";

  genMapReportFilter = (invFilter === "in" || invFilter === "out") ? invFilter : "all";

  let rows = _currentGenList();
  if (q)    rows = rows.filter(r => (r.name || "").toLowerCase().includes(q));
  if (city) rows = rows.filter(r => (r.city || "").toLowerCase().includes(city));
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

function renderGenTable() {
  if (!currentGenState || !genLoaded[currentGenState]) return;
  const myGen = ++genRenderGen;
  _openProfileId = null;
  const rows = getGenFilteredRows();
  const checkedCount = selectedGenGame ? Object.keys(retailerLatestStatus).length : null;
  const countSuffix = checkedCount != null
    ? ` · <strong style="color:var(--grape)">${checkedCount} checked for ${escHtml(selectedGenGame.name)}</strong>`
    : "";
  const countEl = document.getElementById("genResultCount");
  if (countEl) countEl.innerHTML = `${rows.length.toLocaleString()} retailers${countSuffix}`;
  const tbody = document.getElementById("genTableBody");
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="loading-cell">No retailers match.</td></tr>`;
    return;
  }
  if (genMapVisible) renderGenMapLayers(rows);
  const code = currentGenState;
  lazyRenderRows({
    tbody,
    rows,
    rowFn: (r, rank) => _stateRow(r, rank, code),
    getStaleFlag: () => myGen !== genRenderGen,
  });
}

function downloadGenCsv() {
  if (!currentGenState) return;
  const rows = getGenFilteredRows();
  const cols = ["name","address","city","zipCode","phone","latitude","longitude"];
  const blob = new Blob([cols.join(",") + "\n" + rows.map(r =>
    cols.map(c => { const v = String(r[c] ?? ""); return v.includes(",") || v.includes('"') ? `"${v.replace(/"/g,'""')}"` : v; }).join(",")
  ).join("\n")], { type: "text/csv" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = `${currentGenState.toLowerCase()}_retailers.csv`; a.click(); URL.revokeObjectURL(a.href);
}

function toggleGenMap() {
  const sec = document.getElementById("genMapSection");
  genMapVisible = !genMapVisible;
  sec.style.display = genMapVisible ? "" : "none";
  if (genMapVisible) {
    if (!genMap) initGenMap();
    const cfg = GEN_STATES[currentGenState];
    if (cfg && genMap) genMap.setView(cfg.center, cfg.zoom);
    setTimeout(() => genMap && genMap.invalidateSize(), 50);
    renderGenMapLayers(getGenFilteredRows());
  }
}

function initGenMap() {
  const cfg = GEN_STATES[currentGenState] || { center: [39.5, -98.35], zoom: 4 };
  genMap = L.map("genMap", { preferCanvas: true }).setView(cfg.center, cfg.zoom);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors", maxZoom: 19,
  }).addTo(genMap);
  setupMapAutoResize(genMap);
}

function renderGenMapLayers(retailers) {
  if (!genMap) return;
  debounceMapRender("gen", () => updateGenInventoryMapLayer(retailers), 180);
}

function updateGenInventoryMapLayer(visibleRetailers) {
  renderInventoryCluster(genMap, "_genInventoryLayer", {
    retailers: visibleRetailers || getGenFilteredRows(),
    reports: communityReports,
    scopeIds: new Set(_currentGenList().map(r => String(r.id))),
    selectedGame: selectedGenGame,
    reportFilter: genMapReportFilter,
  });
}

function searchGenGameFilter() {
  const input = document.getElementById("genGameFilterInput");
  const dd    = document.getElementById("genGameFilterDropdown");
  const clear = document.getElementById("genGameFilterClear");
  if (!input) return;
  const q = input.value.trim().toLowerCase();
  clear.style.display = q ? "" : "none";
  const games = _currentGenGames();
  const matches = q ? games.filter(g => g.name.toLowerCase().includes(q)) : games.slice(0, 50);
  if (!matches.length) { dd.style.display = "none"; return; }
  dd.innerHTML = matches.map(g => {
    const meta = [g.price != null ? `$${g.price}` : null, g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : null].filter(Boolean).join(" · ");
    const sub = meta ? `<span style="color:var(--text-muted);font-size:.78rem">${escHtml(meta)}</span>` : "";
    return `<div class="store-option" onmousedown="selectGenGameFilter(${JSON.stringify(g.name).replace(/"/g, '&quot;')})">${escHtml(g.name)} ${sub}</div>`;
  }).join("");
  dd.style.display = "";
}

function selectGenGameFilter(name) {
  const input = document.getElementById("genGameFilterInput");
  const dd    = document.getElementById("genGameFilterDropdown");
  const clear = document.getElementById("genGameFilterClear");
  input.value = name; dd.style.display = "none"; clear.style.display = "";
  const g = _currentGenGames().find(g => g.name === name) || { name, price: null };
  selectedGenGame = { name: g.name, price: g.price ?? null };
  applyGenGameFilter();
}

function clearGenGameFilter() {
  document.getElementById("genGameFilterInput").value = "";
  document.getElementById("genGameFilterDropdown").style.display = "none";
  document.getElementById("genGameFilterClear").style.display = "none";
  selectedGenGame = null;
  applyGenGameFilter();
}

function applyGenGameFilter() {
  const th = document.getElementById("genLastReportTh");
  if (th) th.textContent = selectedGenGame ? selectedGenGame.name : "Last Report";
  buildLatestStatusFromReports();
  if (selectedGenGame) {
    let inCount = 0, outCount = 0;
    for (const s of Object.values(retailerLatestStatus)) { s.has_stock ? inCount++ : outCount++; }
    document.getElementById("genStatInStockCard").style.display = "";
    document.getElementById("genStatOutCard").style.display = "";
    document.getElementById("genStatInStock").textContent = inCount.toLocaleString();
    document.getElementById("genStatOut").textContent = outCount.toLocaleString();
    loadRetailerLatest(selectedGenGame.name);
  } else {
    document.getElementById("genStatInStockCard").style.display = "none";
    document.getElementById("genStatOutCard").style.display = "none";
    loadRetailerLatest();
  }
  renderGenTable();
  if (genMapVisible) renderGenMapLayers(getGenFilteredRows());
}

// ── Shared table row renderer for simple states (FL/GA/NY) ───────────────────

function _stateRow(r, rank, stateCode) {
  const addr = encodeURIComponent(`${r.name}, ${r.address}, ${r.city}, ${stateCode} ${r.zipCode}`);
  const mapsUrl       = `https://www.google.com/maps/search/?api=1&query=${addr}`;
  const searchUrl     = `https://www.google.com/search?q=${encodeURIComponent(r.name + ' ' + r.city + ' ' + stateCode + ' lottery')}`;
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
    <td><strong>${escHtml(r.name)}</strong><br><span style="font-size:.78rem;color:var(--text-muted)">${escHtml(r.address)}</span><span class="report-count-badge" id="rbadge-${rid}" style="display:none"></span></td>
    <td>${escHtml(r.city)}</td>
    <td>${escHtml(r.zipCode)}</td>
    <td class="last-report-cell" data-rid="${rid}">${lastReportCellHtml(rid)}</td>
    <td class="links-cell" onclick="event.stopPropagation()">${links}</td>
  </tr>`;
}

// ── My Plays ──────────────────────────────────────────────────────────────────

let _allPlays = [];

async function loadPlays() {
  if (!_currentUser) return;
  try {
    const res = await protectedFetch("/api/plays");
    if (!res.ok) return;
    const data = await res.json();
    _allPlays = data.plays || [];
    renderPlays();
  } catch (e) {
    console.error("loadPlays error", e);
  }
}

function renderPlays() {
  const plays = _allPlays;

  // ── Stats ──
  const totalSpent = plays.reduce((s, p) => s + p.price_paid, 0);
  const totalWon   = plays.reduce((s, p) => s + p.prize_won,  0);
  const net        = totalWon - totalSpent;
  const roi        = totalSpent > 0 ? (totalWon / totalSpent) * 100 : null;

  document.getElementById("playStatSpent").textContent = plays.length ? "$" + totalSpent.toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:2}) : "—";
  document.getElementById("playStatWon").textContent   = plays.length ? "$" + totalWon.toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:2}) : "—";
  document.getElementById("playStatNet").textContent   = plays.length ? (net >= 0 ? "+" : "") + "$" + Math.abs(net).toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:2}) : "—";
  document.getElementById("playStatNet").style.color   = plays.length ? (net >= 0 ? "var(--green)" : "var(--red)") : "";
  document.getElementById("playStatRoi").textContent   = roi !== null ? roi.toFixed(1) + "%" : "—";
  document.getElementById("playStatRoi").style.color   = roi !== null ? (roi >= 100 ? "var(--green)" : "var(--red)") : "";

  // ── Log count ──
  document.getElementById("playsLogCount").textContent = plays.length
    ? `${plays.length} ticket${plays.length !== 1 ? "s" : ""} logged`
    : "No plays yet";

  // ── Log table ──
  const tbody = document.getElementById("playsLogBody");
  if (!plays.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="loading-cell">Log a ticket above to get started.</td></tr>`;
  } else {
    tbody.innerHTML = plays.map(p => {
      const date = p.played_at ? new Date(p.played_at).toLocaleDateString("en-US", {month:"short", day:"numeric", year:"numeric"}) : "—";
      const netVal = p.prize_won - p.price_paid;
      const netStr = (netVal >= 0 ? "+" : "") + "$" + Math.abs(netVal).toFixed(2);
      const netColor = netVal >= 0 ? "var(--green)" : "var(--red)";
      return `<tr>
        <td style="font-size:.83rem;color:var(--text-muted)">${date}</td>
        <td><strong>${escHtml(p.game_name)}</strong></td>
        <td>${p.state_code ? `<span class="state-badge">${escHtml(p.state_code)}</span>` : "—"}</td>
        <td>$${p.price_paid.toFixed(2)}</td>
        <td style="color:${p.prize_won > 0 ? "var(--green)" : "var(--text-muted)"}">$${p.prize_won.toFixed(2)}</td>
        <td style="color:${netColor};font-weight:700">${netStr}</td>
        <td style="font-size:.8rem;color:var(--text-muted)">${p.retailer_name ? escHtml(p.retailer_name) : "—"}</td>
        <td><button class="plays-delete-btn" onclick="deletePlay(${p.id})" title="Delete">✕</button></td>
      </tr>`;
    }).join("");
  }

  // ── Breakdown by game ──
  if (plays.length) {
    const byGame = {};
    plays.forEach(p => {
      const key = (p.game_name || "?").toLowerCase();
      if (!byGame[key]) byGame[key] = { name: p.game_name, state: p.state_code || "", spent: 0, won: 0, count: 0 };
      byGame[key].spent += p.price_paid;
      byGame[key].won   += p.prize_won;
      byGame[key].count += 1;
    });
    const rows = Object.values(byGame).sort((a, b) => (b.won - b.spent) - (a.won - a.spent));
    document.getElementById("playsBreakdownBody").innerHTML = rows.map(g => {
      const n = g.won - g.spent;
      const r = g.spent > 0 ? (g.won / g.spent * 100).toFixed(1) + "%" : "—";
      const nc = n >= 0 ? "var(--green)" : "var(--red)";
      return `<tr>
        <td><strong>${escHtml(g.name)}</strong></td>
        <td>${g.state ? `<span class="state-badge">${escHtml(g.state)}</span>` : "—"}</td>
        <td>${g.count}</td>
        <td>$${g.spent.toFixed(2)}</td>
        <td style="color:${g.won > 0 ? "var(--green)" : "var(--text-muted)"}">$${g.won.toFixed(2)}</td>
        <td style="color:${nc};font-weight:700">${(n >= 0 ? "+" : "") + "$" + Math.abs(n).toFixed(2)}</td>
        <td style="color:${nc}">${r}</td>
      </tr>`;
    }).join("");
    document.getElementById("playsBreakdownSection").style.display = "";
  } else {
    document.getElementById("playsBreakdownSection").style.display = "none";
  }

  // ── 30-day chart ──
  renderPlaysChart(plays);
}

function renderPlaysChart(plays) {
  const chartSection = document.getElementById("playsChartSection");
  if (plays.length < 2) { chartSection.style.display = "none"; return; }

  const now = new Date();
  const since = new Date(now); since.setDate(since.getDate() - 29);

  // bucket by day, compute cumulative net
  const dayMap = {};
  plays.forEach(p => {
    if (!p.played_at) return;
    const d = new Date(p.played_at);
    if (d < since) return;
    const key = d.toISOString().slice(0, 10);
    dayMap[key] = (dayMap[key] || 0) + (p.prize_won - p.price_paid);
  });

  // build 30-day series
  const points = [];
  let cum = 0;
  for (let i = 0; i < 30; i++) {
    const d = new Date(since); d.setDate(d.getDate() + i);
    const key = d.toISOString().slice(0, 10);
    cum += (dayMap[key] || 0);
    points.push(cum);
  }

  if (points.every(v => v === 0)) { chartSection.style.display = "none"; return; }
  chartSection.style.display = "";

  const svg = document.getElementById("playsChart");
  const W = 700, H = 120, padL = 52, padR = 12, padT = 12, padB = 24;
  const iW = W - padL - padR, iH = H - padT - padB;
  const minV = Math.min(0, ...points), maxV = Math.max(0, ...points);
  const range = maxV - minV || 1;

  const xs = points.map((_, i) => padL + (i / (points.length - 1)) * iW);
  const ys = points.map(v => padT + iH - ((v - minV) / range) * iH);
  const zeroY = padT + iH - ((0 - minV) / range) * iH;

  const pathD = xs.map((x, i) => (i === 0 ? `M${x},${ys[i]}` : `L${x},${ys[i]}`)).join(" ");
  const areaD = `${pathD} L${xs[xs.length-1]},${zeroY} L${xs[0]},${zeroY} Z`;

  const lastVal = points[points.length - 1];
  const lineColor = lastVal >= 0 ? "var(--green)" : "var(--red)";
  const areaColor = lastVal >= 0 ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.10)";

  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `
    <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT+iH}" stroke="var(--border)" stroke-width="1"/>
    <line x1="${padL}" y1="${zeroY}" x2="${padL+iW}" y2="${zeroY}" stroke="var(--border)" stroke-width="1" stroke-dasharray="3,3"/>
    <text x="${padL-6}" y="${padT+4}" text-anchor="end" font-size="9" fill="var(--text-muted)">$${maxV >= 0 ? "+" : ""}${maxV.toFixed(0)}</text>
    <text x="${padL-6}" y="${padT+iH+4}" text-anchor="end" font-size="9" fill="var(--text-muted)">${minV < 0 ? "-$" + Math.abs(minV).toFixed(0) : "$0"}</text>
    <text x="${padL-6}" y="${zeroY+4}" text-anchor="end" font-size="9" fill="var(--text-muted)">$0</text>
    <path d="${areaD}" fill="${areaColor}"/>
    <path d="${pathD}" fill="none" stroke="${lineColor}" stroke-width="2" stroke-linejoin="round"/>
    <text x="${padL}" y="${H-4}" font-size="9" fill="var(--text-muted)">30 days ago</text>
    <text x="${padL+iW}" y="${H-4}" text-anchor="end" font-size="9" fill="var(--text-muted)">Today</text>
  `;
}

function _plGamesForState(state) {
  if (!state) return [];
  const pool = (allGamesUnfiltered && allGamesUnfiltered.length)
    ? allGamesUnfiltered
    : (allGames || []);
  return pool
    .filter(g => g.state_code === state)
    .slice()
    .sort((a, b) => (b.return_pct || 0) - (a.return_pct || 0));
}

function _plTodayStr() {
  return new Date().toISOString().slice(0, 10);
}

function _plRowTemplate(idx) {
  const today = _plTodayStr();
  return `
    <div class="plays-log-row" data-row-idx="${idx}">
      <div class="filter-group" style="flex:2;min-width:180px">
        <label>Game *</label>
        <select class="pl-game" onchange="onPlGameSelect(this)">
          <option value="">Pick a state first…</option>
        </select>
      </div>
      <div class="filter-group" style="width:90px">
        <label>Price ($)</label>
        <input type="number" class="pl-price" placeholder="30" min="1" max="100" step="1">
      </div>
      <div class="filter-group" style="width:110px">
        <label>Prize Won ($)</label>
        <input type="number" class="pl-prize" placeholder="0" min="0" step="1" value="0">
      </div>
      <div class="filter-group" style="flex:1;min-width:130px">
        <label>Store <span style="color:var(--text-muted);font-weight:400">(optional)</span></label>
        <input type="text" class="pl-store" placeholder="Store name…">
      </div>
      <div class="filter-group" style="width:130px">
        <label>Date</label>
        <input type="date" class="pl-date" value="${today}">
      </div>
      <button type="button" class="pl-remove-btn" onclick="removePlRow(this)" title="Remove ticket" aria-label="Remove ticket">✕</button>
    </div>`;
}

function _plGameOptionsHtml(state) {
  const games = _plGamesForState(state);
  if (!state) return `<option value="">Pick a state first…</option>`;
  if (!games.length) return `<option value="">No games available</option>`;
  return `<option value="">Select a game…</option>` + games.map(g => {
    const ret = g.return_pct != null ? `${g.return_pct.toFixed(1)}%` : "—";
    const price = g.price != null ? `$${g.price}` : "—";
    return `<option value="${g.id}" data-price="${g.price ?? ''}" data-name="${escHtml(g.name)}">
      ${escHtml(g.name)} — ${ret} · ${price}
    </option>`;
  }).join("");
}

function initPlStateSelect() {
  const sel = document.getElementById("plState");
  if (!sel) return;
  const pool = (allGamesUnfiltered && allGamesUnfiltered.length)
    ? allGamesUnfiltered
    : (allGames || []);
  const states = Array.from(new Set(pool.map(g => g.state_code).filter(Boolean))).sort();
  const prev = sel.value;
  sel.innerHTML = `<option value="">Select state…</option>` +
    states.map(s => `<option value="${s}">${s}</option>`).join("");
  if (prev && states.includes(prev)) sel.value = prev;
}

function onPlStateChange() {
  const state = document.getElementById("plState").value;
  const optsHtml = _plGameOptionsHtml(state);
  document.querySelectorAll("#plRows .pl-game").forEach(sel => {
    sel.innerHTML = optsHtml;
  });
}

function onPlGameSelect(selectEl) {
  const opt = selectEl.options[selectEl.selectedIndex];
  if (!opt || !opt.value) return;
  const row = selectEl.closest(".plays-log-row");
  const priceInput = row.querySelector(".pl-price");
  const price = opt.getAttribute("data-price");
  if (priceInput && !priceInput.value && price) priceInput.value = price;
}

let _plRowCounter = 0;
function addPlRow() {
  const container = document.getElementById("plRows");
  if (!container) return;
  const idx = ++_plRowCounter;
  container.insertAdjacentHTML("beforeend", _plRowTemplate(idx));
  const newRow = container.lastElementChild;
  const sel = newRow.querySelector(".pl-game");
  if (sel) sel.innerHTML = _plGameOptionsHtml(document.getElementById("plState").value);
  // Hide remove btn when only one row
  _updatePlRemoveVisibility();
}

function removePlRow(btn) {
  const row = btn.closest(".plays-log-row");
  if (!row) return;
  const container = document.getElementById("plRows");
  if (container.children.length <= 1) return; // keep at least one
  row.remove();
  _updatePlRemoveVisibility();
}

function _updatePlRemoveVisibility() {
  const rows = document.querySelectorAll("#plRows .plays-log-row");
  const showRemove = rows.length > 1;
  rows.forEach(r => {
    const btn = r.querySelector(".pl-remove-btn");
    if (btn) btn.style.display = showRemove ? "" : "none";
  });
}

function resetPlForm() {
  document.getElementById("plRows").innerHTML = "";
  _plRowCounter = 0;
  addPlRow();
}

async function logPlays() {
  const state = document.getElementById("plState").value || null;
  const rows = Array.from(document.querySelectorAll("#plRows .plays-log-row"));
  const msgEl = document.getElementById("plMsg");
  msgEl.style.display = "none";

  if (!state) { showPlMsg("Select a state first", true); return; }

  const payloads = [];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const sel = row.querySelector(".pl-game");
    const opt = sel.options[sel.selectedIndex];
    const gameDbId = sel.value ? parseInt(sel.value) : null;
    const gameName = opt && opt.getAttribute("data-name") ? opt.getAttribute("data-name") : "";
    const price = parseFloat(row.querySelector(".pl-price").value);
    const prize = parseFloat(row.querySelector(".pl-prize").value) || 0;
    const store = row.querySelector(".pl-store").value.trim();
    const dateVal = row.querySelector(".pl-date").value;

    if (!gameName) { showPlMsg(`Ticket ${i+1}: pick a game`, true); return; }
    if (!price || price <= 0) { showPlMsg(`Ticket ${i+1}: enter a valid price`, true); return; }

    payloads.push({
      game_name: gameName,
      game_db_id: gameDbId,
      state_code: state,
      price_paid: price,
      prize_won: prize,
      retailer_name: store || null,
      played_at: dateVal ? dateVal + "T12:00:00" : null,
    });
  }

  const btn = document.getElementById("plLogBtn");
  btn.disabled = true;
  try {
    const results = await Promise.allSettled(payloads.map(body =>
      protectedFetch("/api/plays", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(r => r.ok ? r.json() : r.json().then(d => Promise.reject(d)))
    ));
    const okCount = results.filter(r => r.status === "fulfilled").length;
    const failCount = results.length - okCount;
    if (failCount === 0) {
      showPlMsg(`Logged ${okCount} ticket${okCount === 1 ? "" : "s"}!`, false);
      resetPlForm();
    } else if (okCount === 0) {
      showPlMsg("Failed to log tickets", true);
    } else {
      showPlMsg(`Logged ${okCount}, ${failCount} failed`, true);
    }
    await loadPlays();
    setTimeout(() => { msgEl.style.display = "none"; }, 3000);
  } catch(e) {
    showPlMsg("Network error", true);
  } finally {
    btn.disabled = false;
  }
}

async function deletePlay(id) {
  if (!confirm("Remove this play from your log?")) return;
  try {
    const res = await protectedFetch(`/api/plays/${id}`, { method: "DELETE" });
    if (res.ok) await loadPlays();
  } catch(e) { /* silent */ }
}

function showPlMsg(msg, isErr) {
  const el = document.getElementById("plMsg");
  el.textContent = msg;
  el.style.display = "";
  el.style.background = isErr ? "var(--red-dim)" : "var(--green-dim)";
  el.style.color = isErr ? "var(--red)" : "var(--green)";
  el.style.border = isErr ? "1px solid rgba(239,68,68,.3)" : "1px solid rgba(34,197,94,.3)";
}

// Init form on first plays-tab open
(function() {
  const orig = window.switchTab;
  window.switchTab = function(name) {
    if (name === "plays") {
      initPlStateSelect();
      const rows = document.getElementById("plRows");
      if (rows && rows.children.length === 0) addPlRow();
    }
    return orig(name);
  };
})();

// ── Admin Health Tab ───────────────────────────────────────────────────────

function _apTimeAgo(iso) {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function _apPctBar(val) {
  const cls = val >= 80 ? "g" : val >= 50 ? "y" : "r";
  return `<div class="ap-bar-wrap ${cls}">
    <div class="ap-bar"><div class="ap-bar-fill" style="width:${Math.min(val,100)}%"></div></div>
    <span class="ap-pct">${val}%</span>
  </div>`;
}

function _apBadge(s) {
  if (!s.last_scrape_at && s.games_in_db === 0)
    return `<span class="ap-badge none">Never Run</span>`;
  if (s.games_in_db === 0) {
    if (s.last_scrape_success === false)
      return `<span class="ap-badge error"><span class="ap-badge-dot"></span>Error</span>`;
    return `<span class="ap-badge warn"><span class="ap-badge-dot"></span>No Data</span>`;
  }
  if (s.last_scrape_success === false)
    return `<span class="ap-badge error"><span class="ap-badge-dot"></span>Error</span>`;
  if (s.ev_pct < 50 || s.image_pct < 50)
    return `<span class="ap-badge warn"><span class="ap-badge-dot"></span>Partial</span>`;
  return `<span class="ap-badge ok"><span class="ap-badge-dot"></span>OK</span>`;
}

function _apRetailerCell(s) {
  if (!s.has_retailer_scraper) return `<span class="ap-ret-none">—</span>`;
  if (!s.retailer_last_scraped) return `<span class="ap-ret-stale">Never</span>`;
  const ageDays = Math.floor((Date.now() - new Date(s.retailer_last_scraped).getTime()) / 86400000);
  const cls = ageDays > 35 ? "ap-ret-stale" : "ap-ret-ok";
  const count = s.retailer_count != null
    ? ` <span class="ap-ret-count">(${s.retailer_count.toLocaleString()})</span>` : "";
  return `<span class="${cls}">${_apTimeAgo(s.retailer_last_scraped)}</span>${count}`;
}


// ── Settings tab ──────────────────────────────────────────────────────────────
function populateSettingsTab() {
  const huntSel = document.getElementById("prefDefaultHuntState");
  if (huntSel) huntSel.value = _prefs.defaultHuntState || "MA";

  const evSel = document.getElementById("prefEvDefaultState");
  if (evSel) {
    if (evSel.options.length <= 1 && states.length) {
      states.forEach(s => {
        const opt = document.createElement("option");
        opt.value = s.state_code;
        opt.textContent = s.state_name;
        evSel.appendChild(opt);
      });
    }
    evSel.value = _prefs.evDefaultState || "";
  }
}
