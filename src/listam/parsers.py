"""HTML -> plain dict parsers for list.am pages.

Everything here is pure (no network) so it can be tested against saved fixtures.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .http import BASE_URL, IMAGE_HOST

CURRENCY_SYMBOLS = {"֏": "AMD", "$": "USD", "₽": "RUB", "€": "EUR"}

# Result-section headers differ per UI language.
_SIMILAR_WORDS = ("similar", "նմանատիպ", "похожие")


# --------------------------------------------------------------------------- helpers


def _text(node: LexborNode | None) -> str | None:
    if node is None:
        return None
    t = node.text(separator=" ", strip=True)
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def _multiline_text(node: LexborNode | None) -> str | None:
    """Text that keeps line breaks (descriptions use <br> and newlines)."""
    if node is None:
        return None
    html = node.html or ""
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    t = LexborHTMLParser(html).text(strip=False)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in t.splitlines()]
    t = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", t) or None


def _abs(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    return urljoin(BASE_URL + "/", url)


def image_url(image_id: str | int, size: str = "full", ext: str = "jpg") -> str:
    """list.am stores photos at img.list.am/<size>/<last 3 digits>/<id>.<ext>.

    Size 'f' is full resolution; 'r' is the small list thumbnail.
    """
    s = str(image_id)
    return f"{IMAGE_HOST}/{'f' if size == 'full' else 'r'}/{s[-3:]}/{s}.{ext}"


def parse_price(text: str | None) -> dict[str, Any] | None:
    """'12,000 ֏' / '$158,000' / '$500 monthly' -> {amount, currency, text}."""
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    currency = next((code for sym, code in CURRENCY_SYMBOLS.items() if sym in text), None)
    m = re.search(r"\d[\d,.\s]*", text)
    amount = None
    if m:
        digits = re.sub(r"[^\d.]", "", m.group(0).replace(",", ""))
        try:
            amount = float(digits) if "." in digits else int(digits)
        except ValueError:
            amount = None
    return {"amount": amount, "currency": currency, "text": text}


def _normalize_phone(raw: str) -> dict[str, str]:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("374"):
        local = "0" + digits[3:]
    elif digits.startswith("0"):
        local = digits
    else:
        local = "0" + digits
    return {"local": local, "e164": "+374" + local[1:]}


# --------------------------------------------------------------------------- search results


def _card(a: LexborNode, section: str) -> dict[str, Any] | None:
    href = a.attributes.get("href") or ""
    m = re.search(r"/item/(\d+)", href)
    if not m:
        return None
    item_id = m.group(1)

    title = (
        _text(a.css_first(".pt"))
        or _text(a.css_first("div.l:not(.category-data-list-card__metadata)"))
        or _text(a.css_first(".jo62__title"))  # job ads
    )
    subtitle = _text(a.css_first(".jo62__subtitle"))  # employer, for job ads
    for at in a.css(".at"):
        if "category-data-list-card__location" not in (at.attributes.get("class") or ""):
            subtitle = _text(at)
            break
    # Several elements share the __metadata class (attributes, category path,
    # district); the district is the `.l` one in the where/when row.
    location = _text(a.css_first(".category-data-list-card__where-when .l")) or _text(
        a.css_first(".category-data-list-card__location")
    )
    category_path = _text(a.css_first(".c.category-data-list-card__metadata"))
    date = _text(a.css_first(".category-data-list-card__date"))
    price = parse_price(_text(a.css_first(".category-data-list-card__amount")))
    pills = [t for t in (_text(p) for p in a.css(".jo62__pill-text")) if t]
    meta = _text(a.css_first(".jo62__meta"))  # "Kentron · Today"
    if meta:
        location, _, when = meta.partition(" · ")
        date = date or when or None
    if price is None:
        salary = next((t for t in pills if any(sym in t for sym in CURRENCY_SYMBOLS)), None)
        price = parse_price(salary)

    photo_ids = [p for p in (a.attributes.get("data-tslider") or "").split(",") if p.strip()]
    np_attr = a.attributes.get("data-tslider-np") or ""
    labels = [t for t in (_text(s) for s in a.css(".po82 span, .clabel")) if t] + pills

    img = a.css_first("img")
    return {
        "id": item_id,
        "url": f"{BASE_URL}/en/item/{item_id}",
        "section": section,
        "title": title,
        "subtitle": subtitle,  # e.g. "Suitable For Toyota Camry" or "3 rm., 82 sq.m."
        "price": price,
        "location": location,
        "date": date,
        "category_path": category_path,  # set on cross-category results, e.g. "Furniture › Sofas"
        "photo_count": int(np_attr) if np_attr.isdigit() else len(photo_ids),
        # Cards preview at most ~6 photos; `listam item` returns the full gallery.
        "images": [image_url(p) for p in photo_ids],
        "thumbnail": _abs(img.attributes.get("src")) if img else None,
        "labels": labels or None,
    }


def parse_search(html: str) -> dict[str, Any]:
    tree = LexborHTMLParser(html)
    root = tree.css_first("#contentr") or tree.body

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    section = "regular"
    for node in root.css('div.toppost, div.hdr, div.glheader, a[data-testid^="favorite-ad-card-"]'):
        cls = node.attributes.get("class") or ""
        if node.tag == "div":
            if "toppost" in cls:
                section = "top"
            else:
                label = (_text(node) or "").lower()
                section = "similar" if any(w in label for w in _SIMILAR_WORDS) else "regular"
            continue
        card = _card(node, section)
        if card and card["id"] not in seen:
            seen.add(card["id"])
            results.append(card)

    total = None
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text())
        except (ValueError, TypeError):
            continue
        offers = data.get("offers") if isinstance(data, dict) else None
        if isinstance(offers, dict) and "offerCount" in offers:
            total = int(offers["offerCount"])
            break
    if total is None:
        meta = tree.css_first('meta[name="description"]')
        m = re.match(r"([\d,]+)\s", (meta.attributes.get("content") or "") if meta else "")
        if m:
            total = int(m.group(1).replace(",", ""))
    if total is None and not any(r["section"] == "regular" for r in results):
        total = 0

    has_next = any(
        (_text(a) or "").endswith(">") and re.search(r"/category[/?]", a.attributes.get("href") or "")
        for a in tree.css("a[href]")
    )
    return {"total": total, "has_next_page": has_next, "results": results}


# --------------------------------------------------------------------------- item page


# Values that only ever appear as the value half of an attribute tile.
_GENERIC_VALUES = {"yes", "no", "available", "not available", "by agreement", "new", "used", "installed", "not installed"}


def _norm_label(s: str) -> str:
    """'Mileage (km) from' -> 'mileage'; used to compare tile text with filter labels."""
    s = re.sub(r"\s*\(.*?\)", "", s).strip().lower()
    return re.sub(r"\s+(from|to)$", "", s)


def _orient_tile(first: str, second: str, known_labels: set[str]) -> tuple[str, str]:
    """Return (key, value) for a two-line attribute tile.

    Tiles render either value-first ('16 GB' / 'Memory (RAM)') or label-first
    ('Cleared Customs' / 'Yes') with identical markup, so decide by content:
    whichever line is a known attribute label for the category is the key.
    """
    if _norm_label(second) in known_labels:
        return second, first
    if _norm_label(first) in known_labels or second.lower() in _GENERIC_VALUES:
        return first, second
    return second, first  # value-first is the common layout


def item_category_id(html: str) -> int | None:
    m = re.search(r"recview\(\d+,\s*(\d+)\)", html)
    return int(m.group(1)) if m else None


def parse_item(html: str, item_id: str, known_labels: set[str] | None = None) -> dict[str, Any]:
    """Parse an item page. `known_labels` (normalized filter labels of the item's
    category) lets attribute tiles be oriented correctly; see _orient_tile."""
    known_labels = {_norm_label(x) for x in (known_labels or ())}
    tree = LexborHTMLParser(html)
    # Job ads mark the title as itemprop="title" (schema.org JobPosting).
    title = _text(tree.css_first('h1[itemprop="name"]')) or _text(tree.css_first('h1[itemprop="title"]'))
    if title is None:
        raise ValueError("not an item page (listing removed or expired?)")

    price_node = tree.css_first('span[itemprop="price"]')
    price = None
    if price_node is not None:
        cur = tree.css_first('meta[itemprop="priceCurrency"]')
        content = price_node.attributes.get("content")
        price = parse_price(_text(price_node))
        if price is not None:
            if content and re.fullmatch(r"[\d.]+", content):
                price["amount"] = float(content) if "." in content else int(content)
            if cur is not None and cur.attributes.get("content"):
                price["currency"] = cur.attributes["content"]
        conv = tree.css_first(".priceConverted")
        converted = [t for t in (_text(s) for s in conv.css("span")) if t] if conv is not None else []
        if price is not None and converted:
            price["converted"] = [parse_price(c) for c in converted]

    attributes: dict[str, str] = {}
    for c in tree.css(".attr .c"):
        k, v = _text(c.css_first(".t")), _text(c.css_first(".i"))
        if k and v:
            attributes[k] = v
    # Icon tiles (electronics, cars, real estate). One-line tiles are features
    # such as "Heated seats" or "Concierge"; ones the listing lacks are greyed
    # out with a "disabled" class.
    features: list[str] = []
    for tile in tree.css(".at3"):
        ps = [t for t in (_text(p) for p in tile.css(".attr-info-wraper p")) if t]
        if len(ps) == 1:
            if "disabled" not in (tile.attributes.get("class") or "").split():
                features.append(ps[0])
        elif len(ps) >= 2:
            key, value = _orient_tile(ps[0], ps[1], known_labels)
            attributes.setdefault(key, value)

    compatibility = []
    for node in tree.css(".compat .cnode, .compat .cleaf"):
        vehicle = _text(node.css_first(".clbl"))
        if not vehicle:
            continue
        gens = [g for g in (_text(x) for x in node.css(".cgen, .cgenin")) if g]
        compatibility.append({"vehicle": vehicle, "generations": gens})

    price_history = []
    for tr in tree.css(".price_history tr"):
        tds = [_text(td) for td in tr.css("td")]
        if len(tds) >= 2 and tds[0]:
            price_history.append({"date": tds[0], "price": tds[1], "change": tds[2] if len(tds) > 2 else None})

    image_ids: list[str] = []
    m = re.search(r"po117\.init\([^{]*\{img:\[(.*?)\]", html, re.S)
    if m:
        image_ids = re.findall(r"/(\d+)\.(?:webp|jpg)", m.group(1))
    if not image_ids:  # single-photo or no-JS fallback
        image_ids = list(dict.fromkeys(re.findall(r"img\.list\.am/f/\d+/(\d+)\.", html)))

    breadcrumbs = []
    for li in tree.css('[itemprop="itemListElement"]'):
        a = li.css_first('a[itemprop="item"]')
        name = _text(li.css_first('[itemprop="name"]'))
        href = a.attributes.get("href") if a else None
        cm = re.search(r"/category/(\d+)", href or "")
        breadcrumbs.append({"name": name, "category_id": int(cm.group(1)) if cm else None})
    cat_id = item_category_id(html)

    footer = tree.css_first(".footer")
    posted = tree.css_first('[itemprop="datePosted"]')
    renewed = None
    if footer is not None:
        for span in footer.css("span"):
            t = _text(span) or ""
            if t.lower().startswith(("renewed", "թարմացվել", "обновлено")):
                renewed = t

    seller = None
    uinfo = tree.css_first("#uinfo")
    if uinfo is not None:
        link = uinfo.css_first("a.n")
        seller = {
            "name": _text(uinfo.css_first(".nmsp")) or _text(uinfo.css_first(".username")),
            "profile_url": _abs(link.attributes.get("href")) if link else None,
            "tenure": _text(uinfo.css_first(".since")),
            "rating": _seller_rating(uinfo),
            "about": _multiline_text(uinfo.css_first(".desc")),
        }

    # On /en and /ru pages list.am machine-translates descriptions and appends a marker.
    desc_node = tree.css_first('[itemprop="description"]')
    translated_from = None
    if desc_node is not None:
        trans = desc_node.css_first(".trans")
        if trans is not None:
            translated_from = _text(trans)
            trans.decompose()

    loc = tree.css_first(".loc a") or tree.css_first(".loc")
    return {
        "id": str(item_id),
        "url": f"{BASE_URL}/en/item/{item_id}",
        "title": title,
        "price": price,
        "location": _text(loc),
        "category_id": cat_id,
        "breadcrumbs": breadcrumbs,
        "attributes": attributes,
        "features": features,
        "compatibility": compatibility,
        "description": _multiline_text(desc_node),
        "description_translation": translated_from,  # e.g. "Translated from Armenian"; use --lang hy for the original
        "price_history": price_history,
        "posted": posted.attributes.get("content") if posted else None,
        "renewed": renewed,
        "seller": seller,
        "images": [image_url(i) for i in image_ids],
        "phone_available": tree.css_first('[data-testid="listing-phone-reveal-button"]') is not None,
    }


def _seller_rating(uinfo: LexborNode) -> dict[str, Any] | None:
    score = _text(uinfo.css_first(".post-view-seller-rating-score"))
    count = _text(uinfo.css_first(".post-view-seller-review-count"))
    if not score:
        return None
    m = re.search(r"\d+", count or "")
    return {"score": float(score), "reviews": int(m.group(0)) if m else None}


# --------------------------------------------------------------------------- phone popup


def parse_phone(html: str, item_id: str) -> dict[str, Any]:
    tree = LexborHTMLParser(html)
    phones = []
    for a in tree.css("a.phone-number"):
        href = a.attributes.get("href") or ""
        raw = href.removeprefix("tel:") or (_text(a) or "")
        if not raw:
            continue
        phones.append({"display": _text(a.css_first(".phone-number-section")) or _text(a), **_normalize_phone(raw)})

    viber = [a.attributes.get("href") for a in tree.css('a[href^="viber:"]')]
    whatsapp = [a.attributes.get("href") for a in tree.css('a[href*="wa.me"]')]
    link = tree.css_first("a.n")
    return {
        "id": str(item_id),
        "seller": _text(tree.css_first(".nmsp")),
        "seller_profile_url": _abs(link.attributes.get("href")) if link else None,
        "phones": phones,
        "viber": viber,
        "whatsapp": whatsapp,
    }


# --------------------------------------------------------------------------- category page: tree + filters


def parse_categories(html: str) -> list[dict[str, Any]]:
    """Category nodes visible in a category page's sidebar tree (ancestors, self and children)."""
    tree = LexborHTMLParser(html)
    form = tree.css_first('form[name="f"]')
    if form is None:
        # Landing pages (Real Estate, Vehicles, Jobs) have no sidebar; children are
        # tile/shortcut links whose class differs per page (reh-shortcut, vl-tile, jl-tile).
        tiles, seen = [], set()
        for a in tree.css('a[href*="/category/"]'):
            if not re.search(r"(^|[\s-])(tile|shortcut)", a.attributes.get("class") or ""):
                continue
            m = re.search(r"/category/(\d+)$", a.attributes.get("href") or "")
            name = a.attributes.get("aria-label") or _text(a)
            if m and name and m.group(1) not in seen:
                seen.add(m.group(1))
                tiles.append({"id": int(m.group(1)), "name": name, "selected": False})
        return tiles
    out = []
    for node in form.css("[data-category-id]"):
        cid = node.attributes.get("data-category-id")
        name = _text(node.css_first(".category-title-text"))
        if cid and name:
            out.append({"id": int(cid), "name": name, "selected": "selected" in (node.attributes.get("class") or "")})
    # Expandable parent nodes carry their id on the toggle button instead.
    for btn in form.css('button[aria-controls^="categoryChildren-"]'):
        cid = (btn.attributes.get("aria-controls") or "").split("-")[-1]
        name = _text(btn.css_first(".category-title-text"))
        if cid.isdigit() and name and not any(c["id"] == int(cid) for c in out):
            out.append({"id": int(cid), "name": name, "selected": False})
    return out


