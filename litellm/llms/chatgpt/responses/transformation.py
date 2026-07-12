import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Optional, cast

from typing_extensions import TypedDict

from litellm._logging import verbose_logger
from litellm.exceptions import AuthenticationError
from litellm.litellm_core_utils.core_helpers import process_response_headers
from litellm.litellm_core_utils.llm_response_utils.convert_dict_to_response import (
    _safe_convert_created_field,
)
from litellm.llms.openai.common_utils import OpenAIError
from litellm.llms.openai.responses.transformation import OpenAIResponsesAPIConfig
from litellm.responses.sse_output_recovery import (
    parse_sse_json_chunk,
    record_output_item_chunk,
    record_output_text_chunk,
)
from litellm.types.llms.openai import (
    ResponseInputParam,
    ResponsesAPIResponse,
    ResponsesAPIStreamEvents,
)
from litellm.types.router import GenericLiteLLMParams
from litellm.types.utils import LlmProviders

from ..authenticator import Authenticator
from ..common_utils import (
    CHATGPT_API_BASE,
    GetAccessTokenError,
    ensure_chatgpt_session_id,
    get_chatgpt_default_headers,
    get_chatgpt_default_instructions,
)


class ChatGPTProviderRequestDiagnostics(TypedDict):
    body_keys: tuple[str, ...]
    header_keys: tuple[str, ...]
    body_hash: str | None
    model_hash: str | None
    prompt_cache_key_hash: str | None
    previous_response_id_hash: str | None
    session_id_hash: str | None
    thread_id_hash: str | None
    account_id_hash: str | None
    input_count: int
    input_prefix_hashes: tuple[tuple[int, str], ...]
    instructions_hash: str | None
    tools_hash: str | None
    tool_choice_hash: str | None
    reasoning_hash: str | None
    truncation_hash: str | None
    include_hash: str | None


