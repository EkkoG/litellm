from pathlib import Path

from scripts.sync_codex_models_to_chatgpt import (
    DEFAULT_PRESERVE_MODELS,
    CodexCatalog,
    CodexModel,
    CodexReasoningLevel,
    _parse_args,
    parse_model_costs,
    sync_file,
    sync_model_costs,
)


def _reasoning_levels(*efforts: str) -> tuple[CodexReasoningLevel, ...]:
    return tuple(CodexReasoningLevel(effort=effort) for effort in efforts)


def _catalog() -> CodexCatalog:
    return CodexCatalog(
        models=(
            CodexModel(
                slug="gpt-5.6-sol",
                supported_in_api=True,
                context_window=372000,
                input_modalities=("text", "image"),
                supported_reasoning_levels=_reasoning_levels("low", "xhigh", "max", "ultra"),
                supports_parallel_tool_calls=True,
                supports_search_tool=True,
            ),
            CodexModel(
                slug="gpt-5.2",
                supported_in_api=True,
                context_window=272000,
                input_modalities=("text",),
                supported_reasoning_levels=_reasoning_levels("low"),
                supports_parallel_tool_calls=False,
                supports_search_tool=False,
            ),
            CodexModel(
                slug="codex-auto-review",
                supported_in_api=True,
                context_window=272000,
                input_modalities=("text", "image"),
                supported_reasoning_levels=_reasoning_levels("medium"),
                supports_parallel_tool_calls=True,
                supports_search_tool=True,
            ),
            CodexModel(
                slug="gpt-disabled",
                supported_in_api=False,
                context_window=128000,
                input_modalities=("text",),
                supported_reasoning_levels=(),
                supports_parallel_tool_calls=False,
                supports_search_tool=False,
            ),
        )
    )


def test_sync_model_costs_uses_catalog_models_and_preserves_image_sync():
    model_costs = parse_model_costs(
        """
        {
            "gpt-5.6-sol": {
                "litellm_provider": "openai",
                "mode": "chat",
                "max_input_tokens": 1050000,
                "input_cost_per_token": 0.000005,
                "supports_low_reasoning_effort": false
            },
            "gpt-5.2": {
                "litellm_provider": "openai",
                "mode": "chat",
                "max_input_tokens": 1050000,
                "input_cost_per_token": 0.00000175,
                "supports_vision": true
            },
            "gpt-disabled": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 99.0
            },
            "gpt-image-2": {
                "litellm_provider": "openai",
                "mode": "image_generation",
                "input_cost_per_image_token": 0.000008
            },
            "chatgpt/gpt-5.6-sol": {
                "litellm_provider": "chatgpt",
                "mode": "responses",
                "provider_specific_entry": {"fast": 1.5}
            }
        }
        """
    )

    result = sync_model_costs(model_costs, _catalog())

    expected = parse_model_costs(
        """
        {
            "sol": {
                "litellm_provider": "chatgpt",
                "mode": "responses",
                "max_input_tokens": 372000,
                "input_cost_per_token": 0.000005,
                "supports_low_reasoning_effort": true,
                "provider_specific_entry": {"fast": 1.5},
                "supported_modalities": ["text", "image"],
                "supports_vision": true,
                "supports_reasoning": true,
                "supports_xhigh_reasoning_effort": true,
                "supports_max_reasoning_effort": true,
                "supports_parallel_function_calling": true,
                "supports_web_search": true,
                "supported_endpoints": ["/v1/chat/completions", "/v1/responses"]
            },
            "gpt52": {
                "litellm_provider": "chatgpt",
                "mode": "responses",
                "max_input_tokens": 272000,
                "input_cost_per_token": 0.00000175,
                "supported_modalities": ["text"],
                "supports_vision": false,
                "supports_reasoning": true,
                "supports_low_reasoning_effort": true,
                "supports_xhigh_reasoning_effort": false,
                "supports_max_reasoning_effort": false,
                "supports_parallel_function_calling": false,
                "supports_web_search": false,
                "supported_endpoints": ["/v1/chat/completions", "/v1/responses"]
            },
            "image": {
                "litellm_provider": "chatgpt",
                "mode": "image_generation",
                "input_cost_per_image_token": 0.000008
            }
        }
        """
    )
    assert result.changed_keys == ("chatgpt/gpt-5.6-sol", "chatgpt/gpt-5.2", "chatgpt/gpt-image-2")
    assert result.removed_keys == ()
    assert result.skipped_models == ("codex-auto-review",)
    assert result.model_costs["chatgpt/gpt-5.6-sol"] == expected["sol"]
    assert result.model_costs["chatgpt/gpt-5.2"] == expected["gpt52"]
    assert result.model_costs["chatgpt/gpt-image-2"] == expected["image"]
    assert tuple(result.model_costs) == (
        *tuple(model_costs),
        "chatgpt/gpt-5.2",
        "chatgpt/gpt-image-2",
    )
    assert "chatgpt/gpt-disabled" not in result.model_costs
    assert "chatgpt/codex-auto-review" not in result.model_costs


