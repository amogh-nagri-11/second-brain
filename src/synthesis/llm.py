"""The one place that knows which model answers, and where it lives.

Everything used to go to Groq's free tier, and the free tier shaped the app: how
many records a question could carry, how long an answer could be, why counting is
a separate call. Those limits are not a provider's business to impose on a design,
so the provider is now a setting.

OpenRouter by default, because one key reaches every model -- changing model is a
settings edit rather than a code change:

    python -m src.config.settings llm_model deepseek/deepseek-v4-flash
    python -m src.config.settings llm_base_url https://api.groq.com/openai/v1

A settings change takes effect on the next start of the service: the client is
built once and kept, which is worth more than picking up an edit mid-run.
"""

from openai import OpenAI

from src.config.env import llm_api_key
from src.config.settings import get as setting

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# the model the prompts were written against. gpt-oss spends its token budget on
# reasoning before answering, which is why max_tokens is set where it is -- a
# different model wants those numbers looked at again
DEFAULT_MODEL = "openai/gpt-oss-120b"

_client: OpenAI | None = None


def base_url() -> str:
    return setting("llm_base_url", DEFAULT_BASE_URL)


def model() -> str:
    return setting("llm_model", DEFAULT_MODEL)


def client() -> OpenAI:
    """Built on first use, so a missing key surfaces as an answer, not an import
    error that stops the app from starting."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=llm_api_key(), base_url=base_url())
    return _client


def forget_client():
    """Drop the cached client -- for tests, and after a settings change."""
    global _client
    _client = None
