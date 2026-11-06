# How Xpensimate works, and why

## Pieces
- **Backend** (`backend/`): FastAPI + Postgres 16 in Docker Compose. Ingests transactions, categorizes them,
  reconciles receipts, tracks budgets and bills, and serves the web app at `/app/`.
- **Gmail worker** (optional): reads bank alert emails through Gmail watch + Pub/Sub streaming pull, so the
  server needs no inbound port for it.
- **Android app** (`android/`): Jetpack Compose. Everything the web app does, plus capturing purchase
  notifications on the phone and receiving budget alerts by push.
- **Web app** (`backend/app/web/app/`): plain HTML/JS, no build step, for iPhone and laptops.

## Taxonomy: what + who, and budget lines
- **Category ("what")** is the household's own list (Grocery, Eating Out, Gas & Parking, …), chosen in the
  setup wizard and editable in the apps (add / rename / merge).
- **Purpose ("who")** comes from `household.toml`: a shared default plus one entry per person, family member
  or other group whose spending is budgeted separately.
- **Budget lines** come from the pair: shared spending is budgeted by category, everything else by its
  purpose's line (e.g. "Alex Personal"). A purpose can also send transfers to a separate line ("Parents"
  for money sent, "Parents Shopping" for things bought for them). Budgets, alerts and the summary all
  work on budget lines. The apps get the lines and their icons from `GET /api/v1/taxonomy`, so nothing
  about the household is hard-coded in them.

## Categorization
In order: rules (remembered edits > generic starter rules) → the merchant's last confident categorization →
Claude, given the household description, the purpose list, and the household's past choices and notes for
that merchant. Manual choices are never overwritten. Without an Anthropic API key the last step is skipped
and unknown merchants stay Uncategorized until you set them (which then teaches the rules).

## Models
A small model categorizes (`CATEGORIZER_MODEL`, default Claude Haiku) and a stronger one reads receipt photos
(`RECEIPT_MODEL`, default Claude Sonnet). Categorizing is a short classification, so the small model does
it well at a fraction of the cost; low-confidence guesses are flagged for review.

Both go through one function (`app/services/llm.py: ask`) that talks to Claude or, with
`LLM_PROVIDER=openai`, to any OpenAI-compatible server (Ollama, llama.cpp, vLLM, LM Studio). Either way the
answer is constrained by a JSON schema built per request from the live categories and purposes, then
validated; a failure leaves the charge uncategorized rather than blocking ingestion. See
[local-models.md](local-models.md).

## Accounts and card digits
Accounts are listed in `household.toml`. Every alert or notification goes to the account whose
`card_last4` matches the card in the alert; when there are no digits (or two accounts share them), the
bank parser's well-known account id is the fallback. A new card is a config change, not a code change.

## Alerts: Firebase push instead of a WebSocket
An always-open WebSocket needs an Android foreground service (battery, a permanent notification, killed by
the OS, alerts missed while offline). Budget alerts are Firebase Cloud Messaging pushes instead (outbound
only from the server), stored server-side so the apps show history. Alerts fire once per budget line per
month: a warning at the threshold, then exceeded over the limit.

## Ingestion
- **Bank alert emails** (Tangerine, PC Financial, Canadian Tire, RBC): only subjects matching known
  transaction alerts are read; statements, offers and one-time codes are never stored.
- **Phone notifications** (Wealthsimple, Google Wallet): captured raw on the phone and parsed on the server,
  so parsing can improve without an app release. A purchase reported by two live feeds is stored once.
- **Statement import** (CSV for Canadian Tire, PC Financial, Tangerine, Wealthsimple; PDF for RBC) confirms
  what the app has, adds what it missed, records card payments as not an expense, and lists app charges
  missing from the statement.
- **Receipts**: a photo is read by Claude into line items, each with its own category and purpose, and
  matched to the card charge; the summary then counts each line under its own budget line.
- **Past spending** (a CSV from a spreadsheet or another app): every row becomes a categorization example
  and feeds history rules; rows older than the first live charge can be added as spending (source
  `history`), later ones only teach, so nothing is counted twice.
- **Expected expenses**: recurring bills detected from history or added by hand; the summary shows
  spent + still expected = projected.

Bank support is Canadian today. Adding a bank is a parser module plus a test: see
[../CONTRIBUTING.md](../CONTRIBUTING.md).

## Not an expense
Card payments, payment reversals and transfers between your own accounts are kept as transactions (so
re-imports stay deduplicated) with `excluded = true`, and left out of every total.

## People and tokens
Each person has their own API token (`API_TOKEN` + `API_TOKEN_USER`, more in `USER_TOKENS`). Manual edits
record `edited_by` and receipts `created_by`; `GET /api/v1/me` says whose token a request used.

## App choices
- ML Kit Document Scanner instead of a hand-built camera screen (automatic edge detection and crop).
- No offline database in the app: screens load live from the server. Captured notifications are queued
  on the phone until the server is reachable.
- Notes on a charge, a split line or a receipt line help the household remember, and recent notes on a
  merchant go to Claude as context.
