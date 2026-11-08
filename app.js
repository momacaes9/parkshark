// ParkShark Duke - main page. Real data only (data/main.json, built by scripts/build_main.py):
// Duke official facility data > OpenStreetMap, Jacob's Tier 3 model for estimated occupancy,
// live sensor rows from Supabase when configured. Unknown fields render as "Not published".
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const NP = '<span class="np">Not published</span>';
  const WALK_M_PER_MIN = 80;   // ~3 mph
  const loadScript = (src) => new Promise((ok, fail) => {
    const s = document.createElement("script"); s.src = src; s.async = true; s.onload = ok; s.onerror = () => fail(new Error("Failed to load " + src));
    document.head.appendChild(s);
  });
  const loadCSS = (href) => { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l); return l; };

  const S = {
    data: null, layouts: null, cfg: {}, items: [], byId: new Map(),
    filters: new Set(), dest: null, home: null, me: null, selected: null,
    live: { lot: {}, spot: {}, rows: 0 }, showAll: false,
  };

  // ---------------------------------------------------------------- guided presentation (isolated, browser-only simulation)
  const demo = { active: false, step: 0, options: [], saved: null };
  function demoOption(it) { return demo.active && demo.options.find((o) => o.id === it.id); }
  function demoWinner() { return ParkSharkDemo.rank(demo.options, demo.step, walkMin)[0]; }
  function renderDemo() {
    const winner = demoWinner();
    const copy = [
      ["Park before you start circling.", "You’re visiting Wilkinson at 9:30 a.m. for two hours. No Duke permit. Let’s compare three visitor options."],
      ["One clear recommendation.", "Bryan Center is the closest of these three options with spaces available in this scenario. Compare the cost and walk below."],
      ["Full lot? A backup is ready.", "A simulated sensor update fills Bryan Center. Garage IV’s visitor area becomes the closest available option in this scenario."],
      ["Less circling. More certainty.", "One destination, three visitor options, and a recommendation that changes with availability. Real sensors can replace the simulated update when connected."],
    ][demo.step];
    $("occBasis").innerHTML = '<span class="demo-badge">DEMO · SIMULATED OCCUPANCY</span><span class="demo-context">Tuesday · 9:30–11:30 a.m. · No permit</span>';
    const cards = demo.options.map((o) => {
      const free = ParkSharkDemo.available(o, demo.step), best = demo.step > 0 && o.id === winner.id;
      return `<article class="demo-card${best ? " recommended" : ""}${free === 0 ? " full" : ""}">
        <div class="demo-card-top"><span>${best ? "★ Recommended" : free === 0 ? "Full in this scenario" : "Visitor option"}</span><b>${free} <small>simulated free</small></b></div>
        <button class="demo-lot" type="button" data-demo-lot="${esc(o.id)}" aria-label="Show ${esc(o.label)} on map">${esc(o.label)} ↗</button>
        <div class="demo-facts"><span><b>$${o.total.toFixed(2)}</b> / 2 hours*</span><span><b>~${Math.max(1, Math.round(walkMin(o.item)))}</b> min walk†</span></div>
        <div class="demo-evidence">${esc(o.capacityEvidence)} · <a href="${esc(o.item.visitorRate.source)}" target="_blank" rel="noopener">Published $${o.rate.toFixed(2)}/h rate</a></div>
      </article>`;
    }).join("");
    $("list").innerHTML = `<section class="demo-story" aria-label="Guided parking demo">
      <div class="demo-progress" aria-label="Step ${demo.step + 1} of 4">${[0,1,2,3].map((n) => `<span class="${n <= demo.step ? "done" : ""}"></span>`).join("")}<small>${demo.step + 1} / 4</small></div>
      <div aria-live="polite" aria-atomic="true"><h1>${copy[0]}</h1><p class="demo-lead">${copy[1]}</p></div>
      ${demo.step >= 2 ? '<div class="demo-event"><span aria-hidden="true">↻</span> Simulated sensor event: Bryan Center 12 → 0 free. Recommendation updated.</div>' : ''}
      <div class="demo-actions"><button type="button" class="btn" id="demo-next">${["Find my parking →", "Simulate lot filling up →", "See the takeaway →", "Replay demo ↺"][demo.step]}</button><button type="button" class="ghost" id="demo-exit">Exit demo</button></div>
      ${demo.step > 0 ? '<button type="button" class="demo-restart" id="demo-restart">↺ Restart scenario</button>' : '<p class="demo-prompt">A guided, repeatable 60-second walkthrough.</p>'}
      <div class="demo-options">${cards}</div>
      <p class="demo-disclosure">Availability is scripted for this demo, not a live reading or a model forecast. Garage counts refer only to visitor areas. Names, capacities and rates use the project’s sourced data snapshot.</p>
      <p class="demo-disclosure">*Base parking cost; transaction fees may apply. †Approximate straight-line walking estimate, not a routed journey. Check current signs, hours and visitor restrictions.</p>
    </section>`;
    $("demo-next").onclick = () => advanceDemo(demo.step === 3 ? 0 : demo.step + 1);
    $("demo-exit").onclick = stopDemo;
    if ($("demo-restart")) $("demo-restart").onclick = () => advanceDemo(0);
    document.querySelectorAll("[data-demo-lot]").forEach((b) => b.onclick = () => select(b.dataset.demoLot, true));
  }
  function advanceDemo(step) {
    demo.step = step;
    S.selected = step > 0 ? demoWinner().id : null;
    mapOutline(S.selected ? S.byId.get(S.selected).ring : null);
    renderDemo(); refreshPins();
    $("results").scrollTop = 0;
    $("demo-next").focus({ preventScroll: true });
  }
  function startDemo() {
    if (demo.active) return;
    try { demo.options = ParkSharkDemo.create(S.items); }
    catch (e) { $("mapNote").hidden = false; $("mapNote").textContent = e.message; return; }
    const c = M.map.getCenter();
    demo.saved = { dest: S.dest, filters: S.filters, selected: S.selected,
      center: { lat: typeof c.lat === "function" ? c.lat() : c.lat, lon: typeof c.lng === "function" ? c.lng() : c.lng },
      zoom: mapZoom(), title: $("destTitle").textContent, sub: $("destSub").textContent,
      resetHidden: $("destReset").hidden, legend: $("legend").innerHTML, scroll: $("results").scrollTop };
    demo.active = true; demo.step = 0;
    S.dest = { ...S.home }; S.filters = new Set(); S.selected = null;
    $("detail").hidden = true; $("suggest").hidden = true;
    document.body.classList.add("demo-mode");
    $("demo-start").textContent = "Exit demo";
    $("demo-start").setAttribute("aria-pressed", "true");
    $("destTitle").textContent = "Your visit to Wilkinson";
    $("destSub").textContent = "3 visitor options · a guided parking story";
    $("legend").innerHTML = '<div class="lg-title">DEMO · SIMULATED OCCUPANCY</div><div>Three visitor options · scripted availability</div><div class="lg-foot">No live sensor readings or reservations</div>';
    mapOutline(null); drawMap(); mapPan(S.home.lat + 0.0008, S.home.lon - 0.002, 16);
    requestAnimationFrame(() => {
      if (!demo.active) return;
      const points = [...demo.options.map((o) => o.item), S.home];
      if (M.kind === "leaflet") {
        M.map.invalidateSize();
        M.map.fitBounds(points.map((p) => [p.lat, p.lon]), { padding: [45, 40], maxZoom: 16 });
      } else {
        const bounds = new google.maps.LatLngBounds();
        points.forEach((p) => bounds.extend({ lat: p.lat, lng: p.lon }));
        M.map.fitBounds(bounds, 65);
      }
    });
    renderDemo(); tick(); $("results").scrollTop = 0;
    $("demo-next").focus({ preventScroll: true });
  }
  function stopDemo() {
    if (!demo.active) return;
    const saved = demo.saved;
    demo.active = false; S.dest = saved.dest; S.filters = saved.filters; S.selected = saved.selected;
    document.body.classList.remove("demo-mode");
    $("demo-start").textContent = "▶ Try demo";
    $("demo-start").setAttribute("aria-pressed", "false");
    $("destTitle").textContent = saved.title; $("destSub").textContent = saved.sub;
    $("destReset").hidden = saved.resetHidden; $("legend").innerHTML = saved.legend;
    mapOutline(null); drawMap(); renderList(); tick();
    requestAnimationFrame(() => {
      if (demo.active) return;
      if (M.kind === "leaflet") M.map.invalidateSize();
      mapPan(saved.center.lat, saved.center.lon, saved.zoom);
    });
    if (S.selected) renderDetail(S.byId.get(S.selected));
    $("results").scrollTop = saved.scroll;
    $("demo-start").focus({ preventScroll: true });
    const url = new URL(location.href); url.searchParams.delete("demo"); history.replaceState(null, "", url);
  }

  // ---------------------------------------------------------------- time (real now; ?date=YYYY-MM-DD&hour=H for demos)
  const qs = new URLSearchParams(location.search);
  function now() {
    if (demo.active) return new Date(2026, 10, 10, 9, 30);
    const d = new Date();
    if (qs.get("date")) { const [y, m, dd] = qs.get("date").split("-").map(Number); d.setFullYear(y, m - 1, dd); }
    if (qs.get("hour")) d.setHours(+qs.get("hour"), 0, 0, 0);
    return d;
  }
  const dayKind = (d) => (d.getDay() === 0 ? "sunday" : d.getDay() === 6 ? "saturday" : "weekday");
  const p2 = (n) => String(n).padStart(2, "0");
  const hourKey = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:00`;

  // ---------------------------------------------------------------- geometry helpers
  function distM(a, b) {
    const kx = 111320 * Math.cos(((a.lat + b.lat) / 2) * Math.PI / 180);
    return Math.hypot((a.lon - b.lon) * kx, (a.lat - b.lat) * 110540);
  }
  const walkMin = (it) => distM(it, S.dest) / WALK_M_PER_MIN;
  function clearanceIn(it) {           // published deck clearance in inches, e.g. 6' 4"
    const c = (it.permitRates || []).map((r) => r.clearance).find(Boolean);
    const m = c && String(c).match(/(\d+)'\s*(\d+)?/);
    return m ? +m[1] * 12 + (+m[2] || 0) : null;
  }
  const hourly = (it) => (it.visitorRate && it.visitorRate.hourlyUsd != null ? +it.visitorRate.hourlyUsd : null);
  function displayName(it) {
    if (it.name) return it.name;
    if (it.kind === "deck") return "Unnamed parking deck";
    if (it.kind === "street") return "Unnamed street segment";
    return "Unnamed lot";
  }
  const shortName = (it) => { const n = displayName(it).split(" / ")[0]; return n.length > 22 ? n.slice(0, 21) + "…" : n; };

  // ---------------------------------------------------------------- occupancy: live sensors > Tier 3 estimate
  function tier3Profile(profile, d) {
    const groups = Object.values(S.data.tier3.groups).filter((g) => (profile === "curb" ? g.lot_type === "curb" : g.lot_type === "department_lot"));
    const rows = groups.map((g) => g.typical && g.typical[dayKind(d)] && g.typical[dayKind(d)][d.getHours()]).filter(Boolean);
    if (!rows.length) return null;
    const avg = (i) => rows.reduce((s, r) => s + r[i], 0) / rows.length;
    return { m: avg(0), lo: avg(1), hi: avg(2), basis: `Tier 3 typical ${dayKind(d)} profile (${profile === "curb" ? "curb" : "department lot"})` };
  }
  function occFor(it, d) {
    if (S.live.lot[it.id] != null) return { m: S.live.lot[it.id], lo: S.live.lot[it.id], hi: S.live.lot[it.id], measured: true, conf: "high", basis: "Live sensors" };
    if (it.tier3) {
      const g = S.data.tier3.groups[it.tier3];
      const row = (g.hourly || []).find((r) => r.t === hourKey(d));
      if (row) return { m: row.m, lo: row.lo, hi: row.hi, conf: g.confidence, basis: "Tier 3 forecast for this hour (with weather)" };
      const t = g.typical && g.typical[dayKind(d)] && g.typical[dayKind(d)][d.getHours()];
      if (t) return { m: t[0], lo: t[1], hi: t[2], conf: g.confidence, basis: `Tier 3 typical ${dayKind(d)}` };
    }
    if (it.tier3Nearby) {                          // Jacob's per-lot forecast for nearby lots without sensors
      const n = S.data.tier3.nearby[it.tier3Nearby];
      const row = (n.hourly || []).find((r) => r.t === hourKey(d));
      if (row) return { m: row.m, lo: row.lo, hi: row.hi, conf: n.confidence, basis: "Tier 3 forecast for this lot and hour (no sensors here)" };
      const t = n.typical && n.typical[dayKind(d)] && n.typical[dayKind(d)][d.getHours()];
      if (t) {   // outside the forecast window: typical day nudged by street traffic for that date
        const days = (S.data.tier3.traffic_unusual || {})[n.traffic_lot] || {};
        const day = hourKey(d).slice(0, 10), tr = days[day] ? days[day][d.getHours()] : 1;
        const f = 1 + (S.data.tier3.traffic_beta ?? 0.3) * (tr - 1), cl = (v) => Math.min(1, Math.max(0, v));
        const m = cl(t[0] * f);
        return { m, lo: Math.min(cl(t[1] * f), m), hi: Math.max(cl(t[2] * f), m), conf: n.confidence, basis: `Tier 3 typical ${dayKind(d)} for this lot, traffic-adjusted` };
      }
    }
    const pr = tier3Profile(it.profile, d);
    return pr && { ...pr, conf: "very_low" };
  }
  function stat(it, d = now()) {
    const option = demoOption(it);
    if (option) {
      const free = ParkSharkDemo.available(option, demo.step), cap = option.visitorCapacity, ratio = free / cap;
      return { free, cap, ratio, freeLo: free, freeHi: free, tier: "simulated",
        o: { basis: "Scripted demo availability; not a live sensor or forecast" },
        color: free === 0 ? "var(--avail-lo)" : "var(--avail-hi)" };
    }
    const cap = it.capacity && it.capacity.value;
    const o = occFor(it, d);
    if (!cap || !o) return { cap: cap || null, o, free: null, color: "var(--avail-unk)", tier: o && o.measured ? "measured" : "estimated" };
    const free = Math.max(0, Math.round(cap * (1 - o.m)));
    const ratio = free / cap;
    return {
      cap, o, free, ratio, freeLo: Math.max(0, Math.round(cap * (1 - o.hi))), freeHi: Math.round(cap * (1 - o.lo)),
      color: ratio >= 0.7 ? "var(--avail-hi)" : ratio >= 0.3 ? "var(--avail-mid)" : "var(--avail-lo)",
      tier: o.measured ? "measured" : "estimated",
    };
  }
  const freeText = (st) => (st.free == null ? "?" : (st.tier === "measured" || st.tier === "simulated" ? "" : "~") + st.free);

  async function fetchLive() {
    const { supabaseUrl: u, supabaseAnonKey: k } = S.cfg;
    if (!u || !k) return;
    try {
      const r = await fetch(u.replace(/\/$/, "") + "/rest/v1/occupancy_latest?select=*", { headers: { apikey: k, Authorization: "Bearer " + k } });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const rows = await r.json();
      S.live = { lot: {}, spot: {}, rows: 0 };
      for (const row of rows) {
        if (row.source === "estimator") continue;          // a model row is not a measurement
        S.live.rows++;
        if (row.spot_id) S.live.spot[row.spot_id] = !!row.occupied;
        else if (row.lot_id != null && row.occupancy != null) S.live.lot[row.lot_id] = Math.min(1, Math.max(0, +row.occupancy));
      }
    } catch (e) { console.info("Live occupancy unavailable:", e.message); }
  }

  // ---------------------------------------------------------------- filters + ranking
  function passes(it, f = S.filters) {
    if (f.has("wheelchair") && !(it.accessible || it.ada > 0)) return false;
    if (f.has("ev") && !(it.ev > 0)) return false;
    if (f.has("large") && it.kind === "deck") { const c = clearanceIn(it); if (c == null || c < 84) return false; }
    if (f.has("large") && it.underground) return false;
    if (f.has("cheapest") && hourly(it) == null) return false;
    return true;
  }
  const listable = (it) => it.kind === "street" || it.kind === "pilot" || it.name || (it.capacity.value || 0) >= 10;
  function ranked() {
    const d = now();
    let list = S.items.filter((it) => (S.showAll || listable(it)) && passes(it)).map((it) => ({ it, st: stat(it, d), walk: walkMin(it) }));
    if (S.filters.has("cheapest")) list.sort((a, b) => hourly(a.it) - hourly(b.it) || a.walk - b.walk);
    else list.sort((a, b) => a.walk - b.walk);
    return list;
  }

  // ---------------------------------------------------------------- map adapter: Google Maps JS (key from /api/config) or OSM fallback
  const M = { kind: null, map: null, pins: new Map(), lines: [], extra: [], outline: null };
  async function mapInit() {
    if (M.kind === "leaflet" && M.map) M.map.remove();
    M.map = null; M.outline = null; M.pins.clear(); M.lines = []; M.extra = [];
    const el = $("map"); el.innerHTML = "";
    const dark = isDark();
    if (S.cfg.googleMapsKey) {
      try {
        if (!(window.google && google.maps && google.maps.importLibrary)) {
          await loadScript(`https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(S.cfg.googleMapsKey)}&v=weekly&loading=async&libraries=places`);
          for (let i = 0; i < 50 && !(window.google && google.maps && google.maps.importLibrary); i++) await new Promise((r) => setTimeout(r, 100));
        }
        const { Map } = await google.maps.importLibrary("maps");
        await google.maps.importLibrary("marker");
        M.kind = "google";
        M.map = new Map(el, {
          center: { lat: S.dest.lat, lng: S.dest.lon }, zoom: 16, mapId: S.cfg.googleMapId || "DEMO_MAP_ID",
          colorScheme: google.maps.ColorScheme ? google.maps.ColorScheme[dark ? "DARK" : "LIGHT"] : undefined,
          disableDefaultUI: true, zoomControl: true, mapTypeControl: true, fullscreenControl: false, clickableIcons: false, gestureHandling: "greedy",
          mapTypeControlOptions: { position: google.maps.ControlPosition.TOP_RIGHT },
        });
        M.map.addListener("zoom_changed", updatePinVisibility);
        $("mapNote").hidden = true;
        return;
      } catch (e) { console.warn("Google Maps failed, using OpenStreetMap:", e.message); }
    }
    if (!window.L) {
      loadCSS("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css");
      try { await loadScript("https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"); }
      catch { loadCSS("vendor/leaflet.css"); await loadScript("vendor/leaflet.js"); }
    }
    M.kind = "leaflet";
    M.map = L.map(el, { zoomControl: true, attributionControl: true }).setView([S.dest.lat, S.dest.lon], 16);
    // Dark mode = the same OSM tiles with a CSS filter (no extra tile provider or key).
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20, maxNativeZoom: 19, className: "osm-tiles",
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(M.map);
    M.map.on("zoomend", updatePinVisibility);
    const note = $("mapNote");
    note.hidden = false;
    note.textContent = S.cfg.googleMapsKey ? "Google Maps could not load; showing OpenStreetMap." : "Showing OpenStreetMap (Google Maps key not configured on the server).";
  }
  function isDark() {
    const t = document.documentElement.dataset.theme;
    return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function mapAddPin(it, html, onClick) {
    const el = document.createElement("div");
    el.innerHTML = html; const node = el.firstElementChild;
    node.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
    let h;
    if (M.kind === "google") {
      const mk = new google.maps.marker.AdvancedMarkerElement({ map: M.map, position: { lat: it.lat, lng: it.lon }, content: node, title: displayName(it) });
      h = { node, show: (v) => { mk.map = v ? M.map : null; }, remove: () => { mk.map = null; } };
    } else {
      const mk = L.marker([it.lat, it.lon], { icon: L.divIcon({ html: node, className: "pin-wrap", iconSize: null }), keyboard: false }).addTo(M.map);
      h = { node, show: (v) => { if (v) mk.addTo(M.map); else mk.remove(); }, remove: () => mk.remove() };
    }
    return h;
  }
  function mapAddLine(coords, color, onClick) {      // coords: [[lon,lat],...]
    if (M.kind === "google") {
      const pl = new google.maps.Polyline({ map: M.map, path: coords.map(([x, y]) => ({ lat: y, lng: x })), strokeColor: color, strokeWeight: 6, strokeOpacity: 0.9 });
      pl.addListener("click", onClick); M.lines.push(() => pl.setMap(null));
    } else {
      const pl = L.polyline(coords.map(([x, y]) => [y, x]), { color, weight: 6, opacity: 0.9 }).addTo(M.map).on("click", onClick);
      M.lines.push(() => pl.remove());
    }
  }
  function mapOutline(ring) {
    if (M.outline) { M.outline(); M.outline = null; }
    if (!ring) return;
    if (M.kind === "google") {
      const pg = new google.maps.Polygon({ map: M.map, paths: ring.map(([x, y]) => ({ lat: y, lng: x })), strokeColor: "#e07b39", strokeWeight: 3, fillColor: "#e07b39", fillOpacity: 0.12, clickable: false });
      M.outline = () => pg.setMap(null);
    } else {
      const pg = L.polygon(ring.map(([x, y]) => [y, x]), { color: "#e07b39", weight: 3, fillOpacity: 0.12, interactive: false }).addTo(M.map);
      M.outline = () => pg.remove();
    }
  }
  function mapPan(lat, lon, zoom) {
    if (M.kind === "google") { M.map.panTo({ lat, lng: lon }); if (zoom) M.map.setZoom(zoom); }
    else M.map.setView([lat, lon], zoom || M.map.getZoom());
  }
  const mapZoom = () => (M.kind === "google" ? M.map.getZoom() : M.map.getZoom());
  function mapPoint(lat, lon, html) {
    const el = document.createElement("div"); el.innerHTML = html; const node = el.firstElementChild;
    if (M.kind === "google") {
      const mk = new google.maps.marker.AdvancedMarkerElement({ map: M.map, position: { lat, lng: lon }, content: node, zIndex: 999 });
      return () => { mk.map = null; };
    }
    const mk = L.marker([lat, lon], { icon: L.divIcon({ html: node, className: "pin-wrap", iconSize: null }), interactive: false, zIndexOffset: 1000 }).addTo(M.map);
    return () => mk.remove();
  }

  function pinHTML(it, st) {
    const cls = ["pin", it.kind === "street" || it.kind === "pilot" ? "street" : "", (it.capacity.value || 0) >= 300 ? "big" : "", S.selected === it.id ? "sel" : "", passes(it) ? "" : "dim"].join(" ");
    const option = demoOption(it);
    const label = option ? `<span>${esc(option.label)}</span>` : it.name || it.kind === "street" ? `<span>${esc(shortName(it))}</span>` : (it.kind === "deck" ? "<span>Deck</span>" : "");
    const live = st.tier === "measured" ? " 📡" : "";
    return `<div class="${cls}" style="--c:${st.color}"><b>${esc(freeText(st))}</b>${label}${live}</div>`;
  }
  function drawMap() {
    for (const h of M.pins.values()) h.remove();
    M.pins.clear(); M.lines.forEach((f) => f()); M.lines = []; M.extra.forEach((f) => f()); M.extra = [];
    const d = now();
    for (const it of S.items) {
      if (demo.active && !demoOption(it)) continue;
      if (it.lines) for (const ln of it.lines) mapAddLine(ln, getComputedStyle(document.documentElement).getPropertyValue("--street").trim() || "#4285f4", () => select(it.id, false));
      M.pins.set(it.id, mapAddPin(it, pinHTML(it, stat(it, d)), () => select(it.id, false)));
    }
    M.extra.push(mapPoint(S.dest.lat, S.dest.lon, `<div class="pin dest"><span>🎯 ${esc(S.dest.name.slice(0, 26))}</span></div>`));
    if (S.me) M.extra.push(mapPoint(S.me.lat, S.me.lon, '<div class="me-dot" title="You"></div>'));
    if (S.selected) { const it = S.byId.get(S.selected); mapOutline(it && it.ring); }
    updatePinVisibility();
  }
  function refreshPins() {
    const d = now();
    for (const it of S.items) {
      const h = M.pins.get(it.id); if (!h) continue;
      const tmp = document.createElement("div"); tmp.innerHTML = pinHTML(it, stat(it, d));
      const n = tmp.firstElementChild; h.node.className = n.className; h.node.style.cssText = n.style.cssText; h.node.innerHTML = n.innerHTML;
    }
    updatePinVisibility();
  }
  function updatePinVisibility() {
    const z = mapZoom();
    $("map").classList.toggle("z-lo", z < 17);
    for (const it of S.items) {
      const h = M.pins.get(it.id); if (!h) continue;
      if (demo.active) { h.show(!!demoOption(it)); continue; }
      const small = !listable(it);
      const big = (it.capacity.value || 0) >= 30 || it.kind === "street" || it.kind === "pilot";
      h.show(S.selected === it.id || (z >= 18 ? true : z >= 17 ? !small : z >= 15 ? !small && big : !!it.name && (it.capacity.value || 0) >= 300));
    }
  }

  // ---------------------------------------------------------------- results list
  function renderList() {
    if (demo.active) { renderDemo(); return; }
    const list = ranked();
    const n = S.showAll ? list.length : Math.min(list.length, 40);
    const d = now();
    const f = [...S.filters];
    $("occBasis").innerHTML = `Sorted by ${S.filters.has("cheapest") ? "visitor price" : "walk to destination"} · ${list.length} places` +
      (f.length ? ` · filters: ${esc(f.join(", "))}` : "") + `<br>Free spaces: ${S.live.rows ? "live sensors where reporting, else " : ""}` +
      `estimated by the Tier 3 model for ${d.toLocaleTimeString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}.`;
    if (!list.length) {
      $("list").innerHTML = `<div class="empty">No places match ${esc(f.join(" + "))}.` +
        (S.filters.has("ev") ? " Duke publishes EV chargers at only a few facilities; none are inside this area's mapped lots." : "") + "</div>";
      return;
    }
    $("list").innerHTML = list.slice(0, n).map(({ it, st, walk }) => {
      const h = hourly(it);
      const price = h != null ? `$${h.toFixed(2)}/h<small>visitor</small>` : (it.permits ? `Permit<small>visitor rate not published</small>` : `<span class="np">Not published</span><small>price</small>`);
      const kind = { deck: "Deck", lot: "Lot", street: "On-street", pilot: "Sensor pilot" }[it.kind];
      return `<button type="button" class="card${S.selected === it.id ? " sel" : ""}" data-id="${esc(it.id)}">
        <div class="free" style="--c:${st.color}">${esc(freeText(st))}<small>${st.cap ? "of " + st.cap : "free"}</small></div>
        <div><div class="nm">${esc(displayName(it))}</div>
          <div class="sub">${kind} · ${Math.max(1, Math.round(walk))} min walk${it.ada > 0 || it.accessible ? " · ♿" : ""}${it.ev > 0 ? " · ⚡" + it.ev : ""}
          · <span class="badge b-${st.cap ? it.capacity.confidence : "kind"}">${st.cap ? it.capacity.confidence : "no count"}</span></div></div>
        <div class="price">${price}</div></button>`;
    }).join("") + (list.length > n ? `<button type="button" class="ghost more-btn" id="showAll">Show all ${list.length} (incl. small unnamed lots)</button>` : "");
    $("list").querySelectorAll(".card").forEach((c) => c.onclick = () => select(c.dataset.id, true));
    const sa = $("showAll"); if (sa) sa.onclick = () => { S.showAll = true; renderList(); };
  }

  // ---------------------------------------------------------------- detail panel + SVG schematic
  async function getLayouts() {
    if (!S.layouts) S.layouts = await fetch("data/layouts.json").then((r) => r.json());
    return S.layouts;
  }
  function schematic(it, lay) {
    const parts = [];
    let stalls = (lay && lay.stalls) || [], outline = lay && lay.outline, ents = (lay && lay.entrances) || [], synthetic = false;
    if (!stalls.length && it.kind === "pilot") {        // sensor pilot: N spots in a row, positions not surveyed
      synthetic = true;
      stalls = Array.from({ length: it.capacity.value }, (_, i) => [i * 2.9 + 1.45, 2.75, 0, 2.74, 5.5, 0]);
    }
    if (!stalls.length && !outline) {
      return `<div class="schem"><div class="cap">${it.kind === "street" ? "Duke publishes the rule for this street but not the curb positions of its spaces." : "No stall layout is available for this facility."}</div></div>`;
    }
    const pts = [...(outline || []), ...stalls.map((s) => [s[0], s[1]])];
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const pad = 6, x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad, w = Math.max(...xs) - x0 + pad, h = Math.max(...ys) - y0 + pad;
    if (outline) parts.push(`<polygon class="lot" points="${outline.map((p) => p.join(",")).join(" ")}"/>`);
    let open = 0, taken = 0, ada = 0, ev = 0;
    stalls.forEach((s, i) => {
      const live = S.live.spot[`${it.id}-s${i}`];
      let cls = "st";
      if (s[5] & 1) { cls += " ada"; ada++; } else if (s[5] & 2) { cls += " ev"; ev++; }
      else if (live === true) { cls += " taken"; taken++; } else if (live === false) { cls += " open"; open++; }
      parts.push(`<rect class="${cls}" x="${-s[3] / 2}" y="${-s[4] / 2}" width="${s[3]}" height="${s[4]}" transform="translate(${s[0]} ${s[1]}) rotate(${s[2]})"/>`);
    });
    const fs = Math.max(1.6, Math.min(w / 40, h / 12));
    ents.forEach(([x, y]) => {
      const r = fs * 0.7;
      parts.push(`<polygon class="ent" points="${x},${y - r} ${x + r},${y + r} ${x - r},${y + r}"/><text x="${x + r * 1.3}" y="${y + r * 0.4}" font-size="${fs}">Entrance</text>`);
    });
    if (it.roof) parts.push(`<text x="${x0 + 2}" y="${y0 + fs + 1}" font-size="${fs}">Deck · Roof level</text>`);
    const anyLive = open + taken > 0;
    const est = stalls.length && stalls.every((s) => s[5] & 4);
    const cap = synthetic ? `${stalls.length} spots drawn in a row: real positions not surveyed yet.`
      : `${stalls.length} stall${stalls.length === 1 ? "" : "s"} drawn${est ? " (estimated)" : ""}${it.capacity.value && it.capacity.value !== stalls.length ? ` · capacity ${it.capacity.value} (${esc(it.capacity.basis)})` : ""}. ${esc(it.layoutStatus)}. Drawing rotated to fit; not to scale with the map.`;
    return `<div class="schem"><svg viewBox="${x0} ${y0} ${w} ${h}" role="img" aria-label="Stall layout of ${esc(displayName(it))}">${parts.join("")}</svg>
      <div class="lg"><span><i style="background:var(--st-open)"></i>open${anyLive ? ` (${open})` : ""}</span><span><i style="background:var(--st-taken)"></i>taken${anyLive ? ` (${taken})` : ""}</span>
        <span><i style="background:var(--st-ada)"></i>ADA (${ada})</span><span><i style="background:var(--st-ev)"></i>EV (${ev})</span>
        <span><i style="background:var(--st-unk)"></i>no sensor</span>${ents.length ? '<span><i style="background:var(--orange);border-radius:50%"></i>entrance</span>' : ""}</div>
      <div class="cap">${cap}${anyLive ? "" : " Per-stall open/taken appears only where live sensors report; none do here yet."}</div></div>`;
  }
  const row = (k, v) => `<tr><th>${k}</th><td>${v == null || v === "" ? NP : v}</td></tr>`;
  const link = (s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>`;
  async function renderDetail(it) {
    const st = stat(it);
    const kind = { deck: "Parking deck", lot: "Surface lot", street: "On-street parking", pilot: "Sensor pilot spots" }[it.kind];
    const h = hourly(it);
    const capTxt = it.capacity.value != null
      ? `${it.capacity.value} <span class="badge b-${it.capacity.confidence}">${it.capacity.confidence}</span><br><small>${esc(it.capacity.basis)}</small>` +
        (it.capacity.scopes ? `<br><small>${it.capacity.scopes.map(esc).join("<br>")}</small>` : "") : null;
    const zone = it.capacity.zoneNote ? `<br><small>${it.capacity.zoneNote.map(esc).join("<br>")}</small>` : "";
    const rate = h != null ? `$${h.toFixed(2)} / hour` + (it.visitorRate.note ? `<br><small>${esc(it.visitorRate.note)}</small>` : "") +
      (it.visitorRate.source ? `<br><small><a href="${esc(it.visitorRate.source)}" target="_blank" rel="noopener">source</a></small>` : "") : null;
    const permitRates = (it.permitRates || []).length ? it.permitRates.map((r) => `${esc(r.name)}: ${r.annualUsd != null ? "$" + r.annualUsd.toLocaleString() + "/yr" : NP}${r.eligibility ? " · " + esc(r.eligibility) : ""}${r.clearance ? " · clearance " + esc(r.clearance) : ""}`).join("<br>") : null;
    const freeBlock = st.free != null
      ? `<div class="d-free" style="--c:${st.color}"><div class="big">${esc(freeText(st))}</div><div style="flex:1"><div class="bar"><i style="width:${Math.round(st.ratio * 100)}%"></i></div>
          <small>free of ${st.cap} · likely ${st.freeLo}–${st.freeHi} · <span class="badge b-${st.tier}">${st.tier}</span> ${esc(st.o.basis)}
          ${st.o.conf ? `<br>Model confidence: <b>${esc(st.o.conf.replace("_", " "))}</b> · ${esc((S.data.tier3.confidence_levels || {})[st.o.conf] || "")}` : ""}</small></div></div>`
      : `<div class="d-free"><div class="big">?</div><small>Free spaces unknown: ${it.capacity.value == null ? "capacity is not published" : "no occupancy estimate for this hour"}.</small></div>`;
    $("detailBody").innerHTML = `
      <div class="d-head"><div><h2>${esc(displayName(it))}</h2>
        <div class="src">${kind}${it.nameSource ? " · name: " + esc(it.nameSource) : " · no published name"} · ${Math.max(1, Math.round(walkMin(it)))} min walk to ${esc(S.dest.name)}</div></div>
        <button type="button" class="ghost small" id="detailClose" aria-label="Close">✕</button></div>
      ${freeBlock}
      ${(it.warnings || []).map((w) => `<div class="warn">⚠️ ${esc(w)}</div>`).join("")}
      <div id="schemSlot"><div class="schem"><div class="cap">Loading layout…</div></div></div>
      <table class="info">
        ${it.kind === "deck" ? row("Levels", it.levels != null ? `${it.levels} (OSM building:levels)` : null) + (it.roof ? row("Roof level", it.roofStalls ? `${it.roofStalls} stalls drawn (estimated)` : null) : "") : ""}
        ${row("Capacity", capTxt ? capTxt + zone : (zone ? NP + zone : null))}
        ${row("Permit zones", it.permits ? esc(it.permits) : (it.access ? esc(it.access) : null))}
        ${row("Visitor rate", rate)}
        ${it.feeTag ? row("Fee (OSM tag)", esc(it.feeTag)) : ""}
        ${permitRates ? row("Permit rates", permitRates) : ""}
        ${row("Hours", it.hours ? esc(it.hours) : null)}
        ${row("ADA", it.ada > 0 ? `${it.ada} (${esc(it.adaBasis)})` : it.accessible ? "Accessible parking available (count not published)" : null)}
        ${row("EV charging", it.ev > 0 ? `${it.ev} (${esc(it.evBasis)})` : null)}
        ${it.description ? row("Description", esc(it.description)) : ""}
        ${(it.rules || []).length ? row("Rules", it.rules.map(esc).join("<br>")) : ""}
        ${it.signs && it.signs.length ? row("Posted signs nearby", it.signs.map((s) => `${esc(s.text)} (${s.count})`).join("<br>") + "<br><small>City of Durham sign inventory</small>") : ""}
        ${row("Sources", (it.sources || []).length ? it.sources.map(link).join("<br>") : null)}
      </table>
      <div class="actions">
        <button type="button" class="btn" id="payBtn" ${h != null ? "" : "disabled"} title="${h != null ? "Google Pay TEST demo" : "Visitor rate not published"}">Pay · Demo, no real charge</button>
        <button type="button" class="btn alt" id="askBtn">Ask the concierge</button>
      </div>`;
    $("detail").hidden = false;
    $("detailClose").onclick = closeDetail;
    $("payBtn").onclick = () => openPay(it);
    $("askBtn").onclick = () => { openChat(); $("chat-input").value = `Tell me about ${displayName(it)} (id ${it.id}).`; $("chat-input").focus(); };
    const lay = (await getLayouts())[it.id];
    if (S.selected === it.id) $("schemSlot").innerHTML = schematic(it, lay);
  }
  function select(id, pan) {
    const it = S.byId.get(id); if (!it) return;
    S.selected = id;
    if (pan) mapPan(it.lat, it.lon, Math.max(mapZoom(), 17));
    mapOutline(it.ring);
    refreshPins(); renderList();
    if (!demo.active) renderDetail(it);
  }
  function closeDetail() { S.selected = null; $("detail").hidden = true; mapOutline(null); refreshPins(); renderList(); }

  // ---------------------------------------------------------------- search: our places + Google Places (when the key is configured)
  let suggestTimer = null;
  function setDest(lat, lon, name, sub) {
    S.dest = { lat, lon, name };
    $("destTitle").textContent = name; $("destSub").textContent = sub || "";
    $("destReset").hidden = name === S.home.name;
    drawMap(); renderList(); mapPan(lat, lon, 16);
  }
  async function suggest(q) {
    const box = $("suggest");
    if (!q.trim()) { box.hidden = true; return; }
    const ql = q.toLowerCase();
    const mine = S.items.filter((it) => it.name && it.name.toLowerCase().includes(ql)).slice(0, 6)
      .map((it) => ({ label: it.name, sub: { deck: "Parking deck", lot: "Lot", street: "On-street", pilot: "Sensor pilot" }[it.kind], go: () => select(it.id, true) }));
    let places = [];
    if (M.kind === "google") {
      try {
        const { AutocompleteSuggestion } = await google.maps.importLibrary("places");
        const { suggestions } = await AutocompleteSuggestion.fetchAutocompleteSuggestions({
          input: q, locationBias: { center: { lat: S.home.lat, lng: S.home.lon }, radius: 3000 } });
        places = suggestions.slice(0, 5).map((s) => s.placePrediction).filter(Boolean).map((p) => ({
          label: p.mainText ? p.mainText.text : p.text.text, sub: p.secondaryText ? p.secondaryText.text : "Place",
          go: async () => { const pl = p.toPlace(); await pl.fetchFields({ fields: ["location", "displayName", "formattedAddress"] });
                            setDest(pl.location.lat(), pl.location.lng(), pl.displayName, pl.formattedAddress); } }));
      } catch (e) { console.info("Places search unavailable:", e.message); }
    }
    const all = [...mine, ...places];
    box.innerHTML = all.length ? all.map((s, i) => `<button type="button" data-i="${i}">${esc(s.label)}<small>${esc(s.sub)}</small></button>`).join("")
      : `<button type="button" disabled>No matching lot or street${M.kind === "google" ? " or place" : ""}</button>`;
    box.querySelectorAll("button[data-i]").forEach((b) => b.onclick = () => { box.hidden = true; all[+b.dataset.i].go(); });
    box.hidden = false;
  }

  // ---------------------------------------------------------------- Google Pay (TEST environment only; nothing stored, no real charge)
  const GPAY_BASE = { apiVersion: 2, apiVersionMinor: 0 };
  const GPAY_CARD = {
    type: "CARD",
    parameters: { allowedAuthMethods: ["PAN_ONLY", "CRYPTOGRAM_3DS"], allowedCardNetworks: ["AMEX", "DISCOVER", "MASTERCARD", "VISA"] },
    tokenizationSpecification: { type: "PAYMENT_GATEWAY", parameters: { gateway: "example", gatewayMerchantId: "exampleGatewayMerchantId" } },
  };
  let gpayClient = null, payCtx = null;
  async function getGpayClient() {
    if (gpayClient) return gpayClient;
    if (!(window.google && google.payments)) await loadScript("https://pay.google.com/gp/p/js/pay.js");
    gpayClient = new google.payments.api.PaymentsClient({ environment: "TEST" });
    return gpayClient;
  }
  function payTotal() { return payCtx.rate * +$("pay-hours").value; }
  function openPay(it) {
    const h = hourly(it); if (h == null) return;
    payCtx = { it, rate: h };
    $("pay-summary").innerHTML = `<b>${esc(displayName(it))}</b><br>Visitor rate $${h.toFixed(2)}/h (published${it.visitorRate.note ? "; " + esc(it.visitorRate.note) : ""})`;
    const upd = () => { $("pay-total").textContent = `Total: $${payTotal().toFixed(2)} (demo)`; };
    $("pay-hours").onchange = upd; upd();
    $("pay-result").innerHTML = ""; $("gpay-container").innerHTML = ""; $("pay-sim").hidden = true;
    $("pay-modal").hidden = false;
    getGpayClient()
      .then((c) => c.isReadyToPay({ ...GPAY_BASE, allowedPaymentMethods: [{ type: "CARD", parameters: GPAY_CARD.parameters }] }).then((r) => {
        if (!r.result) throw new Error("Google Pay not ready in this browser");
        $("gpay-container").appendChild(c.createButton({ onClick: payWithGoogle, buttonType: "pay", buttonSizeMode: "fill" }));
      }))
      .catch((e) => { console.info("Google Pay unavailable:", e.message); $("pay-sim").hidden = false; });
  }
  function payWithGoogle() {
    gpayClient.loadPaymentData({
      ...GPAY_BASE, allowedPaymentMethods: [GPAY_CARD],
      transactionInfo: { totalPriceStatus: "FINAL", totalPrice: payTotal().toFixed(2), currencyCode: "USD", countryCode: "US" },
      merchantInfo: { merchantName: "ParkShark Demo (TEST)" },
    }).then((data) => confirmPay("Google Pay TEST token (" + data.paymentMethodData.type + ")"))   // token discarded, never sent anywhere
      .catch((e) => { if (e.statusCode !== "CANCELED") $("pay-result").innerHTML = `<div class="err">Payment error: ${esc(e.statusMessage || e.message)}</div>`; });
  }
  function confirmPay(method) {
    const code = "PS-" + Math.random().toString(36).slice(2, 8).toUpperCase();
    $("pay-result").innerHTML = `<div class="ok">✅ Demo receipt <b>${code}</b> via ${esc(method)}.<br>No real charge was made and nothing was stored.</div>`;
    $("gpay-container").innerHTML = ""; $("pay-sim").hidden = true;
  }

  // ---------------------------------------------------------------- Parking Concierge (Gemini via /api/concierge; tools run here on the same data)
  const chat = { messages: [], busy: false };
  function parseArrival(str) {
    if (!str || /^now$/i.test(str.trim())) return now();
    let m = str.trim().match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})$/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    m = str.trim().match(/^(\d{1,2}):(\d{2})$/);
    if (m) { const d = now(); d.setHours(+m[1], +m[2], 0, 0); return d; }
    throw new Error(`Unrecognized arrival "${str}". Use now, HH:MM or YYYY-MM-DD HH:MM.`);
  }
  function findItem(id) {
    const it = S.byId.get(id);
    if (!it) throw new Error(`Unknown option_id "${id}". Use an id returned by rank_parking_options.`);
    return it;
  }
  function priceInfo(it, prof) {
    const h = hourly(it);
    const visitorOk = h != null || it.kind === "street";
    if (prof.role === "visitor" && h == null) return { available: false, price_usd: null, label: it.permits ? `Permit only (${it.permits}); no published visitor rate` : "No published visitor rate" };
    return {
      available: true, price_usd: h != null ? Math.round(h * prof.hours * 100) / 100 : null,
      label: h != null ? `$${h}/h visitor rate × ${prof.hours} h` + (it.visitorRate.note ? ` (${it.visitorRate.note})` : "") :
        (it.permits ? `Duke permit ${it.permits}; visitor rate not published` : "Price not published"),
      visitor_parking: visitorOk, source: (it.visitorRate && it.visitorRate.source) || null,
    };
  }
  function profileFilters(i) {
    const f = new Set();
    if (i.wheelchair) f.add("wheelchair"); if (i.ev) f.add("ev"); if (i.vehicle === "large") f.add("large");
    return f;
  }
  function summarize(it, d, prof) {
    const st = stat(it, d), pr = priceInfo(it, prof);
    return {
      option_id: it.id, name: displayName(it), kind: it.kind, walk_min_to_destination: Math.round(walkMin(it) * 10) / 10,
      price_usd: pr.price_usd, price_label: pr.label, available_to_driver: pr.available,
      capacity: it.capacity.value, capacity_confidence: it.capacity.confidence, capacity_basis: it.capacity.basis,
      free_spaces_estimate: st.free, free_range: st.free != null ? [st.freeLo, st.freeHi] : null,
      occupancy_basis: st.o ? st.o.basis : null, occupancy_tier: st.tier, model_confidence: st.o ? st.o.conf : null,
      permits: it.permits || null, ada: it.ada || (it.accessible ? "available, count not published" : null), ev_stations: it.ev || null,
      clearance: (it.permitRates || []).map((r) => r.clearance).find(Boolean) || null,
    };
  }
  const TOOLS = {
    rank_parking_options(i) {
      const prof = { role: i.role, hours: Math.max(0.5, Math.min(24, +i.hours || 2)) };
      const d = parseArrival(i.arrival), f = profileFilters(i);
      let list = S.items.filter((it) => listable(it) && passes(it, f)).map((it) => summarize(it, d, prof));
      if (prof.role === "visitor") list = list.filter((o) => o.available_to_driver);
      const budget = +i.max_budget_usd > 0 ? +i.max_budget_usd : null;
      const fits = budget == null ? list : list.filter((o) => o.price_usd == null || o.price_usd <= budget);
      const key = { time: (o) => o.walk_min_to_destination, cost: (o) => (o.price_usd == null ? 1e9 : o.price_usd),
                    value: (o) => o.walk_min_to_destination + (o.price_usd || 0) * 2 + (o.free_spaces_estimate === 0 ? 30 : 0) }[i.sort] || ((o) => o.walk_min_to_destination);
      fits.sort((a, b) => key(a) - key(b));
      return {
        destination: S.dest.name, arrival: d.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }),
        options_considered: list.length, options_within_budget: fits.length,
        options: fits.slice(0, Math.max(1, Math.min(8, i.limit | 0 || 5))),
        notes: [
          "Free-space numbers are estimates from the Tier 3 model (trained on public car parks in the UK/NL, not calibrated on Duke) unless occupancy_tier is 'measured'.",
          f.has("large") ? "Large vehicle: decks without a published clearance of at least 7 ft are excluded." : null,
          prof.role === "visitor" ? "Visitors: only places with a published Duke visitor rate are listed." : null,
          i.ev && !list.length ? "No EV chargers are published inside the mapped lots in this area." : null,
        ].filter(Boolean),
      };
    },
    get_price(i) {
      const it = findItem(i.option_id);
      return { option_id: it.id, name: displayName(it), ...priceInfo(it, { role: i.role, hours: Math.max(0.5, +i.hours || 2) }),
               permit_rates: it.permitRates || [], hours: it.hours || null };
    },
    async get_lot_details(i) {
      const it = findItem(i.option_id), lay = (await getLayouts())[it.id];
      const stalls = (lay && lay.stalls) || [];
      return {
        ...summarize(it, now(), { role: "permit", hours: 1 }), name_source: it.nameSource,
        visitor_rate: it.visitorRate, hours: it.hours, description: it.description, levels: it.levels, roof_level: it.roof,
        layout: { stalls_drawn: stalls.length, ada_stalls: stalls.filter((s) => s[5] & 1).length, ev_stalls: stalls.filter((s) => s[5] & 2).length,
                  estimated: stalls.length ? stalls.every((s) => s[5] & 4) : null, entrances: ((lay && lay.entrances) || []).length, status: it.layoutStatus },
        rules: it.rules || [], posted_signs: it.signs || [], warnings: it.warnings || [], sources: (it.sources || []).map((s) => s.url),
      };
    },
    show_on_map(i) { findItem(i.option_id); select(i.option_id, true); return { ok: true }; },
  };
  const TOOL_LABEL = { rank_parking_options: "🔎 Ranked parking options", get_price: "💲 Checked price", get_lot_details: "📋 Read lot details", show_on_map: "🗺 Showed on map" };
  function chatAdd(cls, html) {
    const el = document.createElement("div"); el.className = "msg " + cls; el.innerHTML = html;
    $("chat-log").appendChild(el); $("chat-log").scrollTop = $("chat-log").scrollHeight; return el;
  }
  const fmtAnswer = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/^\s*[-•*]\s+/gm, "• ").replace(/\n/g, "<br>");
  async function askConcierge(text) {
    if (chat.busy || !text.trim()) return;
    chat.busy = true; $("chat-send").disabled = true;
    chatAdd("user", esc(text));
    const saved = chat.messages.length;
    const ctx = `<context>Current local time: ${now().toLocaleString([], { weekday: "long", year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}. ` +
      `Destination: ${S.dest.name}. Active map filters: ${[...S.filters].join(", ") || "none"}.` +
      (S.selected ? ` The driver has ${displayName(S.byId.get(S.selected))} (id ${S.selected}) open.` : "") + `</context>`;
    chat.messages.push({ role: "user", content: [{ type: "text", text: `${text}\n\n${ctx}` }] });
    const thinking = chatAdd("status", "Thinking…");
    try {
      for (let step = 0; step < 8; step++) {
        const r = await fetch("api/concierge", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ messages: chat.messages }) });
        const data = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        chat.messages.push({ role: "assistant", content: data.content });      // echo back unchanged
        const said = data.content.filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
        if (data.stop_reason === "tool_use") {
          if (said) chatAdd("bot", fmtAnswer(said));
          const results = [];
          for (const b of data.content.filter((x) => x.type === "tool_use")) {
            let content, isError = false;
            try {
              if (!TOOLS[b.name] || typeof b.input !== "object" || b.input === null) throw new Error(`Unknown tool ${b.name}`);
              content = JSON.stringify(await TOOLS[b.name](b.input));
            } catch (e) { content = "Error: " + e.message; isError = true; }
            chatAdd("tool", `${TOOL_LABEL[b.name] || b.name}${isError ? " (error)" : ""}`);
            results.push({ type: "tool_result", tool_use_id: b.id, content, ...(isError ? { is_error: true } : {}) });
          }
          chat.messages.push({ role: "user", content: results });
          continue;
        }
        if (data.stop_reason === "refusal") chatAdd("bot", "Sorry, I can't help with that request.");
        else chatAdd("bot", fmtAnswer(said || "(no answer)") + (data.stop_reason === "max_tokens" ? "<br><i>(answer cut short)</i>" : ""));
        break;
      }
    } catch (e) {
      chat.messages.length = saved;
      chatAdd("err", "⚠️ " + esc(e.message));
    } finally {
      thinking.remove(); chat.busy = false; $("chat-send").disabled = false; $("chat-input").focus();
    }
  }
  function openChat() { $("chat").hidden = false; $("chat-open").hidden = true; }

  // ---------------------------------------------------------------- details panel (everything else)
  function renderMore() {
    const L = S.data.lots, St = S.data.streets, src = S.data.sources;
    const c = (f) => L.filter(f).length;
    $("moreBody").innerHTML = `
      <h4>What the numbers mean</h4>
      <ul><li><span class="badge b-counted">counted</span> capacity published by Duke, an OSM capacity tag, or stalls mapped one by one in OSM.</li>
        <li><span class="badge b-estimated">estimated</span> stalls drawn by our grid model inside the real OSM outline, or occupancy from the Tier 3 model.</li>
        <li><span class="badge b-measured">measured</span> live sensor readings (Supabase), shown with 📡. ${S.live.rows ? S.live.rows + " live rows now." : "No sensors are reporting yet."}</li>
        <li>"Not published" means no source we trust gives that fact. We never fill gaps with guesses.</li></ul>
      <h4>Coverage (${S.data.radius_m / 1000} km around Wilkinson)</h4>
      <ul><li>${L.length} lots and decks: ${c((x) => x.nameSource === "Duke Parking (official)")} with official Duke data, ${c((x) => x.capacity.confidence === "counted")} with a counted capacity.</li>
        <li>${c((x) => x.visitorRate)} lots and ${St.filter((x) => x.visitorRate).length} streets with a published visitor rate.</li>
        <li>${St.filter((x) => x.kind === "street").length} on-street items, ${St.filter((x) => x.kind === "pilot").length} sensor-pilot spot groups.</li></ul>
      <h4>Occupancy model</h4><p>${esc(src.tier3)}. ${esc(src.tier3Note)} Range = ${esc(src.tier3Band)}.</p>
      <h4>Sources</h4>
      <ul><li>${esc(src.duke)}</li><li>Lot outlines and stalls: ${esc(src.osm)}</li><li>${esc(src.signs)}</li>
        <li>Map: ${M.kind === "google" ? "Google Maps (display only; no Google data in our inventory)" : "OpenStreetMap tiles"}</li></ul>
      <p><a href="classic.html">Classic analyst view</a> · <a href="privacy.html">Privacy</a> · <a href="https://github.com/momacaes9/parkshark" target="_blank" rel="noopener">GitHub</a></p>`;
  }

  // ---------------------------------------------------------------- boot
  function tick() {
    const d = now();
    $("clockTime").textContent = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    $("clockDate").textContent = d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  }
  async function boot() {
    const [cfg, data] = await Promise.all([
      fetch("api/config").then((r) => (r.ok ? r.json() : {})).catch(() => ({})),
      fetch("data/main.json").then((r) => r.json()),
    ]);
    S.cfg = cfg; S.data = data;
    S.home = { lat: data.center.lat, lon: data.center.lon, name: data.center.name };
    S.dest = { ...S.home };
    S.items = [...data.lots, ...data.streets];
    S.items.forEach((it) => S.byId.set(it.id, it));
    tick(); setInterval(tick, 15000);
    await fetchLive();
    await mapInit();
    drawMap(); renderList(); renderMore();

    document.querySelectorAll(".chip").forEach((b) => b.onclick = () => {
      const f = b.dataset.f, on = !S.filters.has(f);
      on ? S.filters.add(f) : S.filters.delete(f);
      b.setAttribute("aria-pressed", String(on));
      refreshPins(); renderList();
    });
    $("search").oninput = (e) => { $("searchClear").hidden = !e.target.value; clearTimeout(suggestTimer); suggestTimer = setTimeout(() => suggest(e.target.value), 200); };
    $("search").onkeydown = (e) => { if (e.key === "Enter") { const b = $("suggest").querySelector("button[data-i]"); if (b) b.click(); } if (e.key === "Escape") $("suggest").hidden = true; };
    $("searchClear").onclick = () => { $("search").value = ""; $("searchClear").hidden = true; $("suggest").hidden = true; };
    document.addEventListener("click", (e) => { if (!$("searchWrap").contains(e.target)) $("suggest").hidden = true; });
    $("destReset").onclick = () => setDest(S.home.lat, S.home.lon, S.home.name, "534 Research Dr · Duke University");
    $("locate").onclick = () => {
      // Geolocation stays in this browser: used only to draw your dot and center the map, never stored or sent.
      if (!navigator.geolocation) { alert("Location is not available in this browser."); return; }
      $("locate").disabled = true;
      navigator.geolocation.getCurrentPosition((p) => {
        $("locate").disabled = false;
        S.me = { lat: p.coords.latitude, lon: p.coords.longitude };
        drawMap(); mapPan(S.me.lat, S.me.lon, 16);
      }, (err) => { $("locate").disabled = false; $("mapNote").hidden = false; $("mapNote").textContent = "Location unavailable: " + err.message; },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
    };
    $("theme").onclick = async () => {
      const t = isDark() ? "light" : "dark";
      document.documentElement.dataset.theme = t;
      try { localStorage.setItem("ps-theme", t); } catch (e) {}
      const c = M.map.getCenter(), lat = typeof c.lat === "function" ? c.lat() : c.lat, lng = typeof c.lng === "function" ? c.lng() : c.lng, z = mapZoom();
      await mapInit(); drawMap(); mapPan(lat, lng, z);
    };
    $("sheetHandle").onclick = () => $("results").classList.toggle("expanded");
    $("chat-open").onclick = () => { openChat(); $("chat-input").focus(); };
    $("chat-close").onclick = () => { $("chat").hidden = true; $("chat-open").hidden = false; };
    $("chat-form").onsubmit = (e) => { e.preventDefault(); const t = $("chat-input").value; $("chat-input").value = ""; askConcierge(t); };
    $("chat-reset").onclick = () => { chat.messages = []; $("chat-log").innerHTML = ""; };
    document.querySelectorAll("#chat .sugg button").forEach((b) => b.onclick = () => askConcierge(b.textContent));
    $("pay-close").onclick = () => { $("pay-modal").hidden = true; };
    $("pay-modal").onclick = (e) => { if (e.target === $("pay-modal")) $("pay-modal").hidden = true; };
    $("pay-sim").onclick = () => confirmPay("simulated demo payment");
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("detail").hidden) closeDetail(); });
    $("demo-start").disabled = false;
    $("demo-start").onclick = () => demo.active ? stopDemo() : startDemo();
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && demo.active) stopDemo(); });
    if (qs.get("demo") === "1") startDemo();
    // Real time: refresh estimates every minute, live sensors too.
    setInterval(async () => { if (demo.active) return; await fetchLive(); if (demo.active) return; refreshPins(); renderList(); if (S.selected) renderDetail(S.byId.get(S.selected)); }, 60000);
    window.parkshark = S;
  }
  boot().catch((e) => { console.error(e); document.body.insertAdjacentHTML("beforeend", `<div class="modal"><div class="modal-box">ParkShark failed to load: ${esc(e.message)}</div></div>`); });
})();
