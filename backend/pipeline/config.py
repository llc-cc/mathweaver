import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


DEFAULT_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


@dataclass
class LLMConfig:
    api_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None


@dataclass
class EmbeddingConfig:
    api_url: str | None = None
    api_key: str | None = None


def normalize_chat_completions_url(api_url):
    if not api_url:
        return api_url
    normalized = str(api_url).strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return normalized + "/chat/completions"
    return normalized


def normalize_embedding_api_url(api_url):
    if not api_url:
        return api_url
    normalized = str(api_url).strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        normalized = normalized[: -len("/chat/completions")]
    return normalized.rstrip("/")


def load_env_file(env_file=None):
    target = env_file or DEFAULT_ENV_FILE
    if target and os.path.exists(target) and load_dotenv is not None:
        load_dotenv(target, override=False)
        return target
    return None


def resolve_llm_config(api_url=None, model_name=None, api_key=None):
    resolved_api_url = api_url or os.getenv("PDFPIPELINE_API_URL") or os.getenv("LLM_API_URL")
    return LLMConfig(
        api_url=normalize_chat_completions_url(resolved_api_url),
        model_name=model_name or os.getenv("PDFPIPELINE_MODEL_NAME") or os.getenv("LLM_MODEL_NAME"),
        api_key=api_key or os.getenv("PDFPIPELINE_API_KEY") or os.getenv("OPENAI_API_KEY"),
    )


def resolve_embedding_config(api_url=None, api_key=None):
    return EmbeddingConfig(
        api_url=normalize_embedding_api_url(
            api_url
            or os.getenv("PDFPIPELINE_EMBEDDING_API_URL")
            or os.getenv("EMBEDDING_API_URL")
            or os.getenv("embedding_api_url")
        ),
        api_key=(
            api_key
            or os.getenv("PDFPIPELINE_EMBEDDING_API_KEY")
            or os.getenv("EMBEDDING_API_KEY")
            or os.getenv("embedding_api_key")
        ),
    )


def resolve_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
