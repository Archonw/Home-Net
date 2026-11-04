/* Übersetzung der Weboberfläche. Deutsch ist die Grundlage; ist der Browser nicht auf Deutsch gestellt, lädt die Seite
   das Wörterbuch /hn/i18n/en.json (aus i18n/en/*.json im Repo) und ersetzt jeden bekannten Text, auch solche, die
   später per JavaScript oder als Meldung vom Server erscheinen. Platzhalter {0}, {1} stehen für wechselnde Teile.
   Der Zugang liefert die Datei unter /hn/i18n.js und setzt sie in jede Modulseite ein. Gleiche Regeln wie I18n.kt. */
(function () {
  "use strict";
  let lang = "";
  try { lang = localStorage.getItem("hn.lang") || ""; } catch (e) { /* privates Fenster */ }
  lang = lang || (navigator.languages && navigator.languages[0]) || navigator.language || "de";
  if (lang.toLowerCase().startsWith("de")) return;

  const root = document.documentElement;
  root.lang = "en";
  root.style.visibility = "hidden";
  const show = () => { root.style.visibility = ""; };
  setTimeout(show, 1500);

  let exact = new Map(), templates = [];
  const cache = new Map();
  const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function pattern(source) {
    let out = "^", rest = source;
    const seen = new Set();
    const re = /\{(\d+)\}/;
    for (let m = re.exec(rest); m; m = re.exec(rest)) {
      out += escape(rest.slice(0, m.index));
      out += seen.has(m[1]) ? `\\k<p${m[1]}>` : `(?<p${m[1]}>[\\s\\S]+?)`;
      seen.add(m[1]);
      rest = rest.slice(m.index + m[0].length);
    }
    return new RegExp(out + escape(rest) + "$");
  }

  function lookup(text, depth) {
    if (exact.has(text)) return exact.get(text);
    if (depth > 1) return text;
    for (const t of templates) {
      const m = t.regex.exec(text);
      if (!m) continue;
      return t.target.replace(/\{(\d+)\}/g, (all, n) => {
        const v = m.groups && m.groups["p" + n];
        return v === undefined ? all : lookup(v, depth + 1);
      });
    }
    return text;
  }

  function translate(text) {
    if (!text || !/[A-Za-zÄÖÜäöüß]/.test(text)) return text;
    if (cache.has(text)) return cache.get(text);
    const trimmed = text.trim();
    const done = trimmed === text ? lookup(text, 0) : text.replace(trimmed, lookup(trimmed, 0));
    if (cache.size > 5000) cache.clear();
    cache.set(text, done);
    return done;
  }
  window.hnT = translate;

  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "CODE", "PRE"]);
  const ATTRS = ["placeholder", "title", "aria-label", "alt"];

  function walk(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const parent = node.parentNode;
      if (parent && SKIP.has(parent.nodeName)) return;
      const done = translate(node.data);
      if (done !== node.data) node.data = done;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || SKIP.has(node.nodeName)) return;
    if (node.closest && node.closest("[data-no-i18n]")) return;
    for (const a of ATTRS) {
      const v = node.getAttribute(a);
      if (v) { const done = translate(v); if (done !== v) node.setAttribute(a, done); }
    }
    if (node.nodeName === "INPUT" && /^(button|submit|reset)$/i.test(node.type) && node.value) {
      const done = translate(node.value);
      if (done !== node.value) node.value = done;
    }
    for (const child of node.childNodes) walk(child);
  }

  for (const name of ["alert", "confirm", "prompt"]) {
    const original = window[name].bind(window);
    window[name] = (message, ...rest) => original(translate(String(message ?? "")), ...rest);
  }

  function start(words) {
    for (const [source, target] of Object.entries(words)) {
      if (source.includes("{0}")) templates.push({ source, target, regex: pattern(source) });
      else exact.set(source, target);
    }
    templates.sort((a, b) => b.source.length - a.source.length);
    const run = () => {
      walk(document.body || root);
      document.title = translate(document.title);
      new MutationObserver(records => {
        for (const r of records) {
          if (r.type === "characterData") walk(r.target);
          else if (r.type === "attributes") walk(r.target);
          else r.addedNodes.forEach(walk);
        }
      }).observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
      show();
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
    else run();
  }

  fetch("/hn/i18n/en.json", { credentials: "same-origin" })
    .then(r => (r.ok ? r.json() : {}))
    .then(start, () => { show(); });
})();
