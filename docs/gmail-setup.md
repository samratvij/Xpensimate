# Gmail ingestion setup

The `gmail-worker` service turns Tangerine, PC Financial and CTFS alert emails into transactions.
Gmail publishes a "mailbox changed" event to a Google Cloud Pub/Sub topic. The worker keeps an
**outbound** streaming pull open to that topic, so nothing on the home network is exposed.
Everything here fits in the GCP free tier.

## 1. Google Cloud project

1. At <https://console.cloud.google.com> create a project, e.g. `xpensimate`. Note its **project ID**.
2. **APIs & Services > Library**: enable **Gmail API** and **Cloud Pub/Sub API**.

## 2. Pub/Sub topic and subscription

**Pub/Sub > Topics > Create topic**
- Topic ID: `gmail-bank-alerts`. Untick "Add a default subscription".
- Open the topic > **Permissions > Add principal**: `gmail-api-push@system.gserviceaccount.com`
  with role **Pub/Sub Publisher**. (Gmail can't publish without this.)

**Pub/Sub > Subscriptions > Create subscription**
- Subscription ID: `bank-alerts-pull`, topic `gmail-bank-alerts`, delivery type **Pull**,
  acknowledgement deadline 60 s.

Same thing with `gcloud`, if you prefer:

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable gmail.googleapis.com pubsub.googleapis.com
gcloud pubsub topics create gmail-bank-alerts
gcloud pubsub topics add-iam-policy-binding gmail-bank-alerts \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com --role=roles/pubsub.publisher
gcloud pubsub subscriptions create bank-alerts-pull --topic=gmail-bank-alerts --ack-deadline=60
```

## 3. OAuth consent screen and client

1. **APIs & Services > OAuth consent screen**: user type **External**, fill in the app name and your
   email, add yourself under **Test users**.
2. Add scopes `.../auth/gmail.readonly` and `.../auth/pubsub`.
3. **Publish the app** ("In production"). In "Testing" mode Google expires the refresh token after
   7 days and ingestion silently stops. As a personal, unverified app you'll see a "Google hasn't
   verified this app" warning during login: click *Advanced > Go to … (unsafe)*.
4. **Credentials > Create credentials > OAuth client ID**, type **Desktop app**. Download the JSON.
   It must contain an `"installed"` key (a "web" client won't work).

## 4. Put the files on the server

From the machine with the downloaded JSON:

```bash
scp client_secret_*.json you@your-server:~/xpensimate/backend/secrets/client_secret.json
```

On the server, lock it down and set the project ID:

```bash
chmod 600 ~/xpensimate/backend/secrets/client_secret.json
sed -i 's/^GCP_PROJECT_ID=.*/GCP_PROJECT_ID=YOUR_PROJECT_ID/' ~/xpensimate/backend/.env
```

`backend/secrets/` is gitignored.

## 5. Log in once

On the server:

```bash
cd ~/xpensimate/backend
docker compose run --rm -p 127.0.0.1:8085:8085 api python -m app.daemons.gmail_auth
```

It prints a Google URL. Open it in a browser that can reach `localhost:8085` **on the server**:
either the server's own desktop browser, or from another machine after opening a tunnel first:

```bash
ssh -L 8085:localhost:8085 you@your-server
```

Approve access. The script saves `secrets/token.json` and prints your Gmail address.

## 6. Start the worker

```bash
cd ~/xpensimate/backend
docker compose --profile gmail up -d gmail-worker
docker logs -f xpensimate-gmail-worker
```

On first start it registers the Gmail watch, imports the last day of bank alerts, then listens.
It renews the watch daily (Gmail watches expire after 7 days) and, if it was down for more than
about a week, falls back to searching the last 7 days.

## What gets stored

Only emails from a bank sender **whose subject matches a known transaction-alert template** are read
further (statements, offers, low-balance notices and one-time codes are ignored and never stored):

| Issuer | Subjects treated as alerts | Account |
|---|---|---|
| Tangerine | "A new Credit Card transaction has been made" | `tangerine_mastercard` (`tangerine_cheq` if not a Mastercard) |
| PC Financial | "PC® Mastercard® purchase notice", "PC Money™ Account purchase notice" | `pc_mastercard` / `pc_money` |
| CTFS | subjects containing "purchase" or "transaction" (wording not yet confirmed) | `ctfs_triangle` |

PC Money Account notices sometimes leave the merchant blank; those arrive as "Unknown merchant".

## When an alert isn't recognised

Alerts that match a transaction subject but don't parse are stored in `raw_ingest_log` with
`parse_status = unparsed`, so the parser can be fixed against the real text:

```bash
docker compose exec db psql -U expense -d expense -c \
  "select created_at, payload->>'issuer', payload->>'subject' from raw_ingest_log where payload->>'parse_status' = 'unparsed' order by created_at desc"
```
