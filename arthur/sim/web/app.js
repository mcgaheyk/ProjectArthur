// Studio entry point: loads metadata, wires the live view, the gallery and the WebSocket.

import { ledOptions, onLedOptions, setLedOptions } from "./led.js";
import { LiveSocket, getJSON, postJSON } from "./net.js";
import { initLive } from "./live.js";
import { initGallery } from "./gallery.js";

const $ = (id) => document.getElementById(id);

let meta = null;
let metaVersion = null;
let toastTimer = null;

function toast(text, isError = false) {
  const t = $("toast");
  t.textContent = text;
  t.classList.toggle("err", isError);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 6000 : 2500);
}

async function control(patch) {
  const res = await postJSON("/studio/control", patch);
  if (!res.ok) {
    toast(`Control refused: ${res.error}`, true);
  } else if (res.data) {
    onState(res.data);
  }
  return res;
}

const live = initLive({ control, getMeta: () => meta });
const gallery = initGallery({ control, toast });

async function loadMeta() {
  try {
    meta = await getJSON("/studio/meta");
    metaVersion = meta.version;
    live.onMeta(meta);
  } catch (e) {
    toast(`Could not load studio metadata: ${e.message || e}`, true);
  }
}

function showReloadError(text) {
  $("reload-error").hidden = !text;
  $("reload-error-text").textContent = text || "";
}

function onState(st) {
  if (!st) return;
  showReloadError(st.reload_error);
  if (meta && st.version !== metaVersion) {
    // The server reloaded (or restarted): refresh everything that depends on the code.
    metaVersion = st.version;
    loadMeta();
    gallery.invalidate();
    if (!$("tab-gallery").hidden) gallery.load(true);
  }
  live.onState(st);
}

function onMessage(msg) {
  if (msg.type === "hello") {
    loadMeta().then(() => {
      gallery.invalidate();
      if (!$("tab-gallery").hidden) gallery.load(true);
      live.onClocks(msg.clocks);
      onState(msg.state);
    });
    return;
  }
  if (msg.clocks) live.onClocks(msg.clocks);
  if (msg.reload) {
    if (msg.reload.ok) toast(`Reloaded: version ${msg.reload.version}`);
    else toast("Reload failed: keeping the last good version", true);
  }
  if (msg.state) onState(msg.state);
}

function onStatus(s) {
  const c = $("conn");
  c.dataset.state = s;
  c.textContent = s === "open" ? "live" : s === "closed" ? "disconnected" : "connecting";
  $("conn-banner").hidden = s !== "closed";
}

// --- tabs ---------------------------------------------------------------------------
function showTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("on", b.dataset.tab === name);
  $("tab-live").hidden = name !== "live";
  $("tab-gallery").hidden = name !== "gallery";
  try { localStorage.setItem("arthur.tab", name); } catch (e) { /* ignore */ }
  if (name === "gallery") gallery.load();
  else live.relayout();
}
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));

// --- LED viewing options ------------------------------------------------------------
function syncOptions() {
  $("opt-brightness").value = String(Math.round(ledOptions.brightness * 100));
  $("opt-brightness-out").textContent = `${Math.round(ledOptions.brightness * 100)}%`;
  $("opt-room").value = ledOptions.room;
  $("opt-diffuser").checked = ledOptions.diffuser;
}
$("opt-brightness").addEventListener("input", (e) => setLedOptions({ brightness: Number(e.target.value) / 100 }));
$("opt-room").addEventListener("change", (e) => setLedOptions({ room: e.target.value }));
$("opt-diffuser").addEventListener("change", (e) => setLedOptions({ diffuser: e.target.checked }));
onLedOptions(() => { syncOptions(); live.redraw(); gallery.redraw(); });
syncOptions();

// --- start --------------------------------------------------------------------------
let startTab = "live";
try { startTab = localStorage.getItem("arthur.tab") || "live"; } catch (e) { /* ignore */ }
await loadMeta();
showTab(startTab === "gallery" ? "gallery" : "live");
try { onState(await getJSON("/studio/state")); } catch (e) { /* the socket will catch up */ }
new LiveSocket("/studio/ws", onMessage, onStatus);