class ChatGPTResponsesAPIConfig(OpenAIResponsesAPIConfig):
    _INPUT_PREFIX_HASH_WINDOW = 16

    def __init__(self) -> None:
        super().__init__()
        self.authenticator = Authenticator()

    @property
    def custom_llm_provider(self) -> LlmProviders:
        return LlmProviders.CHATGPT

    def validate_environment(
        self,
        headers: dict,
        model: str,
        litellm_params: Optional[GenericLiteLLMParams],
    ) -> dict:
        api_key = getattr(litellm_params, "api_key", None)
        try:
            access_token = self.authenticator.get_access_token(api_key=api_key, litellm_params=litellm_params)
        except GetAccessTokenError as e:
            raise AuthenticationError(
                model=model,
                llm_provider="chatgpt",
                message=str(e),
            )

        account_id = self.authenticator.get_account_id(litellm_params=litellm_params, access_token=access_token)
        session_id = ensure_chatgpt_session_id(litellm_params)
        default_headers = get_chatgpt_default_headers(access_token, account_id, session_id)
        return {**default_headers, **headers}

    def transform_responses_api_request(
        self,
        model: str,
        input: str | ResponseInputParam,
        response_api_optional_request_params: dict,
        litellm_params: GenericLiteLLMParams,
        headers: dict,
    ) -> dict:
        request = super().transform_responses_api_request(
            model,
            self._normalize_input_for_chatgpt(input),
            response_api_optional_request_params,
            litellm_params,
            headers,
        )
        self._normalize_system_input_messages(request)
        base_instructions = get_chatgpt_default_instructions()
        existing_instructions = request.get("instructions")
        if existing_instructions:
            if base_instructions not in existing_instructions:
                request["instructions"] = f"{base_instructions}\n\n{existing_instructions}"
        else:
            request["instructions"] = base_instructions
        request["store"] = False
        request["stream"] = True
        include = list(request.get("include") or [])
        if "reasoning.encrypted_content" not in include:
            include.append("reasoning.encrypted_content")
        request["include"] = include

        allowed_keys = {
            "model",
            "input",
            "instructions",
            "stream",
            "store",
            "include",
            "tools",
            "tool_choice",
            "reasoning",
            "previous_response_id",
            "truncation",
            "prompt_cache_key",
        }

        return {k: v for k, v in request.items() if k in allowed_keys}

    def get_provider_request_diagnostics(
        self,
        headers: Mapping[object, object],
        request_data: Mapping[object, object],
    ) -> ChatGPTProviderRequestDiagnostics:
        input_items = request_data.get("input")
        input_sequence = tuple(input_items) if isinstance(input_items, list) else ()
        return ChatGPTProviderRequestDiagnostics(
            body_keys=tuple(sorted(str(key) for key in request_data)),
            header_keys=tuple(sorted(str(key).lower() for key in headers)),
            body_hash=self._hash_value(request_data),
            model_hash=self._hash_value(request_data.get("model")),
            prompt_cache_key_hash=self._hash_value(request_data.get("prompt_cache_key")),
            previous_response_id_hash=self._hash_value(request_data.get("previous_response_id")),
            session_id_hash=self._hash_value(self._get_header(headers, "session_id", "session-id")),
            thread_id_hash=self._hash_value(self._get_header(headers, "thread_id", "thread-id")),
            account_id_hash=self._hash_value(self._get_header(headers, "chatgpt-account-id")),
            input_count=len(input_sequence),
            input_prefix_hashes=self._get_input_prefix_hashes(input_sequence),
            instructions_hash=self._hash_value(request_data.get("instructions")),
            tools_hash=self._hash_value(request_data.get("tools")),
            tool_choice_hash=self._hash_value(request_data.get("tool_choice")),
            reasoning_hash=self._hash_value(request_data.get("reasoning")),
            truncation_hash=self._hash_value(request_data.get("truncation")),
            include_hash=self._hash_value(request_data.get("include")),
        )

    def _get_header(self, headers: Mapping[object, object], *names: str) -> object | None:
        normalized_names = frozenset(name.lower() for name in names)
        return next(
            (value for key, value in headers.items() if isinstance(key, str) and key.lower() in normalized_names),
            None,
        )

    def _hash_value(self, value: object) -> str | None:
        if value is None:
            return None
        serialized = json.dumps(
            self._normalize_hash_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _normalize_hash_value(self, value: object) -> object:
        if isinstance(value, Mapping):
            return (
                "mapping",
                tuple(
                    (str(key), self._normalize_hash_value(item))
                    for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
                ),
            )
        if isinstance(value, (list, tuple)):
            return ("sequence", tuple(self._normalize_hash_value(item) for item in value))
        return value

    def _get_input_prefix_hashes(self, input_items: Sequence[object]) -> tuple[tuple[int, str], ...]:
        if not input_items:
            return ((0, hashlib.sha256().hexdigest()),)
        first_recorded_count = max(1, len(input_items) - self._INPUT_PREFIX_HASH_WINDOW + 1)
        return tuple(
            (count, current_digest)
            for count, current_digest in self._iter_input_prefix_hashes(input_items)
            if count >= first_recorded_count
        )

    def _iter_input_prefix_hashes(self, input_items: Sequence[object]) -> Iterator[tuple[int, str]]:
        digest = hashlib.sha256()
        for count, item in enumerate(input_items, start=1):
            serialized = json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
            digest.update(len(serialized).to_bytes(8, "big"))
            digest.update(serialized)
            yield count, digest.hexdigest()

    def _normalize_input_for_chatgpt(self, input: str | ResponseInputParam) -> ResponseInputParam:
        if isinstance(input, str):
            return cast(
                ResponseInputParam,
                [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": input}],
                    }
                ],
            )
        return input

    def _normalize_system_input_messages(self, request: dict) -> None:
        input_items = request.get("input")
        if not isinstance(input_items, list):
            return

        system_items = tuple(item for item in input_items if self._is_system_input_message(item))
        if not system_items:
            return

        non_system_items = tuple(item for item in input_items if not self._is_system_input_message(item))
        if not non_system_items:
            request["input"] = [self._copy_message_with_role(item, "user") for item in system_items]
            return

        system_instructions = tuple(
            text for item in system_items for text in (self._extract_message_text(item),) if text
        )
        request["input"] = list(non_system_items)
        request["instructions"] = self._join_instructions(system_instructions, request.get("instructions"))

    def _is_system_input_message(self, item: object) -> bool:
        return isinstance(item, dict) and item.get("role") == "system"

    def _copy_message_with_role(self, item: object, role: str) -> object:
        if not isinstance(item, dict):
            return item
        return {**item, "role": role}

    def _extract_message_text(self, item: object) -> Optional[str]:
        if not isinstance(item, dict):
            return None
        return self._extract_content_text(item.get("content"))

    def _extract_content_text(self, content: object) -> Optional[str]:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return None
        parts = tuple(text for block in content for text in (self._extract_content_block_text(block),) if text)
        return "\n".join(parts) if parts else None

    def _extract_content_block_text(self, block: object) -> Optional[str]:
        if isinstance(block, str):
            return block
        if not isinstance(block, dict):
            return None
        text = block.get("text")
        return text if isinstance(text, str) else None

    def _join_instructions(self, system_instructions: tuple[str, ...], existing_instructions: object) -> str:
        existing = (existing_instructions,) if isinstance(existing_instructions, str) and existing_instructions else ()
        return "\n\n".join((*system_instructions, *existing))

    def transform_response_api_response(
        self,
        model: str,
        raw_response: Any,
        logging_obj: Any,
    ):
        body_text = raw_response.text or ""
        if not self._should_parse_as_sse(raw_response=raw_response, body_text=body_text):
            return super().transform_response_api_response(
                model=model,
                raw_response=raw_response,
                logging_obj=logging_obj,
            )

        logging_obj.post_call(
            original_response=raw_response.text,
            additional_args={"complete_input_dict": {}},
        )

        completed_response, error_message = self._extract_completed_response_from_sse(body_text=body_text)
        if completed_response is None:
            raise OpenAIError(
                message=error_message or raw_response.text,
                status_code=raw_response.status_code,
            )

        self._attach_response_headers(completed_response=completed_response, raw_response=raw_response)
        return completed_response

    def _should_parse_as_sse(self, raw_response: Any, body_text: str) -> bool:
        content_type = (raw_response.headers or {}).get("content-type", "")
        if "text/event-stream" in content_type.lower():
            return True
        trimmed_body = body_text.lstrip()
        return bool(
            trimmed_body.startswith("event:")
            or trimmed_body.startswith("data:")
            or "\nevent:" in body_text
            or "\ndata:" in body_text
        )

    def _extract_completed_response_from_sse(
        self, body_text: str
    ) -> tuple[Optional[ResponsesAPIResponse], Optional[str]]:
        completed_response = None
        error_message = None
        streamed_output_items: dict[int, dict] = {}
        text_only_output_items: dict[int, dict] = {}
        event_counts: dict[str, int] = {}
        for chunk in body_text.splitlines():
            parsed_chunk = parse_sse_json_chunk(chunk)
            if parsed_chunk is None:
                continue

            event_type = parsed_chunk.get("type")
            if isinstance(event_type, str):
                event_counts[event_type] = event_counts.get(event_type, 0) + 1

            if event_type == ResponsesAPIStreamEvents.OUTPUT_ITEM_DONE:
                record_output_item_chunk(
                    parsed_chunk=parsed_chunk,
                    output_items=streamed_output_items,
                )
                verbose_logger.debug(
                    "ChatGPT SSE output_item.done output_index=%s item_type=%s item_id=%s",
                    parsed_chunk.get("output_index"),
                    (
                        ((parsed_chunk.get("item") or {}) if isinstance(parsed_chunk.get("item"), dict) else {}).get(
                            "type"
                        )
                    ),
                    (
                        ((parsed_chunk.get("item") or {}) if isinstance(parsed_chunk.get("item"), dict) else {}).get(
                            "id"
                        )
                    ),
                )
                continue

            if event_type == ResponsesAPIStreamEvents.OUTPUT_TEXT_DONE:
                record_output_text_chunk(
                    parsed_chunk=parsed_chunk,
                    output_items=streamed_output_items,
                    text_only_items=text_only_output_items,
                )
                verbose_logger.debug(
                    "ChatGPT SSE output_text.done output_index=%s content_index=%s text_len=%s",
                    parsed_chunk.get("output_index"),
                    parsed_chunk.get("content_index"),
                    len(parsed_chunk.get("text", "")) if isinstance(parsed_chunk.get("text"), str) else None,
                )
                continue

            if event_type == ResponsesAPIStreamEvents.RESPONSE_COMPLETED:
                merged_items: dict[int, dict] = {**text_only_output_items}
                merged_items.update(streamed_output_items)
                completed_response = self._build_completed_response_from_chunk(
                    parsed_chunk=parsed_chunk,
                    streamed_output_items=merged_items,
                )
                response_payload = parsed_chunk.get("response")
                response_output = response_payload.get("output") if isinstance(response_payload, dict) else None
                verbose_logger.debug(
                    "ChatGPT SSE response.completed response_output_len=%s recovered_output_len=%s event_counts=%s incomplete_details=%s",
                    len(response_output) if isinstance(response_output, list) else None,
                    len(merged_items),
                    event_counts,
                    response_payload.get("incomplete_details") if isinstance(response_payload, dict) else None,
                )
                break

            if event_type in (
                ResponsesAPIStreamEvents.RESPONSE_FAILED,
                ResponsesAPIStreamEvents.ERROR,
            ):
                extracted_error = self._extract_error_message(parsed_chunk)
                if extracted_error is not None:
                    error_message = extracted_error
                    verbose_logger.debug(
                        "ChatGPT SSE terminal error event_type=%s message=%s event_counts=%s",
                        event_type,
                        extracted_error,
                        event_counts,
                    )

        if completed_response is None and error_message is None and event_counts:
            verbose_logger.debug(
                "ChatGPT SSE parse ended without completed response event_counts=%s recovered_item_count=%s recovered_text_item_count=%s",
                event_counts,
                len(streamed_output_items),
                len(text_only_output_items),
            )

        return completed_response, error_message

    def _build_completed_response_from_chunk(
        self, parsed_chunk: dict[str, Any], streamed_output_items: dict[int, dict]
    ) -> Optional[ResponsesAPIResponse]:
        response_payload = parsed_chunk.get("response")
        if not isinstance(response_payload, dict):
            return None
        response_payload = dict(response_payload)
        if not response_payload.get("output") and streamed_output_items:
            response_payload["output"] = [item for _, item in sorted(streamed_output_items.items())]
            verbose_logger.debug(
                "ChatGPT SSE filled empty response.completed output from streamed items count=%s item_types=%s",
                len(streamed_output_items),
                [item.get("type") for _, item in sorted(streamed_output_items.items())],
            )
        if "created_at" in response_payload:
            response_payload["created_at"] = _safe_convert_created_field(response_payload["created_at"])
        try:
            return ResponsesAPIResponse(**response_payload)
        except Exception:
            verbose_logger.debug(
                "ChatGPT SSE ResponsesAPIResponse validation failed, using model_construct response_id=%s",
                response_payload.get("id"),
                exc_info=True,
            )
            return ResponsesAPIResponse.model_construct(**response_payload)

    def _extract_error_message(self, parsed_chunk: dict[str, Any]) -> Optional[str]:
        error_obj = parsed_chunk.get("error") or (parsed_chunk.get("response") or {}).get("error")
        if error_obj is None:
            return None
        if isinstance(error_obj, dict):
            return error_obj.get("message") or str(error_obj)
        return str(error_obj)

    def _attach_response_headers(
        self,
        completed_response: ResponsesAPIResponse,
        raw_response: Any,
    ) -> None:
        raw_headers = dict(raw_response.headers)
        processed_headers = process_response_headers(raw_headers)
        if not hasattr(completed_response, "_hidden_params"):
            setattr(completed_response, "_hidden_params", {})
        completed_response._hidden_params["additional_headers"] = processed_headers
        completed_response._hidden_params["headers"] = raw_headers

    def get_complete_url(
        self,
        api_base: Optional[str],
        litellm_params: dict,
    ) -> str:
        api_base = api_base or self.authenticator.get_api_base() or CHATGPT_API_BASE
        api_base = api_base.rstrip("/")
        return f"{api_base}/responses"

    def supports_native_websocket(self) -> bool:
        """ChatGPT does not support native WebSocket for Responses API"""
        return False

    def should_fake_stream(
        self,
        model: Optional[str],
        stream: Optional[bool],
        custom_llm_provider: Optional[str] = None,
    ) -> bool:
        return False
