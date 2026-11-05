"""One-time OAuth consent for Gmail + Pub/Sub. Writes secrets/token.json.

Run (see docs/gmail-setup.md):
    docker compose run --rm -p 127.0.0.1:8085:8085 api python -m app.daemons.gmail_auth
then open the printed URL in a browser that can reach localhost:8085 on this machine.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

from app.daemons.gmail_client import SCOPES, build_gmail, save_credentials, secrets_path

PORT = 8085


def main() -> None:
    client_file = secrets_path("client_secret.json")
    if not client_file.exists():
        raise SystemExit(f"{client_file} not found: copy the OAuth desktop client JSON there first")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    creds = flow.run_local_server(
        host="localhost",
        bind_addr="0.0.0.0",  # inside the container; the host side is published on 127.0.0.1 only
        port=PORT,
        open_browser=False,
        access_type="offline",
        prompt="consent",
    )
    save_credentials(creds)
    email = build_gmail(creds).users().getProfile(userId="me").execute()["emailAddress"]
    print(f"Saved {secrets_path('token.json')} for {email}")


if __name__ == "__main__":
    main()
