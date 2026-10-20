"""`listam` command-line interface. JSON on stdout, errors as JSON on stderr."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__, auth
from .client import PARTS_CATEGORY, Listam
from .http import ListamError, NotFound

EPILOG = """\
examples:
  listam search "camry headlight" --make toyota --model camry --condition used
  listam search -c 535 --make kia --model sportage --price-max 30000 --format text
  listam item 22973564 --phone --download ./photos
  listam phone https://www.list.am/en/item/22973564
  listam images 22973564 --dir ./photos
  listam categories 530          # sub-categories of Car Parts
  listam search -c 23 --make toyota --model camry -f "_a2_1=2018" --currency usd --price-max 25000
  listam models toyota            # model ids for --model
  listam filters 62 --name n      # every filter (query param) of a category

Output is JSON (pretty in a terminal, compact when piped). Exit codes:
0 ok, 1 error, 2 not found, 3 blocked by Cloudflare, 4 not logged in.
"""


def _emit(data: Any, args: argparse.Namespace) -> None:
    pretty = args.pretty if args.pretty is not None else sys.stdout.isatty()
    print(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None))


def _fmt_price(p: dict | None) -> str:
    return p["text"] if p else "-"


def _search_text(data: dict) -> str:
    lines = [
        f"# total exact matches: {data['total_matches']}  results on page(s): {len(data['results'])}"
        f"  next_page: {data['next_page']}  url: {data['url']}"
    ]
    for r in data["results"]:
        bits = [r["id"], r["section"], _fmt_price(r["price"]), r["title"] or ""]
        if r.get("subtitle"):
            bits.append(r["subtitle"])
        bits.append(r.get("location") or "")
        bits.append(f"{r['photo_count']} photos")
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def _item_text(d: dict) -> str:
    out = [f"{d['title']}", f"id {d['id']}  {_fmt_price(d['price'])}  {d.get('location') or ''}", d["url"]]
    for k, v in d["attributes"].items():
        out.append(f"{k}: {v}")
    if d.get("features"):
        out.append(f"Features: {', '.join(d['features'])}")
    for c in d["compatibility"]:
        out.append(f"Fits: {c['vehicle']} {'; '.join(c['generations'])}")
    if d.get("description"):
        out += ["", d["description"] + (f"  [{d['description_translation']}]" if d.get("description_translation") else ""), ""]
    if d.get("seller"):
        out.append(f"Seller: {d['seller'].get('name')}  {d['seller'].get('profile_url') or ''}")
    for p in (d.get("contact") or {}).get("phones", []):
        out.append(f"Phone: {p['display']}  ({p['e164']})")
    for p in d.get("image_files") or d["images"]:
        out.append(f"Image: {p}")
    return "\n".join(out)


# ---------------------------------------------------------------------------- commands


def cmd_search(api: Listam, a: argparse.Namespace) -> Any:
    extra = {}
    for kv in a.filter or []:
        if "=" not in kv:
            raise ListamError(f"--filter expects key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        extra[k.strip()] = v.strip()
    data = api.search(
        a.query,
        a.category,
        make=a.make,
        model=a.model,
        generation=a.generation,
        brand=a.brand,
        condition=a.condition,
        location=a.location,
        price_min=a.price_min,
        price_max=a.price_max,
        currency=a.currency,
        availability=a.availability,
        relevance=a.sort == "relevance",
        extra=extra,
        page=a.page,
        pages=a.pages,
    )
    if a.sections:
        keep = {s.strip() for s in a.sections.split(",")}
        data["results"] = [r for r in data["results"] if r["section"] in keep]
    if a.limit:
        data["results"] = data["results"][: a.limit]
    if a.format == "text":
        return _search_text(data)
    return data


def cmd_item(api: Listam, a: argparse.Namespace) -> Any:
    d = api.item(a.item)
    if a.phone:
        d["contact"] = api.phone(d["id"])
    if a.download:
        d["image_files"] = api.download_images(d["images"], Path(a.download).expanduser() / d["id"], ext=a.image_format)
    if a.format == "text":
        return _item_text(d)
    return d


def cmd_phone(api: Listam, a: argparse.Namespace) -> Any:
    return api.phone(a.item)


def cmd_images(api: Listam, a: argparse.Namespace) -> Any:
    d = api.item(a.item)
    dest = Path(a.dir).expanduser() / d["id"]
    files = api.download_images(d["images"], dest, ext=a.image_format, size=a.size) if d["images"] else []
    return {"id": d["id"], "title": d["title"], "directory": str(dest.resolve()), "files": files, "urls": d["images"]}


def cmd_categories(api: Listam, a: argparse.Namespace) -> Any:
    return api.categories(a.parent)


def cmd_filters(api: Listam, a: argparse.Namespace) -> Any:
    data = api.filters(a.category)
    if a.name:
        if a.name not in data:
            raise ListamError(f"category {a.category} has no filter {a.name!r}; available: {', '.join(data)}")
        return {a.name: data[a.name]}
    if not a.all:
        for v in data.values():
            if len(v["options"]) > 25:
                v["options_total"] = len(v["options"])
                v["options"] = v["options"][:25]
                v["note"] = "truncated; use --name <param> for the full list"
    return data


def cmd_makes(api: Listam, a: argparse.Namespace) -> Any:
    return api.makes(a.category)


def cmd_models(api: Listam, a: argparse.Namespace) -> Any:
    return api.models(a.make, a.category)


def _secret(prompt: str, from_stdin: bool, env: str) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    if os.environ.get(env):
        return os.environ[env]
    if not sys.stdin.isatty():
        raise ListamError(f"no terminal to prompt on; use --password-stdin or set {env}")
    return getpass.getpass(prompt)


def cmd_login(api: Listam, a: argparse.Namespace) -> Any:
    http = api.http
    if a.cookies:
        header = _secret("Paste the Cookie header (hidden): ", a.password_stdin, "LISTAM_COOKIES")
        auth.login_with_cookie_header(http, header)
    else:
        user = a.user or os.environ.get("LISTAM_USER")
        if not user:
            if not sys.stdin.isatty():
                raise ListamError("pass --user or set LISTAM_USER")
            user = input("Phone number or email: ").strip()
        password = _secret("Password (hidden): ", a.password_stdin, "LISTAM_PASSWORD")
        auth.login_with_password(http, user, password)
    path = auth.save_session(http)
    return {"logged_in": True, "session_file": str(path)}


def cmd_logout(api: Listam, a: argparse.Namespace) -> Any:
    return {"logged_in": False, "session_removed": auth.clear_session()}


def cmd_whoami(api: Listam, a: argparse.Namespace) -> Any:
    has_session = auth.load_session(api.http)
    logged_in = has_session and auth.is_logged_in(api.http)
    return {"logged_in": logged_in, "session_file": str(auth.session_path()) if has_session else None}


def _ask(question: str) -> bool:
    """Prompt on the terminal even when stdin carries the ad spec."""
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(question)
            tty.flush()
            return tty.readline().strip().lower() in ("y", "yes")
    except OSError:
        raise ListamError("no terminal to confirm on; pass --yes to publish without a prompt") from None


def _confirm_publish(summary: dict) -> bool:
    lines = ["", "About to PUBLISH this ad on list.am (no paid promotion):"]
    lines += [f"  {k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in summary.items()]
    print("\n".join(lines), file=sys.stderr)
    return _ask("Publish? [y/N] ")


def cmd_post_form(api: Listam, a: argparse.Namespace) -> Any:
    from . import posting

    poster = posting.Poster(api)
    info = poster.describe(a.category, a.type)
    if a.template:
        return posting.template(info)
    if a.name:
        if a.name not in info["fields"]:
            raise ListamError(f"no field {a.name!r}; fields: {', '.join(info['fields'])}")
        return {a.name: info["fields"][a.name]}
    if not a.all:
        for f in info["fields"].values():
            if len(f["options"]) > 25:
                f["options_total"] = len(f["options"])
                f["options"] = f["options"][:25]
    return info


def cmd_post(api: Listam, a: argparse.Namespace) -> Any:
    from . import posting

    spec = posting.load_spec(a.spec)
    mode = "dry-run" if a.dry_run else ("draft" if a.draft else "publish")
    confirm = None if (a.yes or mode != "publish") else _confirm_publish
    if confirm is not None:  # fail before uploading/previewing if nobody can answer the prompt
        try:
            open("/dev/tty").close()
        except OSError:
            raise ListamError("no terminal to confirm on; pass --yes to publish without a prompt, or use --draft") from None
    return posting.Poster(api).post(spec, mode, confirm=confirm)


def cmd_my(api: Listam, a: argparse.Namespace) -> Any:
    from . import posting

    ads = posting.Poster(api).my_ads(a.tab, a.page)
    return [posting._public(ad) for ad in ads]


def cmd_delete(api: Listam, a: argparse.Namespace) -> Any:
    from . import posting

    poster = posting.Poster(api)
    ad = poster.find_my_ad(a.id)
    if ad is None:
        raise NotFound(f"ad {a.id} is not among your ads (see `listam my`)")
    if not a.yes and not _ask(f"Permanently delete {ad['status']} ad {ad['id']} \"{ad['title']}\"? [y/N] "):
        return {"deleted": False, "id": ad["id"], "cancelled": True}
    poster.delete(ad)
    return {"deleted": True, "id": ad["id"], "title": ad["title"]}


def cmd_generations(api: Listam, a: argparse.Namespace) -> Any:
    return api.generations(a.make, a.model, a.category)


# ---------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=["en", "hy", "ru"], default="en", help="UI language for labels/locations (default en)")
    g = common.add_mutually_exclusive_group()
    g.add_argument("--pretty", dest="pretty", action="store_true", default=None, help="indent JSON output")
    g.add_argument("--compact", dest="pretty", action="store_false", help="single-line JSON output")
    common.add_argument("--no-cache", action="store_true", help="ignore cached filter/model lists (~/.cache/listam)")

    p = argparse.ArgumentParser(
        prog="listam",
        description="Agent-friendly CLI for list.am (Armenian classifieds): search listings, "
        "get details, photos and seller phone numbers.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"listam {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    s = sub.add_parser("search", parents=[common], help="search listings", description="Search listings. "
                       "Results are tagged section=top (promoted), regular (exact matches) or similar (fuzzy suggestions).")
    s.add_argument("query", nargs="?", help="free-text query (titles are often Armenian/Russian, so also try those)")
    s.add_argument("-c", "--category", type=int, help="category id (see `listam categories`); "
                   f"defaults to all, or {PARTS_CATEGORY} (Parts and Accessories) when --make is used")
    s.add_argument("--make", help="vehicle make, name or id (e.g. toyota): the car itself in vehicle categories "
                   "such as 23 (Passenger Cars), otherwise the car a part must fit")
    s.add_argument("--model", help="vehicle model, name or id (e.g. camry); needs --make")
    s.add_argument("--generation", help="vehicle generation, name fragment or id (e.g. XV70); needs --model")
    s.add_argument("--brand", help="part manufacturer, name or id (e.g. Bosch, Kashiyama)")
    s.add_argument("--condition", help="new | used (or a category-specific option name/id)")
    s.add_argument("--location", help="region or district name/id (e.g. Yerevan, Arabkir, Gyumri)")
    s.add_argument("--price-min", type=int)
    s.add_argument("--price-max", type=int)
    s.add_argument("--currency", choices=["amd", "usd"], help="currency for --price-min/max (default amd)")
    s.add_argument("--availability", choices=["sale", "order"], help="in stock (sale) or made to order")
    s.add_argument("-f", "--filter", action="append", metavar="KEY=VALUE",
                   help="any raw filter param from `listam filters <category>`; repeatable")
    s.add_argument("--sort", choices=["default", "relevance"], default="default")
    s.add_argument("--sections", help="comma list of sections to keep: top,regular,similar (default all)")
    s.add_argument("--page", type=int, default=1, help="start page (default 1)")
    s.add_argument("--pages", type=int, default=1, help="number of pages to fetch and merge (default 1)")
    s.add_argument("--limit", type=int, help="max results to output")
    s.add_argument("--format", choices=["json", "text"], default="json", help="text = one compact line per result")
    s.set_defaults(func=cmd_search)

    it = sub.add_parser("item", parents=[common], help="full details of a listing (all photos, specs, fitment, seller)")
    it.add_argument("item", help="item id or list.am item URL")
    it.add_argument("--phone", action="store_true", help="also fetch the seller's phone numbers")
    it.add_argument("--download", metavar="DIR", help="download all photos to DIR/<item id>/")
    it.add_argument("--image-format", choices=["jpg", "webp"], default="jpg")
    it.add_argument("--format", choices=["json", "text"], default="json")
    it.set_defaults(func=cmd_item)

    ph = sub.add_parser("phone", parents=[common], help="seller phone numbers (+ Viber/WhatsApp) for a listing")
    ph.add_argument("item", help="item id or list.am item URL")
    ph.set_defaults(func=cmd_phone)

    im = sub.add_parser("images", parents=[common], help="download all photos of a listing")
    im.add_argument("item", help="item id or list.am item URL")
    im.add_argument("--dir", default="listam-images", help="output directory (default ./listam-images); files go in DIR/<item id>/")
    im.add_argument("--size", choices=["full", "thumb"], default="full")
    im.add_argument("--image-format", choices=["jpg", "webp"], default="jpg")
    im.set_defaults(func=cmd_images)

    ca = sub.add_parser("categories", parents=[common], help="list top-level categories, or children of a category")
    ca.add_argument("parent", nargs="?", type=int, help="parent category id")
    ca.set_defaults(func=cmd_categories)

    fl = sub.add_parser("filters", parents=[common], help="filter params (and their option ids) supported by a category")
    fl.add_argument("category", type=int)
    fl.add_argument("--name", help="show only this param, with all options")
    fl.add_argument("--all", action="store_true", help="do not truncate long option lists")
    fl.set_defaults(func=cmd_filters)

    mk = sub.add_parser("makes", parents=[common], help="vehicle makes usable with --make")
    mk.add_argument("-c", "--category", type=int, default=PARTS_CATEGORY)
    mk.set_defaults(func=cmd_makes)

    mo = sub.add_parser("models", parents=[common], help="models of a make (for --model)")
    mo.add_argument("make")
    mo.add_argument("-c", "--category", type=int, default=PARTS_CATEGORY)
    mo.set_defaults(func=cmd_models)

    ge = sub.add_parser("generations", parents=[common], help="generations of a model (for --generation)")
    ge.add_argument("make")
    ge.add_argument("model")
    ge.add_argument("-c", "--category", type=int, default=PARTS_CATEGORY)
    ge.set_defaults(func=cmd_generations)
    lg = sub.add_parser("login", parents=[common], help="log in to your list.am account (stores session cookies only)",
                        description="Log in and save the session to ~/.config/listam/session.json (mode 600). "
                        "The password is never stored. Accounts that use Google/Facebook login have no password: "
                        "use --cookies and paste the Cookie request header from a logged-in browser instead.")
    lg.add_argument("--user", help="phone number or email (or set LISTAM_USER)")
    lg.add_argument("--password-stdin", action="store_true", help="read the password (or, with --cookies, the header) from stdin")
    lg.add_argument("--cookies", action="store_true", help="import a browser session instead of using a password")
    lg.set_defaults(func=cmd_login)

    lo = sub.add_parser("logout", parents=[common], help="delete the saved session")
    lo.set_defaults(func=cmd_logout)

    wh = sub.add_parser("whoami", parents=[common], help="check whether the saved session is still logged in")
    wh.set_defaults(func=cmd_whoami)

    pf = sub.add_parser("post-form", parents=[common], help="fields a category needs for a new ad (requires login)")
    pf.add_argument("category", type=int, help="leaf category id, e.g. 1679 (Brake Pads and Shoes)")
    pf.add_argument("--type", help="ad type: sale, order, rent, … (default: the first one)")
    pf.add_argument("--template", action="store_true", help="print a starter ad spec (JSON) to fill in")
    pf.add_argument("--name", help="show only this field, with all its options")
    pf.add_argument("--all", action="store_true", help="do not truncate long option lists")
    pf.set_defaults(func=cmd_post_form)

    po = sub.add_parser("post", parents=[common], help="post an ad from a JSON spec (requires login)",
                        description="Validate an ad spec, upload its photos, preview it and publish it after a y/N "
                        "confirmation. Paid promotions are never selected. Use `listam post-form CATEGORY --template` "
                        "to get a spec skeleton. Field values may be option names or ids.")
    po.add_argument("spec", help="path to the ad spec JSON, or - for stdin")
    g2 = po.add_mutually_exclusive_group()
    g2.add_argument("--draft", action="store_true", help="save as a draft instead of publishing")
    g2.add_argument("--dry-run", action="store_true", help="validate and resolve everything, upload and submit nothing")
    po.add_argument("--yes", action="store_true", help="publish without the confirmation prompt")
    po.set_defaults(func=cmd_post)

    my = sub.add_parser("my", parents=[common], help="list your own ads (requires login)")
    my.add_argument("--tab", choices=["active", "drafts", "moderation", "inactive", "rejected"], default="active")
    my.add_argument("--page", type=int, default=1)
    my.set_defaults(func=cmd_my)

    de = sub.add_parser("delete", parents=[common], help="permanently delete one of your ads or drafts (requires login)")
    de.add_argument("id", help="ad or draft id from `listam my`")
    de.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    de.set_defaults(func=cmd_delete)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        api = Listam(lang=args.lang, use_cache=not args.no_cache)
        out = args.func(api, args)
    except ListamError as e:
        print(json.dumps({"error": str(e), "type": type(e).__name__}, ensure_ascii=False), file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        return 130
    if isinstance(out, str):
        print(out)
    else:
        _emit(out, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
