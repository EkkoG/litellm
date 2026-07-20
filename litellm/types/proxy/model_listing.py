"""Response types for the model listing/retrieve endpoints (/v1/models, /models)."""

from typing import Literal

from typing_extensions import NotRequired, TypedDict


class ModelInfoMetadata(TypedDict):
    fallbacks: list[str]


class ModelInfoResponse(TypedDict):
    """OpenAI-compatible model object. `metadata` is present only when the
    endpoint is called with include_metadata=true.
    """

    id: str
    object: Literal["model"]
    created: int
    owned_by: str
    metadata: NotRequired[ModelInfoMetadata]
    max_input_tokens: NotRequired[int]
    max_output_tokens: NotRequired[int]


class ModelListResponse(TypedDict):
    data: list[ModelInfoResponse]
    object: Literal["list"]


class ClaudeDesktopModelInfo(TypedDict):
    id: str
    type: Literal["model"]
    created_at: str
    supports1m: NotRequired[Literal[True]]


class ClaudeDesktopModelListResponse(TypedDict):
    data: list[ClaudeDesktopModelInfo]
    has_more: Literal[False]
    first_id: str | None
    last_id: str | None
