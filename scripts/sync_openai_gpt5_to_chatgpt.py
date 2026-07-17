#!/usr/bin/env python3

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Optional, Sequence, Union, cast

JsonScalar = Union[None, bool, int, float, str]
JsonValue = Union[JsonScalar, list["JsonValue"], dict[str, "JsonValue"]]
ModelEntry = dict[str, JsonValue]
ModelCostMap = dict[str, JsonValue]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = (
    REPO_ROOT / "model_prices_and_context_window.json",
    REPO_ROOT / "litellm/model_prices_and_context_window_backup.json",
)
MODEL_PATTERN = re.compile(r"^(?:gpt-5\.(?:4|5|6)|gpt-image-2)(?:-|$)")
CHATGPT_ENDPOINTS = ("/v1/chat/completions", "/v1/responses")
PRESERVED_TARGET_FIELDS = frozenset({"provider_specific_entry", "supported_openai_params"})
logger = logging.getLogger(__name__)


def _as_model_entry(value: JsonValue) -> Optional[ModelEntry]:
    if not isinstance(value, dict):
        return None
    return cast(ModelEntry, value)


def _source_models(model_costs: ModelCostMap) -> tuple[tuple[str, ModelEntry], ...]:
    return tuple(
        (key, entry)
        for key, value in model_costs.items()
        if MODEL_PATTERN.match(key)
        and (entry := _as_model_entry(value)) is not None
        and entry.get("litellm_provider") == "openai"
    )


def _sync_entry(source: ModelEntry, existing: Optional[ModelEntry]) -> ModelEntry:
    existing_entry = existing or {}
    target_only_fields = {key: existing_entry[key] for key in PRESERVED_TARGET_FIELDS if key in existing_entry}
    target_overrides = (
        {"litellm_provider": "chatgpt"}
        if source.get("mode") == "image_generation"
        else {
            "litellm_provider": "chatgpt",
            "mode": "responses",
            "supported_endpoints": list(CHATGPT_ENDPOINTS),
        }
    )
    return {
        **source,
        **target_only_fields,
        **target_overrides,
    }


def sync_model_costs(model_costs: ModelCostMap) -> tuple[ModelCostMap, tuple[str, ...]]:
    sources = _source_models(model_costs)
    if not sources:
        raise ValueError("No supported OpenAI GPT-5 or GPT Image models found")

    target_keys = tuple(f"chatgpt/{source_key}" for source_key, _ in sources)
    target_key_set = frozenset(target_keys)
    synced_entries = {
        target_key: _sync_entry(
            source=source_entry,
            existing=_as_model_entry(model_costs.get(target_key)),
        )
        for target_key, (_, source_entry) in zip(target_keys, sources)
    }
    changed_keys = tuple(key for key in target_keys if model_costs.get(key) != synced_entries[key])

    items = tuple(model_costs.items())
    existing_target_indexes = tuple(index for index, (key, _) in enumerate(items) if key in target_key_set)
    chatgpt_indexes = tuple(index for index, (key, _) in enumerate(items) if key.startswith("chatgpt/"))
    insertion_index = (
        min(existing_target_indexes)
        if existing_target_indexes
        else (max(chatgpt_indexes) + 1 if chatgpt_indexes else len(items))
    )

    synced: ModelCostMap = {}
    inserted = False
    for index, (key, value) in enumerate(items):
        if index == insertion_index:
            synced.update(synced_entries)
            inserted = True
        if key not in target_key_set:
            synced[key] = value
    if not inserted:
        synced.update(synced_entries)

    return synced, changed_keys


def sync_file(path: Path, check: bool = False) -> tuple[str, ...]:
    model_costs = cast(ModelCostMap, json.loads(path.read_text(encoding="utf-8")))
    synced, changed_keys = sync_model_costs(model_costs)
    if changed_keys and not check:
        path.write_text(json.dumps(synced, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed_keys


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync supported OpenAI GPT-5 and GPT Image model metadata to ChatGPT provider entries"
    )
    parser.add_argument("paths", nargs="*", type=Path, help="Model cost JSON files to update")
    parser.add_argument("--check", action="store_true", help="Exit with status 1 when files need synchronization")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    paths = tuple(args.paths) or DEFAULT_PATHS
    stale = False
    for path in paths:
        changed_keys = sync_file(path=path, check=args.check)
        if changed_keys:
            stale = True
            action = "needs sync" if args.check else "updated"
            logger.info("%s: %s %d entries", path, action, len(changed_keys))
        else:
            logger.info("%s: already synchronized", path)
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
