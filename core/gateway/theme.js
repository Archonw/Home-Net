/* Darstellung und Symbolstil der Weboberfläche. Der Zugang liefert die Datei unter /hn/theme.js und setzt sie
   in jede Modulseite ein; die Wahl gilt darum für alle Seiten im selben Browser. Die Symbole entsprechen
   android/.../TileIcons.kt der App. */
(function () {
  "use strict";
  const THEME_KEY = "hn.theme", ICON_KEY = "hn.icons";
  const THEMES = { light: "Hell", dark: "Dunkel", system: "Wie System" };
  const ICONS = { classic: "Klassisch", calm: "Ruhig", tinted: "Getönt", gradient: "Verlauf" };
  const read = (key, allowed, fallback) => {
    try { const v = localStorage.getItem(key); return v in allowed ? v : fallback; } catch (e) { return fallback; }
  };
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch (e) { /* privates Fenster */ } };
  const root = document.documentElement;
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

  function applyTheme() {
    const theme = read(THEME_KEY, THEMES, "system");
    if (theme === "system") delete root.dataset.theme; else root.dataset.theme = theme;
    root.style.colorScheme = theme === "system" ? (systemDark.matches ? "dark" : "light") : theme;
  }
  applyTheme();
  systemDark.addEventListener("change", applyTheme);
  // Andere offene Tabs übernehmen eine Änderung sofort.
  window.addEventListener("storage", e => {
    if (e.key === THEME_KEY) applyTheme();
    if (e.key === ICON_KEY) document.dispatchEvent(new CustomEvent("hn:icons"));
  });

  const isDark = () => root.dataset.theme ? root.dataset.theme === "dark" : systemDark.matches;

  const circle = (cx, cy, r) => `M${cx - r},${cy}a${r},${r} 0 1,0 ${2 * r},0a${r},${r} 0 1,0 ${-2 * r},0z`;
  const rect = (x, y, w, h, r) =>
    `M${x + r},${y}h${w - 2 * r}a${r},${r} 0 0,1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0,1 ${-r},${r}h${-(w - 2 * r)}a${r},${r} 0 0,1 ${-r},${-r}v${-(h - 2 * r)}a${r},${r} 0 0,1 ${r},${-r}z`;
  // Schlüssel: Modul-ID. Farben: hell, Verlauf oben, Verlauf unten.
  const GLYPHS = {
    netzwerkmonitor: [[circle(12, 5, 2.3), circle(5, 18.5, 2.3), circle(19, 18.5, 2.3), "M10.9,7.1L6.1,16.4M13.1,7.1l4.8,9.3M7.3,18.5h9.4"], "#34d399", "#10b981", "#047857"],
    pihole: [["M4,5h16l-6.2,7.4v5.8L10.2,20v-7.6z"], "#f87171", "#ef4444", "#b91c1c"],
    unraid: [[rect(4, 4, 16, 7, 2), rect(4, 13, 16, 7, 2), "M8,7.5h0.01M8,16.5h0.01M12,7.5h4M12,16.5h4"], "#fb923c", "#f97316", "#c2410c"],
    "pocket-id": [[circle(8, 15, 4), "M10.9,12.1l8.1,-8.1M16,7l2.2,2.2M13.8,9.2l1.8,1.8"], "#a78bfa", "#8b5cf6", "#6d28d9"],
    "pc-sicherung": [[rect(3, 4, 18, 12, 2), "M8.5,20h7M12,16v4M12,7v5.2M9.6,9.8L12,12.2l2.4,-2.4"], "#60a5fa", "#3b82f6", "#1d4ed8"],
    "pi-wartung": [[rect(7, 7, 10, 10, 1.6), rect(10, 10, 4, 4, 0.6), "M9.5,3.5V7M14.5,3.5V7M9.5,17v3.5M14.5,17v3.5M3.5,9.5H7M3.5,14.5H7M17,9.5h3.5M17,14.5h3.5"], "#f472b6", "#ec4899", "#be185d"],
    wireguard: [["M12,3L19,6v5c0,4.6 -3,8.2 -7,10c-4,-1.8 -7,-5.4 -7,-10V6z", rect(9.3, 11, 5.4, 4.6, 1), "M10.6,11V9.8a1.4,1.4 0 0,1 2.8,0V11"], "#22d3ee", "#06b6d4", "#0e7490"],
    "proxy-manager": [[circle(12, 12, 9), "M3,12h18M12,3c2.4,2.5 3.7,5.6 3.7,9s-1.3,6.5 -3.7,9c-2.4,-2.5 -3.7,-5.6 -3.7,-9S9.6,5.5 12,3z"], "#facc15", "#eab308", "#a16207"],
    nextcloud: [["M7,18.5h10a4,4 0 0,0 0.6,-7.96A5.6,5.6 0 0,0 6.6,10.9A3.8,3.8 0 0,0 7,18.5z", "M12,16v-4.5M10,13.5l2,-2l2,2"], "#38bdf8", "#0ea5e9", "#0369a1"],
  };
  GLYPHS["pocket-id-server"] = GLYPHS["pocket-id"];
  GLYPHS["wireguard-server"] = GLYPHS.wireguard;

  const css = name => getComputedStyle(root).getPropertyValue(name).trim();
  const alpha = (hex, a) => hex + Math.round(a * 255).toString(16).padStart(2, "0");

  /** Symbol eines Moduls im gewählten Stil; ohne neues Symbol oder bei „Klassisch“ das Bild aus `src`. */
  function icon(id, src, size = 52, style = read(ICON_KEY, ICONS, "classic")) {
    const g = GLYPHS[id];
    const radius = Math.round(size * 0.27) + "px";
    if (style === "classic" || !g) {
      const img = document.createElement("img");
      img.src = src || ""; img.alt = "";
      img.style.cssText = `width:${size}px;height:${size}px;border-radius:${radius};flex:none`;
      return img;
    }
    const [paths, light, top, bottom] = g;
    const box = document.createElement("span");
    box.className = "hn-icon";
    let bg, color, edge = "none";
    if (style === "calm") {
      const accent = css("--accent") || "#4f5bd5";
      bg = alpha(accent.slice(0, 7), isDark() ? 0.22 : 0.13); color = accent;
    } else if (style === "tinted") {
      color = isDark() ? light : bottom; bg = alpha(color, 0.15); edge = `inset 0 0 0 1px ${alpha(color, 0.22)}`;
    } else {
      bg = `linear-gradient(145deg,${top},${bottom})`; color = "#fff";
    }
    box.style.cssText = `width:${size}px;height:${size}px;border-radius:${radius};flex:none;display:inline-grid;place-items:center;background:${bg};color:${color};box-shadow:${edge}`;
    const s = Math.round(size * 0.54);
    box.innerHTML = `<svg viewBox="0 0 24 24" width="${s}" height="${s}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths.map(d => `<path d="${d}"/>`).join("")}</svg>`;
    return box;
  }

  window.HN = {
    THEMES, ICONS, icon,
    get theme() { return read(THEME_KEY, THEMES, "system"); },
    set theme(v) { write(THEME_KEY, v); applyTheme(); document.dispatchEvent(new CustomEvent("hn:theme")); },
    get icons() { return read(ICON_KEY, ICONS, "classic"); },
    set icons(v) { write(ICON_KEY, v); document.dispatchEvent(new CustomEvent("hn:icons")); },
  };
})();
