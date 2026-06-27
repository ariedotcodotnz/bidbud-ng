"""FastAPI dashboard for the self-hosted TradeMe bidding tool."""
from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, scheduler, security
from .models import STRATEGIES, STRATEGY_LABELS
from .money import D, default_two_increment_bid, fmt
from .strategies import StrategyConfig  # noqa: F401 (kept for clarity)
from .trademe import listing as listing_mod
from .trademe.auth import login_manager
from .trademe.browser import browser

NZ = ZoneInfo("Pacific/Auckland")

OPTION_KEYS = (
    "enter_default_bid", "bid_early_single_bid", "dont_add_cents",
    "email_if_outbid", "shipping_preference", "snipe_seconds",
    "fast_lead_seconds", "poll_far_seconds", "poll_near_seconds",
    "poll_final_seconds",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    await browser.start()
    scheduler.start()
    db.log(None, "info", "BidBud-NG started.")
    try:
        yield
    finally:
        await scheduler.stop()
        await browser.stop()


app = FastAPI(title="BidBud-NG", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "app" / "static")),
          name="static")
templates = Jinja2Templates(directory=str(config.BASE_DIR / "app" / "templates"))


# --------------------------------------------------------------------------- #
# Jinja helpers
# --------------------------------------------------------------------------- #
def _nztime(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso).astimezone(NZ)
    except ValueError:
        return iso
    return dt.strftime("%a %d %b, %-I:%M%p").replace("AM", "am").replace("PM", "pm")


def _timeleft(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = (dt - datetime.now(timezone.utc)).total_seconds()
    if secs <= 0:
        return "closed"
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


templates.env.filters["nztime"] = _nztime
templates.env.filters["timeleft"] = _timeleft
templates.env.filters["money"] = lambda v: fmt(D(v)) if v not in (None, "") else "—"
templates.env.globals["STRATEGIES"] = STRATEGIES
templates.env.globals["STRATEGY_LABELS"] = STRATEGY_LABELS


# --------------------------------------------------------------------------- #
# Optional basic-auth
# --------------------------------------------------------------------------- #
_basic = HTTPBasic(auto_error=False)


def require_ui(creds: HTTPBasicCredentials | None = Depends(_basic)):
    if not config.UI_USER and not config.UI_PASS:
        return  # auth disabled
    ok = (
        creds is not None
        and secrets.compare_digest(creds.username, config.UI_USER)
        and secrets.compare_digest(creds.password, config.UI_PASS)
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _options_snapshot() -> dict:
    s = db.all_settings()
    return {k: s.get(k, "") for k in OPTION_KEYS}


def _parse_decimal(raw: str) -> Decimal | None:
    try:
        return D(raw)
    except (InvalidOperation, ValueError):
        return None


async def _login_member_id() -> int | None:
    if login_manager.member_id:
        return login_manager.member_id
    return await browser.session_member_id()


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_ui)])
async def dashboard(request: Request):
    member_id = await _login_member_id()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "jobs": db.list_jobs(),
            "logged_in": member_id is not None,
            "member_id": member_id,
        },
    )


@app.get("/partials/jobs", response_class=HTMLResponse,
         dependencies=[Depends(require_ui)])
async def jobs_partial(request: Request):
    return templates.TemplateResponse(
        request,
        "partials/jobs_table.html",
        {"request": request, "jobs": db.list_jobs()},
    )


# --------------------------------------------------------------------------- #
# Login / 2FA
# --------------------------------------------------------------------------- #
@app.get("/login", response_class=HTMLResponse, dependencies=[Depends(require_ui)])
async def login_page(request: Request):
    member_id = await _login_member_id()
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "email": db.get_setting("trademe_email") or "",
            "have_password": bool(db.get_setting("trademe_password_enc")),
            "logged_in": member_id is not None,
            "member_id": member_id,
            "login": login_manager.status(),
        },
    )


@app.post("/login", dependencies=[Depends(require_ui)])
async def login_start(email: str = Form(""), password: str = Form("")):
    saved_email = db.get_setting("trademe_email") or ""
    saved_pw = security.decrypt(db.get_setting("trademe_password_enc") or "")
    email = (email or saved_email).strip()
    password = password or saved_pw
    if email:
        db.set_setting("trademe_email", email)
    if password:
        db.set_setting("trademe_password_enc", security.encrypt(password))
    if not email or not password:
        return JSONResponse({"error": "Email and password are required."},
                            status_code=400)
    await login_manager.start(email, password)
    return RedirectResponse("/login", status_code=303)


@app.post("/login/2fa", dependencies=[Depends(require_ui)])
async def login_2fa(code: str = Form(...)):
    await login_manager.submit_code(code)
    return RedirectResponse("/login", status_code=303)


@app.get("/login/status", response_class=HTMLResponse,
         dependencies=[Depends(require_ui)])
