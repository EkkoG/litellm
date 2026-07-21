from litellm.llms.chatgpt.common_utils import (
    build_chatgpt_session_prefix_fingerprint,
    derive_chatgpt_session_id,
)


def _chat_messages(system: str, first_user: str):
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": first_user},
    ]


class TestBuildSessionPrefixFingerprint:
    def test_stable_across_multi_turn_growth(self):
        turn_one = _chat_messages("sys", "hello")
        turn_two = [
            *turn_one,
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "follow up"},
        ]
        assert build_chatgpt_session_prefix_fingerprint(turn_one, "acct-1") == build_chatgpt_session_prefix_fingerprint(
            turn_two, "acct-1"
        )

    def test_differs_by_account_id(self):
        messages = _chat_messages("sys", "hello")
        assert build_chatgpt_session_prefix_fingerprint(messages, "acct-1") != build_chatgpt_session_prefix_fingerprint(
            messages, "acct-2"
        )

    def test_differs_by_first_user_message(self):
        assert build_chatgpt_session_prefix_fingerprint(
            _chat_messages("sys", "hello"), "acct-1"
        ) != build_chatgpt_session_prefix_fingerprint(_chat_messages("sys", "goodbye"), "acct-1")

    def test_reads_first_user_not_later_user_messages(self):
        base = _chat_messages("sys", "hello")
        extra_user_turn = [
            *base,
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "different second user"},
        ]
        assert build_chatgpt_session_prefix_fingerprint(base, "acct-1") == build_chatgpt_session_prefix_fingerprint(
            extra_user_turn, "acct-1"
        )

    def test_handles_list_content_blocks(self):
        str_content = _chat_messages("sys", "hello")
        block_content = [
            {"role": "system", "content": [{"type": "text", "text": "sys"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        ]
        assert build_chatgpt_session_prefix_fingerprint(
            str_content, "acct-1"
        ) == build_chatgpt_session_prefix_fingerprint(block_content, "acct-1")

    def test_returns_none_when_no_text(self):
        assert build_chatgpt_session_prefix_fingerprint([], "acct-1") is None
        assert build_chatgpt_session_prefix_fingerprint(None, "acct-1") is None
        assert (
            build_chatgpt_session_prefix_fingerprint([{"role": "assistant", "content": "only assistant"}], "acct-1")
            is None
        )


class TestDeriveSessionId:
    def test_explicit_session_id_wins_over_derivation(self):
        params = {"session_id": "explicit-123"}
        result = derive_chatgpt_session_id(params, _chat_messages("sys", "hello"), "acct-1")
        assert result == "explicit-123"

    def test_explicit_litellm_session_id_wins(self):
        params = {"litellm_session_id": "explicit-xyz"}
        result = derive_chatgpt_session_id(params, _chat_messages("sys", "hello"), "acct-1")
        assert result == "explicit-xyz"

    def test_derives_deterministic_hash_when_no_explicit(self):
        messages = _chat_messages("sys", "hello")
        first = derive_chatgpt_session_id({}, messages, "acct-1")
        second = derive_chatgpt_session_id({}, messages, "acct-1")
        assert first == second
        assert len(first) == 64
        int(first, 16)

    def test_derived_is_not_explicit_value(self):
        result = derive_chatgpt_session_id({}, _chat_messages("sys", "hello"), "acct-1")
        assert result not in ("", None)

    def test_internal_call_id_does_not_block_derivation(self):
        params = {"litellm_call_id": "442b720c-d365-4885-ade9-dd835e95b1b4"}
        result = derive_chatgpt_session_id(params, _chat_messages("sys", "hello"), "acct-1")
        assert result != params["litellm_call_id"]
        assert len(result) == 64
        int(result, 16)

    def test_internal_trace_id_does_not_block_derivation(self):
        params = {"litellm_trace_id": "442b720c-d365-4885-ade9-dd835e95b1b4"}
        result = derive_chatgpt_session_id(params, _chat_messages("sys", "hello"), "acct-1")
        assert result != params["litellm_trace_id"]
        assert len(result) == 64

    def test_falls_back_to_uuid_when_no_text(self):
        first = derive_chatgpt_session_id({}, [], "acct-1")
        second = derive_chatgpt_session_id({}, [], "acct-1")
        assert first
        assert second
        assert first != second
