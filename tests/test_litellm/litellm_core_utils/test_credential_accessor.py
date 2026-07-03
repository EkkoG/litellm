import time

import litellm
from litellm.litellm_core_utils.credential_accessor import CredentialAccessor
from litellm.types.utils import CredentialItem


def test_chatgpt_credential_refresh_updates_in_memory(monkeypatch):
    litellm.credential_list = [
        CredentialItem(
            credential_name="chatgpt-admin",
            credential_values={
                "api_key": "old-access",
                "chatgpt_refresh_token": "old-refresh",
                "chatgpt_id_token": "old-id",
                "chatgpt_expires_at": str(time.time() - 60),
            },
            credential_info={"custom_llm_provider": "chatgpt"},
        )
    ]

    def fake_refresh_tokens(self, refresh_token):
        assert refresh_token == "old-refresh"
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "id_token": "new-id",
        }

    persisted = []

    monkeypatch.setattr(
        "litellm.llms.chatgpt.authenticator.Authenticator._refresh_tokens",
        fake_refresh_tokens,
    )
    monkeypatch.setattr(
        "litellm.proxy.credential_endpoints.chatgpt_credential_utils._schedule_chatgpt_credential_persist",
        lambda credential_name, credential_values, user_id=None: persisted.append(
            (credential_name, credential_values.copy(), user_id)
        ),
    )

    values = CredentialAccessor.get_credential_values("chatgpt-admin")

    assert values["api_key"] == "new-access"
    assert values["chatgpt_refresh_token"] == "new-refresh"
    assert litellm.credential_list[0].credential_values["api_key"] == "new-access"
    assert persisted[0][0] == "chatgpt-admin"
