"""LangChain provider adapters — build a BaseChatModel from a ModelEntry."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from zerodaemon.models.schemas import ModelEntry


def build_llm(model: ModelEntry, settings) -> BaseChatModel:
    """
    Construct the appropriate LangChain chat model for the given ModelEntry.

    Parameters
    ----------
    model:    ModelEntry from the registry
    settings: zerodaemon.core.config.Settings instance
    """
    if model.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        kwargs = dict(
            model=model.id,
            max_tokens=model.max_tokens,
        )
        if settings.anthropic_api_key:
            kwargs["api_key"] = settings.anthropic_api_key
        return ChatAnthropic(**kwargs)

    if model.provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=model.id,
            base_url=settings.ollama_base_url,
        )

    if model.provider == "openai":
        from langchain_openai import ChatOpenAI
        kwargs = dict(
            model=model.id,
            max_tokens=model.max_tokens,
        )
        if settings.openai_api_key:
            kwargs["api_key"] = settings.openai_api_key
        return ChatOpenAI(**kwargs)

    if model.provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        kwargs = dict(model=model.id)
        if settings.google_api_key:
            kwargs["google_api_key"] = settings.google_api_key
        return ChatGoogleGenerativeAI(**kwargs)

    raise ValueError(f"Unknown provider '{model.provider}' for model '{model.id}'")
