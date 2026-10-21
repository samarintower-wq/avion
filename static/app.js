const $ = (id) => document.getElementById(id);
let authMode = "login";
let me = null;
let aircraft = [];
let cesiumViewer = null;
let config = {};
let currentPoint = { latitude: 40.6413, longitude: -73.7781, label: "New York JFK" };
let pendingPointRole = "departure";
let departure = null;
let destination = null;
let earthMode = "realistic";
let planeEntity = null;
let flightTimer = null;
let flightProgress = 0;

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.message || `Request failed (${response.status})`);
  return data;
}
function setMessage(id, text, error = true) {
  const el = $(id); el.textContent = text; el.style.color = error ? "var(--danger)" : "var(--green)";
}
function setAuthMode(mode) {
  authMode = mode;
  document.querySelectorAll("[data-auth-tab]").forEach(b => b.classList.toggle("active", b.dataset.authTab === mode));
  $("nickname-wrap").classList.toggle("hidden", mode !== "register");
  $("auth-submit").textContent = mode === "register" ? "Создать аккаунт" : "Войти в игру";
  $("password").autocomplete = mode === "register" ? "new-password" : "current-password";
  setMessage("auth-message", "");
}
document.querySelectorAll("[data-auth-tab]").forEach(b => b.addEventListener("click", () => setAuthMode(b.dataset.authTab)));

$("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { email: $("email").value.trim(), password: $("password").value };
  if (authMode === "register") body.nickname = $("nickname").value.trim();
  $("auth-submit").disabled = true;
  try {
    const result = await api(authMode === "register" ? "/api/register" : "/api/login", { method: "POST", body: JSON.stringify(body) });
    me = result.user;
    await showGame();
  } catch (err) {
    setMessage("auth-message", err.message);
  } finally { $("auth-submit").disabled = false; }
});

async function showGame() {
  $("auth-screen").classList.add("hidden");
  $("game").classList.remove("hidden");
  $("profile-name").textContent = me.nickname;
  await loadAircraft();
  applyProfile(me);
  await initGlobe();
}
function applyProfile(user) {
  me = user;
  earthMode = user.earth_mode || "realistic";
  departure = user.departure || null;
  destination = user.destination || null;
  $("departure-label").textContent = departure?.label || (departure ? coordsLabel(departure) : "Не выбран");
  $("destination-label").textContent = destination?.label || (destination ? coordsLabel(destination) : "Не выбран");
  document.querySelectorAll("[data-mode]").forEach(b => b.classList.toggle("active", b.dataset.mode === earthMode));
  document.querySelectorAll(".aircraft-card").forEach(c => c.classList.toggle("selected", c.dataset.id === user.selected_aircraft));
  const selected = aircraft.find(a => a.id === user.selected_aircraft) || aircraft[0];
  if (selected) {
    $("selected-label").textContent = `${selected.name} выбран`;
    $("hud-aircraft").textContent = selected.name;
    $("hud-speed").textContent = `${selected.cruise_kmh} km/h`;
  }
}
function coordsLabel(p) { return `${Number(p.latitude).toFixed(3)}, ${Number(p.longitude).toFixed(3)}`; }
async function loadAircraft() {
  const data = await api("/api/aircraft");
  aircraft = data.aircraft || [];
  $("aircraft-grid").innerHTML = aircraft.map((a, i) => `
    <button class="aircraft-card ${a.id === me.selected_aircraft ? "selected" : ""}" data-id="${a.id}">
      <div class="plane-art"><span class="plane-index">AIRCRAFT / ${(i+1).toString().padStart(2,"0")}</span><span class="card-badge">${a.type}</span><span class="plane-icon">✈</span></div>
      <div class="aircraft-info"><h3>${escapeHtml(a.name)}</h3><p>${escapeHtml(a.description)}</p>
      <div class="stats"><span>CRUISE<b>${a.cruise_kmh.toLocaleString()} km/h</b></span><span>RANGE<b>${a.range_km.toLocaleString()} km</b></span></div></div>
    </button>`).join("");
  $("aircraft-grid").querySelectorAll(".aircraft-card").forEach(card => card.addEventListener("click", async () => {
    try {
      const result = await api("/api/preferences", { method: "PUT", body: JSON.stringify({ selected_aircraft: card.dataset.id }) });
      applyProfile(result.user);
    } catch (e) { alert(e.message); }
  }));
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

document.querySelectorAll("[data-view]").forEach(btn => btn.addEventListener("click", async () => {
  document.querySelectorAll("[data-view]").forEach(b => b.classList.toggle("active", b === btn));
  $("hangar-view").classList.toggle("hidden", btn.dataset.view !== "hangar");
  $("earth-view").classList.toggle("hidden", btn.dataset.view !== "earth");
  if (btn.dataset.view === "earth" && cesiumViewer) setTimeout(() => cesiumViewer.resize(), 80);
}));
$("logout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST", body: "{}" }); } catch {}
  location.reload();
});

