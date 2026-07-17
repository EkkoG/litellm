from collections import UserDict

from scripts.check_ldap_stable_user_id import Candidate, assess_candidate, assess_candidates, search_filter


def entry(**attributes: list[bytes]) -> dict[str, object]:
    return {"raw_attributes": attributes}


def test_assess_candidates_recommends_immutable_complete_unique_attribute() -> None:
    entries = (
        entry(objectGUID=[b"guid-1"], mail=[b"first@example.com"]),
        entry(objectGUID=[b"guid-2"], mail=[b"second@example.com"]),
    )

    results = assess_candidates(entries)

    assert results[0].attribute == "objectGUID"
    assert results[0].eligible is True
    assert results[0].coverage == 1.0
    assert results[0].uniqueness == 1.0


def test_assess_candidate_rejects_duplicates_missing_values_and_multiple_values() -> None:
    candidate = Candidate("entryUUID", 100, "stable")
    entries = (
        entry(entryUUID=[b"duplicate"]),
        entry(entryUUID=[b"duplicate"]),
        entry(entryUUID=[]),
        entry(entryUUID=[b"one", b"two"]),
    )

    result = assess_candidate(candidate, entries)

    assert result.eligible is False
    assert result.populated == 3
    assert result.unique == 1
    assert result.multi_valued == 1
    assert result.coverage == 0.75
    assert result.uniqueness == 1 / 3


def test_attribute_matching_is_case_insensitive() -> None:
    candidate = Candidate("objectGUID", 100, "stable")

    result = assess_candidate(candidate, ({"raw_attributes": UserDict({"objectguid": [b"guid-1"]})},))

    assert result.eligible is True


def test_search_filter_prefers_sync_filter_and_ignores_login_placeholder() -> None:
    assert search_filter(
        {"LDAP_SYNC_FILTER": "(objectClass=inetOrgPerson)", "LDAP_SEARCH_FILTER": "(uid={username})"}
    ) == ("(objectClass=inetOrgPerson)")
    assert search_filter({"LDAP_SEARCH_FILTER": "(uid={username})"}) == "(objectClass=person)"
    assert search_filter({"LDAP_SEARCH_FILTER": "(department=engineering)"}) == "(department=engineering)"
