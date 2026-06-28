# BidBud-NG

A small, self-hosted **TradeMe auction bidding assistant** — a bidding-only
alternative to the now-defunct BidBud. You run your own instance, it logs in to
your TradeMe account in a headless browser (passing 2FA), and it bids on
auctions for you using one of four strategies.

It does **not** use the TradeMe API — everything is driven through the website
with Playwright (Chromium), exactly as a person would.

> Use this only with **your own authorised TradeMe account**, for auctions you
> are entitled to bid on. You are responsible for the bids it places — a bid is
> a binding offer to buy.

---

## What it does

* **Web dashboard** (FastAPI + HTMX) to watch auctions, set your max bid and
  strategy, and see live status / activity logs.
* **Headless login with 2FA** — credentials are stored encrypted; when a fresh
  login is needed it prompts you in the dashboard for the 2FA code. The session
  is saved (`data/storage_state.json`) and reused, so this is rare.
* **Four bidding strategies** (all act within the final 2 minutes, like BidBud):
  * **Fast** – lodges a normal TradeMe auto-bid for your maximum, ~2 minutes
    before close.
  * **Slow** – places the minimum bid in the last few seconds; if outbid it
    waits for the close again and repeats, until it wins or hits your max.
  * **Blocking** – continuously keeps an auto-bid one increment above the
    leader, so nobody can take the lead with a mere minimum bid. *(Generates a
    TradeMe email per matched auto-bid — that's TradeMe, not the tool.)*
  * **Adaptive** – probes for a competing auto-bid (places a min bid, sees if it
    is instantly out-bid). If there's an auto-bid it jumps to your max to beat
    it; if opponents are manual it holds back during flurries and snipes at the
    end.
* **BidBud-style options:** *Enter default bid* (prefill two increments above
  current), *Bid early if single bid left* (lodge your max once the price is one
  bid away from it), *Don't add cents* (otherwise round-dollar bids get a few
  cents added to edge out round-number rivals), and *Email me if I'm outbid*.
