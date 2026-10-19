from langchain_openai import ChatOpenAI

from aigateway import activity
from aigateway.auth import token_provider
from aigateway.config import settings


def chat_model(**kwargs) -> ChatOpenAI:
    """Chat model served by Foundry, reached only through APIM.

    The managed identity token goes in ``Authorization``; APIM validates it, applies the caller's
    token budget and swaps it for its own identity towards Foundry.
    """
    return ChatOpenAI(
        model=settings.model,
        base_url=settings.model_base_url,
        api_key=token_provider(),
        http_async_client=activity.async_client(),
        # Usage on streamed responses feeds APIM's token metrics and limits.
        stream_usage=True,
        max_retries=2,
        **kwargs,
    )
