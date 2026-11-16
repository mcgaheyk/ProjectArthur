// The "All states" view: one row per fixture, every variant.

import { LedMatrix } from "./led.js";
import { getJSON } from "./net.js";

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c != null) e.append(c);
  return e;
}

// A tile's frames arrive once each as base64 RGB888 ("frames"), with "seq" naming the frame
// to show at each second of its loop. Returns one array of 0xRRGGBB ints per second.
function decodeLoop(t) {
  const distinct = (t.frames || []).map((b64) => {
    const bin = atob(b64);
    const out = new Array(bin.length / 3);
    for (let i = 0, j = 0; j < out.length; i += 3, j++) {
      out[j] = (bin.charCodeAt(i) << 16) | (bin.charCodeAt(i + 1) << 8) | bin.charCodeAt(i + 2);
    }
    return out;
  });
  return (t.seq || distinct.map((_, i) => i)).map((i) => distinct[i]);
}

export function initGallery({ control, toast }) {
  let tiles = []; // { matrix, frames, fixture, size, variant, kind, node }
  let second = 0; // seconds since the gallery was drawn; every tile shows frame second % length
  let loadedKey = null;
  let loading = false;
  let selected = null; // { fixture, size, variant }

  function pitchFor() {
    const w = $("gallery").clientWidth || window.innerWidth;
    return w < 760 ? 4 : 5;
  }

  async function load(force = false) {
    const stale = $("gallery-stale").checked;
    const key = `${stale}`;
    if (!force && key === loadedKey) return;
    if (loading) return;
    loading = true;
    try {
      const data = await getJSON(`/studio/gallery?stale=${stale ? 1 : 0}`);
      render(data);
      loadedKey = key;
    } catch (e) {
      $("gallery").textContent = "";
      $("gallery").append(el("p", { class: "clock-err", text: `Could not load the gallery: ${e.message || e}` }));
    } finally {
      loading = false;
    }
  }

  function render(data) {
    const host = $("gallery");
    host.textContent = "";
    tiles = [];
    second = 0;
    const pitch = pitchFor();
    for (const row of data.rows || []) {
      const r = el("div", { class: "grow" },
        el("div", { class: "grow-head" },
          el("b", { text: row.fixture }),
          el("span", { class: "chip", text: row.kind }),
          el("span", { class: "desc", text: row.description || "" })));
      if (row.error) r.append(el("div", { class: "clock-err", text: row.error }));
      // Tiles are keyed by size; there is one size.
      for (const size of data.sizes || Object.keys(row.tiles || {})) {
        const [w, h] = size.split("x").map(Number);
        const box = el("div", { class: "tiles" });
        for (const t of (row.tiles && row.tiles[size]) || []) {
          const fig = el("figure", { class: "tile", role: "button", tabindex: "0",
            title: `Send ${row.fixture} / ${t.variant} to the live clock` });
          let matrix = null;
          const frames = t.error ? [] : decodeLoop(t);
          if (t.error) {
            fig.append(el("div", { class: "terr", text: t.error }));
          } else {
            const canvas = el("canvas");
            fig.append(canvas);
            matrix = new LedMatrix(canvas, w, h, pitch);
            matrix.setPixels(frames[0]);
          }
          const cap = el("figcaption", {}, el("em", { text: t.variant }), t.default && t.variant !== "default" ? " · default" : "",
            frames.length > 1 ? " · animates" : "");
          fig.append(cap);
          const info = { matrix, frames, fixture: row.fixture, size, variant: t.variant, kind: row.kind, node: fig };
          const send = () => pick(info);
          fig.addEventListener("click", send);
          fig.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); send(); } });
          tiles.push(info);
          box.append(fig);
        }
        r.append(box);
      }
      host.append(r);
    }
    markSelected();
  }

  async function pick(t) {
    const res = await control({ mode: "fixture", fixture: t.fixture, variants: { [t.size]: { [t.kind]: t.variant } } });
    if (res && res.ok) {
      selected = { fixture: t.fixture, size: t.size, variant: t.variant };
      markSelected();
      toast(`Live clock: ${t.fixture}, variant "${t.variant}"`);
    }
  }

  function markSelected() {
    for (const t of tiles) {
      const on = selected && t.fixture === selected.fixture && t.size === selected.size && t.variant === selected.variant;
      t.node.classList.toggle("selected", !!on);
    }
  }

  function step() {
    second += 1;
    for (const t of tiles) {
      if (t.matrix && t.frames.length > 1) t.matrix.setPixels(t.frames[second % t.frames.length]);
    }
  }

  function redraw() {
    for (const t of tiles) if (t.matrix) t.matrix.draw();
  }

  $("gallery-stale").addEventListener("change", () => load(true));
  setInterval(() => {
    if (!$("tab-gallery").hidden) step();
  }, 1000);

  return {
    load,
    redraw,
    invalidate() { loadedKey = null; },
  };
}
