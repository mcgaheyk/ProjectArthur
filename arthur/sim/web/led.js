// LED matrix drawn on a canvas: soft rounded LEDs with a little glow, unlit LEDs faintly
// visible, sharp on high-DPI screens. Viewing options (brightness, room light, diffuser) are
// shared by every matrix on the page.

const STORE_KEY = "arthur.led.options";

export const ledOptions = { brightness: 0.85, room: "dark", diffuser: true };

try {
  const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
  if (saved && typeof saved === "object") {
    if (typeof saved.brightness === "number") ledOptions.brightness = saved.brightness;
    if (saved.room === "dark" || saved.room === "day") ledOptions.room = saved.room;
    if (typeof saved.diffuser === "boolean") ledOptions.diffuser = saved.diffuser;
  }
} catch (e) {
  /* storage unavailable: defaults are fine */
}

const listeners = new Set();

export function setLedOptions(patch) {
  Object.assign(ledOptions, patch);
  ledOptions.brightness = Math.min(1, Math.max(0.03, Number(ledOptions.brightness) || 1));
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(ledOptions));
  } catch (e) {
    /* ignore */
  }
  for (const fn of listeners) {
    try {
      fn();
    } catch (e) {
      console.error(e);
    }
  }
}

export function onLedOptions(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

const GAMMA = 2.2;

// Colour of one LED as seen, as [r, g, b] or null when unlit.
function seen(n) {
  let r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  if ((r | g | b) === 0) return null;
  // Dim in linear light, then back to display gamma: a simple gamma-correct dimmer.
  const k = Math.pow(ledOptions.brightness, 1 / GAMMA);
  r *= k; g *= k; b *= k;
  if (ledOptions.room === "day") {
    // Daylight washes out contrast: colours lose saturation and lift towards the grey case.
    const wash = 0.35;
    r = r * (1 - wash) + 70 * wash + 18;
    g = g * (1 - wash) + 70 * wash + 18;
    b = b * (1 - wash) + 70 * wash + 18;
  }
  return [Math.min(255, r) | 0, Math.min(255, g) | 0, Math.min(255, b) | 0];
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

export class LedMatrix {
  constructor(canvas, width, height, pitch = 10) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.pixels = null;
    this.resize(width, height, pitch);
  }

  resize(width, height, pitch) {
    this.width = width | 0;
    this.height = height | 0;
    this.pitch = Math.max(2, pitch);
    this.pad = Math.round(this.pitch * 0.6);
    const cssW = this.width * this.pitch + 2 * this.pad;
    const cssH = this.height * this.pitch + 2 * this.pad;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.style.width = cssW + "px";
    this.canvas.style.height = cssH + "px";
    this.canvas.width = Math.round(cssW * dpr);
    this.canvas.height = Math.round(cssH * dpr);
    this.dpr = dpr;
    this.cssW = cssW;
    this.cssH = cssH;
    this.draw();
  }

  setPixels(ints) {
    this.pixels = Array.isArray(ints) && ints.length === this.width * this.height ? ints : null;
    this.draw();
  }

  draw() {
    const ctx = this.ctx;
    if (!ctx) return;
    const { pitch, pad, width, height } = this;
    const day = ledOptions.room === "day";
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = day ? "#2a2a2d" : "#060606";
    ctx.fillRect(0, 0, this.cssW, this.cssH);

    const px = this.pixels;
    const colours = new Array(width * height);
    for (let i = 0; i < width * height; i++) colours[i] = px ? seen(px[i] | 0) : null;

    const diff = ledOptions.diffuser;
    const size = pitch * (diff ? 0.84 : 0.6);
    const radius = pitch * (diff ? 0.28 : 0.1);
    const inset = (pitch - size) / 2;

    // Glow pass: additive halos so neighbouring LEDs bleed into each other a little.
    if (diff && pitch >= 4) {
      ctx.globalCompositeOperation = "lighter";
      const alpha = day ? 0.07 : 0.2;
      const gr = pitch * 0.95;
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          const c = colours[y * width + x];
          if (!c) continue;
          const cx = pad + x * pitch + pitch / 2;
          const cy = pad + y * pitch + pitch / 2;
          ctx.fillStyle = `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;
          ctx.beginPath();
          ctx.arc(cx, cy, gr, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.globalCompositeOperation = "source-over";
    }

    // LED bodies.
    const unlit = day ? "#3b3b3f" : "#161616";
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const c = colours[y * width + x];
        ctx.fillStyle = c ? `rgb(${c[0]},${c[1]},${c[2]})` : unlit;
        roundRect(ctx, pad + x * pitch + inset, pad + y * pitch + inset, size, size, radius);
        ctx.fill();
      }
    }
  }
}
