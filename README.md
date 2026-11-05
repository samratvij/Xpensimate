# Xpensimate

A self-hosted expense tracker for a household. Bank alerts, phone notifications, receipts and statements
come in; spending is categorized by what it was and who it was for; budgets, bills and alerts go out.
Your data stays on a server you control: a spare machine at home or a small VPS.

- **Automatic capture**: bank alert emails (Gmail), Wealthsimple and Google Wallet purchase notifications
  (Android), and month-end statement imports that catch anything missed.
- **Categories and people**: every charge gets a category (Grocery, Eating Out, …) and a purpose (who it
  was for). Shared spending is budgeted by category, personal spending per person.
- **Receipts**: snap a photo, Claude reads the line items, and the receipt is matched to the card charge,
  so a Costco run can count as groceries, household and one person's shopping at once.
- **Budgets and bills**: monthly budgets per line with push alerts, recurring bills tracked as paid,
  upcoming or overdue, and a projected month-end total.
- **Apps**: a native Android app and a web app (works well on iPhone from the home screen).

Bank support today is Canadian (Tangerine, PC Financial, Canadian Tire, RBC, Wealthsimple). Adding a bank
is one parser module: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Get started

1. **Run the server**: [docs/hosting.md](docs/hosting.md) (home server with Tailscale, or a VPS).
2. **Describe your household** in `backend/household.toml` (copied from `household.example.toml`): who
   spending is for, and your cards.
3. **Install the Android app** (build it from `android/`, below) or open the web app at `/app/`.
4. Optional: [bank alert emails](docs/gmail-setup.md) and [push notifications](docs/firebase-setup.md).

How it works and why: [docs/architecture.md](docs/architecture.md).

## Android app (`android/`)

Open `android/` in Android Studio, or build from the command line (JDK 17):

```bash
cd android && ./gradlew assembleDebug   # app/build/outputs/apk/debug/app-debug.apk
```

On first launch, enter your server address and your API token. Purchase notification capture needs
"notification access" for Xpensimate (Settings in the app links to it). Push alerts need
`app/google-services.json` from your own Firebase project ([docs/firebase-setup.md](docs/firebase-setup.md));
without it the app works, minus push.

## Backend (`backend/`)

Its own Docker Compose project (`xpensimate`), isolated from anything else on the host: its own Postgres 16
container, network (`xpensimate`) and volume (`xpensimate_pgdata`).

- API: every `/api/v1/*` call needs `Authorization: Bearer <token>`; `/health` is open. Interactive docs at
  `/docs`.
- Postgres: `127.0.0.1:5434` for local `psql` debugging.
- Migrations, categories and your accounts (from `household.toml`) are set up on container start.

### Categorization

Two tiers (`app/core/taxonomy.py`): **category** is what the money was spent on (Grocery, Eating Out,
Gas & Parking, …) and **purpose** is who it was for, as listed in `household.toml` (e.g. Shared, Alex, Sam,
Parents). Together they give the **budget line** the household budgets against: shared spending by
category, everyone else by their own line (e.g. "Alex Personal"). `GET /api/v1/summary` groups by budget
line by default.

Each incoming transaction gets a category and a purpose from, in order:

1. **Rules** (`/api/v1/rules`): whole-word merchant patterns, optionally limited to an account, weekdays or
   local hours. Priority: rules saved with `"remember": true` (10) > generic starter rules for Canadian
   merchants (1).
2. **History**: the last confident (or manually set) categorization of the same normalized merchant.
3. **Claude** (`CATEGORIZER_MODEL`, default `claude-haiku-5-5`, low effort, structured output), given the
   household's past choices and notes for that merchant: only when `ANTHROPIC_API_KEY` is set in `.env`. Failures leave the
   transaction `Uncategorized`.

Manual changes are never overwritten. `POST /api/v1/transactions/recategorize` re-runs the pipeline on
uncategorized rows (e.g. after adding a key or rules).

### Gmail bank alerts

The optional `gmail-worker` service ingests Tangerine, PC Financial, Canadian Tire and RBC alert emails via Gmail
watch + Pub/Sub streaming pull (outbound only). Setup: [docs/gmail-setup.md](docs/gmail-setup.md).
Email-sourced transactions arrive as `PENDING_MATCH` for receipt reconciliation.

### Receipts

`POST /api/v1/receipts/ocr` takes a receipt photo (multipart field `file`, JPEG/PNG/WebP up to 15 MB).
The image is rotated per EXIF, capped at 2000 px and stored as JPEG under `backend/data/receipts/`; Claude
reads it (`RECEIPT_MODEL`, default `claude-sonnet-5-5`) into merchant, date/time, tax, total and line items, each with a category and purpose.
Receipts whose lines + tax don't add up to the total (±$0.05), or with no readable date, come back with
`needs_review: true`. Needs `ANTHROPIC_API_KEY`; without it the endpoint returns 503.

Receipts are matched to card transactions automatically, whichever arrives first: within 72 hours and
$0.05, or, for restaurant receipts, a card amount up to 30% higher (tip) with a similar merchant name.
Ties are left alone; the app resolves them with `GET /receipts/{id}/candidates` and
`POST /receipts/{id}/match`. A match copies the lines into the transaction's items, spreading tax and
tip so they sum to the card amount, and the summary then counts each line under its own budget line.
`POST /receipts/{id}/unmatch` (or deleting the receipt) undoes it.

### Budget alerts

Budgets are set per budget line (`PUT /api/v1/budgets/{line}`). After any change in spending (new
transaction, edit, receipt match, budget change) the affected lines are re-totalled for the current month:
at the warning threshold (80% by default) a WARNING alert fires, over the limit an EXCEEDED alert, each at
most once per line per month. Alerts are stored (`GET /api/v1/alerts`) and pushed to registered phones via
Firebase Cloud Messaging: setup in [docs/firebase-setup.md](docs/firebase-setup.md).

### Expected expenses

Recurring bills and memberships (`/api/v1/recurring`) have a cadence (weekly, every two weeks, monthly),
a usual amount and a category/who. `POST /recurring/detect` finds steady series in past
transactions; items can also be added or edited by hand. A card charge whose merchant matches an
item's pattern and whose amount is within its tolerance is linked as a payment (and takes the item's
category and who); payments made outside the cards, like rent by e-transfer, are recorded with
`POST /recurring/{id}/paid`. `GET /recurring/expected?month=` lists each due date as paid, upcoming or
overdue, and the summary adds `expected` and `projected` (spent + expected) per budget line, with
`projected_over_limit` against the budget.

### Statement import (month-end)

Upload a credit card statement CSV (Canadian Tire, PC Financial, Tangerine or Wealthsimple; the format is
detected from its header) at `<server>/import` from a laptop, or from the app
(Transactions → upload icon). The preview compares every row with what the app already has for that
account (same amount, within 3 days): known charges are confirmed, missing ones added and categorized
(rules, history and Claude, with the bank's own category as a hint), card payments recorded as not an expense along with the bank-side debit that paid them, and ties
left for you to decide. It also lists app transactions the statement doesn't have. Nothing changes until
you import; the import runs in the background, and importing the same file again adds nothing.

### Backups

See [docs/hosting.md](docs/hosting.md#after-either-option).

## Development

Tests (against the separate `expense_test` database):

```bash
docker compose run --rm api pytest -q
```

New migration after changing `app/db/models.py`:

```bash
docker compose run --rm api alembic revision --autogenerate -m "describe change"
```
