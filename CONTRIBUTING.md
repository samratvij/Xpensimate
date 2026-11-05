# Contributing

Thanks for helping. The most useful contribution is support for another bank, so most of this is about
that. For anything bigger, open an issue first to talk it through.

## Running the tests

Backend tests run in Docker against a separate `expense_test` database:

```bash
cd backend
cp .env.example .env    # any values will do for tests; set POSTGRES_PASSWORD
docker compose up -d db
docker compose run --rm api pytest -q
```

To click around with realistic data, start the stack with `household.example.toml` copied to
`household.toml`, then `docker compose exec api python -m app.scripts.seed_demo`. Debug builds of the
Android app can reach a server on your computer over plain HTTP (`http://10.0.2.2:8000` from the emulator).

Tests use the fictional household in `backend/tests/household.toml`. The Android app builds with
`cd android && ./gradlew assembleDebug` (JDK 17). CI runs both on every push.

## Adding a bank: alert emails

Each bank is a module in `backend/app/daemons/parsers/` exposing:

- `NAME`: the bank's name.
- `SENDER_DOMAINS`: domains its alert emails come from (subdomains match too).
- `is_transaction(subject) -> bool`: true only for purchase/refund alert subjects. Everything else from
  the bank (statements, offers, one-time codes) must return false so it's never stored.
- `parse(subject, body) -> ParsedAlert | None`: amount (negative for refunds), merchant, the fallback
  account id (or `""`), and the card's last four digits when the alert shows them.

Register the module in `ISSUERS` in `parsers/__init__.py`. If the bank has a typical account, give it a
well-known id (like `tangerine_mastercard`) and list it in `household.example.toml`; the card digits decide
the account whenever they're present.

Add the alert text to `backend/tests/fixtures/alerts.py` and tests to `backend/tests/test_parsers.py`.
**Redact real alerts before committing**: your name, card numbers, account numbers, and anything that
identifies you or where you live. Keep the wording and layout exactly; that's what the parser needs.

## Adding a bank: statement exports

CSV statements are parsed in `backend/app/statements/parsers.py`: add a parser function and an entry in
`_FORMATS` with the set of column names that identifies the bank's export. Add a sample to
`backend/tests/fixtures/statements.py` (made-up rows in the real layout) and tests to `test_statements.py`.

## Adding a phone notification source

`backend/app/daemons/parsers/wealthsimple.py` and `google_wallet.py` parse notifications the Android app
forwards. The app only forwards packages listed in `CAPTURED_PACKAGES` in
`android/app/src/main/java/com/xpensimate/app/WealthsimpleListener.kt` and the server only accepts those in `PARSERS` in
`backend/app/api/v1/notifications.py`; a new source needs both.

## Style

- Keep changes focused, with tests for new behavior.
- No personal data in code, tests or docs: use the fictional household (Alex and Sam) and fake digits.
