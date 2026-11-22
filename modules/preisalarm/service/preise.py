"""Preisalarm: Preise von Produktseiten lesen, Verlauf auswerten und gute Preise erkennen.

Ohne Abhängigkeiten außer der Standardbibliothek, damit es überall im Container läuft.
"""
import html as htmllib
import json
import re
import statistics
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_POINTS = 5  # Durchschnitt und Tiefstpreis zählen erst ab so vielen Messungen


class PriceError(Exception):
    pass


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise PriceError(f"Die Seite lässt keine automatische Abfrage zu (HTTP {exc.code}). "
                             "Bitte die Produktseite bei geizhals.de oder direkt beim Shop nehmen.") from exc
        raise PriceError(f"Seite nicht erreichbar: HTTP {exc.code}") from exc
    except OSError as exc:
        raise PriceError(f"Seite nicht erreichbar: {exc}") from exc


def to_number(value):
    """'1.234,56', '1234.56', '89,90 €' → float."""
    text = re.sub(r"[^\d,.]", "", str(value))
    if not text:
        raise ValueError(value)
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def _offers(node):
    if isinstance(node, list):
        for item in node:
            yield from _offers(item)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from _offers(node["@graph"])
        offers = node.get("offers")
        if isinstance(offers, (list, dict)):
            for offer in offers if isinstance(offers, list) else [offers]:
                if isinstance(offer, dict):
                    yield offer


def parse_price(page):
    """Lowest price on a product page: structured data (JSON-LD, used by geizhals, idealo and most shops),
    then microdata and meta tags. Returns a float or raises PriceError."""
    prices = []
    for block in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            data = json.loads(htmllib.unescape(block.strip()))
        except ValueError:
            continue
        for offer in _offers(data):
            for key in ("lowPrice", "price"):
                if offer.get(key) not in (None, ""):
                    try:
                        prices.append(to_number(offer[key]))
                    except ValueError:
                        pass
                    break
    if not prices:
        for pattern in (r'itemprop=["\'](?:lowPrice|price)["\'][^>]*content=["\']([\d.,]+)',
                        r'content=["\']([\d.,]+)["\'][^>]*itemprop=["\'](?:lowPrice|price)["\']',
                        r'property=["\'](?:product|og):price:amount["\'][^>]*content=["\']([\d.,]+)'):
            for found in re.findall(pattern, page, re.I):
                try:
                    prices.append(to_number(found))
                except ValueError:
                    pass
            if prices:
                break
    prices = [p for p in prices if p > 0]
    if not prices:
        raise PriceError("Auf der Seite steht kein lesbarer Preis.")
    return min(prices)


def summary(history):
    """history: list of [time, price]. Average and lowest over all earlier measurements."""
    prices = [p for _, p in history]
    if not prices:
        return {"count": 0, "average": None, "lowest": None}
    return {"count": len(prices), "average": round(statistics.mean(prices), 2), "lowest": min(prices)}


def reasons(price, history, target=None, below_pct=15):
    """Why this price is good, judged against the history before it. Empty list: nothing to report."""
    out = []
    if target and price <= target:
        out.append("Zielpreis erreicht")
    prices = [p for _, p in history]
    if len(prices) >= MIN_POINTS:
        avg = statistics.mean(prices)
        if below_pct and price <= avg * (1 - below_pct / 100):
            out.append("deutlich unter Durchschnitt")
        if price < min(prices):
            out.append("neuer Tiefstpreis")
    return out


def should_report(price, good, last_reported):
    """Report a good price once, and again only when it drops further. Returns (report, new last_reported)."""
    if not good:
        return False, None
    if last_reported is None or price < last_reported:
        return True, price
    return False, last_reported


def parse_feed(text):
    """Items of an RSS feed (mydealz and others): [{id, title, link}]."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PriceError(f"Feed nicht lesbar: {exc}") from exc
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link or title).strip()
        if title:
            items.append({"id": guid, "title": title, "link": link})
    return items


def matches(title, terms):
    """True if all words of one search term appear in the title (case-insensitive)."""
    low = title.lower()
    for term in terms:
        words = [w for w in re.split(r"\s+", term.lower().strip()) if w]
        if words and all(w in low for w in words):
            return True
    return False
