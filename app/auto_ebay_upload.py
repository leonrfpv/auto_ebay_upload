#!/usr/bin/env python3
# auto_ebay_upload – v2.12.x (FULL)
from __future__ import annotations

import os, re, io, json, time, html, uuid, tempfile, webbrowser, traceback, pathlib, threading, datetime
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
from urllib.parse import urlparse

# ---------- Pfade & Logging ----------
ROOT = pathlib.Path(__file__).resolve().parent.parent
APPDIR = ROOT / "app"
LOGDIR = ROOT / "logs"; LOGDIR.mkdir(parents=True, exist_ok=True)
SESSION_LOG = LOGDIR / "session.log"
GUI_ERRLOG = LOGDIR / "gui_error.log"

def now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def logline(msg: str):
    try:
        with open(SESSION_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{now_iso()}] {msg}\n")
    except Exception:
        pass

# ---------- Config / Defaults ----------
from dotenv import load_dotenv
load_dotenv(APPDIR / ".env")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
JS_RENDER_DEFAULT = os.getenv("JS_RENDER_DEFAULT", "1") in ("1", "true", "TRUE", "yes", "Yes")
AUTO_TRANSLATE_TO_DE = os.getenv("AUTO_TRANSLATE_TO_DE", "1") in ("1", "true", "TRUE", "yes", "Yes")
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "EUR") or "EUR"

# ---------- 3rd party ----------
import requests
from bs4 import BeautifulSoup
import html2text
from PIL import Image, ImageTk
import pandas as pd
from langdetect import detect, LangDetectException

# Optional – nur benutzt, wenn auto-translate aktiv
try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None

# ---------- Preis- & Varianten-Modi ----------
PRICING_INPUT = "input"
PRICING_AVG10 = "avg10"

VARIATION_SPLIT = "split"
VARIATION_BUNDLE = "bundle"

# ---------- Helpers ----------
def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")

def split_tokens(s: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]

def variant_synonyms(v: str) -> List[str]:
    v = (v or "").strip().lower()
    out = {v}
    # Normalisierungen
    out.add(v.replace(",", "."))
    out.add(v.replace("l", " l"))
    out.add(v.replace(" ", ""))  # "0,5l"
    # ml <-> l ableiten
    m = re.search(r"(\d+[.,]?\d*)\s*(ml|l)\b", v)
    if m:
        val = m.group(1).replace(",", ".")
        unit = m.group(2)
        try:
            x = float(val)
            if unit == "l":
                ml = int(round(x * 1000))
                out.update({f"{ml} ml", f"{ml}ml", f"{val}l", f"{val} l", f"{val.replace('.',',')} l"})
                if ml == 500:
                    out.update({"0,5l", "0.5l", "0,5 l", "0.5 l"})
            else:  # ml
                ml = int(round(x))
                out.update({f"{ml} ml", f"{ml}ml"})
                if ml == 500:
                    out.update({"0,5l", "0.5l", "0,5 l", "0.5 l"})
                if ml == 1000:
                    out.update({"1l", "1 l"})
        except Exception:
            pass
    # geläufige Schreibweisen
    if "0,5" in v or "0.5" in v:
        out.update({"0,5 l", "0.5 l", "0,5l", "0.5l", "500 ml", "500ml"})
    return sorted({t.strip() for t in out if t.strip()})

def desired_size_patterns(variant: str) -> Tuple[List[re.Pattern], List[re.Pattern]]:
    """
    Liefert (good_patterns, bad_patterns).
    good_patterns matchen exakt die gewünschte Größe (z.B. 500ml / 0,5l),
    bad_patterns matchen gängige andere Größen (um falsche Bilder abzustrafen).
    """
    v = (variant or "").lower()
    good = []
    bad = []
    # Zielgröße extrahieren
    m = re.search(r"(\d+[.,]?\d*)\s*(ml|l)\b", v)
    target_ml = None
    if m:
        val = m.group(1).replace(",", ".")
        unit = m.group(2)
        try:
            x = float(val)
            target_ml = int(round(x * (1000 if unit == "l" else 1)))
        except Exception:
            pass
    # Good-Pattern (Ziel)
    if target_ml:
        ml = target_ml
        l = ml/1000.0
        good += [
            re.compile(rf'(?:^|[_\-]){ml}\s*ml(?:[^0-9]|$)'),
            re.compile(rf'(?:^|[_\-]){ml}ml(?:[^0-9]|$)'),
            re.compile(rf'(?:^|[_\-]){l:.1f}\s*l(?:[^0-9]|$)'.replace('.', r'[.,]')),
            re.compile(rf'(?:^|[_\-]){str(l).replace(".",",")}\s*l(?:[^0-9]|$)'),
            re.compile(rf'(?:^|[_\-]){str(l).replace(".",",")}l(?:[^0-9]|$)'),
        ]
    # Bad-Pattern (andere typische Größen)
    other_mls = [10,20,50,100,250,500,1000,1500,2000,2500,5000,10000]
    if target_ml:
        other_mls = [x for x in other_mls if x != target_ml]
    for ml in other_mls:
        l = ml/1000.0
        bad += [
            re.compile(rf'(?:^|[_\-]){ml}\s*ml(?:[^0-9]|$)'),
            re.compile(rf'(?:^|[_\-]){ml}ml(?:[^0-9]|$)'),
            re.compile(rf'(?:^|[_\-]){l:.1f}\s*l(?:[^0-9]|$)'.replace('.', r'[.,]')),
            re.compile(rf'(?:^|[_\-]){str(l).replace(".",",")}l(?:[^0-9]|$)'),
        ]
    return good, bad


# ---------- Datenmodell ----------
@dataclass
class ItemRow:
    brand: str
    name: str
    variant: str
    quantity: int
    price: Optional[float] = None
    sku: Optional[str] = None
    source_url: Optional[str] = None
    category_id: Optional[int] = None
    condition_id: int = 1000
    vat_percent: Optional[float] = 19.0

    def auto_sku(self) -> str:
        return f"{slugify(self.brand)}-{slugify(self.name)}-{slugify(self.variant)}"

    @property
    def title(self) -> str:
        base = " ".join(x for x in [self.brand.strip(), self.name.strip(), self.variant.strip()] if x)
        return f"{base} | Dünger • Neu"[:80]

# ---------- JS Renderer über Playwright ----------
class JSRenderer:
    @staticmethod
    def available() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def render(url: str, timeout_ms: int = 26000) -> Optional[str]:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=UA, locale="de-DE")
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                # leicht warten, bis Galerie / Tabs gebaut sind
                page.wait_for_timeout(900)
                html = page.content()
                browser.close()
                return html
        except Exception as e:
            logline(f"Playwright error: {e}")
            return None

