from groq import AsyncGroq

from app.core.config import settings


def get_groq_client() -> AsyncGroq:
    """Create and return an AsyncGroq client using application settings.

    Raises:
        ValueError: If GROQ_API_KEY is not configured in environment variables.
    """
    if not settings.has_groq_api_key or not settings.GROQ_API_KEY:
        raise ValueError(
            "GROQ_API_KEY is not set. Please add your Groq API key to your .env file."
        )
    base_url = settings.GROQ_BASE_URL.rstrip("/")
    if base_url.endswith("/openai/v1"):
        base_url = base_url[:-len("/openai/v1")]

    return AsyncGroq(
        api_key=settings.GROQ_API_KEY.get_secret_value(),
        base_url=base_url,
        timeout=settings.GROQ_TIMEOUT_SECONDS,
    )

