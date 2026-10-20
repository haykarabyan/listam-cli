"""HTTP transport for list.am.

list.am sits behind Cloudflare, which rejects ordinary HTTP clients with a
"Just a moment..." JS challenge. curl_cffi impersonates a real Chrome TLS/HTTP2
fingerprint, which is enough to be served normal pages.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from curl_cffi import CurlMime, requests

BASE_URL = "https://www.list.am"
IMAGE_HOST = "https://img.list.am"

# list.am serves Armenian at the root, other languages under a prefix.
LANG_PREFIX = {"en": "/en", "ru": "/ru", "hy": ""}

_RETRY_STATUSES = {403, 429, 500, 502, 503, 504}


class ListamError(Exception):
    """Base error; `code` becomes the CLI exit code."""

    code = 1


class NotFound(ListamError):
    code = 2


class Blocked(ListamError):
    code = 3


def _is_challenge(resp: requests.Response) -> bool:
    if resp.status_code not in (403, 503):
        return False
    head = resp.text[:2000]
    return "Just a moment" in head or "challenge-platform" in head


class Http:
    def __init__(self, lang: str = "en", timeout: float = 30, retries: int = 3, delay: float = 0.4):
        if lang not in LANG_PREFIX:
            raise ValueError(f"unsupported language {lang!r}; choose from {sorted(LANG_PREFIX)}")
        self.lang = lang
        self.timeout = timeout
        self.retries = retries
        self.delay = delay  # politeness delay between consecutive requests
        self._last = 0.0
        self.session = requests.Session(impersonate="chrome")
        self.session.headers.update({"Accept-Language": {"en": "en-US,en;q=0.9", "ru": "ru,en;q=0.8", "hy": "hy,en;q=0.8"}[lang]})

    def page_url(self, path: str) -> str:
        """Absolute URL for a language-dependent page path like '/item/123'."""
        return BASE_URL + LANG_PREFIX[self.lang] + path

    def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> requests.Response:
        return self.request("GET", url, params=params, headers=headers)

    def post(
        self,
        url: str,
        data: dict[str, Any] | list[tuple[str, Any]] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        """POST a form (multipart when `files` = {field: (filename, bytes, type)}). Only retried when Cloudflare blocked it before it reached
        list.am; any other failure may already have been processed (e.g. an ad
        submitted), so it is surfaced instead of repeated."""
        if not files:
            return self.request("POST", url, data=data, headers=headers)
        # curl_cffi takes multipart bodies as CurlMime; plain fields go in as parts too.
        mime = CurlMime()
        for name, value in (data.items() if isinstance(data, dict) else (data or [])):
            mime.addpart(name=name, data=str(value).encode())
        for name, (filename, content, ctype) in files.items():
            mime.addpart(name=name, filename=filename, content_type=ctype, data=content)
        try:
            return self.request("POST", url, multipart=mime, headers=headers)
        finally:
            mime.close()

    def request(self, method: str, url: str, **kw: Any) -> requests.Response:
        if not url.startswith("http"):
            url = BASE_URL + url
        idempotent = method == "GET"
        last_err: str = ""
        for attempt in range(self.retries):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kw)
            except requests.RequestsError as e:
                if not idempotent:
                    raise ListamError(f"{method} {url} failed and was not retried (it may have gone through): {e}") from None
                last_err = str(e)
                time.sleep(1.5 * (attempt + 1))
                continue
            finally:
                self._last = time.monotonic()
            if resp.status_code == 404:
                raise NotFound(f"not found: {resp.url}")
            if resp.status_code in _RETRY_STATUSES and (idempotent or _is_challenge(resp)):
                last_err = "Cloudflare challenge" if _is_challenge(resp) else f"HTTP {resp.status_code}"
                time.sleep(2.0 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise ListamError(f"HTTP {resp.status_code} for {method} {resp.url}")
            return resp
        if last_err == "Cloudflare challenge":
            raise Blocked(f"blocked by Cloudflare at {url}; wait a few minutes and retry")
        raise ListamError(f"request failed after {self.retries} attempts: {url} ({last_err})")

    def get_html(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> str:
        return self.get(url, params=params, **kw).text

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.get(url, params=params, headers={"X-Requested-With": "XMLHttpRequest"}).json()

    def download(self, url: str, dest: Path) -> Path:
        """Download an image. img.list.am negotiates the format from the Accept
        header, so ask for what the extension says and trust the response type."""
        want = "image/webp" if url.endswith(".webp") else "image/jpeg"
        resp = self.get(url, headers={"Accept": want})
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
        ext = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/png": ".png", "image/avif": ".avif"}.get(ctype)
        if ext and dest.suffix != ext:
            dest = dest.with_suffix(ext)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest
