// The live view: the fake clock (Ulanzi TC002, 52x16) and the control panel.

import { LedMatrix } from "./led.js";

const CLOCK_NAME = "Ulanzi TC002";
const MANUAL_FIELDS = [
  // [key, label, type]
  ["status", "Status", "status"],
  ["waste_drawer_level", "Drawer %", "pct"],
  ["litter_level", "Litter %", "pct"],
  ["litter_level_state", "Litter state", "litter"],
  ["power_type", "Power", "power"],
  ["health_error", "Health error", "health"],
  ["wait_time_minutes", "Wait (min)", "wait"],
  ["cat_left", "Cat has left (weight arrived)", "bool"],
  ["is_sleeping", "Sleeping", "bool"],
  ["is_laser_dirty", "Laser dirty", "bool"],
  ["is_online", "Online", "bool"],
  ["is_on", "On", "bool"],
  ["ws_connected", "WebSocket connected", "bool"],
];

const MODE_LABELS = { scenario: "Scenario", fixture: "Fixture", manual: "Manual", log: "Robot log" };
const LOG_SUB_LABELS = { follow: "Follow", replay: "Replay" };
const OBSERVED_LABELS = [
  // [key, label] of what the feeder observed, shown beside the robot's own fields
  ["status_since", "since"],
  ["previous_status", "prev"],
  ["visit_started_at", "visit start"],
  ["cat_left_at", "cat left"],
  ["last_visit_at", "last visit"],
];
const RAW_LABELS = [
  // [key, label] of the robot's own fields shown next to the mapped state
  ["status", "status"],
  ["robotStatus", "robotStatus"],
  ["robotCycleState", "cycleState"],
  ["robotCycleStatus", "cycleStatus"],
  ["displayCode", "displayCode"],
  ["catDetect", "catDetect"],
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

function fmtLocal(iso, withDay = true) {
  if (!iso) return "–";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (!withDay) return t;
  return `${d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} ${t}`;
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

// A scrubber keeps the focus after one click, so focus cannot mean "in use" for it: only a
// drag in progress does. Otherwise its time stands still until something else is clicked.
function free(scrub) {
  return !scrub.dataset.dragging;
}

export function initLive({ control, getMeta }) {
  const clocks = {}; // size key ("52x16", the only one) -> { matrix, nodes }
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
      const tag = el("span", { class: "tag", text: "/api/v1" });
      const kind = el("span", { class: "mono" });
      const push = el("span", { class: "mono" });
      const bar = el("span", { class: "lifebar" }, el("i"));
      const life = el("span", { class: "mono" });
      const scale = el("span", { class: "scale" });
      const err = el("div", { class: "clock-err", hidden: true });
      const card = el(
        "div",
        { class: "clock" },
        el("div", { class: "clock-head" },
          el("h3", { text: `${CLOCK_NAME} · ${size}` }), tag, stale),
        el("div", { class: "canvas-wrap" }, canvas),
        el("div", { class: "clock-info" },
          el("span", {}, "Showing ", kind),
          el("span", {}, "Last push ", push),
          el("span", {}, bar, life),
          scale,
          err),
      );
      host.append(card);
      clocks[size] = {
        matrix: new LedMatrix(canvas, w, h, 10),
        nodes: { stale, kind, push, bar, life, scale, err },
        w, h,
      };
    }
    relayout();
  }

  function relayout() {
    const host = $("clocks");
    const avail = Math.max(200, host.clientWidth - 26);
    for (const c of Object.values(clocks)) {
      const pitch = Math.max(4, Math.min(22, Math.floor(avail / (c.w + 1.2))));
      c.matrix.resize(c.w, c.h, pitch);
    }
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
      modeSeg.append(el("button", { type: "button", "data-mode": m,
        text: MODE_LABELS[m] || m[0].toUpperCase() + m.slice(1),
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
    buildLog(meta);
    buildShow(meta);
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
      if (type === "wait") {
        const [lo, hi] = meta.wait_minutes_range || [1, 60];
        input = el("input", { type: "number", min: lo, max: hi, step: 1, "data-key": key });
        input.onchange = () => {
          const v = Number(input.value);
          if (Number.isInteger(v) && v >= lo && v <= hi) sendManual(key, v);
        };
        host.append(el("label", { class: "field" }, `${label} (${lo}-${hi})`, input));
        continue;
      }
      let options;
      if (type === "status") options = (meta.statuses || []).map((s) => [s, s]);
      else if (type === "litter") options = (meta.litter_states || []).map((s) => [s === null ? "" : s, s === null ? "(none)" : s]);
      else if (type === "power") options = (meta.power_types || []).map((s) => [s, s]);
      else if (type === "health") options = [["", "none"], ...(meta.health_errors || []).map((s) => [s, s])];
      input = el("select", { "data-key": key });
      for (const [v, t] of options || []) input.append(el("option", { value: v, text: t }));
      input.onchange = () => {
        let v = input.value;
        if (type === "litter" || type === "health") v = v === "" ? null : v;
        sendManual(key, v);
      };
      host.append(el("label", { class: "field" }, label, input));
    }
  }

  function buildLog(meta) {
    const sendLog = (patch) => control({ mode: "log", log: patch });
    const subSeg = $("log-sub-seg");
    subSeg.textContent = "";
    for (const m of meta.log_sub_modes || ["follow", "replay"]) {
      subSeg.append(el("button", { type: "button", "data-sub": m, text: LOG_SUB_LABELS[m] || m,
        onclick: () => sendLog({ sub_mode: m }) }));
    }
    const speedSeg = $("log-speed-seg");
    speedSeg.textContent = "";
    for (const sp of meta.speeds || [1, 10, 60]) {
      speedSeg.append(el("button", { type: "button", "data-speed": sp, text: `${sp}x`,
        onclick: () => sendLog({ speed: sp }) }));
    }
    $("log-play-btn").onclick = () => sendLog({ playing: !(lastState && lastState.log && lastState.log.playing) });
    const scrub = $("log-scrubber");
    scrub.oninput = () => {
      scrub.dataset.dragging = "1";
      const lg = lastState && lastState.log;
      if (lg) $("log-time").textContent = `${fmtTime(Number(scrub.value))} / ${fmtTime(lg.duration)}`;
    };
    scrub.onchange = async () => {
      await sendLog({ position: Number(scrub.value) });
      delete scrub.dataset.dragging;
    };
    $("log-visits").dataset.key = "";
    $("log-ticks").dataset.key = "";
  }

  function renderLog(st) {
    const lg = st.log || {};
    for (const b of $("log-sub-seg").children) b.classList.toggle("on", b.dataset.sub === lg.sub_mode);
    for (const blk of document.querySelectorAll(".log-sub")) blk.hidden = blk.dataset.sub !== lg.sub_mode;

    const missing = $("log-missing");
    const noFiles = lg.loaded && !lg.has_files;
    missing.hidden = !noFiles;
    if (noFiles) {
      missing.textContent = "";
      missing.append(`No spike logs in ${lg.dir}/. Start the spike to record the robot: `,
        el("code", { text: lg.spike_command }));
    }
    $("log-file").textContent = lg.file ? `${lg.dir}/${lg.file}` : "";

    // follow
    const info = $("log-follow-info");
    info.textContent = "";
    if (lg.newest_ts) {
      info.append("Following ", el("b", { class: "mono", text: lg.file || "?" }),
        ` · last line ${fmtAge(lg.newest_age_s)} (${lg.newest_kind}, ${fmtLocal(lg.newest_ts, false)})`);
    }
    const stale = $("log-stale");
    const notFresh = lg.loaded && lg.has_files && lg.fresh === false;
    stale.hidden = !(notFresh || lg.contact_error);
    if (lg.contact_error) {
      stale.textContent = `The spike's newest news is a ${lg.contact_error} error: it cannot reach Whisker, so the clock shows NO DATA.`;
    } else if (notFresh) {
      stale.textContent = "";
      stale.append(`The log is not fresh: no line for ${fmtTime(lg.newest_age_s)} (the spike writes at least every ` +
        `${Math.round((lg.fresh_for_s - 120) / 60)} min). Is the spike still running? The clock shows NO DATA once ` +
        "the last contact is too old. Start it with ", el("code", { text: lg.spike_command }));
    }

    // replay
    const visits = lg.visits || [];
    const vbox = $("log-visits");
    const key = visits.map((v) => `${v.id}|${v.duration_s}`).join(",");
    if (vbox.dataset.key !== key) {
      vbox.textContent = "";
      for (const v of visits) {
        const label = `${fmtLocal(v.start)} · ${fmtTime(v.duration_s)}`;
        const b = el("button", { type: "button", class: "btn", "data-visit": v.id,
          title: `${v.statuses.join(" → ")}${v.finished ? "" : " (still going)"}`,
          onclick: () => control({ mode: "log", log: { sub_mode: "replay", visit: v.id, position: 0, playing: true } }) },
          label);
        if (!v.finished) b.append(" ", el("em", { text: "live" }));
        vbox.append(b);
      }
      if (!visits.length) vbox.append(el("span", { class: "muted small", text: lg.loaded ? "No visits in the log yet." : "" }));
      vbox.dataset.key = key;
    }
    for (const b of vbox.querySelectorAll("button")) b.classList.toggle("on", b.dataset.visit === lg.visit);
    $("log-play-btn").textContent = lg.playing ? "Pause" : "Play";
    for (const b of $("log-speed-seg").children) b.classList.toggle("on", Number(b.dataset.speed) === lg.speed);
    const scrub = $("log-scrubber");
    scrub.max = String(lg.duration || 0);
    if (free(scrub)) {
      scrub.value = String(lg.position || 0);
      $("log-time").textContent = `${fmtTime(lg.position)} / ${fmtTime(lg.duration)}` +
        (lg.sub_mode === "replay" && lg.log_time ? `  · ${fmtLocal(lg.log_time, false)}` : "");
    }
    const visit = visits.find((v) => v.id === lg.visit);
    const ticks = $("log-ticks");
    const tkey = visit ? `${visit.id}|${visit.window_s}|${visit.changes.length}` : "";
    if (ticks.dataset.key !== tkey) {
      ticks.textContent = "";
      if (visit && visit.window_s > 0) {
        visit.changes.forEach((c, i) => {
          const pct = (100 * c.at_s) / visit.window_s;
          ticks.append(el("span", {
            class: "tick" + (i % 2 ? " alt" : "") + (pct < 6 ? " first" : pct > 94 ? " last" : ""),
            style: `left:${pct}%`,
            title: `${c.status} at ${fmtTime(c.at_s)}`,
            text: c.status,
          }));
        });
      }
      ticks.dataset.key = tkey;
    }

    $("log-skipped").textContent = lg.loaded && lg.has_files
      ? `${lg.lines} lines read` + (lg.skipped ? `, ${lg.skipped} malformed skipped` : "") + `. Read-only: the studio never writes to ${lg.dir}/ or talks to Whisker.`
      : "";
  }

  // --- idle show and sample weather -------------------------------------------------
  function buildShow(meta) {
    const sh = meta.idle_show_shares || {};
    $("idle-show").onchange = (e) => control({ idle_show: e.target.checked });
    $("idle-show").parentElement.querySelector("span").textContent =
      `Idle show (page ${sh.page_pct ?? "?"} %, scene ${sh.scene_pct ?? "?"} %)`;
    const code = $("wx-code");
    code.textContent = "";
    for (const [c, label] of meta.weather_codes || []) code.append(el("option", { value: c, text: `${label} (${c})` }));
    code.onchange = () => control({ weather: { code: Number(code.value) } });
    $("wx-day").onchange = (e) => control({ weather: { is_day: e.target.value === "1" } });
    const temp = $("wx-temp");
    const [lo, hi] = meta.weather_temp_range || [-40, 120];
    temp.min = lo;
    temp.max = hi;
    temp.onchange = () => {
      const v = Number(temp.value);
      if (temp.value !== "" && isFinite(v) && v >= lo && v <= hi) control({ weather: { temp_f: v } });
    };
    $("wx-reading").onchange = (e) => control({ weather: { reading: e.target.checked } });
  }

  function showPartText(show, kind) {
    if (!show) return kind ? `Not idle (${kind}): no show` : "";
    if (show.error) return `Show: ${show.error}`;
    const left = show.left_s == null ? "" : ` · ${Math.ceil(show.left_s)} s left`;
    if (show.part === "home") {
      return show.next
        ? `Home page · next slot (${show.next}) in ${Math.ceil(show.left_s)} s`
        : "Home page · no show slots";
    }
    return `${show.part[0].toUpperCase()}${show.part.slice(1)} "${show.name ?? "?"}"` +
      (show.phase ? ` (${show.phase})` : "") + left;
  }

  function renderShow(st) {
    const on = !!st.idle_show;
    const box = $("idle-show");
    if (idle(box)) box.checked = on;
    const cs = st.config_shares || {};
    $("idle-show-note").textContent = on
      ? "Studio only: the shares apply in memory; config/display.toml is not written."
      : `Off: the config's shares (page ${cs.page_pct} %, scene ${cs.scene_pct} %).`;
    const w = st.weather || {};
    const rd = $("wx-reading");
    if (idle(rd)) rd.checked = !!w.reading;
    $("wx-fields").setAttribute("aria-disabled", w.reading ? "false" : "true");
    const code = $("wx-code");
    if (idle(code) && w.code != null) code.value = String(w.code);
    const day = $("wx-day");
    if (idle(day)) day.value = w.is_day ? "1" : "0";
    const temp = $("wx-temp");
    if (idle(temp) && w.temp_f != null) temp.value = String(Math.round(w.temp_f));
  }

  function buildVariants(meta) {
    const host = $("variant-pickers");
    host.textContent = "";
    let any = false;
    // The variants map is keyed by size; there is one size.
    for (const size of meta.sizes || []) {
      const kinds = (meta.variants && meta.variants[size]) || {};
      for (const kind of meta.kinds || []) {
        const names = kinds[kind] || [];
        if (names.length < 2) continue;
        const sel = el("select", { "data-size": size, "data-kind": kind });
        names.forEach((n, i) => sel.append(el("option", { value: n, text: i === 0 ? `${n} (default)` : n })));
        sel.onchange = () => control({ variants: { [size]: { [kind]: sel.value } } });
        host.append(el("div", { class: "vrow" }, el("span", { text: kind }), sel));
        any = true;
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
      if (sh.kind === "ready" && sh.show) {
        showing.append(el("div", { class: "small" }, el("b", { text: "Idle show " }),
          showPartText(sh.show, sh.kind)));
      }
      if (sh.sim_time) showing.append(el("div", { class: "muted small", text: `source time ${sh.sim_time.replace("T", " ").replace("+00:00", "Z")}` }));
    }
    renderShow(st);
    if (st.mode === "log") {
      const raw = (st.log && st.log.raw) || null;
      const line = el("div", { class: "raw small" }, "Robot said ");
      if (raw) {
        for (const [k, label] of RAW_LABELS) {
          line.append(el("span", { class: "chip", title: k }, el("b", { text: `${label} ` }), String(raw[k] ?? "–")));
        }
        if (st.log.line_ts) line.append(el("span", { class: "muted", text: ` (line at ${fmtLocal(st.log.line_ts, false)})` }));
      } else {
        line.append(el("span", { class: "muted", text: "nothing (no state line)" }));
      }
      showing.append(line);
      const obs = (st.log && st.log.observed) || null;
      if (obs) {
        const oline = el("div", { class: "raw small" }, "Feeder saw ");
        for (const [k, label] of OBSERVED_LABELS) {
          const v = obs[k];
          const text = v == null ? "–" : (k === "previous_status" ? String(v) : fmtLocal(v, k === "last_visit_at"));
          oline.append(el("span", { class: "chip", title: k }, el("b", { text: `${label} ` }), text));
        }
        showing.append(oline);
      }
    }
    renderLog(st);

    // scenario
    const sSel = $("scenario-select");
    if (idle(sSel)) sSel.value = st.scenario;
    if (meta && $("ticks").dataset.scenario !== st.scenario) buildTicks(meta, st);
    $("play-btn").textContent = st.playing ? "Pause" : "Play";
    for (const b of $("speed-seg").children) b.classList.toggle("on", Number(b.dataset.speed) === st.speed);
    const scrub = $("scrubber");
    scrub.max = String(st.duration || 0);
    if (free(scrub)) {
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
      // The clock's enlargeApps setting: on, pushed apps are drawn doubled (top-left quarter).
      n.scale.textContent = cs.enlarge_apps ? "double size (enlargeApps on)" : "native size";
      n.scale.classList.toggle("warn", !!cs.enlarge_apps);
      n.scale.title = cs.enlarge_apps
        ? "The clock draws pushed apps at double size on a 26x8 grid; the feeder's transport turns this off before it pushes."
        : "enlargeApps is off: pushed apps use all 52x16 pixels.";
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
