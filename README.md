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

1. Go to **Account** → enter your TradeMe email + password → **Log in**.
2. When prompted, type the 2FA code TradeMe sends → the session is saved.
3. **Add listing** → paste a listing URL (or number) → **Look up** → set your
   maximum bid + strategy → **Schedule bid**.
4. Watch it on the dashboard. Leave the process running 24/7 (a VPS is ideal so
   bids fire even when your laptop is off).

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
  db.py                # SQLite (settings, jobs, bid_log)
  security.py          # Fernet password encryption
  money.py             # Decimal money + bid-increment + cents logic
  models.py            # ListingState, BidResult, strategy list
  strategies.py        # the four strategies as pure decision functions
  engine.py            # per-job timing loop: poll -> decide -> bid -> verify
  scheduler.py         # APScheduler supervisor (one engine per active job)
  trademe/
    browser.py         # shared Playwright browser/context + storage_state
    auth.py            # login + 2FA state machine (driven from the UI)
    listing.py         # parse listing state from embedded #frend-state JSON
    bidder.py          # drive the bid modal (amount, shipping, autobid, submit)
  templates/  static/  # HTMX dashboard
tests/test_core.py     # pure-logic tests (money, parser, all strategies)
```

Run the logic tests:

```bash
.venv/bin/python -m tests.test_core
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
