// The live view: both fake clocks at the same LED pitch, and the control panel.

import { LedMatrix } from "./led.js";

const CLOCK_NAMES = { "32x8": "Ulanzi TC001", "52x16": "Ulanzi TC002" };
const MANUAL_FIELDS = [
  // [key, label, type]
  ["status", "Status", "status"],
  ["waste_drawer_level", "Drawer %", "pct"],
  ["litter_level", "Litter %", "pct"],
  ["litter_level_state", "Litter state", "litter"],
  ["power_type", "Power", "power"],
  ["health_error", "Health error", "health"],
  ["wait_time_minutes", "Wait (min)", "wait"],
  ["is_sleeping", "Sleeping", "bool"],
  ["is_laser_dirty", "Laser dirty", "bool"],
  ["is_online", "Online", "bool"],
  ["is_on", "On", "bool"],
  ["ws_connected", "WebSocket connected", "bool"],
];

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c != null) e.append(c);
  return e;
}

function fmtTime(s) {
  if (s == null || !isFinite(s)) return "–";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(sec).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function fmtAge(s) {
  if (s == null || !isFinite(s)) return "never";
  if (s < 60) return `${s.toFixed(1)} s ago`;
  return `${fmtTime(s)} ago`;
}

// Leave a control alone while the person is using it.
function idle(e) {
  return document.activeElement !== e && !e.dataset.dragging;
}

export function initLive({ control, getMeta }) {
  const clocks = {}; // size -> { matrix, nodes }
  let lastState = null;

  // --- clocks ---------------------------------------------------------------------
  function buildClocks(meta) {
    const host = $("clocks");
    host.textContent = "";
    for (const k of Object.keys(clocks)) delete clocks[k];
    for (const size of meta.sizes || []) {
      const [w, h] = size.split("x").map(Number);
      const canvas = el("canvas", { "aria-label": `${size} clock` });
      const stale = el("span", { class: "stale-label", text: "STALE", hidden: true });
      const tag = el("span", { class: "tag", text: `/clock/${size}` });
      const alias = el("span", { class: "tag", text: "default /api/v1", hidden: true });
      const kind = el("span", { class: "mono" });
      const push = el("span", { class: "mono" });
      const bar = el("span", { class: "lifebar" }, el("i"));
      const life = el("span", { class: "mono" });
      const err = el("div", { class: "clock-err", hidden: true });
      const card = el(
        "div",
        { class: "clock" },
        el("div", { class: "clock-head" },
          el("h3", { text: `${CLOCK_NAMES[size] || "Clock"} · ${size}` }), tag, alias, stale),
        el("div", { class: "canvas-wrap" }, canvas),
        el("div", { class: "clock-info" },
          el("span", {}, "Showing ", kind),
          el("span", {}, "Last push ", push),
          el("span", {}, bar, life),
          err),
      );
      host.append(card);
      clocks[size] = {
        matrix: new LedMatrix(canvas, w, h, 10),
        nodes: { stale, alias, kind, push, bar, life, err },
        w, h,
      };
    }
    relayout();
  }

  function relayout() {
    const host = $("clocks");
    const avail = Math.max(200, host.clientWidth - 26);
    let widest = 0;
    for (const c of Object.values(clocks)) widest = Math.max(widest, c.w);
    if (!widest) return;
    // Same pitch for every clock, so their relative size is honest.
    const pitch = Math.max(4, Math.min(22, Math.floor(avail / (widest + 1.2))));
    for (const c of Object.values(clocks)) c.matrix.resize(c.w, c.h, pitch);
  }

  function onClocks(data) {
    if (!data) return;
    for (const [size, scr] of Object.entries(data)) {
      const c = clocks[size];
      if (c && scr && Array.isArray(scr.pixels)) c.matrix.setPixels(scr.pixels);
    }
  }

  function redraw() {
    for (const c of Object.values(clocks)) c.matrix.draw();
  }

  // --- controls -------------------------------------------------------------------
  function buildControls(meta) {
    const modeSeg = $("mode-seg");
    modeSeg.textContent = "";
    for (const m of meta.modes || ["scenario", "fixture", "manual"]) {
      modeSeg.append(el("button", { type: "button", "data-mode": m, text: m[0].toUpperCase() + m.slice(1),
        onclick: () => control({ mode: m }) }));
    }

    const sSel = $("scenario-select");
    sSel.textContent = "";
    for (const s of meta.scenarios || []) sSel.append(el("option", { value: s.name, text: s.name }));
    sSel.onchange = () => control({ scenario: sSel.value, mode: "scenario" });

    const speedSeg = $("speed-seg");
    speedSeg.textContent = "";
    for (const sp of meta.speeds || [1, 10, 60]) {
      speedSeg.append(el("button", { type: "button", "data-speed": sp, text: `${sp}x`,
        onclick: () => control({ speed: sp }) }));
    }
    $("play-btn").onclick = () => control({ playing: !(lastState && lastState.playing) });

    const scrub = $("scrubber");
    scrub.oninput = () => {
      scrub.dataset.dragging = "1";
      const st = lastState;
      if (st) $("scenario-time").textContent = `${fmtTime(Number(scrub.value))} / ${fmtTime(st.duration)}`;
    };
    scrub.onchange = async () => {
      await control({ position: Number(scrub.value) });
      delete scrub.dataset.dragging;
    };

    const fSel = $("fixture-select");
    fSel.textContent = "";
    for (const f of meta.fixtures || []) fSel.append(el("option", { value: f.name, text: `${f.name}  (${f.kind})` }));
    fSel.onchange = () => control({ fixture: fSel.value, mode: "fixture" });

    buildManual(meta);
    $("feeder-alive").onchange = (e) => control({ feeder_alive: e.target.checked });
    $("fast-expiry").onchange = (e) => control({ fast_expiry: e.target.checked });
    buildVariants(meta);
  }

  function buildManual(meta) {
    const host = $("manual-controls");
    host.textContent = "";
    const sendManual = (k, v) => control({ manual: { [k]: v }, mode: "manual" });
    for (const [key, label, type] of MANUAL_FIELDS) {
      let input;
      if (type === "bool") {
        input = el("input", { type: "checkbox", "data-key": key });
        input.onchange = () => sendManual(key, input.checked);
        host.append(el("label", { class: "check-field" }, input, label));
        continue;
      }
      if (type === "pct") {
        const out = el("span", { class: "val" });
        input = el("input", { type: "range", min: 0, max: 100, step: 1, "data-key": key });
        input.oninput = () => { input.dataset.dragging = "1"; out.textContent = `${input.value}%`; };
        input.onchange = async () => { await sendManual(key, Number(input.value)); delete input.dataset.dragging; };
        input._out = out;
        host.append(el("label", { class: "field" }, el("span", {}, `${label} `, out), input));
        continue;
      }
      let options;
      if (type === "status") options = (meta.statuses || []).map((s) => [s, s]);
      else if (type === "litter") options = (meta.litter_states || []).map((s) => [s === null ? "" : s, s === null ? "(none)" : s]);
      else if (type === "power") options = (meta.power_types || []).map((s) => [s, s]);
      else if (type === "health") options = [["", "none"], ...(meta.health_errors || []).map((s) => [s, s])];
      else if (type === "wait") options = (meta.wait_minutes || []).map((s) => [String(s), String(s)]);
      input = el("select", { "data-key": key });
      for (const [v, t] of options || []) input.append(el("option", { value: v, text: t }));
      input.onchange = () => {
        let v = input.value;
        if (type === "litter" || type === "health") v = v === "" ? null : v;
        if (type === "wait") v = Number(v);
        sendManual(key, v);
      };
      host.append(el("label", { class: "field" }, label, input));
    }
  }

  function buildVariants(meta) {
    const host = $("variant-pickers");
    host.textContent = "";
    let any = false;
    for (const size of meta.sizes || []) {
      const kinds = (meta.variants && meta.variants[size]) || {};
      const rows = [];
      for (const kind of meta.kinds || []) {
        const names = kinds[kind] || [];
        if (names.length < 2) continue;
        const sel = el("select", { "data-size": size, "data-kind": kind });
        names.forEach((n, i) => sel.append(el("option", { value: n, text: i === 0 ? `${n} (default)` : n })));
        sel.onchange = () => control({ variants: { [size]: { [kind]: sel.value } } });
        rows.push(el("div", { class: "vrow" }, el("span", { text: kind }), sel));
      }
      if (rows.length) {
        any = true;
        host.append(el("div", { class: "vsize" }, el("h3", { text: size }), ...rows));
      }
    }
    if (!any) host.append(el("p", { class: "muted small", text: "No state has more than one variant." }));
  }

  function buildTicks(meta, st) {
    const ticks = $("ticks");
    ticks.textContent = "";
    const sc = (meta.scenarios || []).find((s) => s.name === st.scenario);
    if (!sc || !sc.duration_s) return;
    sc.steps.forEach((step, i) => {
      const pct = (100 * step.at_s) / sc.duration_s;
      ticks.append(el("span", {
        class: "tick" + (i % 2 ? " alt" : "") + (pct < 6 ? " first" : pct > 94 ? " last" : ""),
        style: `left:${pct}%`,
        title: `${step.label} at ${fmtTime(step.at_s)}`,
        text: step.label,
      }));
    });
    ticks.dataset.scenario = st.scenario;
    $("scenario-desc").textContent = sc.description || "";
  }

  // --- state ----------------------------------------------------------------------
  function onState(st) {
    if (!st) return;
    lastState = st;
    const meta = getMeta();

    for (const b of $("mode-seg").children) b.classList.toggle("on", b.dataset.mode === st.mode);
    for (const blk of document.querySelectorAll(".mode-block")) blk.hidden = blk.dataset.mode !== st.mode;

    const sh = st.showing || {};
    const showing = $("showing");
    showing.textContent = "";
    if (sh.kind) {
      showing.append("Mapped state ", el("b", { text: sh.kind }));
      if (sh.detail) showing.append(" ", el("span", { class: "chip", text: sh.detail }));
      for (const b of sh.badges || []) showing.append(" ", el("span", { class: "chip", text: b }));
      if (sh.sim_time) showing.append(el("div", { class: "muted small", text: `source time ${sh.sim_time.replace("T", " ").replace("+00:00", "Z")}` }));
    }

    // scenario
    const sSel = $("scenario-select");
    if (idle(sSel)) sSel.value = st.scenario;
    if (meta && $("ticks").dataset.scenario !== st.scenario) buildTicks(meta, st);
    $("play-btn").textContent = st.playing ? "Pause" : "Play";
    for (const b of $("speed-seg").children) b.classList.toggle("on", Number(b.dataset.speed) === st.speed);
    const scrub = $("scrubber");
    scrub.max = String(st.duration || 0);
    if (idle(scrub)) {
      scrub.value = String(st.position || 0);
      $("scenario-time").textContent = `${fmtTime(st.position)} / ${fmtTime(st.duration)}`;
    }

    // fixture
    const fSel = $("fixture-select");
    if (idle(fSel)) fSel.value = st.fixture;
    const fx = meta && (meta.fixtures || []).find((f) => f.name === st.fixture);
    $("fixture-desc").textContent = fx ? fx.description : "";

    // manual
    for (const input of $("manual-controls").querySelectorAll("[data-key]")) {
      if (!idle(input)) continue;
      const v = st.manual ? st.manual[input.dataset.key] : undefined;
      if (input.type === "checkbox") input.checked = !!v;
      else if (input.type === "range") {
        input.value = String(v ?? 0);
        if (input._out) input._out.textContent = `${Math.round(v ?? 0)}%`;
      } else input.value = v === null || v === undefined ? "" : String(v);
    }

    // feeder
    $("feeder-alive").checked = !!st.feeder_alive;
    $("fast-expiry").checked = !!st.fast_expiry;
    $("feeder-timing").textContent = `Heartbeat ${st.heartbeat_s} s, lifetime ${st.ttl_s} s` +
      (st.fast_expiry ? " (fast expiry)" : " (from config)");
    $("feeder-stats").textContent = `${st.push_count} pushes` + (st.feeder_alive ? "" : " · feeder is DEAD: no pushes");
    const errs = $("feeder-errors");
    errs.textContent = "";
    for (const e of (st.errors || []).slice(-5).reverse()) {
      const when = e.at ? new Date(e.at * 1000).toLocaleTimeString() : "";
      errs.append(el("li", { text: `${when} [${e.where}] ${e.message}` }));
    }

    // variants
    for (const sel of $("variant-pickers").querySelectorAll("select")) {
      if (!idle(sel)) continue;
      const chosen = st.variants && st.variants[sel.dataset.size] && st.variants[sel.dataset.size][sel.dataset.kind];
      sel.value = chosen || sel.options[0].value;
    }

    // clocks
    for (const [size, c] of Object.entries(clocks)) {
      const cs = st.clocks && st.clocks[size];
      const n = c.nodes;
      n.alias.hidden = st.default_size !== size;
      if (!cs) continue;
      n.stale.hidden = !cs.marked;
      n.kind.textContent = cs.kind ? `${cs.kind} / ${cs.variant}` : (cs.app ? `app ${cs.app}` : "nothing pushed");
      n.push.textContent = fmtAge(cs.last_push_age_s);
      const bar = n.bar;
      if (cs.lifetime_ms > 0 && cs.lifetime_remaining_s != null) {
        const frac = Math.max(0, Math.min(1, cs.lifetime_remaining_s / (cs.lifetime_ms / 1000)));
        bar.firstChild.style.width = `${(frac * 100).toFixed(1)}%`;
        bar.classList.toggle("low", frac < 0.34 && frac > 0);
        bar.classList.toggle("out", frac <= 0);
        n.life.textContent = cs.marked ? "lifetime over: marked stale" : `${cs.lifetime_remaining_s.toFixed(1)} s left`;
      } else {
        bar.firstChild.style.width = "0";
        bar.classList.remove("low", "out");
        n.life.textContent = cs.app ? "no lifetime" : "no app";
      }
      const err = cs.feeder_error;
      n.err.hidden = !err;
      n.err.textContent = err ? `Last push failed: ${err}` : "";
    }
  }

  function onMeta(meta) {
    buildClocks(meta);
    buildControls(meta);
    $("ticks").dataset.scenario = "";
    if (lastState) onState(lastState);
  }

  window.addEventListener("resize", () => relayout());
  return { onMeta, onState, onClocks, redraw, relayout };
}