def test_replace_removes_chatgpt_models_outside_priced_catalog():
    model_costs = parse_model_costs(
        """
        {
            "gpt-5.6-sol": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 0.000005
            },
            "gpt-5.2": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 0.00000175
            },
            "gpt-image-2": {
                "litellm_provider": "openai",
                "mode": "image_generation",
                "input_cost_per_image_token": 0.000008
            },
            "chatgpt/gpt-5.6-sol": {
                "litellm_provider": "chatgpt",
                "mode": "responses"
            },
            "chatgpt/gpt-image-2": {
                "litellm_provider": "chatgpt",
                "mode": "image_generation"
            },
            "chatgpt/gpt-5.1-codex-max": {
                "litellm_provider": "chatgpt",
                "mode": "responses"
            },
            "chatgpt/codex-auto-review": {
                "litellm_provider": "chatgpt",
                "mode": "responses"
            }
        }
        """
    )

    result = sync_model_costs(model_costs, _catalog(), replace=True)

    assert result.removed_keys == (
        "chatgpt/gpt-image-2",
        "chatgpt/gpt-5.1-codex-max",
        "chatgpt/codex-auto-review",
    )
    assert result.changed_keys == (
        "chatgpt/gpt-5.6-sol",
        "chatgpt/gpt-5.2",
        *result.removed_keys,
    )
    assert tuple(key for key in result.model_costs if key.startswith("chatgpt/")) == (
        "chatgpt/gpt-5.6-sol",
        "chatgpt/gpt-5.2",
    )
    assert result.model_costs["chatgpt/gpt-5.6-sol"]["input_cost_per_token"] == 0.000005
    assert result.model_costs["chatgpt/gpt-5.2"]["input_cost_per_token"] == 0.00000175


def test_preserve_models_are_neither_updated_nor_removed():
    model_costs = parse_model_costs(
        """
        {
            "gpt-5.6-sol": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 0.000005
            },
            "gpt-5.2": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 0.00000175
            },
            "chatgpt/gpt-5.6-sol": {
                "litellm_provider": "chatgpt",
                "mode": "responses",
                "input_cost_per_token": 99.0
            },
            "chatgpt/gpt-image-2": {
                "litellm_provider": "chatgpt",
                "mode": "image_generation",
                "input_cost_per_image_token": 88.0
            },
            "chatgpt/legacy": {
                "litellm_provider": "chatgpt",
                "mode": "responses"
            }
        }
        """
    )

    result = sync_model_costs(
        model_costs,
        _catalog(),
        replace=True,
        preserve_models=("gpt-5.6-sol", "chatgpt/gpt-image-2"),
    )

    assert result.removed_keys == ("chatgpt/legacy",)
    assert result.model_costs["chatgpt/gpt-5.6-sol"]["input_cost_per_token"] == 99.0
    assert result.model_costs["chatgpt/gpt-image-2"]["input_cost_per_image_token"] == 88.0
    assert result.model_costs["chatgpt/gpt-5.2"]["input_cost_per_token"] == 0.00000175


def test_sync_file_is_idempotent(tmp_path: Path):
    path = tmp_path / "model_prices.json"
    path.write_text(
        """
        {
            "gpt-5.6-sol": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 0.000005
            }
        }
        """,
        encoding="utf-8",
    )

    first_result = sync_file(path, _catalog())
    first_sync = path.read_text(encoding="utf-8")
    second_result = sync_file(path, _catalog())

    assert first_result.changed_keys == ("chatgpt/gpt-5.6-sol",)
    assert first_result.removed_keys == ()
    assert first_result.skipped_models == ("gpt-5.2", "codex-auto-review")
    assert second_result.changed_keys == ()
    assert second_result.removed_keys == ()
    assert path.read_text(encoding="utf-8") == first_sync


def test_default_preserve_list_keeps_gpt_image_2():
    assert "gpt-image-2" in DEFAULT_PRESERVE_MODELS
    assert "gpt-image-2-2026-04-21" in DEFAULT_PRESERVE_MODELS


def test_parse_args_supports_no_default_preserve():
    args = _parse_args(["--replace", "--no-default-preserve", "--preserve-model", "gpt-5.4"])
    assert args.no_default_preserve is True
    assert args.preserve_models == ("gpt-5.4",)

    default_args = _parse_args(["--replace"])
    assert default_args.no_default_preserve is False
    assert default_args.preserve_models is None


def test_replace_with_default_preserve_keeps_gpt_image_2_entries():
    model_costs = parse_model_costs(
        """
        {
            "gpt-5.6-sol": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 0.000005
            },
            "chatgpt/gpt-image-2": {
                "litellm_provider": "chatgpt",
                "mode": "image_generation",
                "input_cost_per_image_token": 0.000008
            },
            "chatgpt/gpt-image-2-2026-04-21": {
                "litellm_provider": "chatgpt",
                "mode": "image_generation",
                "input_cost_per_image_token": 0.000008
            },
            "chatgpt/gpt-5.1-codex-max": {
                "litellm_provider": "chatgpt",
                "mode": "responses"
            }
        }
        """
    )

    result = sync_model_costs(
        model_costs,
        _catalog(),
        replace=True,
        preserve_models=DEFAULT_PRESERVE_MODELS,
    )

    assert result.removed_keys == ("chatgpt/gpt-5.1-codex-max",)
    assert result.model_costs["chatgpt/gpt-image-2"]["mode"] == "image_generation"
    assert result.model_costs["chatgpt/gpt-image-2-2026-04-21"]["mode"] == "image_generation"
