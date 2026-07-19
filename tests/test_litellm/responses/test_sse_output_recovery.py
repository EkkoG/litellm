"""Tests for litellm.responses.sse_output_recovery helpers."""

from litellm.responses.sse_output_recovery import (
    _MAX_CONTENT_INDEX,
    record_output_item_chunk,
    record_output_text_chunk,
)


def test_item_chunk_with_negative_output_index_falls_back_to_append():
    output_items: dict = {}
    record_output_item_chunk(
        parsed_chunk={
            "type": "response.output_item.done",
            "output_index": -1,
            "item": {"type": "message", "id": "msg_neg"},
        },
        output_items=output_items,
    )
    assert -1 not in output_items
    assert output_items[0]["id"] == "msg_neg"


def test_text_chunk_with_negative_output_index_falls_back_to_append():
    output_items: dict = {}
    text_only_items: dict = {}
    record_output_text_chunk(
        parsed_chunk={
            "type": "response.output_text.done",
            "output_index": -1,
            "content_index": 0,
            "text": "kept",
        },
        output_items=output_items,
        text_only_items=text_only_items,
    )
    assert -1 not in text_only_items
    assert text_only_items[0]["content"][0]["text"] == "kept"


def test_text_chunk_with_oversized_content_index_is_dropped():
    output_items: dict = {}
    text_only_items: dict = {}
    record_output_text_chunk(
        parsed_chunk={
            "type": "response.output_text.done",
            "output_index": 0,
            "content_index": _MAX_CONTENT_INDEX + 1,
            "text": "ignored",
        },
        output_items=output_items,
        text_only_items=text_only_items,
    )
    item = text_only_items[0]
    assert item["content"] == []


def test_text_chunk_with_negative_content_index_is_dropped():
    output_items: dict = {}
    text_only_items: dict = {}
    record_output_text_chunk(
        parsed_chunk={
            "type": "response.output_text.done",
            "output_index": 0,
            "content_index": -1,
            "text": "ignored",
        },
        output_items=output_items,
        text_only_items=text_only_items,
    )
    assert text_only_items[0]["content"] == []


def test_text_chunk_at_max_content_index_is_recorded():
    output_items: dict = {}
    text_only_items: dict = {}
    record_output_text_chunk(
        parsed_chunk={
            "type": "response.output_text.done",
            "output_index": 0,
            "content_index": _MAX_CONTENT_INDEX,
            "text": "kept",
        },
        output_items=output_items,
        text_only_items=text_only_items,
    )
    content = text_only_items[0]["content"]
    assert len(content) == _MAX_CONTENT_INDEX + 1
    assert content[_MAX_CONTENT_INDEX]["text"] == "kept"
