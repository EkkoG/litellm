#!/usr/bin/env python3

import argparse
import json
import logging
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

ModelEntry = Mapping[str, JsonValue]
ModelCostMap = Mapping[str, ModelEntry]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = (
    REPO_ROOT / "model_prices_and_context_window.json",
    REPO_ROOT / "litellm/model_prices_and_context_window_backup.json",
)
DEFAULT_CODEX_PATH = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
CHATGPT_ENDPOINTS = ("/v1/chat/completions", "/v1/responses")
PRESERVED_TARGET_FIELDS = frozenset(("provider_specific_entry", "supported_openai_params"))
MODEL_COST_MAP_ADAPTER = TypeAdapter(ModelCostMap)
logger = logging.getLogger(__name__)


class CodexReasoningLevel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    effort: str = Field(min_length=1)


class CodexModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    slug: str = Field(min_length=1)
    supported_in_api: bool
    context_window: int = Field(gt=0)
    input_modalities: tuple[str, ...]
    supported_reasoning_levels: tuple[CodexReasoningLevel, ...]
    supports_parallel_tool_calls: bool
    supports_search_tool: bool


SourceModel = tuple[str, ModelEntry, Optional[CodexModel]]


class CodexCatalog(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    models: tuple[CodexModel, ...]


class CliArgs(BaseModel):
    model_config = ConfigDict(frozen=True)

    paths: tuple[Path, ...] = ()
    codex_bin: Optional[Path] = None
    bundled: bool = False
    check: bool = False
    replace: bool = False


@dataclass(frozen=True, slots=True)
class SyncResult:
    model_costs: ModelCostMap
    changed_keys: tuple[str, ...]
    removed_keys: tuple[str, ...]
    skipped_models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileSyncResult:
    changed_keys: tuple[str, ...]
    removed_keys: tuple[str, ...]
    skipped_models: tuple[str, ...]


def _resolve_codex_binary(override: Optional[Path]) -> Path:
    if override is not None:
        return override
    if DEFAULT_CODEX_PATH.is_file():
        return DEFAULT_CODEX_PATH
    path_codex = shutil.which("codex")
    if path_codex is not None:
        return Path(path_codex)
    raise FileNotFoundError("Codex executable not found; pass --codex-bin")


def load_codex_catalog(codex_binary: Path, bundled: bool = False) -> CodexCatalog:
    command = (str(codex_binary), "debug", "models", *(("--bundled",) if bundled else ()))
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit status {completed.returncode}"
        raise RuntimeError(f"codex debug models failed: {detail}")
    return CodexCatalog.model_validate_json(completed.stdout)


def _catalog_overrides(model: CodexModel) -> ModelEntry:
    reasoning_efforts = frozenset(level.effort for level in model.supported_reasoning_levels)
    modalities = list[JsonValue](model.input_modalities)  # mutable-ok: JSON arrays require list values
    return {  # mutable-ok: complete JSON object is built once and never mutated
        "max_input_tokens": model.context_window,
        "supported_modalities": modalities,
        "supports_vision": "image" in model.input_modalities,
        "supports_reasoning": bool(reasoning_efforts),
        "supports_low_reasoning_effort": "low" in reasoning_efforts,
        "supports_xhigh_reasoning_effort": "xhigh" in reasoning_efforts,
        "supports_max_reasoning_effort": "max" in reasoning_efforts,
        "supports_parallel_function_calling": model.supports_parallel_tool_calls,
        "supports_web_search": model.supports_search_tool,
    }


def _sync_entry(source: ModelEntry, existing: Optional[ModelEntry], catalog_model: Optional[CodexModel]) -> ModelEntry:
    existing_entry = existing or {}  # mutable-ok: empty JSON object is never mutated
    target_only_fields = {  # mutable-ok: preserved JSON fields are built once
        key: existing_entry[key] for key in PRESERVED_TARGET_FIELDS if key in existing_entry
    }
    if catalog_model is None:
        return {  # mutable-ok: complete JSON object is built once and never mutated
            **source,
            **target_only_fields,
            "litellm_provider": "chatgpt",
        }
    return {  # mutable-ok: complete JSON object is built once and never mutated
        **source,
        **target_only_fields,
        **_catalog_overrides(catalog_model),
        "litellm_provider": "chatgpt",
        "mode": "responses",
        "supported_endpoints": list(CHATGPT_ENDPOINTS),  # mutable-ok: JSON arrays require list values
    }


def _catalog_source(model_costs: ModelCostMap, model: CodexModel) -> Optional[tuple[str, ModelEntry, CodexModel]]:
    source = model_costs.get(model.slug)
    if source is None or source.get("litellm_provider") != "openai":
        return None
    return model.slug, source, model


def _image_source(key: str, source: ModelEntry) -> Optional[tuple[str, ModelEntry, None]]:
    if not key.startswith("gpt-image-2"):
        return None
    if source.get("litellm_provider") != "openai" or source.get("mode") != "image_generation":
        return None
    return key, source, None


def _present_sources(candidates: tuple[Optional[SourceModel], ...]) -> tuple[SourceModel, ...]:
    return tuple(source for source in candidates if source is not None)


def sync_model_costs(model_costs: ModelCostMap, catalog: CodexCatalog, replace: bool = False) -> SyncResult:
    catalog_models = tuple(model for model in catalog.models if model.supported_in_api)
    catalog_sources = _present_sources(tuple(_catalog_source(model_costs, model) for model in catalog_models))
    image_sources = (
        () if replace else _present_sources(tuple(_image_source(key, value) for key, value in model_costs.items()))
    )
    sources: tuple[SourceModel, ...] = (*catalog_sources, *image_sources)
    if not sources:
        raise ValueError("No Codex catalog models have matching OpenAI metadata")

    matched_slugs = frozenset(key for key, _, _ in catalog_sources)
    skipped_models = tuple(model.slug for model in catalog_models if model.slug not in matched_slugs)
    target_keys = tuple(f"chatgpt/{source_key}" for source_key, _, _ in sources)
    target_key_set = frozenset(target_keys)
    synced_entries = {  # mutable-ok: generated JSON entries are built once and never mutated
        target_key: _sync_entry(source, model_costs.get(target_key), catalog_model)
        for target_key, (_, source, catalog_model) in zip(target_keys, sources)
    }
    updated_keys = tuple(key for key in target_keys if model_costs.get(key) != synced_entries[key])
    removed_keys = tuple(
        key for key in model_costs if replace and key.startswith("chatgpt/") and key not in target_key_set
    )
    removed_key_set = frozenset(removed_keys)
    changed_keys = (*updated_keys, *removed_keys)
    items = tuple(model_costs.items())
    replaced_items = tuple(
        (key, synced_entries[key] if key in target_key_set else value)
        for key, value in items
        if key not in removed_key_set
    )
    missing_entries = tuple((key, synced_entries[key]) for key in target_keys if key not in model_costs)
    chatgpt_indexes = tuple(index for index, (key, _) in enumerate(replaced_items) if key.startswith("chatgpt/"))
    insertion_index = max(chatgpt_indexes) + 1 if chatgpt_indexes else len(replaced_items)
    synced = dict(  # mutable-ok: output JSON mapping is built once and never mutated
        (*replaced_items[:insertion_index], *missing_entries, *replaced_items[insertion_index:])
    )
    return SyncResult(
        model_costs=synced,
        changed_keys=changed_keys,
        removed_keys=removed_keys,
        skipped_models=skipped_models,
    )


def parse_model_costs(json_text: str) -> ModelCostMap:
    return MODEL_COST_MAP_ADAPTER.validate_json(json_text)


def sync_file(path: Path, catalog: CodexCatalog, check: bool = False, replace: bool = False) -> FileSyncResult:
    model_costs = parse_model_costs(path.read_text(encoding="utf-8"))
    result = sync_model_costs(model_costs=model_costs, catalog=catalog, replace=replace)
    if result.changed_keys and not check:
        serialized = json.dumps(result.model_costs, indent=4, ensure_ascii=False)
        path.write_text(serialized + "\n", encoding="utf-8")
    return FileSyncResult(
        changed_keys=result.changed_keys,
        removed_keys=result.removed_keys,
        skipped_models=result.skipped_models,
    )


def _parse_args(argv: Optional[Sequence[str]] = None) -> CliArgs:
    parser = argparse.ArgumentParser(description="Sync the Codex model catalog to LiteLLM ChatGPT provider entries")
    parser.add_argument("paths", nargs="*", type=Path, help="Model cost JSON files to update")
    parser.add_argument("--codex-bin", type=Path, help="Path to the Codex executable")
    parser.add_argument("--bundled", action="store_true", help="Use the catalog bundled with the Codex executable")
    parser.add_argument("--check", action="store_true", help="Exit with status 1 when files need synchronization")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Remove ChatGPT models missing from the Codex catalog or without matching OpenAI metadata",
    )
    return CliArgs.model_validate(vars(parser.parse_args(argv)))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        catalog = load_codex_catalog(_resolve_codex_binary(args.codex_bin), bundled=args.bundled)
        paths = tuple(args.paths) or DEFAULT_PATHS
        results = tuple(
            (path, sync_file(path=path, catalog=catalog, check=args.check, replace=args.replace)) for path in paths
        )
    except (OSError, RuntimeError, ValueError, ValidationError) as exc:
        logger.error("%s", exc)
        return 2

    for path, result in results:
        action = "needs sync" if args.check else "updated"
        message = f"{action} {len(result.changed_keys)} entries" if result.changed_keys else "already synchronized"
        logger.info("%s: %s", path, message)
        if result.removed_keys:
            logger.info("%s: removed %d unsupported ChatGPT models", path, len(result.removed_keys))
    skipped_models = results[0][1].skipped_models if results else ()
    if skipped_models:
        logger.info("Skipped catalog models without OpenAI metadata: %s", ", ".join(skipped_models))
    return 1 if args.check and any(result.changed_keys for _, result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
