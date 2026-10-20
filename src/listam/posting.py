"""Posting and managing your own ads.

The flow mirrors the website's 4-step wizard:

    GET  /add/{category}            ad types ("For sale", "To order", ...)
    GET  /add/{category}/{type}     compose form (with a one-time submission_token)
    POST /img/upload.php            one request per photo -> upload handle
    POST compose form               "Save as Draft" -> /my, or "Preview" -> preview form
    POST preview form               "Post" -> published (or sent to moderation)

Paid promotions are never selected: every submit forces an empty premium/payment
selection and no bundle.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from . import auth, parsers
from .client import Listam, resolve_option
from .http import ListamError, NotFound

# Form plumbing that callers never set directly.
INTERNAL_FIELDS = {
    "submission_token", "payment_method", "selected_premium", "selected_bundle_id", "category_id",
    "suggested_category_id", "category_suggestion", "category_suggestion_id", "category_suggestion_query",
    "_form_action", "form0_form_visited", "_form_confirm", "dpid", "ufilessort", "minPhotosCount",
    "part_codes", "cancel", "feloctype", "feloc1", "feloc2", "for_which_cars_", "contact_information_phone",
    "auto_part_number[0]", "auto_part_number[1]",
}
NO_PROMOTION = {"selected_premium": "", "payment_method": "", "selected_bundle_id": "-1"}
MY_TABS = {"active": 0, "drafts": 1, "inactive": 2, "moderation": 3, "rejected": 4}
MAX_PHOTO_BYTES = 16 * 1024 * 1024
PHOTO_MAX_SIZE = (1280, 960)  # what the website's uploader resizes to


# --------------------------------------------------------------------------- parsing


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip() if node is not None else ""


def _row(node):
    while node is not None and node.tag != "form":
        if "frow" in (node.attributes.get("class") or "").split():
            return node
        node = node.parent
    return None


def parse_ad_types(html: str) -> list[dict[str, str]]:
    tree = LexborHTMLParser(html)
    out = []
    for radio in tree.css('input[name="ad_type"]'):
        label = tree.css_first(f'label[for="{radio.attributes.get("id")}"]')
        name = _text(label) or _text(radio.parent)
        out.append({"value": radio.attributes.get("value") or "", "name": name})
    return out


def parse_ad_form(html: str) -> dict[str, Any]:
    """Describe the compose form: user-facing fields with labels, required flags and options."""
    tree = LexborHTMLParser(html)
    form = tree.css_first('form[name="form0"]')
    if form is None:
        raise ListamError("posting form not found (not logged in, or list.am changed the page)")
    fields: dict[str, dict[str, Any]] = {}
    dynamic: list[str] = []
    for inp in form.css("input[name], textarea[name], select[name]"):
        a = inp.attributes
        name = a.get("name")
        typ = (a.get("type") or inp.tag).lower()
        if name in INTERNAL_FIELDS or name in fields or typ in ("submit", "button", "file"):
            continue
        row = _row(inp)
        label = _text(row.css_first(".l label") or row.css_first(".l")) if row is not None else ""
        options = []
        if typ == "radio":  # e.g. "Your Status": Owner / Dealer
            for r in form.css(f'input[type="radio"][name="{name}"]'):
                lab = tree.css_first(f'label[for="{r.attributes.get("id")}"]')
                title = lab.css_first(".r1") if lab is not None else None  # .r1 title, .r2 explanation
                options.append({"value": r.attributes.get("value") or "",
                                "name": _text(title or lab) or r.attributes.get("value") or ""})
            if not label and row is not None:
                label = _text(row.css_first(".t, .title, h3, .label-container")) or ""
        elif row is not None and typ not in ("text", "textarea"):
            for o in row.css("[data-value]"):
                oname = (o.attributes.get("data-name") or _text(o)).replace(" ", " ").strip()
                if oname and not oname.startswith("@"):
                    options.append({"value": o.attributes.get("data-value"), "name": oname.lstrip("!")})
        if typ == "hidden" and not options:
            continue  # form plumbing (context ids, VIN decoder state, ...): submitted as-is
        if a.get("hx-post"):
            dynamic.append(name)
        fields[name] = {
            "label": label or {"title": "Title", "description": "Description", "status": "Your Status"}.get(name, name),
            "required": a.get("ferequired") == "1",
            "kind": "text" if typ in ("text", "textarea") else ("choice" if options else typ),
            "options": options,
        }

    phones = []
    for radio in form.css('input[name="contact_information_phone"]'):
        label = tree.css_first(f'label[for="{radio.attributes.get("id")}"]')
        phones.append({"id": radio.attributes.get("value"), "label": _text(label), "default": "checked" in radio.attributes})

    min_photos = form.css_first('input[name="minPhotosCount"]')
    m = re.search(r"init_upload\((\d+)", html)
    return {
        "fields": fields,
        # Fields whose choice makes the server send more fields (HTMX), e.g. car make -> model/year.
        "dynamic_fields": dynamic,
        "has_compatibility": form.css_first('input[name="for_which_cars_"]') is not None,
        "has_part_number": form.css_first('input[name="auto_part_number[0]"]') is not None,
        "phones": phones,
        "min_photos": int(min_photos.attributes.get("value") or 0) if min_photos else 0,
        "max_photos": int(m.group(1)) if m else None,
    }


def parse_form_errors(html: str) -> list[dict[str, str]]:
    """Validation messages on a re-rendered compose form, attached to their field."""
    tree = LexborHTMLParser(html)
    form = tree.css_first('form[name="form0"]')
    if form is None:
        return []
    out = []
    for err in form.css(".err"):
        msg = _text(err)
        if not msg:
            continue
        node, field = err, None
        for _ in range(6):
            node = node.parent
            if node is None:
                break
            lab = node.css_first(".l label")
            inp = node.css_first("input[name], textarea[name]")
            if lab is not None or inp is not None:
                field = _text(lab) if lab is not None else inp.attributes.get("name")
                break
        out.append({"field": field or "?", "message": msg})
    return out


def current_step(html: str) -> str | None:
    m = re.findall(r'step-wrapper current[^>]*>.*?</div>\s*([^<]+?)\s*</div>', html, re.S)
    return m[0] if m else None


def parse_my_ads(html: str) -> list[dict[str, Any]]:
    tree = LexborHTMLParser(html)
    ads = []
    for card in tree.css('[data-role="my-ad-card"][data-post-id]'):
        pid = card.attributes.get("data-post-id")
        status = card.css_first('[data-testid="my-ad-card-status"]')
        link = card.css_first('a[data-testid="my-ad-card-post-link"]')
        price = card.css_first('[data-testid="my-ad-card-price"]')
        call = re.search(r"(deldraft|delpost)\(\s*'?(\d+)'?\s*(?:,\s*'?(\d+)'?)?\s*\)", card.html or "")
        delete_url = None
        if call:
            delete_url = f"/ad-delete?i={call.group(2)}&m={2 if call.group(1) == 'deldraft' else (call.group(3) or 0)}"
        ads.append({
            "id": pid,
            "title": _text(card.css_first('[data-testid="my-ad-card-title"]')),
            "price": parsers.parse_price(_text(price)) if price is not None else None,
            "status": status.attributes.get("data-state") if status is not None else None,
            "category": _text(card.css_first(".category-breadcrumbs")) or None,
            "edit_url": parsers._abs(link.attributes.get("href")) if link is not None else None,
            "_delete": delete_url,
        })
    return ads


# --------------------------------------------------------------------------- client


class Poster:
    def __init__(self, api: Listam):
        self.api = api
        self.http = api.http
        if not auth.load_session(self.http):
            raise auth.NotLoggedIn("not logged in; run `listam login` first")

    def _get_logged_in(self, path: str, **kw):
        resp = self.http.get(self.http.page_url(path), **kw)
        if "/login" in str(resp.url):
            raise auth.NotLoggedIn("session expired; run `listam login` again")
        return resp

    # ---------------------------------------------------------------- form discovery

    def ad_types(self, category: int) -> list[dict[str, str]]:
        resp = self._get_logged_in(f"/add/{category}")
        if "/add" not in str(resp.url):
            raise NotFound(f"category {category} does not exist (see `listam categories`)")
        if 'name="form0"' in resp.text:  # single-type categories go straight to the compose form
            return [{"value": "", "name": "default"}]
        subs = re.findall(r'name="ad_sub_category"[^>]*value="/add/(\d+)/\d+"', resp.text)
        if subs:  # e.g. Passenger Cars asks for "Cars with Mileage" / "New Cars"
            names = {c: self._category_name(resp.text, c) for c in subs}
            listing = ", ".join(f"{c} ({n})" if n else c for c, n in names.items())
            raise NotFound(f"category {category} needs a sub-category: {listing}")
        types = parse_ad_types(resp.text)
        if not types:
            raise NotFound(f"category {category} does not accept new ads (pick a more specific sub-category)")
        return types

    @staticmethod
    def _category_name(html: str, cat: str) -> str:
        tree = LexborHTMLParser(html)
        radio = tree.css_first(f'input[name="ad_sub_category"][value^="/add/{cat}/"]')
        label = tree.css_first(f'label[for="{radio.attributes.get("id")}"]') if radio is not None else None
        return _text(label) if label is not None else ""

    def resolve_type(self, category: int, ad_type: str | None) -> str:
        types = self.ad_types(category)
        if ad_type is None or (len(types) == 1 and types[0]["name"] == "default"):
            return types[0]["value"]
        alias = {"sale": "for sale", "order": "to order", "rent": "for rent"}.get(str(ad_type).lower(), str(ad_type))
        return resolve_option(types, alias, "ad type")

    def compose_page(self, category: int, ad_type: str) -> tuple[str, str]:
        resp = self._get_logged_in(f"/add/{category}" + (f"/{ad_type}" if ad_type else ""))
        if 'name="form0"' not in resp.text:
            raise NotFound(f"category {category} has no posting form; ads go in a leaf sub-category "
                           f"(see `listam categories {category}`)")
        return str(resp.url), resp.text

    def describe(self, category: int, ad_type: str | None = None) -> dict[str, Any]:
        type_value = self.resolve_type(category, ad_type)
        _, html = self.compose_page(category, type_value)
        info = parse_ad_form(html)
        info["category"] = category
        info["ad_type"] = type_value
        info["ad_types"] = self.ad_types(category)
        return info

    # ---------------------------------------------------------------- building

    def build(self, spec: dict[str, Any], info: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
        """Turn an ad spec into form values; returns (values, human summary)."""
        fields = info["fields"]
        values: dict[str, str] = {}
        summary: dict[str, Any] = {}

        def by_label(key: str) -> str | None:
            if key in fields:
                return key
            low = key.lower().replace("_", " ")
            hits = [n for n, f in fields.items() if f["label"].lower() == low]
            return hits[0] if len(hits) == 1 else None

        def set_field(key: str, value: Any) -> None:
            name = by_label(key)
            if name is None:
                raise ListamError(f"unknown field {key!r} for this category; see `listam post-form {info['category']}`")
            f = fields[name]
            label = "Currency" if name == "currency" else (f["label"] or name)  # shares the "Price" label
            if f["kind"] == "choice":
                vals = value if isinstance(value, list) else [value]
                try:
                    ids = [resolve_option(f["options"], str(v), f["label"] or name) for v in vals]
                except ListamError as e:
                    msg = str(e).split(";")[0]
                    raise ListamError(f"{msg}; see `listam post-form {info['category']} --name {name}`") from None
                values[name] = ",".join(ids)
                names = {o["value"]: o["name"] for o in f["options"]}
                summary[label] = ", ".join(names.get(i, i) for i in ids)
            else:
                values[name] = str(value)
                summary[label] = str(value)

        for key in ("title", "description", "price", "location"):
            if spec.get(key) is not None:
                set_field(key, spec[key])
        if spec.get("currency") is not None:
            cur = {"amd": "֏", "usd": "$", "eur": "€", "rub": "₽"}.get(str(spec["currency"]).lower(), spec["currency"])
            set_field("currency", cur)
        for key, value in (spec.get("fields") or {}).items():
            set_field(key, value)

        if spec.get("part_number"):
            if not info["has_part_number"]:
                raise ListamError("this category has no part number field")
            values["auto_part_number[0]"] = str(spec["part_number"])
            if spec.get("hide_part_number"):
                values["auto_part_number[1]"] = "1"
            summary["Part number"] = f"{spec['part_number']}{' (hidden)' if spec.get('hide_part_number') else ''}"

        if spec.get("fits"):
            if not info["has_compatibility"]:
                raise ListamError("this category has no vehicle compatibility field")
            values["for_which_cars_"], summary["Fits"] = self._fits(spec["fits"])

        phone = spec.get("phone", True)
        phones = [p for p in info["phones"] if p["id"] != "0"]
        if phone is False or phone in ("none", 0, "0"):
            values["contact_information_phone"] = "0"
            summary["Phone"] = "not attached"
        elif phones:
            pick = phones[0] if phone is True else next((p for p in phones if p["id"] == str(phone)), None)
            if pick is None:
                raise ListamError(f"phone {phone!r} is not one of your account's numbers: {[p['id'] for p in phones]}")
            values["contact_information_phone"] = pick["id"]
            summary["Phone"] = "attached"

        if "Price" in summary and "Currency" in summary:
            summary["Price"] = f"{summary['Price']} {summary.pop('Currency').split(' ')[0]}"
        defaults = info.get("defaults", {})
        missing = [f["label"] or n for n, f in fields.items() if f["required"] and not (values.get(n) or defaults.get(n))]
        if "location" in fields and "location" not in values and defaults.get("location"):
            loc = {o["value"]: o["name"] for o in fields["location"]["options"]}.get(defaults["location"], defaults["location"])
            summary["Location"] = f"{loc} (account default)"
        if missing:
            raise ListamError(f"missing required fields: {', '.join(missing)}")
        photos = spec.get("photos") or []
        if len(photos) < info["min_photos"]:
            raise ListamError(f"this category needs at least {info['min_photos']} photo(s)")
        if info["max_photos"] and len(photos) > info["max_photos"]:
            raise ListamError(f"at most {info['max_photos']} photos allowed")
        summary["Photos"] = len(photos)
        return values, summary

    def _fits(self, fits: list[dict[str, str]]) -> tuple[str, list[str]]:
        tokens, labels = [], []
        for fit in fits:
            if not fit.get("make") or not fit.get("model"):
                raise ListamError(f"each 'fits' entry needs make and model: {fit!r}")
            make_id = self.api.resolve_make(fit["make"])
            model_id = self.api.resolve_model(make_id, fit["model"])
            gen = fit.get("generation")
            gen_id = self.api.resolve_generation(make_id, model_id, gen) if gen and str(gen).lower() != "all" else "0"
            tokens.append(f"{make_id}:{model_id}:{gen_id}")
            labels.append(f"{fit['make']} {fit['model']} {gen or '(all generations)'}")
        return ",".join(tokens), labels

    # ---------------------------------------------------------------- photos

    def upload_photo(self, path: Path, referer: str) -> str:
        data, name = _prepare_photo(path)
        resp = self.http.post(
            "/img/upload.php",
            data={"name": name},
            files={"file": (name, data, "image/jpeg")},
            headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest"},
        )
        handle = resp.text.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]+", handle):
            raise ListamError(f"photo upload rejected for {path.name}: {handle[:120]}")
        return handle

    # ---------------------------------------------------------------- submitting

    def _submit(self, url: str, base: list[tuple[str, str]], values: dict[str, str], handles: list[str], action: str):
        fields = dict(base)
        fields.update(values)
        fields.update(NO_PROMOTION)
        fields["_form_action"] = action
        button = {"Save as Draft": "_form_action3", "Preview": "_form_action0", "Post": "_form_action1"}[action]
        fields[button] = action
        fields["ufilessort"] = ",".join(handles)
        items = [(k, v) for k, v in fields.items() if k != "ufiles[]"] + [("ufiles[]", h) for h in handles]
        return self.http.post(url, data=items, headers={"Referer": url})

    def post(self, spec: dict[str, Any], mode: str, confirm=None) -> dict[str, Any]:
        """mode: 'dry-run' | 'draft' | 'publish'. `confirm(summary) -> bool` gates publishing."""
        category = int(spec["category"])
        type_value = self.resolve_type(category, spec.get("type"))
        url, html = self.compose_page(category, type_value)
        info = parse_ad_form(html)
        info["category"] = category
        base = parsers.parse_form(html, 'form[name="form0"]')["fields"]
        info["defaults"] = dict(base)
        if info["dynamic_fields"]:
            raise ListamError(
                f"category {category} uses a dynamic form ({', '.join(info['dynamic_fields'])} load further fields "
                "from the server); `listam post` does not support it yet. Post this one on the website.")
        values, summary = self.build(spec, info)
        summary = {"Category": category, **summary}
        if mode == "dry-run":
            return {"ok": True, "mode": mode, "summary": summary, "form_values": values}

        handles = [self.upload_photo(Path(p).expanduser(), url) for p in spec.get("photos") or []]

        if mode == "draft":
            resp = self._submit(url, base, values, handles, "Save as Draft")
            errors = parse_form_errors(resp.text) if "/my" not in str(resp.url) else []
            if errors:
                return {"ok": False, "step": "compose", "errors": errors}
            draft = next((a for a in self.my_ads("drafts") if a["title"] == spec.get("title")), None)
            return {"ok": True, "mode": mode, "draft": _public(draft), "summary": summary}

        # Previewing makes list.am save the ad as a draft; remember what existed so a
        # cancelled publish can remove only the draft it created.
        drafts_before = {a["id"] for a in self.my_ads("drafts")}
        resp = self._submit(url, base, values, handles, "Preview")
        if current_step(resp.text) != "Preview":
            return {"ok": False, "step": current_step(resp.text) or "compose", "errors": parse_form_errors(resp.text)}
        preview = parsers.parse_form(resp.text, 'form[name="form0"]')
        if confirm is not None and not confirm(summary):
            created = [a for a in self.my_ads("drafts") if a["id"] not in drafts_before and a["title"] == spec.get("title")]
            for draft in created:
                self.delete(draft)
            return {"ok": False, "mode": mode, "cancelled": True, "summary": summary,
                    "note": "nothing was published" + ("; the draft list.am made during preview was deleted" if created else "")}
        final = self._submit(preview["action"] or url, preview["fields"], {}, handles, "Post")
        return {"ok": True, "mode": mode, **self._outcome(final, spec), "summary": summary}

    def _outcome(self, resp, spec: dict[str, Any]) -> dict[str, Any]:
        final_url = str(resp.url)
        m = re.search(r"/item/(\d+)", final_url)
        if m:
            return {"status": "published", "id": m.group(1), "url": final_url}
        for tab in ("moderation", "active"):
            ad = next((a for a in self.my_ads(tab) if a["title"] == spec.get("title")), None)
            if ad and tab == "moderation":
                # In review: no public page yet (/item/<id> is 404) and list.am offers no
                # actions until moderation finishes; /mym/<id> is the owner's view.
                return {"status": "in review", "id": ad["id"], "url": ad["edit_url"],
                        "note": "list.am reviews new ads before they go public; it cannot be edited or "
                                "deleted until then. Check `listam my --tab active`."}
            if ad:
                return {"status": "published", "id": ad["id"], "url": f"https://www.list.am/en/item/{ad['id']}"}
        # Anything else (e.g. a promotion/payment page) is reported, never acted on.
        return {"status": "unknown", "step": current_step(resp.text), "url": final_url,
                "note": "posted, but the ad was not found in your active/review lists yet; check `listam my`"}

    # ---------------------------------------------------------------- my ads

    def my_ads(self, tab: str = "active", page: int = 1) -> list[dict[str, Any]]:
        if tab not in MY_TABS:
            raise ListamError(f"unknown tab {tab!r}; choose from {', '.join(MY_TABS)}")
        params = {"_t": MY_TABS[tab]}
        if page > 1:
            params["pg"] = page
        return parse_my_ads(self._get_logged_in("/my", params=params).text)

    def find_my_ad(self, ad_id: str) -> dict[str, Any] | None:
        for tab in MY_TABS:
            for ad in self.my_ads(tab):
                if ad["id"] == str(ad_id):
                    return ad
        return None

    def delete(self, ad: dict[str, Any]) -> None:
        if not ad.get("_delete"):
            if ad.get("status") == "moderation":
                raise ListamError(f"ad {ad['id']} is in review; list.am allows deleting it only after moderation")
            raise ListamError(f"ad {ad['id']} has no delete action")
        self.http.post(ad["_delete"], data=[("_form_action", ""), ("form0_form_visited", "1")],
                       headers={"X-Requested-With": "XMLHttpRequest", "Referer": self.http.page_url("/my")})
        if self.find_my_ad(ad["id"]) is not None:
            raise ListamError(f"ad {ad['id']} is still listed after deleting")


def _public(ad: dict[str, Any] | None) -> dict[str, Any] | None:
    return {k: v for k, v in ad.items() if not k.startswith("_")} if ad else None


def _prepare_photo(path: Path) -> tuple[bytes, str]:
    """Resize to the website's 1280x960 JPEG when Pillow is installed; otherwise
    upload JPEGs as-is."""
    if not path.exists():
        raise ListamError(f"photo not found: {path}")
    try:
        from PIL import Image, ImageOps
    except ImportError:
        if path.suffix.lower() not in (".jpg", ".jpeg"):
            raise ListamError(f"{path.name}: only JPEG is accepted; install Pillow (`pip install pillow`) to convert other formats")
        data = path.read_bytes()
        if len(data) > MAX_PHOTO_BYTES:
            raise ListamError(f"{path.name} is larger than 16 MB; install Pillow to resize it automatically")
        return data, path.name
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail(PHOTO_MAX_SIZE)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue(), path.with_suffix(".jpg").name


def load_spec(source: str) -> dict[str, Any]:
    import sys

    try:
        text = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
    except OSError as e:
        raise ListamError(f"cannot read ad spec {source}: {e.strerror}") from None
    try:
        spec = json.loads(text)
    except ValueError as e:
        raise ListamError(f"ad spec is not valid JSON: {e}") from None
    if not isinstance(spec, dict) or "category" not in spec:
        raise ListamError("ad spec must be a JSON object with at least a 'category'")
    base = Path(source).expanduser().parent if source != "-" else Path.cwd()
    spec["photos"] = [str(p if Path(p).expanduser().is_absolute() else base / p) for p in spec.get("photos") or []]
    return spec


def template(info: dict[str, Any]) -> dict[str, Any]:
    fields, help_ = {}, {}
    for name, f in info["fields"].items():
        if name in ("title", "description", "price", "currency", "location"):
            continue
        fields[name] = f["options"][0]["name"] if f["options"] else ""
        opts = ", ".join(o["name"] for o in f["options"][:8]) + (", …" if len(f["options"]) > 8 else "")
        help_[name] = f"{'required' if f['required'] else 'optional'} — {f['label']}" + (f"; options: {opts}" if opts else "")
    spec: dict[str, Any] = {
        "category": info["category"],
        "type": info["ad_types"][0]["name"] if info.get("ad_types") else None,
        "title": "...",
        "description": "...",
        "price": 0,
        "currency": "amd",
        "location": "Yerevan",
        "photos": ["photo1.jpg"],
        "phone": True,
        "fields": fields,
    }
    if info["has_part_number"]:
        spec["part_number"] = ""
        spec["hide_part_number"] = False
        help_["part_number"] = "OEM number; some categories require it before preview. hide_part_number keeps it out of the ad"
    if info["has_compatibility"]:
        spec["fits"] = [{"make": "toyota", "model": "camry", "generation": "xv70"}]
        help_["fits"] = "vehicles the part fits; omit generation for all generations"
    help_["phone"] = "true = attach your account phone, false = messages only"
    help_["type"] = "ad type: " + ", ".join(t["name"] for t in info.get("ad_types") or [])
    help_["photos"] = f"{info['min_photos']}-{info['max_photos'] or '?'} JPEG paths, relative to this file"
    spec["_help"] = help_  # ignored by `listam post`
    return spec
