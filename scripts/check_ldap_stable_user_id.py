import argparse
import hashlib
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Iterable, cast
from urllib.parse import urlparse

from dotenv import dotenv_values
from ldap3 import AUTO_BIND_NO_TLS, AUTO_BIND_TLS_BEFORE_BIND, NONE, Connection, Server
from ldap3.core.exceptions import LDAPException
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError


class CliArgs(BaseModel):
    model_config = ConfigDict(frozen=True)

    env_file: Path
    limit: int
    allow_insecure: bool


@dataclass(frozen=True, slots=True)
class Candidate:
    attribute: str
    confidence: int
    stability: str


@dataclass(frozen=True, slots=True)
class CandidateResult:
    attribute: str
    confidence: int
    stability: str
    populated: int
    unique: int
    multi_valued: int
    sampled: int

    @property
    def coverage(self) -> float:
        return self.populated / self.sampled if self.sampled else 0.0

    @property
    def uniqueness(self) -> float:
        return self.unique / self.populated if self.populated else 0.0

    @property
    def eligible(self) -> bool:
        return self.populated == self.sampled and self.unique == self.populated and self.multi_valued == 0


CANDIDATES = (
    Candidate("objectGUID", 100, "Active Directory immutable object identifier"),
    Candidate("entryUUID", 100, "OpenLDAP immutable entry identifier"),
    Candidate("ipaUniqueID", 95, "FreeIPA unique object identifier"),
    Candidate("nsUniqueId", 95, "389 Directory Server unique entry identifier"),
    Candidate("orclGuid", 95, "Oracle directory global identifier"),
    Candidate("ibm-entryUUID", 95, "IBM directory entry identifier"),
    Candidate("ds-entry-uuid", 95, "OpenDJ/PingDS entry identifier"),
    Candidate("msDS-ConsistencyGuid", 85, "AD source anchor when consistently populated and managed as immutable"),
    Candidate("objectSid", 75, "Stable for an AD object but can change during cross-domain migration"),
    Candidate("uidNumber", 55, "Usually stable, but numeric IDs can be reassigned"),
    Candidate("employeeNumber", 40, "Organization-managed identifier that can be reused"),
    Candidate("employeeID", 40, "Organization-managed identifier that can be reused"),
    Candidate("uid", 15, "Login name that administrators may rename"),
    Candidate("sAMAccountName", 15, "AD login name that administrators may rename"),
    Candidate("userPrincipalName", 10, "Login principal that can change with name or domain"),
    Candidate("mail", 5, "Email address that commonly changes"),
)
RAW_ATTRIBUTES_ADAPTER = TypeAdapter(dict[str, tuple[bytes | str, ...]])
SEARCH_ENTRY_ADAPTER = TypeAdapter(dict[str, object])


def parse_args(argv: Sequence[str]) -> CliArgs:
    parser = argparse.ArgumentParser(description="Find the best LDAP attribute for LiteLLM Stable User ID Attribute")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--allow-insecure", action="store_true")
    return CliArgs.model_validate(vars(parser.parse_args(argv)))


def load_settings(env_file: Path) -> Mapping[str, str]:
    file_values = {key: value for key, value in dotenv_values(env_file).items() if value is not None}
    return {**file_values, **os.environ}


def required(settings: Mapping[str, str], *names: str) -> str:
    value = next((settings[name] for name in names if settings.get(name)), None)
    if value is None:
        raise ValueError(f"Missing required setting: {' or '.join(names)}")
    return value


def search_filter(settings: Mapping[str, str]) -> str:
    configured = settings.get("LDAP_SYNC_FILTER") or settings.get("LDAP_USER_SYNC_FILTER")
    if configured:
        return configured
    configured_search = settings.get("LDAP_SEARCH_FILTER") or settings.get("LDAP_USER_SEARCH_FILTER")
    if configured_search and "{" not in configured_search:
        return configured_search
    return "(objectClass=person)"


def canonical_value(value: bytes | str) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def values_for(entry: Mapping[str, object], attribute: str) -> tuple[bytes | str, ...]:
    try:
        raw_attributes = RAW_ATTRIBUTES_ADAPTER.validate_python(entry.get("raw_attributes"))
    except ValidationError:
        return ()
    matched_name = next((name for name in raw_attributes if str(name).casefold() == attribute.casefold()), None)
    if matched_name is None:
        return ()
    return raw_attributes[matched_name]


def assess_candidate(candidate: Candidate, entries: Sequence[Mapping[str, object]]) -> CandidateResult:
    entry_values = tuple(values_for(entry, candidate.attribute) for entry in entries)
    populated_values = tuple(values[0] for values in entry_values if len(values) == 1)
    canonical_values = tuple(canonical_value(value) for value in populated_values)
    return CandidateResult(
        attribute=candidate.attribute,
        confidence=candidate.confidence,
        stability=candidate.stability,
        populated=sum(bool(values) for values in entry_values),
        unique=len(frozenset(canonical_values)),
        multi_valued=sum(len(values) > 1 for values in entry_values),
        sampled=len(entries),
    )


