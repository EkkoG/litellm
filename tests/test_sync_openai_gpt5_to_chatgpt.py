import json
from pathlib import Path

from scripts.sync_openai_gpt5_to_chatgpt import sync_file, sync_model_costs


def test_sync_model_costs_copies_gpt5_families_and_preserves_chatgpt_fields():
    model_costs = {
        "gpt-5.4": {
            "litellm_provider": "openai",
            "mode": "chat",
            "input_cost_per_token": 2.5e-6,
            "input_cost_per_token_above_272k_tokens": 5e-6,
            "supported_endpoints": ["/v1/chat/completions", "/v1/batch", "/v1/responses"],
        },
        "gpt-5.6-sol": {
            "litellm_provider": "openai",
            "mode": "chat",
            "input_cost_per_token": 5e-6,
            "cache_read_input_token_cost": 5e-7,
        },
        "anthropic/gpt-5.5": {
            "litellm_provider": "anthropic",
            "input_cost_per_token": 99.0,
        },
        "chatgpt/gpt-5.4": {
            "litellm_provider": "chatgpt",
            "mode": "responses",
            "provider_specific_entry": "keep-me",
        },
    }

    synced, changed_keys = sync_model_costs(model_costs)

    assert changed_keys == ("chatgpt/gpt-5.4", "chatgpt/gpt-5.6-sol")
    assert synced["chatgpt/gpt-5.4"] == {
        "litellm_provider": "chatgpt",
        "mode": "responses",
        "input_cost_per_token": 2.5e-6,
        "input_cost_per_token_above_272k_tokens": 5e-6,
        "supported_endpoints": ["/v1/chat/completions", "/v1/responses"],
        "provider_specific_entry": "keep-me",
    }
    assert synced["chatgpt/gpt-5.6-sol"] == {
        "litellm_provider": "chatgpt",
        "mode": "responses",
        "input_cost_per_token": 5e-6,
        "cache_read_input_token_cost": 5e-7,
        "supported_endpoints": ["/v1/chat/completions", "/v1/responses"],
    }
    assert "chatgpt/anthropic/gpt-5.5" not in synced


def test_sync_file_is_idempotent(tmp_path: Path):
    path = tmp_path / "model_prices.json"
    path.write_text(
        json.dumps(
            {
                "gpt-5.5": {
                    "litellm_provider": "openai",
                    "mode": "chat",
                    "input_cost_per_token": 3e-6,
                    "output_cost_per_token": 20e-6,
                }
            }
        ),
        encoding="utf-8",
    )

    assert sync_file(path) == ("chatgpt/gpt-5.5",)
    first_sync = path.read_text(encoding="utf-8")
    assert sync_file(path) == ()
    assert path.read_text(encoding="utf-8") == first_sync