# ---------- Parser: Beschreibung & Bilder ----------
class Parser:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})

    def _get(self, url: str, timeout: int = 20) -> requests.Response:
        return self.s.get(url, timeout=timeout)

    # ---- JSON-Helfer (LD+JSON / Shopify-ähnlich) ----
    def _extract_json_blobs(self, soup: BeautifulSoup) -> Dict[str, List[str]]:
        data = {"images": [], "descriptions": []}
        # LD+JSON
        for sc in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                js = json.loads(sc.string or "null")
            except Exception:
                continue
            arr = [js] if isinstance(js, dict) else js if isinstance(js, list) else []
            for obj in arr:
                if not isinstance(obj, dict):
                    continue
                if obj.get("@type") == "Product":
                    d = obj.get("description")
                    if d: data["descriptions"].append(d)
                    imgs = obj.get("image")
                    if isinstance(imgs, str): imgs = [imgs]
                    if isinstance(imgs, list):
                        data["images"] += [u for u in imgs if isinstance(u, str)]
        # generische <script>-Blobs
        for sc in soup.find_all("script"):
            txt = (sc.string or "") + "".join(sc.stripped_strings)
            if not txt: continue
            if any(k in txt for k in ['"media"', '"images"', '"image"', '"description"', "product"]):
                for m in re.finditer(r"https?://[^\s\"']+\.(?:png|jpe?g|webp)", txt, re.I):
                    data["images"].append(m.group(0))
                m = re.search(r'"description"\s*:\s*"([^"]+)"', txt)
                if m: data["descriptions"].append(m.group(1))
        return data

    # ---- Beschreibung sammeln (mehrere große Blöcke zulassen) ----
    def _desc_blocks(self, soup: BeautifulSoup) -> List[str]:
        sels = [
            "#tab-description", "#description", "div[itemprop='description']",
            ".product-description", ".product__description", ".product-single__description",
            "section.description", "div.description", "article.product__description",
            ".rte", ".entry-content", "main article",
            "div[data-product-description]", "section#description", "section.product-description",
            ".product__tabs", ".accordion", ".tab-content", ".section--description"
        ]
        blocks = []
        for sel in sels:
            for el in soup.select(sel):
                if not el: continue
                txt = el.get_text(" ", strip=True)
                if txt and len(txt) > 120:
                    blocks.append(str(el))
        # Fallback: größter Textblock
        if not blocks:
            big = sorted(soup.find_all(["div", "section", "article"]), key=lambda e: len(e.get_text(" ")), reverse=True)
            if big:
                blocks.append(str(big[0]))
        # JSON-LD / JSON
        jb = self._extract_json_blobs(soup)
        if jb["descriptions"]:
            for d in jb["descriptions"]:
                if d and len(d) > 80:
                    blocks.append(f"<p>{html.escape(d)}</p>")
        # einzigartig machen
        uniq, seen = [], set()
        for b in blocks:
            t = BeautifulSoup(b, "lxml").get_text(" ", strip=True)
            if t and t not in seen:
                uniq.append(b); seen.add(t)
        return uniq[:6]  # nicht zu viel

    # ---- Bilder sammeln ----
    def _absurl(self, maybe: str, base: str) -> str:
        if not maybe: return ""
        if maybe.startswith("//"): return "https:" + maybe
        if maybe.startswith("http"): return maybe
        if maybe.startswith("/"):
            return base.rstrip("/") + maybe
        return maybe

    def _base_of(self, url: str, soup: BeautifulSoup) -> str:
        can = soup.select_one("link[rel='canonical']")
        if can and can.get("href"): 
            try: return can["href"].split("/products")[0]
            except Exception: pass
        og = soup.select_one("meta[property='og:url']")
        if og and og.get("content"):
            try: return og["content"].split("/products")[0]
            except Exception: pass
        if "/products/" in url:
            return url.split("/products")[0]
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    def _collect_imgs(self, soup: BeautifulSoup, base: str) -> List[str]:
        imgs = []
        # Galerien / generische Container
        gallery_sel = ".product__media, .product-gallery, .gallery, .fotorama, .swiper, .slick, .thumbnails, .product-media, [class*='gallery']"
        containers = soup.select(gallery_sel) or [soup]
        for root in containers:
            nodes = root.select("img[src], img[data-src], img[data-zoom-image], img[data-large-image], source[srcset], [data-srcset], [data-bg], [data-background-image]")
            for n in nodes:
                # srcset
                ss = n.get("srcset") or n.get("data-srcset")
                if ss:
                    for part in ss.split(","):
                        u = self._absurl(part.strip().split(" ")[0], base)
                        imgs.append(u)
                # direct
                for attr in ("data-zoom-image", "data-large-image", "data-src", "src", "data-bg", "data-background-image"):
                    if n.get(attr):
                        u = self._absurl(n.get(attr), base)
                        imgs.append(u)
                # CSS background
                style = n.get("style", "")
                m = re.search(r'background-image\s*:\s*url\(([^\)]+)\)', style)
                if m:
                    u = self._absurl(m.group(1).strip('\'"'), base)
                    imgs.append(u)

        # OpenGraph / Preload
        for og in soup.select("meta[property='og:image']"):
            u = self._absurl((og.get("content") or "").strip(), base)
            imgs.append(u)
        for l in soup.select("link[rel='preload'][as='image']"):
            u = self._absurl((l.get("href") or "").strip(), base)
            imgs.append(u)

        # JSON-Blobs
        jb = self._extract_json_blobs(soup)
        for u in jb["images"]:
            imgs.append(self._absurl(u, base))

        # Filter & dedup
        imgs = [u for u in imgs if u and u.startswith("http") and re.search(r"\.(?:png|jpe?g|webp)(?:\?|$)", u, re.I)]
        out, seen = [], set()
        for u in imgs:
            if u not in seen:
                out.append(u); seen.add(u)
        return out[:50]  # vor Scoring

    def parse_html(self, html_text: str, url: str) -> Tuple[List[str], List[str]]:
        soup = BeautifulSoup(html_text, "lxml")
        base = self._base_of(url, soup)
        desc_blocks = self._desc_blocks(soup)
        imgs = self._collect_imgs(soup, base)
        return desc_blocks, imgs