def assess_candidates(entries: Sequence[Mapping[str, object]]) -> tuple[CandidateResult, ...]:
    results = tuple(assess_candidate(candidate, entries) for candidate in CANDIDATES)
    return tuple(
        sorted(
            (result for result in results if result.populated),
            key=lambda result: (result.eligible, result.confidence, result.coverage, result.uniqueness),
            reverse=True,
        )
    )


def fetch_entries(
    settings: Mapping[str, str], limit: int, allow_insecure: bool
) -> tuple[tuple[Mapping[str, object], ...], str]:
    ldap_url = required(settings, "LDAP_URL")
    base_dn = required(settings, "LDAP_SEARCH_BASE", "LDAP_BASE_DN")
    bind_dn = required(settings, "LDAP_BIND_DN", "LDAP_BIND_USER")
    bind_password = required(settings, "LDAP_BIND_PASSWORD")
    parsed_url = urlparse(ldap_url)
    if parsed_url.scheme not in {"ldap", "ldaps"} or not parsed_url.hostname:
        raise ValueError("LDAP_URL must use ldap:// or ldaps:// and include a hostname")
    if limit < 1:
        raise ValueError("--limit must be greater than zero")
    use_ssl = parsed_url.scheme == "ldaps"
    auto_bind = AUTO_BIND_NO_TLS if use_ssl or allow_insecure else AUTO_BIND_TLS_BEFORE_BIND
    server = Server(
        parsed_url.hostname,
        port=parsed_url.port or (636 if use_ssl else 389),
        use_ssl=use_ssl,
        get_info=NONE,
        connect_timeout=10,
    )
    connection = Connection(
        server,
        user=bind_dn,
        password=bind_password,
        auto_bind=auto_bind,
        receive_timeout=30,
        raise_exceptions=True,
    )
    try:
        raw_results = cast(
            Iterable[object],
            connection.extend.standard.paged_search(  # pyright: ignore[reportAny]  # ldap3 paged search is untyped
                search_base=base_dn,
                search_filter=search_filter(settings),
                search_scope="SUBTREE",
                attributes=[candidate.attribute for candidate in CANDIDATES],
                paged_size=min(limit, 500),
                size_limit=limit,
                generator=True,
            ),
        )
        validated_results = (
            entry
            for result in raw_results
            if (entry := SEARCH_ENTRY_ADAPTER.validate_python(result)).get("type") == "searchResEntry"
        )
        entries = tuple(islice(validated_results, limit))
        transport = "LDAPS" if use_ssl else ("plaintext LDAP" if allow_insecure else "LDAP with StartTLS")
        return entries, transport
    finally:
        connection.unbind()  # pyright: ignore[reportUnknownMemberType]  # ldap3 stubs do not type unbind controls


def percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def write_stdout(line: str = "") -> None:
    sys.stdout.write(f"{line}\n")


def write_stderr(line: str) -> None:
    sys.stderr.write(f"{line}\n")


def print_report(results: Sequence[CandidateResult], sampled: int, transport: str) -> int:
    write_stdout(f"Sampled LDAP users: {sampled}")
    write_stdout(f"Transport: {transport}")
    write_stdout("No LDAP attribute values, DNs, usernames, or email addresses are printed.")
    if sampled == 0:
        write_stderr("No entries matched the configured LDAP sync filter.")
        return 2
    if not results:
        write_stderr("None of the known stable-ID candidate attributes were populated.")
        write_stdout("LiteLLM can fall back to the entry DN, but a rename or move can change that identity.")
        return 2
    write_stdout()
    write_stdout(f"{'Attribute':24} {'Coverage':>10} {'Unique':>10} {'Multi':>7}  Assessment")
    for result in results:
        status = "eligible" if result.eligible else "not eligible"
        write_stdout(
            f"{result.attribute:24} {percent(result.coverage):>10} {percent(result.uniqueness):>10} "
            f"{result.multi_valued:>7}  {status}; {result.stability}"
        )
    recommendation = next((result for result in results if result.eligible and result.confidence >= 75), None)
    write_stdout()
    if recommendation is None:
        write_stdout("Recommendation: no high-confidence attribute passed all checks.")
        write_stdout("Do not use a mutable login or email attribute solely because it is unique in this sample.")
        return 2
    write_stdout(f"Recommendation: set Stable User ID Attribute to {recommendation.attribute}")
    write_stdout(
        "This validates coverage and uniqueness in the current sample; confirm immutability in the directory's policy."
    )
    return 0


def main(argv: Sequence[str] = ()) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        entries, transport = fetch_entries(load_settings(args.env_file), args.limit, args.allow_insecure)
    except (LDAPException, OSError, ValueError) as error:
        write_stderr(f"LDAP check failed: {error}")
        if not args.allow_insecure:
            write_stderr(
                "The script requires LDAPS or StartTLS. Use --allow-insecure only for a trusted isolated network."
            )
        return 1
    return print_report(assess_candidates(entries), len(entries), transport)


if __name__ == "__main__":
    raise SystemExit(main())
