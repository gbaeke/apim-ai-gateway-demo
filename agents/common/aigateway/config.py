import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).rstrip("/")


@dataclass(frozen=True)
class Settings:
    gateway_url: str = field(default_factory=lambda: _env("APIM_GATEWAY_URL"))
    model: str = field(default_factory=lambda: _env("MODEL_DEPLOYMENT", "gpt-6-luna"))
    agent_id: str = field(default_factory=lambda: _env("AGENT_ID", "agent"))
    # Tokens are requested for this audience; APIM checks the audience and the caller's client id.
    token_scope: str = "https://cognitiveservices.azure.com/.default"

    @property
    def model_base_url(self) -> str:
        return f"{self.gateway_url}/foundry/openai/v1"


settings = Settings()
