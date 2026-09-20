"""The one place that knows which model answers, and where it lives.

Everything used to go to Groq's free tier, and the free tier shaped the app: how
many records a question could carry, how long an answer could be, why counting is
a separate call. Those limits are not a provider's business to impose on a design,
so the provider is now a setting.

OpenRouter by default, because one key reaches every model -- changing model is a
settings edit rather than a code change:

    python -m src.config.settings llm_model deepseek/deepseek-v4-flash
    python -m src.config.settings llm_base_url https://api.groq.com/openai/v1

Pointing it somewhere else is the whole of switching: the key it asks for follows
from the url, so a provider you already have a key for works straight away.

A settings change takes effect on the next start of the service: the client is
built once and kept, which is worth more than picking up an edit mid-run.
"""

from urllib.parse import urlparse

from openai import OpenAI

from src.config.env import llm_api_key
from src.config.settings import get as setting

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# which key belongs to which provider, so pointing llm_base_url somewhere else is
# the whole of switching -- the key it needs follows from the url. Anything not
# listed uses LLM_API_KEY
KEY_NAMES = {
    "openrouter.ai": "OPENROUTER_API_KEY",
    "api.groq.com": "GROQ_API_KEY",
    "api.openai.com": "OPENAI_API_KEY",
}
FALLBACK_KEY_NAME = "LLM_API_KEY"
# the model the prompts were written against. gpt-oss spends its token budget on
# reasoning before answering, which is why max_tokens is set where it is -- a
# different model wants those numbers looked at again
DEFAULT_MODEL = "openai/gpt-oss-120b"

_client: OpenAI | None = None


def base_url() -> str:
    return setting("llm_base_url", DEFAULT_BASE_URL)


def key_name(url: str | None = None) -> str:
    """The credential this provider is asked for."""
    host = urlparse(url or base_url()).hostname or ""
    return KEY_NAMES.get(host, FALLBACK_KEY_NAME)


def model() -> str:
    return setting("llm_model", DEFAULT_MODEL)


def client() -> OpenAI:
    """Built on first use, so a missing key surfaces as an answer, not an import
    error that stops the app from starting."""
    global _client
    if _client is None:
        url = base_url()
        _client = OpenAI(api_key=llm_api_key(key_name(url)), base_url=url)
    return _client


def forget_client():
    """Drop the cached client -- for tests, and after a settings change."""
    global _client
    _client = None