async def login_status(request: Request):
    member_id = await _login_member_id()
    return templates.TemplateResponse(
        request,
        "partials/login_status.html",
        {
            "request": request,
            "login": login_manager.status(),
            "logged_in": member_id is not None,
            "member_id": member_id,
        },
    )


# --------------------------------------------------------------------------- #
# Add / preview / create listing
# --------------------------------------------------------------------------- #
@app.get("/add", response_class=HTMLResponse, dependencies=[Depends(require_ui)])
async def add_page(request: Request):
    return templates.TemplateResponse(
        request,
        "add_listing.html",
        {
            "request": request,
            "default_strategy": db.get_setting("default_strategy"),
            "preview": None,
        },
    )


@app.post("/listings/preview", response_class=HTMLResponse,
          dependencies=[Depends(require_ui)])
async def preview_listing(request: Request, url: str = Form(...)):
    target = listing_mod.normalise_url(url)
    listing_id = listing_mod.listing_id_from_url(target)
    html = await browser.fetch_html(target)
    state = listing_mod.parse_state(html, listing_id) if html else None
    if state is None:
        return templates.TemplateResponse(
            request,
            "partials/preview.html",
            {"request": request, "error": "Couldn't read that listing. Check the "
                                          "URL and that you're logged in.",
             "preview": None},
        )
    enter_default = db.get_bool("enter_default_bid")
    suggested = (default_two_increment_bid(state.current_price, state.min_next_bid)
                 if enter_default else state.min_next_bid)
    return templates.TemplateResponse(
        request,
        "partials/preview.html",
        {
            "request": request,
            "error": None,
            "preview": state,
            "url": target,
            "listing_id": state.listing_id,
            "suggested": suggested,
            "default_strategy": db.get_setting("default_strategy"),
            "shipping_default": db.get_setting("shipping_preference") or "cheapest",
        },
    )


@app.post("/listings", dependencies=[Depends(require_ui)])
async def create_listing(
    url: str = Form(...),
    listing_id: str = Form(...),
    title: str = Form(""),
    strategy: str = Form("fast"),
    max_bid: str = Form(...),
    end_date: str = Form(""),
    current_price: str = Form(""),
    shipping_choice: str = Form("cheapest"),
):
    if strategy not in STRATEGIES:
        strategy = "fast"
    amount = _parse_decimal(max_bid)
    if amount is None or amount <= 0:
        raise HTTPException(400, "Invalid maximum bid.")
    options = _options_snapshot()
    # The specific shipping option (or keyword) chosen for THIS listing.
    options["shipping_choice"] = (shipping_choice or "cheapest").strip()
    db.create_job(
        listing_id=listing_id,
        url=listing_mod.normalise_url(url),
        title=title,
        strategy=strategy,
        max_bid=str(amount),
        end_date=end_date or None,
        current_price=current_price or None,
        options=options,
    )
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------------------- #
# Job actions / detail
# --------------------------------------------------------------------------- #
@app.get("/jobs/{job_id}", response_class=HTMLResponse,
         dependencies=[Depends(require_ui)])
async def job_detail(request: Request, job_id: int):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "No such job.")
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "request": request,
            "job": job,
            "options": json.loads(job["options"] or "{}"),
            "logs": db.job_logs(job_id),
        },
    )


@app.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_ui)])
async def cancel_job(job_id: int):
    scheduler.cancel_job(job_id)
    db.update_job(job_id, status="cancelled", last_action="cancelled by user")
    db.log(job_id, "info", "Cancelled by user.")
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/{job_id}/delete", dependencies=[Depends(require_ui)])
async def remove_job(job_id: int):
    scheduler.cancel_job(job_id)
    db.delete_job(job_id)
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(require_ui)])
async def settings_page(request: Request):
    s = db.all_settings()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"request": request, "s": s, "have_password":
            bool(s.get("trademe_password_enc"))},
    )


@app.post("/settings", dependencies=[Depends(require_ui)])
async def save_settings(request: Request):
    form = await request.form()
    text_keys = ("default_strategy", "shipping_preference", "snipe_seconds",
                 "fast_lead_seconds", "poll_far_seconds", "poll_near_seconds",
                 "poll_final_seconds", "trademe_email")
    bool_keys = ("enter_default_bid", "bid_early_single_bid", "dont_add_cents",
                 "email_if_outbid")
    updates: dict[str, str] = {}
    for k in text_keys:
        if k in form:
            updates[k] = str(form[k]).strip()
    for k in bool_keys:
        updates[k] = "1" if form.get(k) else "0"
    pw = str(form.get("trademe_password", "")).strip()
    if pw:
        updates["trademe_password_enc"] = security.encrypt(pw)
    db.set_settings(updates)
    return RedirectResponse("/settings", status_code=303)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
