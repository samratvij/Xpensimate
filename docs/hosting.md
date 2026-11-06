# Hosting

Xpensimate is one Docker Compose project: Postgres, the API (which also serves the web app) and an optional
Gmail worker. It needs very little: 1 GB of RAM is plenty. Pick where it runs:

| | Home server + Tailscale | VPS |
|---|---|---|
| Reachable from | your devices on your tailnet only | the internet |
| HTTPS | `tailscale serve` (for the web app) | Caddy, automatic Let's Encrypt |
| Cost | a machine you already have | a few dollars a month |
| Exposure | nothing public | the API is public (token-protected) |

Your financial data is the point of the app, so the home server (or a VPS that also uses Tailscale, see
the end) is the safer default.

Both start the same way:

```bash
git clone https://github.com/samratvij/Xpensimate.git && cd Xpensimate/backend
cp .env.example .env                       # then fill it in (below)
```

In `.env`, generate secrets with `openssl rand -hex 32` for `POSTGRES_PASSWORD` and `API_TOKEN`, and set
`API_TOKEN_USER` to your name. Each other person gets their own token in `USER_TOKENS` (`Name:token,...`),
so edits show who made them. `ANTHROPIC_API_KEY` is optional but recommended: without it (or a local
model, [local-models.md](local-models.md)) there is no automatic categorization fallback or receipt reading.

Your household (who spending is for, and your cards) is set up in the app: the first time you sign in to
the web app, a short setup wizard asks for it, and More → Household changes it later. It's saved in
`backend/household.toml`, which you can also write by hand from `household.example.toml` if you prefer.

## Option A: home server with Tailscale

1. Install [Tailscale](https://tailscale.com/download) on the server and on your phone, signed in to the
   same tailnet. Enable MagicDNS in the Tailscale admin console.
2. In `.env`, set `API_BIND_HOST` to the server's Tailscale IP (`tailscale ip -4`). The API then listens
   only on the tailnet, never on your home network or the internet.
3. Start it:
   ```bash
   docker compose up -d --build
   ```
4. Android app: server address `http://<server>.<tailnet>.ts.net:8000` and your `API_TOKEN`.
   (Plain HTTP is allowed only for `*.ts.net` names: Tailscale already encrypts the traffic.)
5. Web app (iPhone, laptop): browsers want HTTPS, so let Tailscale provide it:
   ```bash
   sudo tailscale serve --bg --https=443 http://<tailscale-ip>:8000
   ```
   Then open `https://<server>.<tailnet>.ts.net/app/` and use Share → Add to Home Screen. If port 443 is
   already used by something else on the server, pick another (`--https=8443`) and add it to the URL.

## Option B: VPS

1. A small VPS (1 vCPU, 1 GB RAM) with Docker and Docker Compose 2.24+, and a domain or subdomain whose
   DNS A/AAAA record points at it.
2. Open ports 80 and 443 in the VPS firewall; keep everything else closed (SSH aside).
3. In `.env`, leave `API_BIND_HOST=127.0.0.1` and set `DOMAIN=expenses.example.com`.
4. Start it with the VPS overlay, which puts Caddy in front and stops publishing the API port:
   ```bash
   docker compose -f docker-compose.yml -f compose.vps.yml up -d --build
   ```
   Caddy fetches the certificate on first start (watch `docker logs xpensimate-caddy`).
5. Android app: server address `https://expenses.example.com`. Web app: `https://expenses.example.com/app/`.

On a public server the bearer token is the only lock, so:
- use long random tokens (`openssl rand -hex 32`), one per person, and never reuse them;
- keep the OS and images updated (`docker compose pull && docker compose up -d`);
- consider skipping the public setup and running Option A on the VPS instead: install Tailscale there,
  keep ports 80/443 closed, and nothing is reachable from the internet at all.

## After either option

- **Bank alerts by email** (optional): [gmail-setup.md](gmail-setup.md), then
  `docker compose --profile gmail up -d`.
- **Budget alert push notifications** (optional, Android): [firebase-setup.md](firebase-setup.md).
- **Backups**: `scripts/backup.sh` dumps the database and receipt photos into `backups/`, keeping 30 days.
  Run it nightly from cron:
  ```bash
  (crontab -l 2>/dev/null; echo "30 3 * * * $PWD/scripts/backup.sh >> $PWD/backups/backup.log 2>&1") | crontab -
  ```
  Those backups sit on the same disk as the data, so copy them somewhere else too (another machine,
  or encrypted cloud storage with a tool like restic). `scripts/restore.sh backups/db-....dump --yes`
  restores one.
- **Updating**: `git pull && docker compose up -d --build` (add `-f compose.vps.yml` on a VPS).
  Database migrations run automatically on start.