def _shopify_handle_and_base(product_url: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        p = urlparse(product_url)
        parts = p.path.strip("/").split("/")
        if "products" in parts:
            idx = parts.index("products")
            handle = parts[idx+1] if idx+1 < len(parts) else None
            base = f"{p.scheme}://{p.netloc}"
            return handle, base
    except Exception:
        pass
    return None, None

def fetch_shopify_product_json(session: requests.Session, product_url: str) -> Tuple[Optional[dict], str]:
    """
    Versucht zuerst /products/<handle>.json (reichhaltig, inkl. variant_ids an Bildern),
    fällt dann auf /products/<handle>.js zurück (einfacheres JSON).
    Gibt (product_dict, mode) zurück, mode in {"json","js",""}.
    """
    handle, base = _shopify_handle_and_base(product_url)
    if not handle or not base:
        return None, ""
    # .json
    try:
        jurl = f"{base}/products/{handle}.json"
        r = session.get(jurl, timeout=12, headers={"User-Agent": UA})
        if r.ok and r.headers.get("Content-Type","").startswith("application/json"):
            data = r.json().get("product")
            if data: return data, "json"
    except Exception:
        pass
    # .js
    try:
        jurl = f"{base}/products/{handle}.js"
        r = session.get(jurl, timeout=12, headers={"User-Agent": UA})
        if r.ok and "application/javascript" in r.headers.get("Content-Type",""):
            data = r.json()  # Shopify liefert auch hier JSON
            return data, "js"
    except Exception:
        pass
    return None, ""
def horti_en_candidates_from_url(any_product_url: str) -> List[str]:
    """
    Aus einem beliebigen hortitec-Produktlink (DE/ES/EN) den Handle extrahieren
    und eine Prioritätenliste an EN/ES/DE-Kandidaten zurückgeben.
    """
    handle, base = _shopify_handle_and_base(any_product_url)
    if not handle:
        return [any_product_url]  # nichts ableitbar
    cands = [
        f"https://hortitec.es/en/products/{handle}",  # bevorzugt
        f"https://hortitec.es/products/{handle}",     # ES (ohne /en) – manchmal mit englischem Body
    ]
    # Ursprungs-URL und ggf. deutsche Variante auch anhängen (Fallback)
    cands.append(any_product_url)
    cands.append(f"https://www.hortitec.de/products/{handle}")
    # Duplikate entfernen, Reihenfolge behalten
    seen, out = set(), []
    for u in cands:
        if u not in seen:
            out.append(u); seen.add(u)
    return out

def images_for_variant_from_shopify(product: dict, mode: str, variant_text: str) -> List[str]:
    """
    Liefert eine geordnete Liste von Bild-URLs passend zur Variantengröße.
    Nutzt bei .json 'images[].variant_ids' zum harten Mapping.
    Fällt sonst auf Dateiname/alt-Text/Scoring zurück.
    """
    vtoks = set(split_tokens(" ".join(variant_synonyms(variant_text))))
    out = []

    if mode == "json":
        # product["images"] = [{src, alt, variant_ids:[...]}], product["variants"]=[{id,title},...]
        id_by_title = {}
        for v in product.get("variants", []):
            t = (v.get("title") or "").lower()
            id_by_title[t] = v.get("id")
        # best variant id candidate
        best_id = None
        for title, vid in id_by_title.items():
            if any(t in title for t in vtoks):
                best_id = vid; break
        imgs = product.get("images", [])
        # 1) exakte variant_ids-Treffer
        if best_id:
            for im in imgs:
                if best_id in (im.get("variant_ids") or []):
                    src = im.get("src") or ""
                    if src.startswith("//"): src = "https:" + src
                    if src: out.append(src)
        # 2) fallback: alt/Filename-Matching
        if not out:
            cands = []
            for im in imgs:
                src = im.get("src") or ""
                if src.startswith("//"): src = "https:" + src
                alt = (im.get("alt") or "").lower()
                fname = os.path.basename(urlparse(src).path).lower()
                score = 0
                score += sum(1 for t in vtoks if t and (t in alt or t in fname))
                cands.append((score, src))
            cands.sort(key=lambda x: x[0], reverse=True)
            out = [u for s,u in cands if u][:12]
        return out

    if mode == "js":
        # product["images"] = [url,...]  (ohne variant_ids); wir scoren nach Dateiname/alt-Anteil
        urls = []
        for u in product.get("images", []):
            if isinstance(u, str):
                if u.startswith("//"): u = "https:" + u
                urls.append(u)
        return urls  # wird später noch gescored

    return out

# ---------- Hersteller-/Quell-Resolver ----------
class SourceResolver:
    HORTI_EN = ["https://hortitec.es/en"]
    HORTI_ROOT = "https://hortitec.es"
    HORTI_DE = ["https://www.hortitec.de"]
    HORTI_ES = ["https://hortitec.es", "https://www.hortitec.es"]

    def __init__(self, manufacturers_cfg_path: pathlib.Path):
        self.s = requests.Session(); self.s.headers.update({"User-Agent": UA})
        try:
            self.manu_cfg = json.load(open(manufacturers_cfg_path, "r", encoding="utf-8"))
        except Exception:
            self.manu_cfg = {}

    # --- 1) Direkter Guess auf EN ---
    def _guess_horti_en(self, row: ItemRow) -> Optional[str]:
        handles = [
            slugify(f"{row.brand} {row.name} {row.variant}"),
            slugify(f"{row.brand} {row.name}"),
            slugify(row.name),
            slugify(f"{row.brand} {row.name} 500 ml"),
            slugify(f"{row.brand} {row.name} 0.5 l"),
        ]
        for base in self.HORTI_EN:
            for h in handles:
                url = f"{base}/products/{h}"
                try:
                    r = self.s.get(url, timeout=8)
                    if r.ok and "text/html" in r.headers.get("Content-Type",""):
                        return url
                except Exception:
                    continue
        return None

    # --- 2) EN-Suche (JSON suggest + HTML fallback) ---
    def _search_horti_en(self, row: ItemRow) -> Optional[str]:
        q = f"{row.brand} {row.name}".strip()
        # a) Shopify suggest.json
        try:
            su = f"{self.HORTI_ROOT}/search/suggest.json"
            params = {
                "q": q,
                "resources[type]":"product",
                "resources[limit]":"10",
                "resources[options][fields]":"title,product_type,variants.title,tag"
            }
            r = self.s.get(su, params=params, timeout=10, headers={"User-Agent": UA})
            if r.ok and "application/json" in r.headers.get("Content-Type",""):
                data = r.json() or {}
                prods = (((data.get("resources") or {}).get("results") or {}).get("products") or [])
                # Pick best product by title tokens
                qtoks = set(split_tokens(q))
                best = None; best_score = -1
                for p in prods:
                    title = (p.get("title") or "").lower()
                    handle = p.get("handle")
                    if not handle: continue
                    score = sum(1 for t in qtoks if t in title)
                    if score > best_score:
                        best_score = score; best = handle
                if best:
                    return f"{self.HORTI_EN[0]}/products/{best}"
        except Exception:
            pass
        # b) HTML-Suche
        try:
            hurl = f"{self.HORTI_EN[0]}/search?q={requests.utils.quote(q)}"
            r = self.s.get(hurl, timeout=10, headers={"User-Agent": UA})
            if r.ok:
                soup = BeautifulSoup(r.text, "lxml")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/products/" in href:
                        if href.startswith("/"): href = self.HORTI_EN[0].rstrip("/") + href
                        return href
        except Exception:
            pass
        return None

    # --- 3) DE/ES Guess (nur als Zwischenstufe, wird zu EN gemappt) ---
    def _guess_horti_de_es(self, row: ItemRow) -> Optional[str]:
        slugs = [
            slugify(f"{row.brand} {row.name} {row.variant}"),
            slugify(f"{row.brand} {row.name}"),
            slugify(row.name),
        ]
        for base in (self.HORTI_DE + self.HORTI_ES):
            for s in slugs:
                url = f"{base}/products/{s}"
                try:
                    r = self.s.get(url, timeout=8)
                    if r.ok and "text/html" in r.headers.get("Content-Type",""):
                        return url
                except Exception:
                    continue
        return None

    # --- 4) Map DE/ES -> EN wenn möglich ---
    def _map_to_en(self, url: str) -> Optional[str]:
        try:
            p = urlparse(url)
            parts = [x for x in p.path.split("/") if x]
            if "products" in parts:
                idx = parts.index("products")
                handle = parts[idx+1] if idx+1 < len(parts) else None
                if handle:
                    cand = f"{self.HORTI_EN[0]}/products/{handle}"
                    r = self.s.get(cand, timeout=8, headers={"User-Agent": UA})
                    if r.ok and "text/html" in r.headers.get("Content-Type",""):
                        return cand
        except Exception:
            pass
        return None

    # --- Hersteller-Hints bleiben wie gehabt ---
    def _manufacturer_hints(self, row: ItemRow) -> List[str]:
        brand = row.brand.strip().lower()
        name = row.name.strip().lower()
        hints = []
        if brand == "hesi" and name == "boost":
            hints += ["https://hesi.nl/de/Boost", "https://hesi.nl/Boost"]
        return hints

    # --- Gesamtauswahl ---
    def discover(self, row: ItemRow) -> Optional[str]:
        if row.source_url:
            return row.source_url
        # 1) EN-Guess
        url = self._guess_horti_en(row)
        if url: return url
        # 2) EN-Suche
        url = self._search_horti_en(row)
        if url: return url
        # 3) DE/ES finden & auf EN mappen
        tmp = self._guess_horti_de_es(row)
        if tmp:
            en = self._map_to_en(tmp)
            if en: return en
            return tmp  # zur Not
        # 4) Hersteller-Hints / generische Herstellersuche (nur wenn EN nichts liefert)
        for u in self._manufacturer_hints(row):
            try:
                r = self.s.get(u, timeout=8)
                if r.ok and "text/html" in r.headers.get("Content-Type", ""):
                    return u
            except Exception:
                pass
        bases = self.manu_cfg.get(row.brand.strip().lower(), [])
        queries = [f"{row.brand} {row.name} {row.variant}", f"{row.brand} {row.name}", row.name]
        for base in bases:
            for q in queries:
                for path in (f"/?s={requests.utils.quote(q)}", f"/search?q={requests.utils.quote(q)}", f"/products?search={requests.utils.quote(q)}"):
                    u = base.rstrip("/") + path
                    try:
                        r = self.s.get(u, timeout=10)
                        if not r.ok: continue
                        soup = BeautifulSoup(r.text, "lxml")
                        for a in soup.find_all("a", href=True):
                            href = a["href"]
                            if href.startswith("/"): href = base.rstrip("/") + href
                            low = href.lower()
                            if any(p in low for p in ["/product", "/products", "/produkt", "/producto", "/shop", "/store"]):
                                return href
                    except Exception:
                        continue
        return None


# ---------- Beschreibung aufbereiten (sanitizen + Übersetzen) ----------
ALLOWED_TAGS = {"p","br","ul","ol","li","b","strong","i","em","u","span","h1","h2","h3","h4","table","thead","tbody","tr","th","td"}

def sanitize_html(desc_html: str) -> str:
    soup = BeautifulSoup(desc_html or "", "lxml")
    for bad in soup(["script", "style", "iframe", "noscript", "svg", "form", "video", "audio"]):
        bad.decompose()
    for tag in soup(True):
        if tag.has_attr("style"):
            del tag["style"]
        if tag.name not in ALLOWED_TAGS:
            tag.name = "span"
    return str(soup)

def ensure_german(desc_html: str) -> str:
    text = BeautifulSoup(desc_html or "", "lxml").get_text(" ", strip=True)
    if not text:
        return desc_html

    auto_flag = os.getenv("AUTO_TRANSLATE_TO_DE", "1").lower() in ("1","true","yes")
    if not auto_flag:
        return desc_html

    try:
        lang = detect(text)
    except LangDetectException:
        lang = "unknown"
    if lang == "de":
        return desc_html

    # 1) DeepL (falls Key vorhanden)
    deepl_key = os.getenv("DEEPL_API_KEY", "").strip()
    if deepl_key:
        try:
            import deepl
            translator = deepl.Translator(deepl_key)
            # auto-detect source
            result = translator.translate_text(text, target_lang="DE")
            if result and result.text:
                paras = "".join(f"<p>{html.escape(p.strip())}</p>" for p in result.text.split("\n") if p.strip())
                return paras
        except Exception as e:
            logline(f"DeepL translation error: {e}")

    # 2) Fallback: GoogleTranslator
    try:
        if GoogleTranslator is not None:
            translated = GoogleTranslator(source="auto", target="de").translate(text)
            if translated and translated.strip():
                paras = "".join(f"<p>{html.escape(p.strip())}</p>" for p in translated.split("\n") if p.strip())
                return paras
    except Exception as e:
        logline(f"GoogleTranslator error: {e}")

    return desc_html

POS_KEYS = {"anwendung","dosierung","inhalt","zusammensetzung","npk","gebrauchsanweisung","analyse","hinweis","eigenschaften","beschreibung","produktbeschreibung"}
NEG_KEYS_HARD = {"menü schließen","menü schliessen","weiterlesen","ähnliche produkte","related products","you may also like","newsletter","breadcrumb","warenkorb","shop","kategorie","filter"}
NEG_OTHER_PRODUCTS = {"root","supervit","hydro","blüh","blüh","bloom","coco","kokos","complex"}  # typische Hesi-Begriffe anderer Produkte

def rank_desc_blocks(blocks: List[str], brand: str, name: str) -> List[str]:
    ranked = []
    for b in blocks:
        txt = BeautifulSoup(b, "lxml").get_text(" ", strip=True).lower()
        if not txt or len(txt) < 80:
            continue
        # harte Exklusion von Navigations-/Shop-Elementen
        if any(k in txt for k in NEG_KEYS_HARD):
            continue
        # Score aufbauen
        s = 0
        # Länge (gedeckelt)
        s += min(len(txt)//120, 10)
        # Marke/Produkt
        if brand.lower() in txt: s += 5
        if name.lower() in txt:  s += 6
        # Fachwörter
        s += sum(2 for k in POS_KEYS if k in txt)
        # Andere Hesi-Produkte abwerten
        if sum(1 for k in NEG_OTHER_PRODUCTS if k in txt) >= 2:
            s -= 6
        ranked.append((s, b))
    ranked.sort(key=lambda x: x[0], reverse=True)
    # die besten 1–3 Blöcke
    return [b for s, b in ranked[:3] if s > 0]

def combine_description(brand: str, name: str, variant: str, blocks: List[str]) -> str:
    # nur beste Blöcke zulassen
    top = rank_desc_blocks(blocks, brand, name)
    if not top:
        top = blocks[:1]  # Fallback: wenigstens der beste
    # Duplikate vermeiden
    uniq, seen = [], set()
    for b in top:
        t = BeautifulSoup(b, "lxml").get_text(" ", strip=True)
        if t and t not in seen:
            uniq.append(b); seen.add(t)
    body = "\n".join(uniq)
    header = f"<h2>{html.escape(brand)} {html.escape(name)} – {html.escape(variant)}</h2>"
    return header + sanitize_html(body)




# ---------- Bilder-Scoring (richtige Bilder priorisieren) ----------
def score_images(urls: List[str], brand: str, name: str, variant: str, source_domain: str) -> List[str]:
    brand_tokens = set(split_tokens(brand))
    name_tokens = set(split_tokens(name))
    var_tokens = set(split_tokens(" ".join(variant_synonyms(variant))))

    # klare No-Gos (andere Hesi-Produkte etc.)
    negative_words = {
        "root", "supervit", "hydro", "coco", "kokos", "bluh", "blüh", "complex", "complexe", "bloom", "starter", "kit", "test"
    }

    good_sizes, bad_sizes = desired_size_patterns(variant)

    def tokens_in(s: str, toks: set) -> int:
        low = s.lower()
        return sum(1 for t in toks if t and t in low)

    def match_any(patterns: List[re.Pattern], s: str) -> bool:
        return any(p.search(s) for p in patterns)

    def score(u: str) -> int:
        from urllib.parse import urlparse
        p = urlparse(u)
        netloc = p.netloc.lower()
        fname = os.path.basename(p.path).lower()

        sc = 0
        # Quelle gewichten
        if source_domain:
            try:
                src_netloc = urlparse(source_domain).netloc.lower()
                if src_netloc and src_netloc in netloc:
                    sc += 6
            except Exception:
                pass
        if "cdn.shopify.com" in netloc:
            sc += 2

        # Variantengröße: Treffer massiv belohnen / falsche stark bestrafen
        if good_sizes and match_any(good_sizes, fname):
            sc += 20
        if bad_sizes and match_any(bad_sizes, fname):
            sc -= 15

        # Produkt-/Markenbezug
        sc += 5 * tokens_in(fname, brand_tokens)
        sc += 8 * tokens_in(fname, name_tokens)   # Produktname noch wichtiger
        sc += 4 * tokens_in(fname, var_tokens)

        # hochauflösende Produktshots etwas bevorzugen
        if re.search(r"(?:^|[_-])(1200|1600|1920|2048|2400)(?:x|[_.-])", fname):
            sc += 2

        # Off-Topic hart abwerten
        if any(w in fname for w in negative_words):
            sc -= 10

        return sc

    ranked = sorted(urls, key=score, reverse=True)
    # Dateinamen-Deduplizierung (gleiche Bilder in verschiedenen Auflösungen)
    out, seen = [], set()
    for u in ranked:
        fn = os.path.basename(urlparse(u).path).lower()
        if fn not in seen:
            out.append(u); seen.add(fn)
        if len(out) >= 12:
            break
    return out


# ---------- Kuratierte letzte Rettung ----------
FALLBACK_HTML = {
    ("hesi","boost"): (
        "<p><strong>Hesi Boost</strong> ist ein Blühstimulator für die generative Phase. Er unterstützt die "
        "Bildung dichter, aromatischer Blüten und sorgt für eine gleichmäßigere Reife. Geeignet für Erde, "
        "Coco und Hydro.</p><ul><li>Fördert Blütenbildung &amp; Reife</li><li>Für Indoor &amp; Outdoor</li>"
        "<li>Kombinierbar mit Hesi-Grunddüngern</li></ul>"
        "<p><em>Hinweis:</em> Bitte Dosier- und Anwendungshinweise des Herstellers beachten.</p>"
    )
}

# ---------- Hersteller-Backfill ----------
def manufacturer_backfill(row: ItemRow, parser: Parser) -> Tuple[str, List[str], List[str]]:
    # gezielte, bekannte Seiten
    hints = {
        ("hesi","boost"): ["https://hesi.nl/de/Boost", "https://hesi.nl/Boost"]
    }
    key = (row.brand.strip().lower(), row.name.strip().lower())
    for u in hints.get(key, []):
        try:
            r = parser._get(u, 20)
            if r.ok:
                bl, im = parser.parse_html(r.text, u)
                if bl or im:
                    return u, bl, im
        except Exception:
            continue
    return "", [], []

# ---------- Listing-Payload bauen ----------
def build_item_payload(row: ItemRow, desc_html: str, picture_urls: List[str], price: float) -> Dict:
    cat = row.category_id or 3187
    return {
        "Title": row.title,
        "DescriptionHTML": desc_html,
        "CategoryID": int(cat),
        "Price": float(price or 9.99),
        "Quantity": int(row.quantity),
        "ConditionID": int(row.condition_id),
        "SKU": row.sku or row.auto_sku(),
        "PictureURLs": picture_urls[:12] if picture_urls else []
    }

# ---------- Hauptpipeline für ein Produkt ----------
def process_single(row: ItemRow, *, dry: bool, js_render: bool, variant_image_filter: bool, price_mode: str) -> Dict[str, object]:
    logline(f"process_single brand={row.brand} name={row.name} variant={row.variant} js={js_render} dry={dry}")
    resolver = SourceResolver(APPDIR / "manufacturers.json")
    parser = Parser()
        url = resolver.discover(row)
    if not url:
        return {"Status": "NO_SOURCE_URL", "SKU": row.sku or row.auto_sku(), "When": now_iso()}

    from urllib.parse import urlparse as _u
    p = _u(url)
    used_js = False
    back_src = ""
    source_used = "page"
    blocks, imgs = [], []

    # --- Shop-JSON zuerst mit EN-Prioritaet ---
    shop_json = None
    shop_mode = ""
    try_candidates = horti_en_candidates_from_url(url) if ("hortitec." in p.netloc.lower()) else [url]

    for cand in try_candidates:
        sj, sm = fetch_shopify_product_json(parser.s, cand)
        if sj:
            shop_json, shop_mode = sj, sm
            url = cand  # wir setzen die Quelle um auf EN-Kandidat, wenn erfolgreich
            break

    if shop_json:
        source_used = f"shopify:{shop_mode}"
        # Beschreibung direkt aus Shopify (EN bevorzugt) -> später nach DE
        body_html = ""
        if shop_mode == "json":
            body_html = shop_json.get("body_html") or ""
        elif shop_mode == "js":
            body_html = shop_json.get("description") or ""
        if body_html and len(BeautifulSoup(body_html, "lxml").get_text(" ", strip=True)) > 80:
            blocks = [body_html]
        # VARIANTEN-Bilder exakt
        imgs = images_for_variant_from_shopify(product=shop_json, mode=shop_mode, variant_text=row.variant)

    # EN-Flag nach evtl. Ueberschreibung von url neu bestimmen
    p = _u(url)
    host = p.netloc.lower()
    prefer_en = ("hortitec.es" in host) and ("/en/" in p.path)

   
    # Hilfsfunktion für Textlänge
    def desc_len(blist: List[str]) -> int:
        return len(BeautifulSoup("\n".join(blist), "lxml").get_text(" ", strip=True))

    # --- 2) Wenn noch nötig: HTML parsen + optional JS-Render ---
    if not blocks or not imgs:
        try:
            r = parser._get(url, 20)
            if r.ok and r.text:
                bl, im = parser.parse_html(r.text, url)
                if not blocks: blocks = bl
                if not imgs: imgs = im
        except Exception as e:
            logline(f"requests error: {e}")

        if js_render and (not imgs or desc_len(blocks) < 220):
            if JSRenderer.available():
                rendered = JSRenderer.render(url, 26000)
                if rendered:
                    used_js = True
                    bl2, im2 = parser.parse_html(rendered, url)
                    if desc_len(bl2) >= desc_len(blocks):
                        blocks = bl2 or blocks
                    if im2 and len(im2) > len(imgs):
                        imgs = im2
            else:
                logline("JSRenderer not available; skipping JS render")

    # --- 3) Hersteller-Backfill NUR wenn EN-Quelle nicht greift ---
    if (not imgs or desc_len(blocks) < 180) and not prefer_en:
        burl, bblocks, bimgs = manufacturer_backfill(row, parser)
        if burl:
            back_src = burl
            if desc_len(bblocks) > desc_len(blocks):
                blocks = bblocks
            if not imgs and bimgs:
                imgs = bimgs

    # --- 4) Harte Fallback-Beschreibung, falls gar nichts ---
    if desc_len(blocks) == 0:
        key = (row.brand.strip().lower(), row.name.strip().lower())
        if key in FALLBACK_HTML:
            blocks = [FALLBACK_HTML[key]]
            logline(f"Using curated fallback text for {key}")

    # --- 5) Bilder scoren + ggf. nach Variante filtern ---
    src_domain = f"{p.scheme}://{p.netloc}"
    imgs = score_images(imgs, row.brand, row.name, row.variant, src_domain)

    if variant_image_filter and row.variant:
        vt = variant_synonyms(row.variant)
        var_imgs = [u for u in imgs if any(t in u.lower() for t in vt)]
        if var_imgs:
            imgs = var_imgs

    # --- 6) Beschreibung bauen + ins Deutsche bringen ---
    desc_html = combine_description(row.brand, row.name, row.variant, blocks)
    desc_html = ensure_german(desc_html)

    # --- 7) Preislogik ---
    price = float(row.price or 9.99)
    if price_mode == PRICING_AVG10 and row.price:
        price = round(float(row.price) * 0.9, 2)

    item = build_item_payload(row, desc_html, imgs, price)

    meta = {
        "DescLen": len(html2text.html2text(desc_html or "")),
        "Pics": len(imgs),
        "JSUsed": used_js,
        "BackfillFrom": back_src,
        "SourceUsed": source_used,
        "PreferredEN": prefer_en
    }

    if dry:
        return {"Status": "DRY_OK", "Preview": item, "SourceURL": url, "When": now_iso(), **meta}

    # ---- eBay Upload (Stub) ----
    ok, msg = True, "OK (Stub)"
    return {"Status": "LISTED_OK" if ok else "LISTED_FAIL", "Message": msg, "Preview": item, "SourceURL": url, "When": now_iso(), **meta}

# ---------- CSV-Batch ----------
def process_csv(path: str, *, dry: bool, js_render: bool, variant_image_filter: bool, price_mode: str, variation_mode: str, spec_name: str, progress_cb=lambda p: None):
    df = pd.read_csv(path)
    required = ["Brand", "ProductName", "Variant", "Quantity"]
    for c in required:
        if c not in df.columns:
            raise ValueError(f"Spalte fehlt: {c}")

    rows: List[ItemRow] = []
    for _, r in df.iterrows():
        rows.append(ItemRow(
            brand=str(r.get("Brand","")).strip(),
            name=str(r.get("ProductName","")).strip(),
            variant=str(r.get("Variant","")).strip(),
            quantity=int(r.get("Quantity", 1)),
            price=(float(r["Price"]) if "Price" in df.columns and not pd.isna(r.get("Price")) else None),
            sku=(str(r["SKU"]).strip() if "SKU" in df.columns and not pd.isna(r.get("SKU")) else None),
            source_url=(str(r["SourceURL"]).strip() if "SourceURL" in df.columns and not pd.isna(r.get("SourceURL")) else None),
            category_id=(int(r["CategoryID"]) if "CategoryID" in df.columns and not pd.isna(r.get("CategoryID")) else None),
            condition_id=(int(r["ConditionID"]) if "ConditionID" in df.columns and not pd.isna(r.get("ConditionID")) else 1000),
            vat_percent=(float(r["VATPercent"]) if "VATPercent" in df.columns and not pd.isna(r.get("VATPercent")) else 19.0),
        ))

    out = []; total = len(rows)
    if variation_mode == VARIATION_SPLIT or total == 1:
        for i, row in enumerate(rows, 1):
            out.append(process_single(row, dry=dry, js_render=js_render, variant_image_filter=variant_image_filter, price_mode=price_mode))
            progress_cb(int(i/total*100))
        return out

    # Variation-Bundle (ein Listing, mehrere Varianten -> Dry-Run/Preview)
    groups: Dict[Tuple[str,str], List[ItemRow]] = {}
    for r in rows:
        key = (r.brand.strip().lower(), r.name.strip().lower())
        groups.setdefault(key, []).append(r)

    processed = 0
    for (brand, name), items in groups.items():
        pres = process_single(items[0], dry=True, js_render=js_render, variant_image_filter=variant_image_filter, price_mode=price_mode)
        parent_url = pres.get("SourceURL"); parent_desc = pres.get("Preview", {}).get("DescriptionHTML", "")
        variations = []; pic_map = {}
        for r in items:
            res = process_single(r, dry=True, js_render=js_render, variant_image_filter=variant_image_filter, price_mode=price_mode)
            variations.append({
                "SKU": r.sku or r.auto_sku(),
                "Value": r.variant,
                "Quantity": r.quantity,
                "Price": float(r.price or 9.99),
            })
            pic_map[r.variant] = res.get("Preview", {}).get("PictureURLs", [])[:12]
            processed += 1; progress_cb(int(processed/total*100))
        parent = {
            "Title": f"{items[0].brand} {items[0].name} | Dünger • Neu"[:80],
            "DescriptionHTML": parent_desc or f"<h2>{html.escape(items[0].brand)} {html.escape(items[0].name)}</h2>",
            "CategoryID": items[0].category_id or 3187,
            "ConditionID": items[0].condition_id or 1000
        }
        out.append({
            "Status": "DRY_OK" if dry else "LISTED_GROUP",
            "Group": f"{items[0].brand} {items[0].name}",
            "Variations": variations,
            "Pictures": {k: len(v) for k, v in pic_map.items()},
            "PreviewBase": parent,
            "SourceURL": parent_url,
            "When": now_iso()
        })
    return out
# ---------- GUI ----------
def launch_gui():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except Exception as e:
        with open(GUI_ERRLOG, "a", encoding="utf-8") as f:
            f.write(f"{now_iso()} Tkinter konnte nicht geladen werden: {e}\n")
        print("Tkinter konnte nicht geladen werden. Siehe logs/gui_error.log")
        return

    root = tk.Tk()
    root.title("auto_ebay_upload – eBay Listing Generator")
    root.geometry("1200x880")

    nb = ttk.Notebook(root); nb.pack(fill="both", expand=True)

    # ---- State ----
    brand_var   = tk.StringVar()
    name_var    = tk.StringVar()
    variant_var = tk.StringVar()
    qty_var     = tk.StringVar(value="1")
    price_var   = tk.StringVar(value="")
    source_var  = tk.StringVar(value="")

    dry_var     = tk.BooleanVar(value=True)
    js_var      = tk.BooleanVar(value=JS_RENDER_DEFAULT)
    trans_var   = tk.BooleanVar(value=AUTO_TRANSLATE_TO_DE)
    price_mode_var = tk.StringVar(value=PRICING_INPUT)
    varmode_var    = tk.StringVar(value=VARIATION_SPLIT)
    specname_var   = tk.StringVar(value="Größe")
    variant_img_filter_var = tk.BooleanVar(value=True)

    # ---- Tab: Aktion ----
    act = ttk.Frame(nb, padding=12)
    nb.add(act, text="Aktion")

    r=0
    ttk.Label(act, text="Marke").grid(row=r, column=0, sticky="w"); ttk.Entry(act, textvariable=brand_var, width=40).grid(row=r, column=1, sticky="ew"); r+=1
    ttk.Label(act, text="Artikelname").grid(row=r, column=0, sticky="w"); ttk.Entry(act, textvariable=name_var, width=40).grid(row=r, column=1, sticky="ew"); r+=1
    ttk.Label(act, text="Variante/Größe").grid(row=r, column=0, sticky="w"); ttk.Entry(act, textvariable=variant_var, width=40).grid(row=r, column=1, sticky="ew"); r+=1
    ttk.Label(act, text="Menge").grid(row=r, column=0, sticky="w"); ttk.Entry(act, textvariable=qty_var, width=12).grid(row=r, column=1, sticky="w"); r+=1
    ttk.Label(act, text="Preis (optional)").grid(row=r, column=0, sticky="w"); ttk.Entry(act, textvariable=price_var, width=12).grid(row=r, column=1, sticky="w"); r+=1
    ttk.Label(act, text="SourceURL (optional)").grid(row=r, column=0, sticky="w"); ttk.Entry(act, textvariable=source_var, width=60).grid(row=r, column=1, sticky="ew"); r+=1

    opts = ttk.LabelFrame(act, text="Optionen"); opts.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8,6)); r+=1
    ttk.Checkbutton(opts, text="Dry-Run (nur testen, kein Upload)", variable=dry_var).grid(row=0, column=0, sticky="w", padx=4, pady=2)
    ttk.Checkbutton(opts, text="JavaScript-Rendering (Playwright) aktivieren", variable=js_var).grid(row=0, column=1, sticky="w", padx=12, pady=2)
    ttk.Checkbutton(opts, text="Nicht-deutsche Beschreibung automatisch ins Deutsche übersetzen", variable=trans_var, command=lambda: toggle_translate()).grid(row=0, column=2, sticky="w", padx=12, pady=2)
    ttk.Checkbutton(opts, text="Nur variantenspezifische Bilder", variable=variant_img_filter_var).grid(row=0, column=3, sticky="w", padx=12, pady=2)

    pricebox = ttk.LabelFrame(act, text="Preis-Modus"); pricebox.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(4,6)); r+=1
    ttk.Radiobutton(pricebox, text="CSV/GUI-Preis verwenden", variable=price_mode_var, value=PRICING_INPUT).grid(row=0, column=0, sticky="w")
    ttk.Radiobutton(pricebox, text="eBay-Durchschnitt −10 % (Platzhalter)", variable=price_mode_var, value=PRICING_AVG10).grid(row=0, column=1, sticky="w", padx=12)

    varbox = ttk.LabelFrame(act, text="Varianten (CSV-Batch)"); varbox.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(4,6)); r+=1
    ttk.Radiobutton(varbox, text="Jede Größe als eigenes Listing", variable=varmode_var, value=VARIATION_SPLIT).grid(row=0, column=0, sticky="w")
    ttk.Radiobutton(varbox, text="Größen in EINEM Listing bündeln (Variations)", variable=varmode_var, value=VARIATION_BUNDLE).grid(row=0, column=1, sticky="w", padx=12)
    ttk.Label(varbox, text="Name des Varianten-Merkmals:").grid(row=1, column=0, sticky="w", pady=2)
    ttk.Entry(varbox, textvariable=specname_var, width=20).grid(row=1, column=1, sticky="w")

    btnrow = ttk.Frame(act); btnrow.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8,6)); r+=1
    ttk.Button(btnrow, text="Listing automatisch generieren", command=lambda: threading.Thread(target=start_single, daemon=True).start()).pack(side="left", padx=(0,8))
    ttk.Button(btnrow, text="CSV hochladen & alle listen", command=lambda: threading.Thread(target=open_csv, daemon=True).start()).pack(side="left")
    ttk.Button(btnrow, text="Protokoll exportieren (CSV/XLSX)", command=lambda: threading.Thread(target=export_log, daemon=True).start()).pack(side="left", padx=8)
    ttk.Button(btnrow, text="Abbrechen", command=lambda: prog.configure(value=0)).pack(side="left", padx=8)

    prog = ttk.Progressbar(act, mode="determinate", maximum=100)
    prog.grid(row=r, column=0, columnspan=2, sticky="ew"); r+=1
    log = tk.Text(act, height=12); log.grid(row=r, column=0, columnspan=2, sticky="nsew"); act.rowconfigure(r, weight=1); act.columnconfigure(1, weight=1)

    results_log: List[Dict[str,object]] = []
    def logln(m): log.insert("end", m+"\n"); log.see("end"); logline(m)

    # ---- Tab: Vorschau ----
    prev = ttk.Frame(nb, padding=10); nb.add(prev, text="Vorschau (aktuelles Listing)")
    title_lbl = ttk.Label(prev, text="Titel", font=("Segoe UI", 12, "bold")); title_lbl.grid(row=0, column=0, sticky="w")
    open_prev_btn = ttk.Button(prev, text="In Browser öffnen (HTML)"); open_prev_btn.grid(row=0, column=1, sticky="e")
    meta_lbl = ttk.Label(prev, text="", font=("Segoe UI", 10)); meta_lbl.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0,8))
    img_canvas = tk.Canvas(prev, height=170); img_scroll = ttk.Scrollbar(prev, orient="horizontal", command=img_canvas.xview)
    img_canvas.configure(xscrollcommand=img_scroll.set); img_frame = ttk.Frame(img_canvas); img_canvas.create_window((0,0), window=img_frame, anchor="nw")
    img_canvas.grid(row=2, column=0, columnspan=2, sticky="ew"); img_scroll.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0,8))
    ttk.Label(prev, text="Beschreibung", font=("Segoe UI", 10, "bold")).grid(row=4, column=0, sticky="w")
    desc_txt = tk.Text(prev, wrap="word", height=20); desc_txt.grid(row=5, column=0, columnspan=2, sticky="nsew")
    prev.columnconfigure(0, weight=1); prev.rowconfigure(5, weight=1)

    thumb_refs: List[ImageTk.PhotoImage] = []
    last_preview: Dict[str,object] = {}

    def render_preview(item: Dict[str,object]):
        from PIL import Image, ImageTk
        s = requests.Session(); s.headers.update({"User-Agent": UA})
        title_lbl.configure(text=item.get("Title","(kein Titel)"))
        meta_lbl.configure(text=f"SKU: {item.get('SKU','')}   |   Preis: {item.get('Price','')} {DEFAULT_CURRENCY}   |   Bilder: {len(item.get('PictureURLs',[]))}")
        for w in list(img_frame.children.values()): w.destroy()
        thumb_refs.clear()
        x = 6
        for u in item.get("PictureURLs", []):
            try:
                r = s.get(u, timeout=10); r.raise_for_status()
                im = Image.open(io.BytesIO(r.content))
                if im.mode not in ("RGB","RGBA"):
                    im = im.convert("RGB")
                im.thumbnail((150, 150))
                tkim = ImageTk.PhotoImage(im)
                thumb_refs.append(tkim)
                ttk.Label(img_frame, image=tkim).pack(side="left", padx=6, pady=6)
                img_canvas.update_idletasks()
                img_canvas.configure(scrollregion=img_canvas.bbox("all"))
            except Exception as e:
                logline(f"Image load error: {e} :: {u}")
                continue


        try:
            converter = html2text.HTML2Text(); converter.ignore_links=False; converter.ignore_images=True; converter.body_width=0
            txt = converter.handle(item.get("DescriptionHTML",""))
        except Exception:
            txt = "(Konnte Beschreibung nicht rendern)"
        desc_txt.delete("1.0","end"); desc_txt.insert("1.0", txt)
        last_preview.clear(); last_preview.update(item)

    def open_in_browser():
        if not last_preview: return
        item = last_preview
        html_doc = f"""<!DOCTYPE html><html lang="de"><meta charset="utf-8"><title>{item.get('Title','Vorschau')}</title>
        <body style="font-family:Segoe UI,Arial,sans-serif;margin:20px;">
        <h2>{item.get('Title','')}</h2>
        <p><b>SKU:</b> {item.get('SKU','')} &nbsp; | &nbsp; <b>Preis:</b> {item.get('Price','')} {DEFAULT_CURRENCY}</p>
        <div>""" + "".join(f'<img src="{u}" style="max-height:180px;margin:6px;border:1px solid #ddd;border-radius:6px;" />' for u in item.get("PictureURLs",[])) + """</div>
        <hr>
        <div>""" + item.get("DescriptionHTML","") + """</div>
        </body></html>"""
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".html"); f.write(html_doc.encode("utf-8")); f.close(); webbrowser.open(f.name)

    open_prev_btn.configure(command=open_in_browser)

    # ---- Actions ----
    def toggle_translate():
        # nur GUI-State; eigentliche Logik liest AUTO_TRANSLATE_TO_DE beim Prozessieren
        os.environ["AUTO_TRANSLATE_TO_DE"] = "1" if trans_var.get() else "0"

    def start_single():
        try:
            row = ItemRow(
                brand=brand_var.get().strip(),
                name=name_var.get().strip(),
                variant=variant_var.get().strip(),
                quantity=int(qty_var.get().strip() or "1"),
                price=(float(price_var.get().strip().replace(",", ".")) if price_var.get().strip() else None),
                source_url=source_var.get().strip() or None
            )
            res = process_single(row, dry=dry_var.get(), js_render=js_var.get(), variant_image_filter=variant_img_filter_var.get(), price_mode=price_mode_var.get())
            results_log.append(res)
            logln(json.dumps({k:v for k,v in res.items() if k not in ("Preview","PreviewBase")}, ensure_ascii=False))
            if res.get("Preview"):
                render_preview(res["Preview"]); nb.select(1)
        except Exception as e:
            traceback.print_exc()
            with open(GUI_ERRLOG, "a", encoding="utf-8") as f:
                f.write(f"{now_iso()} {e}\n{traceback.format_exc()}\n")
            logln(f"FEHLER: {e}")

    def open_csv():
        path = filedialog.askopenfilename(title="CSV wählen", filetypes=[("CSV","*.csv"),("Alle Dateien","*.*")])
        if not path: return
        try:
            res = process_csv(path, dry=dry_var.get(), js_render=js_var.get(), variant_image_filter=variant_img_filter_var.get(), price_mode=price_mode_var.get(), variation_mode=varmode_var.get(), spec_name=specname_var.get(), progress_cb=lambda p: prog.configure(value=p))
            ok = sum(1 for r in res if r.get("Status") in ("DRY_OK","LISTED_OK","LISTED_GROUP"))
            logln(f"Fertig. {ok}/{len(res)} erfolgreich.")
            for r in res:
                results_log.append(r)
                logln(json.dumps({k:v for k,v in r.items() if k not in ("Preview","PreviewBase")}, ensure_ascii=False))
            for r in res:
                if r.get("Preview"):
                    render_preview(r["Preview"]); nb.select(1); break
        except Exception as e:
            traceback.print_exc()
            with open(GUI_ERRLOG, "a", encoding="utf-8") as f:
                f.write(f"{now_iso()} {e}\n{traceback.format_exc()}\n")
            logln(f"FEHLER: {e}")

    def export_log():
        from tkinter import filedialog, messagebox
        if not results_log:
            messagebox.showinfo("Export", "Noch keine Ergebnisse zum Exportieren."); return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel","*.xlsx"),("CSV","*.csv")], initialfile=f"ebay_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        if not path: return
        df = pd.DataFrame([{k: ("" if k in ("Preview","PreviewBase") else v) for k,v in r.items()} for r in results_log])
        if path.lower().endswith(".csv"):
            df.to_csv(path, index=False, encoding="utf-8-sig")
        else:
            with pd.ExcelWriter(path, engine="openpyxl") as xw:
                df.to_excel(xw, index=False, sheet_name="Log")
        logln(f"Gespeichert: {path}")

    root.mainloop()

def safe_main():
    try:
        launch_gui()
    except Exception as e:
        with open(GUI_ERRLOG, "a", encoding="utf-8") as f:
            f.write(f"{now_iso()} {e}\n{traceback.format_exc()}\n")
        print("GUI konnte nicht gestartet werden. Siehe logs/gui_error.log")

if __name__ == "__main__":
    safe_main()
