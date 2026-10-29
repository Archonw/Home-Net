// Gemeinsame Teile aller Seiten: Designs (Soft/Kacheln) wie in der Android-App, Hell/Dunkel,
// Akzentfarben, Hilfsfunktionen. Wird im <head> geladen, damit das Design ohne Flackern greift.
"use strict";

const NM = (() => {
  const ACCENTS = {
    indigo: ["Indigo", "#4F5BD5"], petrol: ["Petrol", "#0B7A6C"],
    beere: ["Beere", "#C2366B"], bernstein: ["Bernstein", "#B35A0A"],
    gruen: ["Grün", "#1A7F37"], blau: ["Blau", "#2F5FD0"],
    violett: ["Violett", "#6D4AE0"], orange: ["Orange", "#C25E0A"],
  };
  const STYLES = {
    soft: { label: "Soft", accents: ["indigo", "petrol", "beere", "bernstein"] },
    tiles: { label: "Kacheln", accents: ["gruen", "blau", "violett", "orange"] },
  };
  const THEMES = { light: "Hell", dark: "Dunkel", system: "Wie System" };

  // Einstellungen gelten je Browser, wie in der App je Handy
  const KEY = "nm.prefs";
  const DEFAULTS = { style: "soft", theme: "system", accent: "indigo", sort: "name_asc", filter: "all" };
  function load() {
    try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; }
    catch { return { ...DEFAULTS }; }
  }
  const prefs = load();
  if (!STYLES[prefs.style]) prefs.style = DEFAULTS.style;
  if (!STYLES[prefs.style].accents.includes(prefs.accent)) prefs.accent = STYLES[prefs.style].accents[0];

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* privates Fenster */ }
  }

  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const mix = (a, b, t) => "#" + hex(a).map((x, i) => Math.round(x + (hex(b)[i] - x) * t)
    .toString(16).padStart(2, "0")).join("");

  // Gleiche Farbwerte wie Theme.kt der App
  function palette(style, dark, accentHex) {
    const a = accentHex;
    if (style === "soft") {
      if (dark) {
        const bg = "#0E1120";
        return { bg, card: "#191D31", text: "#E9EBF7", muted: "#A3A8C6", line: "#2A2F48",
          accent: a, "accent-text": mix(a, "#FFFFFF", .45), "accent-soft": mix(a, bg, .7), "on-accent": "#FFFFFF",
          "hero-start": mix("#C2366B", bg, .55), "hero-end": mix(a, bg, .45), "hero-text": "#FFFFFF",
          allowed: "#7EDCA4", unknown: "#F5B05A", blocked: "#FF8A80",
          "allowed-bg": "#12301F", "unknown-bg": "#3A2610", "blocked-bg": "#3A1715", danger: "#FF8A80" };
      }
      return { bg: "#F4F5FA", card: "#FFFFFF", text: "#1B1F4B", muted: "#5F6485", line: "#E6E8F2",
        accent: a, "accent-text": a, "accent-soft": mix(a, "#FFFFFF", .88), "on-accent": "#FFFFFF",
        "hero-start": "#F9D3DF", "hero-end": mix(a, "#FFFFFF", .55), "hero-text": "#1B1F4B",
        allowed: "#146C3A", unknown: "#8A4B00", blocked: "#B42318",
        "allowed-bg": "#E3F4EA", "unknown-bg": "#FDEFD9", "blocked-bg": "#FDE4E1", danger: "#C62828" };
    }
    if (dark) {
      const bg = "#0B0C0E", unknown = "#F5A524", blocked = "#FF6B60", at = mix(a, "#FFFFFF", .4);
      return { bg, card: "#17191D", text: "#F2F3F5", muted: "#9EA3AD", line: "#2A2D33",
        accent: a, "accent-text": at, "accent-soft": mix(a, bg, .7), "on-accent": "#FFFFFF",
        "hero-start": bg, "hero-end": bg, "hero-text": "#F2F3F5",
        allowed: at, unknown, blocked,
        "allowed-bg": mix(at, bg, .85), "unknown-bg": mix(unknown, bg, .85), "blocked-bg": mix(blocked, bg, .85),
        danger: "#D93025",
        "online-tile": mix(at, bg, .8), "unknown-tile": mix(unknown, bg, .8), "blocked-tile": mix(blocked, bg, .8) };
    }
    const bg = "#FFFFFF", unknown = "#A65800", blocked = "#C62828";
    return { bg, card: "#F2F3F5", text: "#111317", muted: "#5A5F69", line: "#E1E3E8",
      accent: a, "accent-text": a, "accent-soft": mix(a, bg, .85), "on-accent": "#FFFFFF",
      "hero-start": bg, "hero-end": bg, "hero-text": "#111317",
      allowed: a, unknown, blocked,
      "allowed-bg": mix(a, bg, .9), "unknown-bg": mix(unknown, bg, .9), "blocked-bg": mix(blocked, bg, .9),
      danger: blocked,
      "online-tile": mix(a, bg, .88), "unknown-tile": mix(unknown, bg, .88), "blocked-tile": mix(blocked, bg, .88) };
  }

  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  const isDark = () => prefs.theme === "dark" || (prefs.theme === "system" && systemDark.matches);

  function apply() {
    const root = document.documentElement;
    const dark = isDark();
    const colors = palette(prefs.style, dark, ACCENTS[prefs.accent][1]);
    for (const [name, value] of Object.entries(colors)) root.style.setProperty("--" + name, value);
    root.dataset.style = prefs.style;
    root.dataset.theme = dark ? "dark" : "light";
    root.style.colorScheme = dark ? "dark" : "light";
    let meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement("meta");
      meta.name = "theme-color";
      document.head.append(meta);
    }
    meta.content = colors.bg;
  }
  systemDark.addEventListener("change", () => { if (prefs.theme === "system") apply(); });
  apply();

  function set(changes) {
    Object.assign(prefs, changes);
    if (!STYLES[prefs.style].accents.includes(prefs.accent)) prefs.accent = STYLES[prefs.style].accents[0];
    save();
    apply();
    document.dispatchEvent(new CustomEvent("nm:prefs"));
  }

  // ---- Hilfsfunktionen ----

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || r.statusText);
    return body;
  }

  const LABEL = { unknown: "unbekannt", allowed: "erlaubt", blocked: "gesperrt" };
  const IFACE = { "802.11": "WLAN", "Ethernet": "LAN-Kabel" };
  const statusLabel = s => LABEL[s] || LABEL.unknown;
  const ifaceLabel = i => IFACE[i] || i || "–";
  const title = d => d.name || d.hostname || d.mac;
  const fmt = ts => ts ? new Date(ts).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–";

  // Zwischenablage geht im Browser nur über https; im Heimnetz (http) der alte Weg
  async function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); return true; } catch { /* Fallback unten */ }
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    area.remove();
    return ok;
  }

  // Dialog im Stil der Seite statt confirm()/prompt(); liefert true/false bzw. den Text oder null
  function dialog({ title: heading, text = "", input = null, ok = "OK", cancel = "Abbrechen", danger = false }) {
    return new Promise(resolve => {
      const dlg = document.createElement("dialog");
      dlg.className = "nm-dialog";
      dlg.innerHTML = `<form method="dialog">
          <h3>${esc(heading)}</h3>
          ${text ? `<p>${esc(text).replace(/\n/g, "<br>")}</p>` : ""}
          ${input !== null ? `<input type="text" value="${esc(input)}" autocomplete="off">` : ""}
          <div class="nm-dialog-actions">
            <button value="cancel" class="text">${esc(cancel)}</button>
            <button value="ok" class="text ${danger ? "danger" : "strong"}">${esc(ok)}</button>
          </div>
        </form>`;
      document.body.append(dlg);
      const field = dlg.querySelector("input");
      dlg.addEventListener("close", () => {
        const confirmed = dlg.returnValue === "ok";
        dlg.remove();
        resolve(input !== null ? (confirmed ? field.value : null) : confirmed);
      });
      dlg.showModal();
      if (field) { field.focus(); field.select(); }
    });
  }

  let toastTimer;
  function toast(text) {
    let el = document.querySelector(".nm-toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "nm-toast";
      el.setAttribute("role", "status");
      document.body.append(el);
    }
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  // Ziehen nach unten am Seitenanfang lädt neu, wie in der App (nur Touch)
  function pullToRefresh(onRefresh) {
    const ind = document.createElement("div");
    ind.className = "nm-ptr";
    ind.innerHTML = '<div class="nm-spinner"></div>';
    document.body.append(ind);
    let startY = null, dist = 0, busy = false;
    window.addEventListener("touchstart", e => {
      if (busy || window.scrollY > 0 || e.touches.length !== 1 || e.target.closest("dialog")) return;
      startY = e.touches[0].clientY;
      dist = 0;
    }, { passive: true });
    window.addEventListener("touchmove", e => {
      if (startY === null) return;
      dist = Math.max(0, e.touches[0].clientY - startY);
      ind.style.transform = `translate(-50%, ${Math.min(dist, 120) * .6 - 48}px) rotate(${dist * 2}deg)`;
      ind.classList.toggle("ready", dist > 80);
    }, { passive: true });
    window.addEventListener("touchend", async () => {
      if (startY === null) return;
      startY = null;
      if (dist > 80) {
        busy = true;
        ind.classList.add("spin");
        ind.style.transform = "translate(-50%, 24px)";
        try { await onRefresh(); } finally {
          busy = false;
          ind.classList.remove("spin", "ready");
        }
      }
      ind.style.transform = "";
    });
  }

  // Material-Symbole wie in der App
  const ICONS = {
    search: "M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z",
    close: "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
    more: "M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z",
    back: "M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z",
    chevron: "M8.59 16.59 13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z",
    devices: "M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z",
    settings: "M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.488.488 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z",
  };
  const icon = (name, cls = "") =>
    `<svg viewBox="0 0 24 24" aria-hidden="true" class="${cls}"><path d="${ICONS[name]}"/></svg>`;

  // Untere Leiste „Geräte“ / „Einstellungen“
  function tabbar(active) {
    const nav = document.createElement("nav");
    nav.className = "tabbar";
    nav.setAttribute("aria-label", "Hauptnavigation");
    nav.innerHTML = [["devices", "/", "Geräte"], ["settings", "/settings", "Einstellungen"]]
      .map(([id, href, label]) => `<a class="tab ${id === active ? "active" : ""}" href="${href}"
        ${id === active ? 'aria-current="page"' : ""}><span class="pill">${icon(id)}</span>${label}</a>`).join("");
    document.body.append(nav);
    document.body.classList.add("with-tabs");
  }

  return { prefs, set, icon, tabbar, ACCENTS, STYLES, THEMES, esc, api, statusLabel, ifaceLabel, title, fmt, copy, dialog, toast, pullToRefresh };
})();
