"""``load_backend()`` factory — dispatches a :class:`BackendConfig` to the
right concrete :class:`ChatBackend`.

Anthropic / OpenAI / DeepSeek / Groq / Together / **NVIDIA** (Spec 20) /
**OpenRouter** (Spec 22) → :class:`OpenAICompatibleBackend`.
Ollama → :class:`OllamaBackend`.
Local HuggingFace → :class:`HFLocalBackend` (lazy import; raises a clear
:class:`AuthenticationError` if ``[local]`` extras are missing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.backends.errors import ProviderError
from persona.backends.ollama import OllamaBackend
from persona.backends.openai_compat import OpenAICompatibleBackend

if TYPE_CHECKING:
    from persona.backends.config import BackendConfig
    from persona.backends.protocol import ChatBackend

__all__ = ["load_backend"]


# D-20-X-nvidia-allow-set-extend: NVIDIA dispatches through the openai SDK
# (with custom base_url at integrate.api.nvidia.com/v1/) — same path as
# deepseek/groq/together. The atomic-four-touch invariant per Spec 20 T09
# (Provider Literal + DEFAULT_BASE_URLS + capability matrices + this
# allow-set) is in fact a FIVE-touch including this factory's allow-set
# (omission would surface at app startup as ProviderError "unknown provider
# 'nvidia'"). Production startup at 2026-06-10 19:11 UTC caught this gap
# before commit. Spec 22 T06/T15: ``openrouter`` joins the same path (the
# T15 cross-spec integration test caught the identical omission here).
_OPENAI_COMPAT_PROVIDERS = frozenset(
    {"anthropic", "openai", "deepseek", "groq", "together", "nvidia", "openrouter", "cloudflare"}
)


def load_backend(config: BackendConfig) -> ChatBackend:
    """Construct the concrete :class:`ChatBackend` for ``config.provider``.

    Args:
        config: Backend configuration. Provider must be one of the eight
            supported values; otherwise :class:`ProviderError` is raised.

    Returns:
        A concrete backend implementing the :class:`ChatBackend` Protocol.

    Raises:
        AuthenticationError: API key missing (API backends) or ``[local]``
            extras missing (HF local).
        ProviderError: Unknown / unsupported provider.
        ModelNotFoundError: ``provider="local"`` with no ``local_model_id``.
    """
    provider = config.provider
    if provider in _OPENAI_COMPAT_PROVIDERS:
        return OpenAICompatibleBackend(config)
    if provider == "ollama":
        return OllamaBackend(config)
    if provider == "local":
        # Lazy import keeps ``persona.backends`` importable without the
        # ``[local]`` extras; HFLocalBackend itself raises a clear
        # AuthenticationError if torch/transformers aren't installed.
        from persona.backends.hf_local import HFLocalBackend

        return HFLocalBackend(config)
    raise ProviderError(
        f"unknown provider {provider!r}; expected one of "
        "anthropic, openai, deepseek, groq, together, nvidia, openrouter, cloudflare, "
        "ollama, local",
        context={"provider": provider},
    )
