import litellm
from litellm.llms.openai.responses.transformation import OpenAIResponsesAPIConfig
from litellm.secret_managers.main import get_secret_str
from litellm.types.llms.openai import ResponsesAPIOptionalRequestParams
from litellm.types.router import GenericLiteLLMParams
from litellm.types.utils import LlmProviders


class MoonshotResponsesAPIConfig(OpenAIResponsesAPIConfig):
    @property
    def custom_llm_provider(self) -> LlmProviders:
        return LlmProviders.MOONSHOT

    def map_openai_params(
        self,
        response_api_optional_params: ResponsesAPIOptionalRequestParams,
        model: str,
        drop_params: bool,
    ) -> dict[str, object]:
        return dict(response_api_optional_params)

    def validate_environment(
        self,
        headers: dict[str, str],
        model: str,
        litellm_params: GenericLiteLLMParams | None,
    ) -> dict[str, str]:
        resolved_params = litellm_params or GenericLiteLLMParams()
        api_key = resolved_params.api_key or litellm.api_key or get_secret_str("MOONSHOT_API_KEY")
        if not api_key:
            raise ValueError("Moonshot API key is required. Set MOONSHOT_API_KEY or pass api_key.")
        return {**headers, "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def get_complete_url(
        self,
        api_base: str | None,
        litellm_params: dict[str, object],
    ) -> str:
        resolved_api_base = (
            api_base or litellm.api_base or get_secret_str("MOONSHOT_API_BASE") or "https://api.moonshot.ai/v1"
        ).rstrip("/")
        if resolved_api_base.endswith("/responses"):
            return resolved_api_base
        return f"{resolved_api_base}/responses"

    def supports_native_websocket(self) -> bool:
        return False
