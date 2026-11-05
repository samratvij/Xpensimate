from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://expense:expense@db:5432/expense"
    # The original token (the Android app's) and whose it is.
    api_token: str
    api_token_user: str = "Me"
    # More people, each with their own token: "Sam:<token>,Someone:<token>".
    user_tokens: str = ""
    timezone: str = "America/Toronto"
    anthropic_api_key: str = ""
    # Categorizing is a short classification, so a small model does it; reading receipt photos needs more.
    categorizer_model: str = "claude-haiku-5-5"
    receipt_model: str = "claude-sonnet-5-5"

    receipts_dir: str = "/app/data/receipts"
    # Firebase service account (docs/firebase-setup.md); push is off when the file is missing.
    fcm_credentials: str = "/app/secrets/fcm-service-account.json"

    # Gmail ingestion (see docs/gmail-setup.md)
    gcp_project_id: str = ""
    pubsub_topic: str = "gmail-bank-alerts"
    pubsub_subscription: str = "bank-alerts-pull"
    gmail_secrets_dir: str = "/app/secrets"

    def tokens(self) -> dict[str, str]:
        """Person -> token. Tokens must be long random strings (openssl rand -hex 32)."""
        tokens = {self.api_token_user: self.api_token}
        for entry in filter(None, (e.strip() for e in self.user_tokens.split(","))):
            user, _, token = entry.partition(":")
            if not user.strip() or len(token.strip()) < 16:
                raise ValueError(f"USER_TOKENS entry for {user!r} needs a name and a token of 16+ characters")
            tokens[user.strip()] = token.strip()
        if len(set(tokens.values())) != len(tokens):
            raise ValueError("Each person needs a different token")
        return tokens


@lru_cache
def get_settings() -> Settings:
    return Settings()