* Reads authoritative listing state (price, closing time, minimum next bid,
  reserve, who's leading) from the page's embedded data, so it's robust to
  cosmetic site changes.

---

## Quick start

Requires **Python 3.11+**. A virtualenv already exists in `.venv`.

```bash
# 1. install deps + the Chromium browser
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
# on a bare server you may also need the system libraries:
#   .venv/bin/python -m playwright install-deps chromium   (needs root/apt)

# 2. (optional) configure environment
cp .env.example .env        # then edit if you like

# 3. run
.venv/bin/python run.py
```

Open <http://SERVER:8000>.

1. Go to **Account** and authenticate (see below).
2. **Add listing** → paste a listing URL (or number) → **Look up** → set your
   maximum bid + strategy → **Schedule bid**.
3. Watch it on the dashboard. Leave the process running 24/7 (a VPS is ideal so
   bids fire even when your laptop is off).

### Authenticating (important)

TradeMe's **login page is protected by an F5/Shape bot-challenge (a CAPTCHA)**
that is shown to automated/headless browsers — especially from a VPS/datacenter
IP. So fully automated login (email + password + 2FA in the tool) **often won't
work**: the tool will report that a bot-challenge was served. Everything *after*
login (reading listings, placing bids) runs on `www.trademe.co.nz`, which is
**not** challenged — so the fix is to authenticate as a human once and import
that session.

**Option A — automated login (try first).** On the **Account** page, enter your
email + password and log in; if prompted, type the 2FA code. If TradeMe shows
the bot-challenge, you'll get a clear message telling you to use Option B.

**Option B — import a session (reliable).** Log in to TradeMe in your **normal
browser** (no challenge for a human), then bring that session to the tool:

- *Easiest:* run the helper on a machine with a real browser (e.g. your laptop):
  ```bash
  pip install playwright && python -m playwright install chromium
  python -m tools.get_session     # opens a browser; log in; press Enter
  ```
  It writes `trademe_session.json`. On the **Account → Import session** card,
  upload that file (or copy it to the server as `data/storage_state.json`).
- *Or* paste cookies: in your logged-in browser, export your trademe.co.nz
  cookies (e.g. the "Cookie-Editor" extension → *Export* as JSON) and paste them
  into the **Import session** box. A raw `name=value; name2=value2` cookie header
  also works.

The imported session is saved to `data/storage_state.json` and reused; refresh
it the same way when it eventually expires.

### Protecting the dashboard

If the dashboard is reachable from the internet, set basic-auth in `.env`:

```
BIDBUD_UI_USER=me
BIDBUD_UI_PASS=a-long-password
```

Also put it behind HTTPS (e.g. a reverse proxy) — your TradeMe session lives
behind this UI.

---

## Configuration (`.env`)

| Var | Default | Meaning |
|-----|---------|---------|
| `BIDBUD_HOST` / `BIDBUD_PORT` | `0.0.0.0` / `8000` | Dashboard bind address |
| `BIDBUD_HEADLESS` | `1` | Run Chromium headless (keep `1` on a server) |
| `BIDBUD_SECRET_KEY` | auto | Fernet key for encrypting your stored password |
| `BIDBUD_UI_USER` / `BIDBUD_UI_PASS` | – | Optional dashboard basic-auth |
| `BIDBUD_ENGINE_LEAD_SECONDS` | `180` | How early the engine starts close watching |

Everything else (default strategy, the toggles, shipping choice, timing) is
edited live on the **Settings** page and stored in `data/bidbud.sqlite3`. Each
scheduled bid snapshots the settings at scheduling time, so changing settings
won't alter in-flight jobs.

---

## How it's built

```
run.py                 # loads .env, starts uvicorn
app/
  main.py              # FastAPI app, routes, HTMX pages
  config.py            # env/paths
  db.py                # SQLModel sessions + Alembic-backed SQLite persistence
  security.py          # Fernet password encryption
  money.py             # Decimal money + bid-increment + cents logic
  models.py            # Pydantic runtime models + SQLModel table models
  strategies.py        # the four strategies as pure decision functions
  engine.py            # per-job timing loop: poll -> decide -> bid -> verify
  scheduler.py         # APScheduler supervisor (one engine per active job)
  trademe/
    browser.py         # shared Playwright browser/context + storage_state
    auth.py            # login + 2FA state machine (driven from the UI)
    listing.py         # parse listing state from embedded #frend-state JSON
    bidder.py          # drive the bid modal (amount, shipping, autobid, submit)
  templates/  static/  # HTMX dashboard
alembic/               # database migrations
tests/                 # pytest suite (155 tests)
```

### Database migrations

SQLite persistence is modeled with SQLModel and versioned with Alembic. The app
runs migrations automatically on startup; existing pre-Alembic databases are
stamped at the initial revision when the current schema is already present.

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic current
```

### Tests

A comprehensive `pytest` suite covers money/increment math, the listing-state
parser, the data models, all four strategies, settings/DB persistence,
security (password encryption), the bidder's shipping-selection / verification /
error-detection helpers, the login state machine, and the FastAPI endpoints.
The **engine tests simulate whole auctions** end-to-end with the browser and
bidder mocked, so they run in well under a second and need no Chromium or
network.

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest            # 155 passed
.venv/bin/python -m pytest -v         # verbose
.venv/bin/python -m pytest tests/test_strategies.py   # one module
```

---

## Important caveats (read before trusting it with real money)

* **Selector tuning may be required on first real login.** The listing/bid
  *data* is read from TradeMe's embedded page state (very stable). But the
  **login/2FA iframe** and the **bid modal** are driven by DOM selectors derived
  from sample HTML. They use several fallback selectors and, on any failure,
  save a screenshot to `data/screenshots/` so you can adjust the selectors in
  `app/trademe/auth.py` / `app/trademe/bidder.py` for your account/page variant.
  **Do a low-stakes test bid on a cheap auction first.**
* Strategies act in the final 2 minutes; TradeMe's anti-snipe auto-extends the
  auction when a late bid lands — the engine re-reads the (moving) close time
  each poll and keeps going.
* "Blocking" deliberately places many auto-bids → many TradeMe emails. That's
  expected.
* Keep the process running continuously; if it's down when an auction closes, no
  bid is placed.
* This is an independent project and is **not affiliated with TradeMe or BidBud**.
```
