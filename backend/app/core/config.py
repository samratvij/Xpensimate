from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


# Provider -> (categorizer, receipt reader, bank screenshot reader). A local receipt reader must understand images; one vision model
# can do both jobs, so a home server only has to hold one.
DEFAULT_MODELS = {
    "anthropic": ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"),
    "openai": ("qwen2.5vl:7b", "qwen2.5vl:7b", "qwen2.5vl:7b"),
}
# Provider -> (chat assistant, its fallback when the first is overloaded). The assistant calls tools, which the
# vision model above doesn't do, so a local server needs a second model for it.
DEFAULT_CHAT_MODELS = {
    "anthropic": ("claude-sonnet-5-5", "claude-haiku-5-5"),
    "openai": ("qwen3:8b", ""),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://expense:expense@db:5432/expense"
    # The original token (the Android app's) and whose it is.
    api_token: str
    api_token_user: str = "Me"
    # More people, each with their own token: "Sam:<token>,Someone:<token>".
    user_tokens: str = ""
    timezone: str = "America/Toronto"
    # Who answers the AI questions (sorting a new merchant, reading a receipt photo): "anthropic" for Claude,
    # or "openai" for any server with an OpenAI-compatible API, such as Ollama, llama.cpp, vLLM or LM Studio,
    # so a model can run on your own server.
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_api_key: str = ""
    llm_base_url: str = "http://ollama:11434/v1"
    llm_api_key: str = ""
    # Seconds to wait for one answer from an OpenAI-compatible server (a model on a CPU can be slow).
    llm_timeout: float = 180
    # Categorizing is a short classification, so a small model does it; reading receipt photos needs more.
    # Empty means the provider's default (DEFAULT_MODELS).
    categorizer_model: str = ""
    receipt_model: str = ""
    # Reads screenshots of a bank app's transaction list: clean, printed text, so a small model does it.
    screenshot_model: str = ""

    def ai_enabled(self) -> bool:
        """Claude needs a key; an OpenAI-compatible server only an address."""
        return bool(self.anthropic_api_key if self.llm_provider == "anthropic" else self.llm_base_url)

    def categorizer(self) -> str:
        return self.categorizer_model or DEFAULT_MODELS[self.llm_provider][0]

    def receipt_reader(self) -> str:
        return self.receipt_model or DEFAULT_MODELS[self.llm_provider][1]

    def screenshot_reader(self) -> str:
        return self.screenshot_model or DEFAULT_MODELS[self.llm_provider][2]

    # The chat assistant (the Ask tab). Empty means the provider's default (DEFAULT_CHAT_MODELS); a fallback model
    # takes over the rest of an answer when the first is overloaded or unreachable ("none" turns it off).
    chat_model: str = ""
    chat_fallback_model: str = ""
    chat_effort: str = "medium"
    # Questions each person can ask per calendar month (local time); 0 turns the limit off.
    chat_monthly_limit: int = 100
    # Web searches Claude may run while answering one question (Claude only; 0 turns web search off).
    chat_web_searches: int = 3
    # Where the household is, so web searches find local prices and rules: "City, Region, CC" (a two-letter
    # country code), or just "CC". Empty: no location beyond the time zone.
    chat_location: str = ""

    def chat_models(self) -> list[str]:
        """The chat model, then its fallback if there is one."""
        default_model, default_fallback = DEFAULT_CHAT_MODELS[self.llm_provider]
        model = self.chat_model or default_model
        fallback = self.chat_fallback_model or default_fallback
        return [model] + ([fallback] if fallback and fallback != "none" and fallback != model else [])

    receipts_dir: str = "/app/data/receipts"
    # Firebase service account (docs/firebase-setup.md); push is off when the file is missing.
    fcm_credentials: str = "/app/secrets/fcm-service-account.json"

    # Gmail ingestion (see docs/gmail-setup.md)
    gcp_project_id: str = ""
    pubsub_topic: str = "gmail-bank-alerts"
    pubsub_subscription: str = "bank-alerts-pull"
    gmail_secrets_dir: str = "/app/secrets"
    # Whose Gmail it is (one of the household's people): a bank email that doesn't say who acted ("You made a
    # transfer") is theirs, which picks between accounts that differ only by owner. Empty: unknown.
    gmail_owner: str = ""

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
