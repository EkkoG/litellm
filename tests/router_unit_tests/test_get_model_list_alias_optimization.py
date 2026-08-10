from litellm import Router


class NoItemsAliasDict(dict):
    def items(self):
        raise AssertionError("Unexpected full alias iteration via items()")


def test_get_model_list_from_model_alias_should_not_iterate_for_non_alias_lookup():
    router = Router(
        model_list=[
            {
                "model_name": "gpt-5-mini",
                "litellm_params": {"model": "gpt-5-mini"},
            }
        ],
        model_group_alias={"alias-1": "gpt-5.5"},
    )
    router.model_group_alias = NoItemsAliasDict(
        {f"alias-{idx}": "gpt-5.5" for idx in range(200)}
    )

    model_alias_list = router.get_model_list_from_model_alias(
        model_name="gpt-5-mini"
    )
    assert model_alias_list == []


def test_map_team_model_should_not_iterate_aliases_for_non_alias_team_model_name():
    router = Router(
        model_list=[
            {
                "model_name": "gpt-5-mini",
                "litellm_params": {"model": "gpt-5-mini"},
                "model_info": {
                    "team_id": "team-1",
                    "team_public_model_name": "team-model",
                },
            }
        ],
        model_group_alias={"alias-1": "gpt-5.5"},
    )
    router.model_group_alias = NoItemsAliasDict(
        {f"alias-{idx}": "gpt-5.5" for idx in range(200)}
    )

    # map_team_model should return the public name unchanged (not the internal UUID name)
    # so the router can find all sibling deployments via team_id filtering
    result = router.map_team_model(team_model_name="team-model", team_id="team-1")
    assert result == "team-model", f"Expected public name 'team-model', got {result}"


def test_model_group_alias_disabled_and_hidden_semantics():
    router = Router(
        model_list=[
            {
                "model_name": "gpt-5-mini",
                "litellm_params": {"model": "gpt-5-mini"},
            }
        ],
        model_group_alias={
            "listed-alias": {"model": "gpt-5-mini", "hidden": False, "enabled": True},
            "hidden-alias": {"model": "gpt-5-mini", "hidden": True, "enabled": True},
            "disabled-alias": {"model": "gpt-5-mini", "hidden": False, "enabled": False},
        },
    )

    model_names = router.get_model_names()
    assert "listed-alias" in model_names
    assert "hidden-alias" not in model_names
    assert "disabled-alias" not in model_names

    assert router._get_model_from_alias("listed-alias") == "gpt-5-mini"
    assert router._get_model_from_alias("hidden-alias") == "gpt-5-mini"
    assert router._get_model_from_alias("disabled-alias") is None

    assert router.get_model_group_info("disabled-alias") is None
    assert router.get_model_group_info("hidden-alias") is None
    assert router.get_model_group_info("listed-alias") is not None
