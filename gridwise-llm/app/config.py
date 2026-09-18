import os

from dotenv import load_dotenv

load_dotenv()


def _infer_provider() -> str:
    explicit = os.getenv("LLM_PROVIDER")
    if explicit:
        return explicit.strip().lower()
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return "anthropic"


LLM_PROVIDER = _infer_provider()  # "anthropic" | "gemini"

LLM_API_KEY = (
    os.getenv("LLM_API_KEY")
    or os.getenv("GEMINI_API_KEY")
    or os.getenv("ANTHROPIC_API_KEY")
)

_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "gemini": "gemini-flash-lite-latest",
}
LLM_MODEL = os.getenv("LLM_MODEL", _DEFAULT_MODELS.get(LLM_PROVIDER, _DEFAULT_MODELS["anthropic"]))

LLM_BASE_URL = os.getenv("LLM_BASE_URL") or None
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "12"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))

PORT = int(os.getenv("PORT", "8000"))

REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "28"))

NUMERIC_TOLERANCE = 0.01