def parse_top_categories(html: str) -> list[dict[str, Any]]:
    tree = LexborHTMLParser(html)
    out, seen = [], set()
    for a in tree.css('a[href*="/category/"][title]'):
        m = re.search(r"/category/(\d+)$", a.attributes.get("href") or "")
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append({"id": int(m.group(1)), "name": a.attributes.get("title")})
    return out


_WIDGETS = ("mu8", "si7", "dr4", "pr8")


def parse_filters(html: str) -> dict[str, dict[str, Any]]:
    """Discover the filter query parameters a category supports, with their options.

    Returns {param_name: {"label", "kind", "options": [{"value", "name", "group"}]}}.
    """
    tree = LexborHTMLParser(html)
    form = tree.css_first('form[name="f"]')
    if form is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for inp in form.css("input[name]"):
        a = inp.attributes
        name = a.get("name")
        if not name or name in ("seen_ids", "sname") or a.get("type") == "checkbox":
            continue
        # The widget is the nearest ancestor that holds the options; never look
        # beyond the input's own filter-section, or we'd pick up a neighbour's.
        widget = inp.parent
        while widget is not None and widget.tag != "form":
            cls = (widget.attributes.get("class") or "").split()
            if any(w in cls for w in _WIDGETS) or "filter-section" in cls or widget.css_first("[data-value]") is not None:
                break
            widget = widget.parent
        if widget is not None and widget.tag == "form":
            widget = None
        wcls = (widget.attributes.get("class") or "").split() if widget is not None else []

        label = None
        section = widget
        while section is not None and section.tag != "form":
            lab = section.css_first(".label-container, .dropdown-component-label, .label-wrapper p, .range-component-label")
            if lab is not None:
                label = _text(lab)
                break
            if "filter-section" in (section.attributes.get("class") or "").split():
                break
            section = section.parent
        if label and re.search(r"_[12]$", name) and name.startswith("_a"):  # range pair, e.g. _a2_1/_a2_2 = year from/to
            label = f"{label} {'from' if name.endswith('_1') else 'to'}"
        if name == "crc":
            label = "Price currency"
        if name in ("price1", "price2"):
            label = f"Price {'from' if name == 'price1' else 'to'}"

        options = []
        if widget is not None and "pr8" not in wcls and name not in ("price1", "price2"):
            group = None
            for o in widget.css("[data-value]"):
                value = o.attributes.get("data-value")
                oname = o.attributes.get("data-name") or _text(o)
                if value is None or not oname or oname.startswith("@"):
                    continue
                oname = oname.replace(" ", " ").strip()
                if oname.startswith("!"):  # group header, e.g. "!Yerevan" (selects whole region)
                    group = oname[1:]
                    options.append({"value": value, "name": group, "group": None, "is_group": True})
                    continue
                options.append({"value": value, "name": oname, "group": group})
        kind = "text" if a.get("type") == "text" else ("choice" if options else "hidden")
        out[name] = {"label": label, "kind": kind, "default": a.get("value") or None, "options": _dedupe(options)}
    return out


def _dedupe(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for o in options:
        key = (o["value"], o["name"])
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out


def parse_chain(data: dict[str, Any], key: str) -> dict[str, Any]:
    """`/lsw-chain` returns [[id, "!Parent"], [id, " Child"], ...]."""
    parent, items = None, []
    for value, name in data.get(key) or []:
        if name.startswith("!"):
            parent = {"id": int(value), "name": name[1:].strip()}
        else:
            items.append({"id": int(value), "name": name.strip()})
    return {"parent": parent, "items": items}
