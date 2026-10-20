"""Account session: log in, persist cookies, check login state.

Only session cookies are stored (never the password), in a file readable only by
the current user: $XDG_CONFIG_HOME/listam/session.json (default ~/.config/listam).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import parsers
from .http import Http, ListamError


class NotLoggedIn(ListamError):
    code = 4


def session_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "listam" / "session.json"


def save_session(http: Http) -> Path:
    cookies = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path, "expires": c.expires}
        for c in http.session.cookies.jar
        if "list.am" in (c.domain or "")
    ]
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"cookies": cookies}, f)
    return path


def load_session(http: Http) -> bool:
    path = session_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return False
    for c in data.get("cookies", []):
        http.session.cookies.set(c["name"], c["value"], domain=c.get("domain") or "www.list.am", path=c.get("path") or "/")
    return True


def clear_session() -> bool:
    path = session_path()
    if path.exists():
        path.unlink()
        return True
    return False


def is_logged_in(http: Http) -> bool:
    """/add redirects to /login?next=... when the session is missing or expired."""
    resp = http.get(http.page_url("/add"))
    return "/login" not in str(resp.url)


def login_with_password(http: Http, identity: str, password: str) -> None:
    page = http.get(http.page_url("/login")).text
    form = parsers.parse_form(page, 'form[name="form0"]')
    if form is None:
        raise ListamError("login form not found; list.am may have changed its login page")
    data = [(k, v) for k, v in form["fields"] if k not in ("phone_number_or_email", "password", "_form_action")]
    data += [("phone_number_or_email", identity), ("password", password), ("_form_action", "Login")]
    submit = next(iter(form["submits"]), None)
    if submit:
        data.append((submit, form["submits"][submit]))
    resp = http.post(form["action"] or http.page_url("/login"), data=data, headers={"Referer": http.page_url("/login")})
    if not is_logged_in(http):
        after = parsers.parse_form(resp.text, 'form[name="form0"]')
        detail = "; ".join(after["errors"]) if after and after["errors"] else "check the phone/email and password"
        raise NotLoggedIn(f"login failed: {detail}")


def login_with_cookie_header(http: Http, header: str) -> None:
    """Import a `Cookie:` header copied from a logged-in browser (for Google/Facebook accounts)."""
    header = header.strip().removeprefix("Cookie:").removeprefix("cookie:").strip()
    pairs = [p.split("=", 1) for p in header.split(";") if "=" in p]
    if not pairs:
        raise ListamError("no cookies found; paste the value of the Cookie request header")
    for name, value in pairs:
        http.session.cookies.set(name.strip(), value.strip(), domain="www.list.am", path="/")
    if not is_logged_in(http):
        raise NotLoggedIn("those cookies are not a logged-in list.am session (expired, or copied from another site?)")