async function initGlobe() {
  try {
    config = await api("/api/config");
    if (!window.Cesium) throw new Error("CesiumJS did not load. Check network access.");
    if (config.cesium_token) Cesium.Ion.defaultAccessToken = config.cesium_token;
    cesiumViewer = new Cesium.Viewer("cesiumContainer", {
      animation: false, timeline: false, geocoder: false, homeButton: true,
      sceneModePicker: false, navigationHelpButton: false, baseLayerPicker: true,
      infoBox: false, selectionIndicator: false,
      terrain: Cesium.Terrain.fromWorldTerrain(),
      requestRenderMode: true,
    });
    cesiumViewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(-35, 24, 21000000), duration: 1.2 });
    cesiumViewer.screenSpaceEventHandler.setInputAction(async (movement) => {
      const ray = cesiumViewer.camera.getPickRay(movement.position);
      const cartesian = ray && cesiumViewer.scene.globe.pick(ray, cesiumViewer.scene);
      if (!cartesian) return;
      const carto = Cesium.Cartographic.fromCartesian(cartesian);
      currentPoint = { latitude: Cesium.Math.toDegrees(carto.latitude), longitude: Cesium.Math.toDegrees(carto.longitude), label: "Выбранная точка" };
      updatePointDisplay();
      if (pendingPointRole === "departure") await savePoint("departure", currentPoint);
      else if (pendingPointRole === "destination") await savePoint("destination", currentPoint);
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
    if (earthMode === "weather") await setEarthMode("weather", false);
    updatePointDisplay();
  } catch (err) {
    $("coords").textContent = "Глобус не загрузился";
    $("place-label").textContent = err.message;
  }
}
function updatePointDisplay() {
  $("coords").textContent = `${currentPoint.latitude.toFixed(4)}°, ${currentPoint.longitude.toFixed(4)}°`;
  $("place-label").textContent = currentPoint.label || "Выбранная точка";
}
async function savePoint(role, point) {
  try {
    const body = { [role]: { latitude: point.latitude, longitude: point.longitude, label: point.label || coordsLabel(point) } };
    const result = await api("/api/preferences", { method: "PUT", body: JSON.stringify(body) });
    applyProfile(result.user);
    addMarker(role, point);
  } catch (e) { alert(e.message); }
}
function addMarker(role, point) {
  if (!cesiumViewer) return;
  const id = `marker-${role}`;
  const existing = cesiumViewer.entities.getById(id);
  if (existing) cesiumViewer.entities.remove(existing);
  cesiumViewer.entities.add({ id, position: Cesium.Cartesian3.fromDegrees(point.longitude, point.latitude), point: { pixelSize: 11, color: role === "departure" ? Cesium.Color.LIME : Cesium.Color.CYAN, outlineColor: Cesium.Color.WHITE, outlineWidth: 2 }, label: { text: role === "departure" ? "ВЫЛЕТ" : "ПРИЛЁТ", font: "12px sans-serif", fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK, outlineWidth: 3, pixelOffset: new Cesium.Cartesian2(0, -20) } });
}
$("set-departure").addEventListener("click", () => { pendingPointRole = "departure"; $("place-label").textContent = "Нажми на глобус, чтобы выбрать точку вылета"; });
$("set-destination").addEventListener("click", () => { pendingPointRole = "destination"; $("place-label").textContent = "Нажми на глобус, чтобы выбрать точку прилёта"; });
$("search-place").addEventListener("click", async () => {
  const q = $("place-search").value.trim(); if (!q) return;
  try {
    const p = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
    currentPoint = p; updatePointDisplay();
    if (cesiumViewer) cesiumViewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(p.longitude, p.latitude, 700000), duration: 1.4 });
    if (pendingPointRole === "departure" || pendingPointRole === "destination") await savePoint(pendingPointRole, p);
  } catch (e) { setMessage("auth-message", e.message); alert(e.message); }
});
$("place-search").addEventListener("keydown", e => { if (e.key === "Enter") $("search-place").click(); });

async function setEarthMode(mode, persist = true) {
  earthMode = mode;
  document.querySelectorAll("[data-mode]").forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
  if (persist) {
    try { const r = await api("/api/preferences", { method: "PUT", body: JSON.stringify({ earth_mode: mode }) }); applyProfile(r.user); }
    catch (e) { alert(e.message); }
  }
  if (!cesiumViewer) return;
  if (cesiumViewer.imageryLayers.length) {
    // Preserve the default layer and remove only a previously added weather layer.
    for (let i = cesiumViewer.imageryLayers.length - 1; i >= 1; i--) cesiumViewer.imageryLayers.remove(cesiumViewer.imageryLayers.get(i), true);
  }
  if (mode === "weather" && config.openweather_configured) {
    const provider = new Cesium.UrlTemplateImageryProvider({
      url: "/api/weather-tiles/temp_new/{z}/{x}/{y}.png",
      tilingScheme: new Cesium.WebMercatorTilingScheme(),
      maximumLevel: 8,
      credit: "Weather tiles: OpenWeather",
    });
    cesiumViewer.imageryLayers.addImageryProvider(provider);
  } else if (mode === "weather") {
    alert("Добавь OPENWEATHER_API_KEY в Render, чтобы включить погодный слой.");
  }
}
document.querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click", () => setEarthMode(b.dataset.mode)));

$("fly-route").addEventListener("click", () => {
  if (!departure || !destination) {
    alert("Сначала выбери точку вылета и точку прилёта.");
    pendingPointRole = departure ? "destination" : "departure";
    return;
  }
  if (!cesiumViewer) return;
  if (flightTimer) { clearInterval(flightTimer); flightTimer = null; }
  if (planeEntity) cesiumViewer.entities.remove(planeEntity);
  const selected = aircraft.find(a => a.id === me.selected_aircraft) || aircraft[0];
  const start = Cesium.Cartesian3.fromDegrees(departure.longitude, departure.latitude, 3000);
  const end = Cesium.Cartesian3.fromDegrees(destination.longitude, destination.latitude, 10000);
  cesiumViewer.entities.add({ polyline: { positions: [start, end], width: 3, material: Cesium.Color.CYAN.withAlpha(0.8), arcType: Cesium.ArcType.GEODESIC } });
  planeEntity = cesiumViewer.entities.add({ position: start, point: { pixelSize: 12, color: Cesium.Color.ORANGE, outlineColor: Cesium.Color.WHITE, outlineWidth: 2 }, label: { text: selected.name, font: "12px sans-serif", fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK, outlineWidth: 3, pixelOffset: new Cesium.Cartesian2(0, -20) } });
  flightProgress = 0;
  const duration = Math.max(60, Math.min(600, Cesium.Cartesian3.distance(start, end) / Math.max(1, selected.cruise_kmh * 1000 / 3.6) / 10));
  flightTimer = setInterval(() => {
    flightProgress = Math.min(1, flightProgress + 1 / duration);
    const lon = departure.longitude + (destination.longitude - departure.longitude) * flightProgress;
    const lat = departure.latitude + (destination.latitude - departure.latitude) * flightProgress;
    planeEntity.position = Cesium.Cartesian3.fromDegrees(lon, lat, 3000 + 7000 * Math.sin(Math.PI * flightProgress));
    $("hud-speed").textContent = `${selected.cruise_kmh} km/h`;
    $("hud-altitude").textContent = `${Math.round((3000 + 7000 * Math.sin(Math.PI * flightProgress)) * 3.28084).toLocaleString()} ft`;
    cesiumViewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(lon, lat, 550000), duration: 0.7 });
    if (flightProgress >= 1) { clearInterval(flightTimer); flightTimer = null; $("place-label").textContent = "Маршрут завершён"; }
  }, 1000);
});
async function aiRequest(endpoint, body) {
  $("ai-panel").classList.remove("hidden"); $("ai-title").textContent = "Загрузка…"; $("ai-text").textContent = "Обращаемся к сервису. Это может занять некоторое время."; $("ai-image").classList.add("hidden");
  try {
    const result = await api(endpoint, { method: "POST", body: JSON.stringify(body) });
    if (result.description) { $("ai-title").textContent = "Описание местности"; $("ai-text").textContent = result.description; }
    if (result.image_data_url) { $("ai-title").textContent = "Изображение местности"; $("ai-text").textContent = "Сгенерированная иллюстрация, а не реальная спутниковая фотография."; $("ai-image").src = result.image_data_url; $("ai-image").classList.remove("hidden"); }
  } catch (e) { $("ai-title").textContent = "Не удалось выполнить запрос"; $("ai-text").textContent = e.message; }
}
$("describe-place").addEventListener("click", () => aiRequest("/api/describe", { latitude: currentPoint.latitude, longitude: currentPoint.longitude }));
$("generate-image").addEventListener("click", () => aiRequest("/api/generate-image", { latitude: currentPoint.latitude, longitude: currentPoint.longitude, place_name: currentPoint.label || "selected location" }));
$("get-weather").addEventListener("click", async () => {
  try {
    const w = await api(`/api/weather?latitude=${currentPoint.latitude}&longitude=${currentPoint.longitude}`);
    $("ai-panel").classList.remove("hidden"); $("ai-title").textContent = `Погода${w.name ? " — " + w.name : ""}`;
    $("ai-text").textContent = `Температура: ${w.temperature_c ?? "—"} °C\nОщущается как: ${w.feels_like_c ?? "—"} °C\nУсловия: ${w.description ?? "—"}\nВетер: ${w.wind_mps ?? "—"} м/с\nВлажность: ${w.humidity ?? "—"}%`;
    $("ai-image").classList.add("hidden");
  } catch (e) { alert(e.message); }
});
$("close-ai").addEventListener("click", () => $("ai-panel").classList.add("hidden"));

async function boot() {
  try {
    const data = await api("/api/me");
    me = data.user;
    await showGame();
  } catch {
    $("auth-screen").classList.remove("hidden");
    $("game").classList.add("hidden");
  }
}
boot();
      
