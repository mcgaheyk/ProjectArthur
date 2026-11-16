// Small fetch helpers and a WebSocket that reconnects by itself.

export async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

// Returns { ok, data, error }; never throws.
export async function postJSON(url, body) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    if (!res.ok) return { ok: false, data, error: (data && data.error) || `HTTP ${res.status}` };
    return { ok: true, data, error: null };
  } catch (e) {
    return { ok: false, data: null, error: String(e && e.message ? e.message : e) };
  }
}

// onMessage(msg) for every JSON message; onStatus("open" | "closed" | "connecting").
export class LiveSocket {
  constructor(path, onMessage, onStatus) {
    this.path = path;
    this.onMessage = onMessage;
    this.onStatus = onStatus;
    this.delay = 500;
    this.ws = null;
    this.timer = null;
    this.connect();
  }

  url() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}${this.path}`;
  }

  connect() {
    clearTimeout(this.timer);
    this.onStatus("connecting");
    let ws;
    try {
      ws = new WebSocket(this.url());
    } catch (e) {
      this.retry();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.delay = 500;
      this.onStatus("open");
    };
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      try {
        this.onMessage(msg);
      } catch (e) {
        console.error("message handler failed", e);
      }
    };
    ws.onclose = () => {
      if (this.ws === ws) {
        this.onStatus("closed");
        this.retry();
      }
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch (e) {
        /* ignore */
      }
    };
  }

  retry() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.connect(), this.delay);
    this.delay = Math.min(5000, this.delay * 1.6);
  }
}
