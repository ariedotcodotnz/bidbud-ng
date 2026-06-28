"""Parse a pasted/uploaded TradeMe session into a Playwright storage-state.

Because TradeMe's *login* is protected by an F5/Shape bot-challenge (a CAPTCHA
shown to automated browsers), the reliable way to authenticate the tool is to
log in once as a human in a normal browser and import that session here.

We accept three formats:
  1. A Playwright ``storage_state.json`` (``{"cookies": [...], "origins": [...]}``)
     — e.g. produced by ``tools/get_session.py``.
  2. A JSON list of cookie objects — e.g. exported by the "Cookie-Editor"
     browser extension (``[{"name":..,"value":..,"domain":..}, ...]``).
  3. A raw cookie header string — ``name=value; name2=value2`` (copied from
     devtools / the ``Cookie`` request header).
"""
from __future__ import annotations

import json

TRADEME = "trademe.co.nz"

_SAMESITE = {
    "lax": "Lax", "strict": "Strict", "none": "None",
    "no_restriction": "None", "unspecified": "Lax",
}


def _norm_samesite(value) -> str:
    return _SAMESITE.get(str(value or "").lower(), "Lax")


def _cookie(name, value, *, domain=None, path="/", expires=-1,
            http_only=False, secure=True, same_site="Lax") -> dict:
    return {
        "name": name,
        "value": value if value is not None else "",
        "domain": domain or ".trademe.co.nz",
        "path": path or "/",
        "expires": expires,
        "httpOnly": bool(http_only),
        "secure": bool(secure),
        "sameSite": same_site,
    }


def _from_obj(c: dict) -> dict:
    name = c.get("name")
    if not name:
        raise ValueError("A cookie was missing its name.")
    expires = c.get("expires", c.get("expirationDate", -1))
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        expires = -1
    return _cookie(
        name, c.get("value", ""),
        domain=c.get("domain") or ".trademe.co.nz",
        path=c.get("path") or "/",
        expires=expires,
        http_only=c.get("httpOnly", c.get("hostOnly", False)),
        secure=c.get("secure", True),
        same_site=_norm_samesite(c.get("sameSite")),
    )


def parse_session_blob(text: str) -> dict:
    """Return a Playwright storage-state dict, or raise ``ValueError``."""
    text = (text or "").strip()
    if not text:
        raise ValueError("No session data provided.")

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        data = None

    cookies: list[dict] = []
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        cookies = [_from_obj(c) for c in data["cookies"]]
    elif isinstance(data, list):
        cookies = [_from_obj(c) for c in data if isinstance(c, dict)]
    elif data is None:
        # Raw "Cookie:" header form.
        body = text.split(":", 1)[1] if text.lower().startswith("cookie:") else text
        for part in body.replace("\n", " ").split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, _, value = part.partition("=")
            if name.strip():
                cookies.append(_cookie(name.strip(), value.strip()))
    else:
        raise ValueError("Unrecognised session format.")

    cookies = [c for c in cookies if TRADEME in (c.get("domain") or "")]
    if not cookies:
        raise ValueError(
            "No trademe.co.nz cookies were found in the pasted data."
        )
    return {"cookies": cookies, "origins": []}
