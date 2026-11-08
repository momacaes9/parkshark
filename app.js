/* ParkShark Duke - dashboard logic (plain JS + Leaflet). */
(function () {
  "use strict";

  const DEFAULTS = {
    SUPABASE_URL: "", SUPABASE_KEY: "", GOOGLE_MAPS_KEY: "",
    DESTINATION: { name: "Wilkinson Building", lat: 36.00346, lon: -78.93805 },
    ORIGIN: { name: "Erwin Rd & Research Dr", lat: 36.0076, lon: -78.9418 },
  };
  const COLORS = { open: "#22c55e", taken: "#ef4444", ada: "#3b82f6", ev: "#a855f7", lot: "#facc15", street: "#22c55e" };
  const params = new URLSearchParams(location.search);   // ?weather=hot|rain|normal  ?hour=9  for demos
  let CFG = DEFAULTS;

  // ------------------------------------------------------------------ utils
  const $ = (id) => document.getElementById(id);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function hash01(str) {           // deterministic pseudo-random in [0,1)
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    h ^= h >>> 13; h = Math.imul(h, 0x5bd1e995); h ^= h >>> 15;
    return (h >>> 0) / 4294967296;
  }
  function distM(lat1, lon1, lat2, lon2) {
    const R = 6371000, toR = Math.PI / 180;
    const dLat = (lat2 - lat1) * toR, dLon = (lon2 - lon1) * toR;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * toR) * Math.cos(lat2 * toR) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }
  function ringCentroid(coords) {   // [[lon,lat],...]  simple vertex average (fine for stalls)
    let x = 0, y = 0; const n = coords.length - 1 || 1;
    for (let i = 0; i < n; i++) { x += coords[i][0]; y += coords[i][1]; }
    return [x / n, y / n];
  }
  function distToRingsM(lon, lat, rings) {   // metres from point to nearest polygon edge
    const kx = 111320 * Math.cos(lat * Math.PI / 180), ky = 111320;
    let best = Infinity;
    for (const r of rings) for (let i = 0; i < r.length - 1; i++) {
      const ax = (r[i][0] - lon) * kx, ay = (r[i][1] - lat) * ky;
      const bx = (r[i + 1][0] - lon) * kx, by = (r[i + 1][1] - lat) * ky;
      const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy;
      const t = L ? clamp(-(ax * dx + ay * dy) / L, 0, 1) : 0;
      best = Math.min(best, Math.hypot(ax + t * dx, ay + t * dy));
    }
    return best;
  }
  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.onload = res; s.onerror = () => rej(new Error("failed " + src));
      document.head.appendChild(s);
    });
  }

  // ------------------------------------------------------------------ state
  const state = {
    lots: new Map(),          // id -> {props, feature, layer, spots:[], occ, free}
    curbs: new Map(),         // id -> {props, layer, spots:[], occ}
    curbCV: {},               // curb segment id -> CV verification result
    pricing: null, events: [], activeEvent: null, destElev: null, sortBy: "value",
    spots: new Map(),         // id -> {props, layer, center:[lon,lat], taken, big}
    rowIndex: new Map(),      // "lot|row|pos" -> spot id
    streets: [], chargers: [],
    occSource: "prior", liveLot: {}, liveSpot: {},
    weather: { tempF: null, precip: 0, cloud: null, isDay: 1, label: "loading…" },
    selectedSpot: null, origin: null,
  };

  // ------------------------------------------------------------------ map
  let map, spotRenderer, originMarker;
  function initMap() {
    map = L.map("map", { zoomControl: true, preferCanvas: true, maxZoom: 21 })
      .setView([CFG.DESTINATION.lat, CFG.DESTINATION.lon], 17);
    spotRenderer = L.canvas({ padding: 0.3, tolerance: 2 });
    const esriSat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxNativeZoom: 19, maxZoom: 21, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" }).addTo(map);
    const esriStreets = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
      { maxNativeZoom: 19, maxZoom: 21, attribution: "Tiles © Esri" });
    const bases = { "Esri Satellite": esriSat, "Esri Streets": esriStreets };
    const layerCtl = L.control.layers(bases, {}, { collapsed: true }).addTo(map);
    L.control.scale({ imperial: true, metric: true }).addTo(map);

    if (CFG.GOOGLE_MAPS_KEY) {     // optional Google satellite basemap (display only)
      loadScript("https://maps.googleapis.com/maps/api/js?key=" + encodeURIComponent(CFG.GOOGLE_MAPS_KEY) + "&v=weekly")
        .then(() => loadScript("https://unpkg.com/leaflet.gridlayer.googlemutant@0.13.5/dist/Leaflet.GoogleMutant.js"))
        .then(() => layerCtl.addBaseLayer(L.gridLayer.googleMutant({ type: "satellite", maxZoom: 21 }), "Google Satellite"))
        .catch((e) => console.warn("Google basemap unavailable:", e.message));
    }

    const destIcon = L.divIcon({ className: "", html: '<div style="font-size:26px;line-height:26px">🏛️</div>', iconSize: [26, 26], iconAnchor: [13, 13] });
    L.marker([CFG.DESTINATION.lat, CFG.DESTINATION.lon], { icon: destIcon, zIndexOffset: 1000 })
      .addTo(map).bindTooltip(CFG.DESTINATION.name + " (destination)");
    const carIcon = L.divIcon({ className: "", html: '<div style="font-size:24px;line-height:24px">🚗</div>', iconSize: [24, 24], iconAnchor: [12, 12] });
    state.origin = { ...CFG.ORIGIN };
    originMarker = L.marker([state.origin.lat, state.origin.lon], { icon: carIcon, draggable: true, zIndexOffset: 1000 })
      .addTo(map).bindTooltip("Start point (drag me or right-click map)");
    originMarker.on("dragend", () => { const p = originMarker.getLatLng(); setOrigin(p.lat, p.lng); });
    map.on("contextmenu", (e) => { originMarker.setLatLng(e.latlng); setOrigin(e.latlng.lat, e.latlng.lng); });
  }
  function setOrigin(lat, lon) { state.origin = { name: "Custom start", lat, lon }; renderAgent(); }

  // ------------------------------------------------------------------ data
  function curbLineStyle(p) {
    const obs = p.confidence === "observed parking";
    return { color: obs ? "#22c55e" : "#f59e0b", weight: obs ? 4 : 3, opacity: 0.9, dashArray: obs ? null : "5,6" };
  }
  function curbPopup(p) {
    return `<b>${esc(p.name || "Unnamed road")} · ${esc(p.side)} curb</b><br>` +
      `${p.length_m} m · ~${p.est_spaces} parallel spaces (6.1 m)<br>Confidence: <b>${esc(p.confidence)}</b>` +
      (p.observed_cars != null ? `<br>Cars detected in NC OneMap ortho${p.cv_date ? " (" + esc(p.cv_date) + ")" : ""}: <b>${p.observed_cars}</b>` : "") +
      (p.cv_image ? `<br><img src="${esc(p.cv_image)}" style="width:220px;margin-top:4px;border-radius:4px" alt="detections">` : "") +
      `<br><small>Generated from OSM road geometry; no signage data. A car seen in one aerial photo may be a drop-off, not legal parking. Check the signs.</small>`;
  }
  function lotName(p) {
    if (p.name) return p.name;
    if (p.duke_name) return (p.duke_match === "osm_name" ? "" : "≈ ") + p.duke_name;
    if (p.ref) return "Lot " + p.ref;
    const kind = p.garage ? "Parking garage" : p.source === "parking_aisle" ? "Unmapped lot (from aisles)" :
      p.source === "manual" ? "Manual lot" : (p.parking === "rooftop" ? "Rooftop lot" : "Surface lot");
    return `${kind} · ${p.id}`;
  }

  function buildLayers(fc) {
    const lotLayer = L.layerGroup().addTo(map);
    const spotLayer = L.layerGroup().addTo(map);
    const streetLayer = L.layerGroup().addTo(map);
    const chargerLayer = L.layerGroup().addTo(map);
    const curbLayer = L.layerGroup().addTo(map);       // candidate curb segments + their spaces

    for (const f of fc.features) {
      const p = f.properties;
      if (p.kind === "lot") {
        const lc = p.source === "manual" ? "#22d3ee" : COLORS.lot;
        const layer = L.geoJSON(f, {
          style: { color: lc, weight: 2, fillColor: lc, fillOpacity: p.garage ? 0.18 : 0.04,
                   dashArray: p.source === "parking_aisle" ? "6,4" : null },
          renderer: spotRenderer, interactive: true,
        });
        layer.on("click", (e) => { if (!e.originalEvent._spotHit) selectLot(p.id, false); });
        layer.addTo(lotLayer);
        state.lots.set(p.id, { props: p, feature: f, layer, spots: [], occ: 0, free: 0 });
      }
    }
    for (const f of fc.features) {
      const p = f.properties;
      if (p.kind !== "curb") continue;
      const cv = state.curbCV[p.id];
      if (cv) Object.assign(p, { confidence: cv.observed ? "observed parking" : "candidate - checked, no cars seen",
                                 observed_cars: cv.cars, cv_image: cv.image, cv_date: cv.imagery_date });
      const layer = L.polyline(f.geometry.coordinates.map((c) => [c[1], c[0]]), curbLineStyle(p));
      layer.bindPopup(() => curbPopup(p));
      layer.addTo(curbLayer);
      state.curbs.set(p.id, { props: p, layer, spots: [], occ: 0 });
    }
    for (const f of fc.features) {
      const p = f.properties;
      if (p.kind === "space") {
        let layer, center;
        if (f.geometry.type === "Point") {
          center = f.geometry.coordinates;
          layer = L.circleMarker([center[1], center[0]], { radius: 4, renderer: spotRenderer, weight: 1 });
        } else {
          center = ringCentroid(f.geometry.coordinates[0]);
          layer = L.polygon(f.geometry.coordinates[0].map((c) => [c[1], c[0]]), { renderer: spotRenderer, weight: 1 });
        }
        layer.on("click", (e) => { e.originalEvent._spotHit = true; L.DomEvent.stopPropagation(e); selectSpot(p.id); });
        layer.addTo(p.curb ? curbLayer : spotLayer);
        const s = { props: p, layer, center, taken: false, big: false };
        state.spots.set(p.id, s);
        if (p.lot && state.lots.has(p.lot)) state.lots.get(p.lot).spots.push(s);
        if (p.curb && state.curbs.has(p.curb)) state.curbs.get(p.curb).spots.push(s);
        if (p.row != null && p.pos != null) state.rowIndex.set(`${p.lot}|${p.row}|${p.pos}`, p.id);
      } else if (p.kind === "street") {
        const layer = L.polyline(f.geometry.coordinates.map((c) => [c[1], c[0]]), { color: COLORS.street, weight: 7, opacity: 0.85 });
        layer.bindPopup(`<b>${esc(p.name || "Street parking")}</b><br>${esc(p.sides.join(" + "))} side · ~${p.est_spaces} spaces`);
        layer.addTo(streetLayer); state.streets.push(p);
      } else if (p.kind === "candidate") {
        L.geoJSON(f, { style: { color: "#94a3b8", weight: 2, dashArray: "4,4", fillOpacity: 0.05 } })
          .bindPopup(`<b>Paved area (candidate)</b><br>${p.area_m2} m² · not tagged as parking in OSM.<br>Draw it as a lot if it is one.`)
          .addTo(lotLayer);
      } else if (p.kind === "charger") {
        L.circleMarker([f.geometry.coordinates[1], f.geometry.coordinates[0]], { radius: 7, color: "#fff", weight: 2, fillColor: COLORS.ev, fillOpacity: 1 })
          .bindPopup(`<b>⚡ ${esc(p.name || "EV charging station")}</b><br>${esc(p.operator || "")} ${p.capacity ? "· " + p.capacity + " ports" : ""}`)
          .addTo(chargerLayer);
        state.chargers.push(p);
      }
    }
    // spots only make sense when zoomed in; hide below z15 to keep the overview readable
    const toggleSpots = () => {
      if (map.getZoom() >= 15) spotLayer.addTo(map); else spotLayer.remove();
      if (map.getZoom() >= 16 && $("show-curbs").checked) curbLayer.addTo(map); else curbLayer.remove();
    };
    map.on("zoomend", toggleSpots); $("show-curbs").onchange = toggleSpots; toggleSpots();
  }

  // ------------------------------------------------------------------ draw missing lots (Leaflet.draw)
  const DRAW_KEY = "parkshark.drawnLots";
  function initDraw() {
    if (!L.Draw) { $("tools").hidden = true; return; }
    const drawn = new L.FeatureGroup().addTo(map);
    const style = { color: "#22d3ee", weight: 2, dashArray: "6,4", fillOpacity: 0.15 };
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem(DRAW_KEY) || "[]"); } catch (e) { saved = []; }
    const persist = () => {
      const feats = [];
      drawn.eachLayer((l) => feats.push(l.toGeoJSON()));
      try { localStorage.setItem(DRAW_KEY, JSON.stringify(feats)); } catch (e) { /* private mode */ }
      $("drawn-count").textContent = feats.length ? `${feats.length} drawn` : "";
      return feats;
    };
    const addFeature = (f) => {
      const layer = L.geoJSON(f, { style }).getLayers()[0];
      layer.feature = f;
      layer.bindTooltip(f.properties.name || "Drawn lot");
      drawn.addLayer(layer);
    };
    saved.forEach(addFeature);
    persist();
    const control = new L.Control.Draw({
      position: "topleft",
      draw: { polygon: { allowIntersection: false, showArea: true, shapeOptions: style }, rectangle: { shapeOptions: style },
              polyline: false, circle: false, circlemarker: false, marker: false },
      edit: { featureGroup: drawn },
    });
    map.addControl(control);
    map.on(L.Draw.Event.CREATED, (e) => {
      const f = e.layer.toGeoJSON();
      const name = window.prompt("Lot name (optional)", "") || "";
      const cap = window.prompt("Capacity if known (optional)", "") || "";
      f.properties = { name: name || undefined, capacity: cap.replace(/\D/g, "") || undefined, drawn_at: new Date().toISOString() };
      addFeature(f);
      persist();
    });
    map.on(L.Draw.Event.EDITED, persist);
    map.on(L.Draw.Event.DELETED, persist);
    $("draw-btn").onclick = () => new L.Draw.Polygon(map, control.options.draw.polygon).enable();
    $("clear-btn").onclick = () => { drawn.clearLayers(); persist(); };
    $("dl-btn").onclick = async () => {
      let existing = [];
      try { existing = (await (await fetch("data/manual_lots.geojson", { cache: "no-store" })).json()).features || []; } catch (e) { /* none yet */ }
      const fc = { type: "FeatureCollection", features: existing.concat(persist()) };
      const url = URL.createObjectURL(new Blob([JSON.stringify(fc, null, 1)], { type: "application/geo+json" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: "manual_lots.geojson" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    };
  }

  // ------------------------------------------------------------------ occupancy
  function nowDate() {
    const d = new Date();
    if (params.has("date")) { const [y, m, dd] = params.get("date").split("-").map(Number); d.setFullYear(y, m - 1, dd); }
    if (params.has("hour")) d.setHours(+params.get("hour"), 0, 0, 0);
    return d;
  }
  // Time-of-day prior: typical weekday campus curve, much lower on weekends.
  function priorOcc(p, d) {
    const h = d.getHours() + d.getMinutes() / 60, wd = d.getDay();
    const curve = [[0, .12], [6, .15], [7.5, .45], [9, .82], [10.5, .93], [14, .92], [16, .80], [18, .50], [20, .30], [24, .12]];
    let base = .12;
    for (let i = 0; i < curve.length - 1; i++) {
      const [h0, v0] = curve[i], [h1, v1] = curve[i + 1];
      if (h >= h0 && h <= h1) { base = v0 + (v1 - v0) * (h - h0) / (h1 - h0); break; }
    }
    if (wd === 0 || wd === 6) base *= 0.4;
    const closeness = Math.exp(-(p.dist_to_wilkinson_m || 800) / 700);       // closer lots fill first
    const jitter = (hash01(p.id) - 0.5) * 0.12;
    return clamp(base * (0.82 + 0.25 * closeness) + jitter, 0.02, 0.99);
  }

  // ------------------------------------------------------------------ events (goduke.com home games)
  function eventWindow(e) {
    if (!e.time) return null;
    const [y, m, d] = e.date.split("-").map(Number), [hh, mm] = e.time.split(":").map(Number);
    const start = new Date(y, m - 1, d, hh, mm);
    return { start, from: new Date(start - e.before_h * 3600e3), to: new Date(+start + e.after_h * 3600e3) };
  }
  function findActiveEvent(d) {
    const today = d.toDateString();
    let active = null, next = null;
    for (const e of state.events) {
      const w = eventWindow(e);
      const day = new Date(e.date + "T12:00:00");
      if (w && d >= w.from && d <= w.to) active = { e, w, live: true };
      else if (!active && day.toDateString() === today) active = { e, w, live: false };
      if (!next && day >= new Date(d.toDateString())) next = e;
    }
    return { active, next };
  }
  function eventBoost(p, occ) {
    const a = state.activeEvent;
    if (!a || !a.live) return occ;
    const [vlat, vlon] = a.e.venue_ll;
    const dist = distM(p.center[1], p.center[0], vlat, vlon);
    if (dist > a.e.radius_m) return occ;
    return clamp(occ + (1 - occ) * a.e.boost * Math.sqrt(1 - dist / a.e.radius_m), 0, 0.995);
  }
  function renderEvent(d) {
    const { active, next } = findActiveEvent(d);
    state.activeEvent = active;
    const sportIcon = (e) => (e.sport === "football" ? "🏈" : "🏀");
    const fmt = (e) => `${new Date(e.date + "T12:00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} ${e.time_text}`;
    $("next-event").textContent = next ? `${sportIcon(next)} ${next.opponent} · ${fmt(next)}` : "none scheduled";
    const b = $("event-banner");
    if (!active) { b.hidden = true; return; }
    const e = active.e, rules = (state.pricing && state.pricing.events[e.sport]) || {};
    const fill = active.w ? new Date(active.w.from).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "early";
    b.hidden = false;
    b.innerHTML = `${sportIcon(e)} <b>Game day: Duke vs ${esc(e.opponent)}</b>, ${esc(e.time_text)} at ${esc(e.venue)}.<br>` +
      (active.live ? "Event traffic is ON: lots near the venue are predicted near full." : `Lots near ${esc(e.venue)} are predicted to fill from ${fill}.`) +
      (e.sport === "football"
        ? ` General public parking is free at ${esc((rules.general_public_free || []).join(", "))}. Blue Zone permit holders must move their cars.`
        : ` General public lots (${esc((rules.general_public_lots || []).join(", "))}) cost $${rules.general_public_per_car || 25}.`) +
      ` <a href="${esc(e.sport === "football" ? state.pricing.sources.football : state.pricing.sources.mens_basketball)}" target="_blank" rel="noopener" style="color:#fde68a">Duke rules</a>`;
  }

  async function fetchLive() {
    if (!CFG.SUPABASE_URL || !CFG.SUPABASE_KEY) return false;
    try {
      const r = await fetch(CFG.SUPABASE_URL.replace(/\/$/, "") + "/rest/v1/occupancy_latest?select=*", {
        headers: { apikey: CFG.SUPABASE_KEY, Authorization: "Bearer " + CFG.SUPABASE_KEY },
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const rows = await r.json();
      state.liveLot = {}; state.liveSpot = {};
      for (const row of rows) {
        if (row.spot_id) state.liveSpot[row.spot_id] = !!row.occupied;
        else if (row.lot_id != null && row.occupancy != null) state.liveLot[row.lot_id] = clamp(+row.occupancy, 0, 1);
      }
      return rows.length > 0;
    } catch (e) {
      console.warn("Supabase unavailable, using time-of-day prior:", e.message);
      return false;
    }
  }

  async function updateOccupancy() {
    const live = await fetchLive();
    const d = nowDate(), bucket = Math.floor(d.getTime() / (5 * 60 * 1000));
    renderEvent(d);
    state.occSource = (live ? "Supabase live sensors" : "Time-of-day prior") + (state.activeEvent && state.activeEvent.live ? " + game-day boost" : "");
    for (const lot of state.lots.values()) {
      const p = lot.props;
      let occ = state.liveLot[p.id];
      const liveSpots = lot.spots.filter((s) => s.props.id in state.liveSpot);
      if (occ == null && liveSpots.length) occ = liveSpots.filter((s) => state.liveSpot[s.props.id]).length / liveSpots.length;
      if (occ == null) occ = eventBoost(p, priorOcc(p, d));
      lot.occ = occ;
      // mark exactly round(occ * n) spots as taken, chosen by a stable per-5-min shuffle
      const order = lot.spots.slice().sort((a, b) => hash01(a.props.id + bucket) - hash01(b.props.id + bucket));
      const nTaken = Math.round(occ * order.length);
      order.forEach((s, i) => {
        s.taken = s.props.id in state.liveSpot ? state.liveSpot[s.props.id] : i < nTaken;
        s.big = s.taken && hash01("veh" + s.props.id + bucket) > 0.72;   // ~28% trucks/SUVs
      });
      lot.free = Math.max(0, Math.round((p.capacity || 0) * (1 - occ)));
    }
    for (const s of state.spots.values()) if (!s.props.lot && !s.props.curb) s.taken = hash01(s.props.id + bucket) < 0.7;
    for (const c of state.curbs.values()) {          // curb occupancy only meaningful once observed
      c.occ = eventBoost(c.props, clamp(priorOcc({ id: c.props.id, dist_to_wilkinson_m: c.props.dist_to_wilkinson_m }, d) * 1.05, 0.02, 0.99));
      c.spots.forEach((s) => { s.taken = hash01(s.props.id + bucket) < c.occ; s.big = s.taken && hash01("veh" + s.props.id) > 0.72; });
    }
    styleSpots();
    $("occ-source").textContent = state.occSource;
    $("updated").textContent = (params.has("date") ? d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " : "") +
      d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + (params.has("hour") || params.has("date") ? " (simulated)" : "");
    renderTotals(); renderLots(); renderAgent();
    if (state.selectedSpot) selectSpot(state.selectedSpot, true);
  }

  function spotStyle(s) {
    const p = s.props;
    if (p.curb) {
      const c = state.curbs.get(p.curb);
      if (!c || c.props.confidence !== "observed parking")
        return { color: "#f59e0b", weight: 1, dashArray: "3,3", fillColor: "#f59e0b", fillOpacity: 0.12, opacity: 0.8 };
    }
    const special = p.ada ? COLORS.ada : p.ev ? COLORS.ev : null;
    const fill = s.taken ? COLORS.taken : (special || COLORS.open);
    return {
      color: s.taken && special ? special : (p.estimated ? "#f8fafc" : "#0f172a"),
      weight: s.props.id === state.selectedSpot ? 3 : (s.taken && special ? 2 : 1),
      dashArray: p.estimated ? "2,2" : null,
      fillColor: fill, fillOpacity: 0.75, opacity: 0.9,
    };
  }
  function styleSpots() { for (const s of state.spots.values()) s.layer.setStyle(spotStyle(s)); }

  // ------------------------------------------------------------------ weather (Open-Meteo, keyless)
  async function updateWeather() {
    const w = state.weather;
    try {
      const u = `https://api.open-meteo.com/v1/forecast?latitude=${CFG.DESTINATION.lat}&longitude=${CFG.DESTINATION.lon}` +
        "&current=temperature_2m,precipitation,cloud_cover,is_day&temperature_unit=fahrenheit";
      const j = await (await fetch(u)).json();
      Object.assign(w, { tempF: j.current.temperature_2m, precip: j.current.precipitation, cloud: j.current.cloud_cover, isDay: j.current.is_day });
    } catch (e) { console.warn("weather unavailable:", e.message); }
    const sim = params.get("weather");
    if (sim === "hot") Object.assign(w, { tempF: 95, precip: 0, cloud: 5, isDay: 1 });
    if (sim === "rain") Object.assign(w, { tempF: 58, precip: 2.5, cloud: 100 });
    w.label = w.tempF == null ? "unavailable" :
      `${Math.round(w.tempF)}°F · ${w.precip > 0 ? "rain 🌧" : (w.cloud < 40 && w.isDay ? "sunny ☀️" : "cloudy ☁️")}${sim ? " (simulated)" : ""}`;
    $("weather").textContent = w.label;
  }

  // ------------------------------------------------------------------ driver profile + pricing
  function profile() {
    return { role: $("role").value, hours: +$("hours").value, vehicle: $("vehicle").value, vot: +$("vot").value,
             wheelchair: $("wheelchair").checked, senior: $("senior").checked, ev: $("evneed").checked };
  }
  function matchRule(rules, p) {
    return (rules || []).find((r) => (r.match.osm_name && r.match.osm_name === p.name) ||
                                     (r.match.duke_name && r.match.duke_name === p.duke_name));
  }
  function isVisitorFacility(p) { return !!matchRule(state.pricing && state.pricing.visitor_hourly, p); }
  // Price for parking in a lot (or curb segment) for this profile. cost=null means unknown.
  function priceFor(p, prof) {
    const P = state.pricing, d = nowDate();
    if (!P) return { ok: true, cost: null, label: "price unknown" };
    if (p.kind === "curb") return { ok: true, cost: null, label: "unknown, check signs", note: P.default_rules.curb_candidate };
    const ev = state.activeEvent && state.activeEvent.live ? state.activeEvent.e : null;
    const dn = (p.duke_name || p.name || "").replace(/^Parking Garage IV.*/, "PG4");
    if (ev && ev.sport === "football") {
      const free = P.events.football.general_public_free.some((n) => dn.startsWith(n.replace(" Lot", "")) || dn === n);
      if (free) return { ok: true, cost: 0, label: "free (game day)", note: "Football general public parking" };
      const near = distM(p.center[1], p.center[0], ev.venue_ll[0], ev.venue_ll[1]) < ev.radius_m;
      if (near && prof.role !== "permit") return { ok: false, label: "reserved for football", note: P.events.football.notes };
    }
    if (ev && ev.sport === "mens-basketball" && P.events["mens-basketball"].general_public_lots.some((n) => dn.startsWith(n.replace(" Lot", ""))))
      return { ok: true, cost: P.events["mens-basketball"].general_public_per_car, label: "$25 game day", note: P.events["mens-basketball"].notes };
    const vis = matchRule(P.visitor_hourly, p);
    if (vis) {
      const sundayFree = /Sundays 8 am/.test(vis.notes || "") && d.getDay() === 0 && d.getHours() >= 8 && d.getHours() < 13 && !p.garage;
      if (prof.vehicle === "large" && vis.clearance_ft && vis.clearance_ft < 7.5)
        return { ok: false, label: `clearance ${vis.clearance_ft.toFixed(1)} ft`, note: "Too low for a large SUV/truck with roof load" };
      return { ok: true, cost: sundayFree ? 0 : vis.per_hour * prof.hours, label: sundayFree ? "free (Sun AM)" : `$${vis.per_hour.toFixed(2)}/h`,
               note: `${vis.name} · ${vis.payment}${vis.notes ? " · " + vis.notes : ""}` };
    }
    if (prof.role === "permit") return { ok: true, cost: 0, label: "your permit", note: "If your permit zone covers this lot" };
    if (prof.role === "affiliate") {
      const day = matchRule(P.daily_permits_affiliates_only, p);
      if (day) return { ok: true, cost: day.per_day, label: `$${day.per_day}/day pass`, note: `Blue Spot daily permit: ${day.name}` };
    }
    return { ok: false, label: p.duke_name ? "Duke permit lot" : "permit (assumed)",
             note: p.duke_name ? P.default_rules.duke_permit_lot : P.default_rules.unnamed_campus_lot };
  }
  function climbM(elev) {
    return state.destElev != null && elev != null ? Math.max(0, state.destElev - elev) : 0;
  }

  // ------------------------------------------------------------------ agent
  function rankOptions() {
    const o = state.origin, D = CFG.DESTINATION, prof = profile();
    const out = [];
    const hasEV = [...state.lots.values()].some((l) => l.props.ev > 0);
    const consider = (kind, item, p, free, occ) => {
      if (free < 1) return;
      if (prof.wheelchair && kind === "lot" && !(p.ada > 0) && !p.garage) return;   // garages: ADA spaces required by law
      if (prof.wheelchair && kind === "curb") return;
      if (prof.ev && hasEV && !(p.ev > 0)) return;
      const price = priceFor(p, prof);
      if (!price.ok) return;
      const [lat, lon] = [p.center[1], p.center[0]];
      const drive = distM(o.lat, o.lon, lat, lon) * 1.4 / 400 + (p.garage ? 1.5 : 0.5);
      const search = Math.min(15, 1 / Math.max(0.0001, 1 - occ));
      const speed = prof.wheelchair || prof.senior ? 55 : 80;                  // m/min
      const climb = climbM(p.elev_m);
      const walk = distM(lat, lon, D.lat, D.lon) * 1.3 / speed + (prof.senior || prof.wheelchair ? climb * 0.15 : 0);
      const total = drive + search + walk;
      const cost = price.cost == null ? 5 : price.cost;       // unknown legality: ~10% x $50 ticket risk
      out.push({ kind, item, p, drive, search, walk, total, price, climb, value: cost + total * prof.vot / 60 });
    };
    for (const lot of state.lots.values()) if (lot.props.capacity) consider("lot", lot, lot.props, lot.free, lot.occ);
    for (const c of state.curbs.values())
      if (c.props.confidence === "observed parking") consider("curb", c, c.props, Math.round(c.props.est_spaces * (1 - c.occ)), c.occ);
    const key = { value: (r) => r.value, time: (r) => r.total, cost: (r) => (r.price.cost == null ? 999 : r.price.cost) + r.total / 1000 }[state.sortBy];
    return { list: out.sort((a, b) => key(a) - key(b)), hasEV, prof };
  }
  function optionName(r) { return r.kind === "curb" ? `Curb: ${r.p.name || "street"} (${r.p.side})` : lotName(r.p); }
  function renderAgent() {
    const { list, hasEV, prof } = rankOptions();
    $("dest-name").textContent = CFG.DESTINATION.name;
    const rows = list.slice(0, 8);
    $("compare").querySelector("tbody").innerHTML = rows.map((r, i) => `
      <tr data-kind="${r.kind}" data-id="${esc(r.p.id)}"><td>${i + 1}</td>
        <td>${esc(optionName(r))}<div class="sm">🚗 ${r.drive.toFixed(1)} + 🔎 ${r.search.toFixed(1)} + 🚶 ${r.walk.toFixed(1)} min${r.climb >= 3 && (prof.senior || prof.wheelchair) ? ` · ⛰ ${Math.round(r.climb)} m up` : ""}</div></td>
        <td class="tt">${r.total.toFixed(1)} min</td>
        <td class="cost" title="${esc(r.price.note || "")}">${r.price.cost == null ? "?" : r.price.cost === 0 ? "$0" : "$" + r.price.cost.toFixed(2)}<div class="sm">${esc(r.price.label)}</div></td></tr>`).join("")
      || `<tr><td colspan="4">No option fits this profile right now.</td></tr>`;
    $("compare").querySelectorAll("tr[data-id]").forEach((tr) => tr.onclick = () => {
      if (tr.dataset.kind === "lot") selectLot(tr.dataset.id, true);
      else { const c = state.curbs.get(tr.dataset.id); map.fitBounds(c.layer.getBounds(), { maxZoom: 19 }); c.layer.openPopup(); }
    });
    const notes = [];
    if (list.slice(0, 8).some((r) => r.kind === "curb")) notes.push("Curb options: price/legality unknown, ranked with a $5 ticket-risk premium.");
    if (prof.role === "visitor") notes.push("Visitors can only use Duke visitor garages/lots ($2/h) unless it's game day; most campus lots are permit-only.");
    if (prof.ev && !hasEV) notes.push("⚡ No EV charging is mapped in OpenStreetMap near Wilkinson, so the EV filter can't narrow anything yet.");
    if (prof.wheelchair) notes.push("♿ Only lots with ADA stalls are shown. Accessible entrances aren't mapped in OSM, so walks are measured to the building centre.");
    if (prof.senior) notes.push("👵 Walk speed 55 m/min, plus 9 s per metre of climb (Copernicus DEM).");
    $("compare-note").textContent = notes.join(" ");
    $("profile-note").textContent = prof.vehicle === "large" ? "Large vehicle: avoids garages under 7.5 ft clearance (PG2 is 7'1\")." : "";
  }

  // ------------------------------------------------------------------ sidebar
  function renderTotals() {
    let cap = 0, free = 0, ada = 0, ev = 0;
    for (const l of state.lots.values()) { cap += l.props.capacity || 0; free += l.free; ada += l.props.ada; ev += l.props.ev; }
    $("totals").innerHTML = [["Lots", state.lots.size], ["Capacity", cap.toLocaleString()], ["Free now", free.toLocaleString()],
      ["Stalls drawn", [...state.spots.values()].filter((x) => !x.props.curb).length.toLocaleString()], ["ADA", ada], ["EV", ev + state.chargers.length], ["Curb candidates", [...state.curbs.values()].reduce((a, c) => a + c.props.est_spaces, 0).toLocaleString()],
      ["Curb observed", [...state.curbs.values()].filter((c) => c.props.confidence === "observed parking").reduce((a, c) => a + c.props.est_spaces, 0)]]
      .map(([k, v]) => `<div><b>${v}</b><small>${k}</small></div>`).join("");
  }
  function lotTags(p) {
    const t = [`<span class="tag ${p.tier}">${p.tier}</span>`];
    if (p.garage) t.push(`<span class="tag">garage${p.levels > 1 ? " · " + p.levels + " lv" : ""}</span>`);
    if (p.access) t.push(`<span class="tag ${["private", "no", "permit"].includes(p.access) ? "lock" : ""}">access: ${esc(p.access)}</span>`);
    if (p.fee) t.push(`<span class="tag">fee: ${esc(p.fee)}</span>`);
    if (p.operator) t.push(`<span class="tag">${esc(p.operator)}</span>`);
    const pr = priceFor(p, profile());
    t.push(`<span class="tag ${pr.ok ? "" : "lock"}">${pr.ok ? (pr.cost == null ? "?" : pr.cost === 0 ? "$0" : "$" + pr.cost.toFixed(2)) + " · " : ""}${esc(pr.label)}</span>`);
    if (p.ada) t.push(`<span class="tag">♿ ${p.ada}</span>`);
    if (p.ev) t.push(`<span class="tag">⚡ ${p.ev}</span>`);
    return t.join("");
  }
  function renderLots() {
    const q = $("filter").value.trim().toLowerCase();
    const lots = [...state.lots.values()]
      .filter((l) => !q || JSON.stringify(l.props).toLowerCase().includes(q) || lotName(l.props).toLowerCase().includes(q))
      .sort((a, b) => a.props.dist_to_wilkinson_m - b.props.dist_to_wilkinson_m);
    $("lot-count").textContent = `${lots.length} of ${state.lots.size}`;
    $("lots").innerHTML = lots.map((l) => {
      const p = l.props, pct = Math.round(l.occ * 100);
      const c = pct > 90 ? COLORS.taken : pct > 70 ? "#f59e0b" : COLORS.open;
      return `<li data-id="${esc(p.id)}">
        <div class="lot-top"><b title="${esc(lotName(p))}">${esc(lotName(p))}</b><span class="free" style="color:${c}">${l.free} / ${p.capacity} free</span></div>
        <div class="hint" style="margin:0">${p.dist_to_wilkinson_m} m from Wilkinson · ${p.mapped_spaces ? p.mapped_spaces + " mapped" : p.estimated_spaces ? p.estimated_spaces + " est. stalls" : "capacity only"}</div>
        <div class="bar"><i style="width:${pct}%;background:${c}"></i></div>
        <div class="tags">${lotTags(p)}</div></li>`;
    }).join("");
    $("lots").querySelectorAll("li").forEach((li) => li.onclick = () => selectLot(li.dataset.id, true));
  }
  function selectLot(id, zoom) {
    const lot = state.lots.get(id); if (!lot) return;
    if (zoom) map.fitBounds(lot.layer.getBounds(), { maxZoom: 19, padding: [30, 30] });
    const p = lot.props;
    L.popup().setLatLng([p.center[1], p.center[0]]).setContent(
      `<b>${esc(lotName(p))}</b><br>${lot.free} of ${p.capacity} free (${Math.round(lot.occ * 100)}% full)<br>` +
      `Confidence: <b>${p.tier}</b>${p.capacity_tagged ? " (capacity tagged in OSM)" : ""}<br>` +
      (() => { const pr = priceFor(p, profile()); return `Price: <b>${pr.ok ? (pr.cost == null ? "?" : "$" + pr.cost.toFixed(2)) : "n/a"}</b> · ${esc(pr.label)}<br>`; })() +
      (p.duke_name ? `Duke: ${esc(p.duke_name)} (${p.duke_capacity} spaces, ${esc(p.duke_rate_tier || "")}${p.duke_match === "proximity" ? ", name matched by proximity" : ""})<br>` : "") +
      `${p.area_m2.toLocaleString()} m² · ${p.dist_to_wilkinson_m} m to Wilkinson<br>` +
      `<a href="https://www.openstreetmap.org/${{ w: "way", r: "relation" }[p.id[0]]}/${p.id.slice(1)}" target="_blank" rel="noopener">View on OSM</a>`
    ).openOn(map);
    document.querySelectorAll("#lots li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
  }

  // ------------------------------------------------------------------ spot score
  function scoreSpot(s) {
    const p = s.props, w = state.weather, D = CFG.DESTINATION, reasons = [], prof = profile();
    let score = 100;
    const add = (delta, text) => { score += delta; reasons.push({ delta: Math.round(delta), text }); };
    const size = { compact: 0.6, standard: 1, large: 1.5 }[prof.vehicle];

    // neighbors in the same row
    if (p.row != null) {
      for (const [side, dp] of [["left", -1], ["right", 1]]) {
        const nid = state.rowIndex.get(`${p.lot}|${p.row}|${p.pos + dp}`);
        const n = nid && state.spots.get(nid);
        if (n && n.big) add(-12 * size, `Large vehicle parked on the ${side}: tight door clearance`);
        else if (n && n.taken) add(-4 * size, `Car parked on the ${side}`);
      }
      if (!p.curb) {
        if (p.row_len > 2 && p.pos > 0 && p.pos < p.row_len - 1) add(-8 * size, "Middle of the row: neighbors on both sides");
        else if (p.row_len > 1) add(prof.vehicle === "large" ? 6 : 0, "End stall: one side open, easier doors");
      }
    }
    if (prof.vehicle === "compact") add(3, "Compact car: easy fit in a 2.74 m stall");
    if (p.curb && prof.vehicle === "large") add(-10, "6.1 m parallel space is tight for a full-size truck");
    if (p.curb) add(-6, "Parallel parking at the curb, next to moving traffic");
    // walking distance (+ slope) to the destination
    const walkM = distM(s.center[1], s.center[0], D.lat, D.lon) * 1.3;
    const walkW = prof.senior || prof.wheelchair ? 2 : 1;
    add(-Math.min(35 * walkW, walkM / 20 * walkW), `${Math.round(walkM)} m walk to ${D.name} (~${Math.max(1, Math.round(walkM / (walkW > 1 ? 55 : 80)))} min)`);
    const lot = p.lot && state.lots.get(p.lot);
    const curb = p.curb && state.curbs.get(p.curb);
    const climb = climbM(lot ? lot.props.elev_m : curb ? curb.props.elev_m : null);
    if ((prof.senior || prof.wheelchair) && climb >= 2) add(-Math.min(20, climb * 2), `Uphill walk: about ${Math.round(climb)} m of climb`);
    // accessibility / EV
    if (prof.wheelchair) {
      if (p.ada) add(15, "ADA stall with access aisle");
      else if (p.curb) add(-25, "Curb space: no access aisle or ramp guaranteed");
      else add(-15, "Not an ADA stall: no access aisle");
    } else if (p.ada) add(-60, "ADA stall: disability placard required");
    if (p.ev) add(prof.ev ? 15 : -40, prof.ev ? "EV charger at this stall" : "EV charging stall: plug-in vehicles only");
    else if (prof.ev) add(-10, "No charger at this stall");
    // sun exposure in hot weather (interior stalls are farther from trees at the lot edge)
    if (w.tempF != null && w.tempF >= 80 && w.isDay && (w.cloud == null || w.cloud < 60)) {
      let exposure = 0.6;
      if (lot) {
        const g = lot.feature.geometry;
        const rings = g.type === "Polygon" ? g.coordinates : g.coordinates.flat();
        exposure = clamp(distToRingsM(s.center[0], s.center[1], rings) / 15, 0.2, 1);
      }
      if (lot && lot.props.garage) exposure = 0.1;
      add(-15 * exposure * clamp((w.tempF - 75) / 20, 0.3, 1), `${Math.round(w.tempF)}°F and sunny: ${exposure > 0.6 ? "open asphalt, no shade" : "partly shaded near lot edge"}`);
    }
    if (w.precip > 0) add(-Math.min(25, walkM / 25) * walkW, `Raining: ${Math.round(walkM)} m in the rain`);
    // price
    const price = priceFor(lot ? lot.props : curb ? curb.props : { kind: "curb" }, prof);
    if (!price.ok) add(-30, `Not available to you: ${price.label}`);
    else if (price.cost > 0) add(-Math.min(15, price.cost), `Costs $${price.cost.toFixed(2)} (${price.label})`);
    if (s.taken) { score = Math.min(score, 10); reasons.unshift({ delta: 0, text: "Occupied right now" }); }
    if (p.curb) reasons.push({ delta: 0, text: curb && curb.props.confidence === "observed parking" ? "Curb parking seen in aerial imagery (CV)" : "Curb candidate: unverified, check signs" });
    else if (p.estimated) reasons.push({ delta: 0, text: "Stall position estimated from lot outline/aisles (not individually mapped in OSM)" });
    return { score: Math.round(clamp(score, 0, 100)), reasons, price };
  }
  function selectSpot(id, silent) {
    const prev = state.selectedSpot && state.spots.get(state.selectedSpot);
    state.selectedSpot = id;
    if (prev) prev.layer.setStyle(spotStyle(prev));
    const s = state.spots.get(id); if (!s) return;
    s.layer.setStyle(spotStyle(s));
    const { score, reasons, price } = scoreSpot(s);
    const col = score >= 75 ? COLORS.open : score >= 50 ? "#f59e0b" : COLORS.taken;
    $("spot").hidden = false;
    $("spot-score").textContent = score;
    $("spot-score").style.background = col; $("spot-score").style.color = "#0b1220";
    const lot = s.props.lot && state.lots.get(s.props.lot);
    $("spot-meta").textContent = `${s.taken ? "Taken" : "Open"} · ${lot ? lotName(lot.props) : "curbside"} · spot ${id}`;
    $("spot-price").textContent = `💲 ${price.ok ? (price.cost == null ? "Price unknown" : price.cost === 0 ? "Free for you" : "$" + price.cost.toFixed(2) + " for your stay") : "Not available"} · ${price.label}${price.note ? " · " + price.note : ""}`;
    $("spot-reasons").innerHTML = reasons.map((r) =>
      `<li class="${r.delta < 0 ? "neg" : r.delta > 0 ? "pos" : ""}">${r.delta ? (r.delta > 0 ? "+" : "") + r.delta + " · " : ""}${esc(r.text)}</li>`).join("");
    if (!silent) $("spot").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    try { await loadScript("config.js"); } catch (e) { console.info("No config.js found, using defaults (copy config.example.js)."); }
    CFG = Object.assign({}, DEFAULTS, window.PARKSHARK_CONFIG || {});
    initMap();
    const getJSON = (u) => fetch(u).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    const [fc, cv, pricing, events] = await Promise.all([
      fetch("data/parking.geojson").then((r) => r.json()),
      getJSON("data/curb_cv.json"), getJSON("data/pricing.json"), getJSON("data/events.json"),
    ]);
    state.curbCV = (cv && cv.segments) || {};
    state.pricing = pricing; state.events = (events && events.events) || [];
    state.destElev = fc.metadata.dest_elev_m;
    buildLayers(fc);
    initDraw();
    $("filter").oninput = renderLots;
    const rerender = () => { renderAgent(); renderLots(); if (state.selectedSpot) selectSpot(state.selectedSpot, true); };
    ["role", "hours", "vehicle", "vot", "wheelchair", "senior", "evneed"].forEach((id) => $(id).onchange = rerender);
    $("sort").querySelectorAll("button").forEach((b) => b.onclick = () => {
      state.sortBy = b.dataset.sort;
      $("sort").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      renderAgent();
    });
    await updateWeather();
    await updateOccupancy();
    setInterval(updateOccupancy, 60 * 1000);
    setInterval(updateWeather, 10 * 60 * 1000);
    window.parkshark = state; window.parksharkMap = map;   // handy for debugging in the console
  }
  boot().catch((e) => { console.error(e); alert("ParkShark failed to load: " + e.message); });
})();
