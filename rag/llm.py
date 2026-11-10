"""Step 7 of the pipeline (generation): the local chat model, served by Ollama.

Purpose
    One factory, get_chat_model(), so every caller gets the same settings: the model from .env,
    temperature 0.0, a large context window (num_ctx) and keep_alive so the model stays in VRAM.
    The returned ChatOllama supports .bind_tools(), which Phase 2's agent needs.

Public interface (stable): get_chat_model(temperature=None) -> BaseChatModel

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from config import get_settings


class OllamaUnavailableError(RuntimeError):
    """Ollama is not running or the configured model is not pulled."""


def check_ollama(model: Optional[str] = None, timeout: float = 3.0) -> list[str]:
    """Check that the Ollama server answers and that `model` is pulled. Returns the model list."""
    s = get_settings()
    model = model or s.llm_model
    try:
        with urllib.request.urlopen(f"{s.ollama_base_url}/api/tags", timeout=timeout) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except (urllib.error.URLError, OSError) as exc:
        raise OllamaUnavailableError(
            f"Ollama is not reachable at {s.ollama_base_url}. Start the Ollama app "
            "(or run: ollama serve).") from exc
    if model not in names and f"{model}:latest" not in names:
        raise OllamaUnavailableError(f"Model '{model}' is not pulled. Run: ollama pull {model}")
    return names


def get_chat_model(temperature: Optional[float] = None, model: Optional[str] = None) -> BaseChatModel:
    """Return a ChatOllama configured from .env.

    temperature 0.0 reduces run-to-run variation; it does not make the answer correct.
    num_ctx is set explicitly because Ollama's default context window is small and would
    silently cut off part of the retrieved context.
    """
    from langchain_ollama import ChatOllama

    s = get_settings()
    return ChatOllama(
        model=model or s.llm_model,
        base_url=s.ollama_base_url,
        temperature=s.llm_temperature if temperature is None else temperature,
        num_ctx=s.llm_num_ctx,
        keep_alive=s.llm_keep_alive,
        num_predict=s.llm_num_predict,      # hard cap on generated tokens
        repeat_penalty=s.llm_repeat_penalty,  # discourages loops of the same tokens
    )


def with_limits(llm: Any, **fields: Any) -> Any:
    """Return a copy of a ChatOllama with other generation settings (e.g. num_predict=48).

    ChatOllama.bind(num_predict=...) does not work in langchain-ollama 1.1.0 (the keyword is passed
    to the client and rejected), so a pydantic model_copy with updated fields is used. Objects
    without model_copy (fake LLMs in tests) are returned unchanged.
    """
    copy = getattr(llm, "model_copy", None)
    return copy(update=fields) if callable(copy) else llm
