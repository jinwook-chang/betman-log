#!/usr/bin/env python3
"""Audit a Betman slate and allocate a conservative price-gap portfolio."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

KST = timezone(timedelta(hours=9))
POLICY_VERSION = "2026-08-11-v4"
DEFAULT_UNIT = 1_000
WORKSPACE_LEDGER_PATH = Path("/Users/jinwook/Desktop/betmen/ledger.json")

DISPOSITIONS = {
    "shortlist",
    "expired",
    "no_comparable",
    "insufficient_evidence",
    "dominated",
}
STATUSES = {"open", "suspended", "cancelled", "postponed", "closed"}
ORIGINS = {"assistant", "user", "both"}
SCOPES = {"board-wide", "candidate-only", "comparison"}
PURCHASE_MODES = {"single", "parlay"}
SUPPORTED_MARKETS = {
    "1x2": {"home", "draw", "away"},
    "moneyline": {"home", "away"},
    "total": {"under", "over"},
}
TOKEN_RE = re.compile(r"^[a-z0-9_]+$")

# All thresholds used for decisions and exposure live here.
POLICY = {
    "price": {
        "base_probability_haircut": 0.01,
        "max_probability_dispersion": 0.08,
        "min_market_ev": 0.03,
        "min_robust_ev": 0.00,
        "max_live_age_minutes": 5,
        "future_tolerance_minutes": 2,
        "max_price_observation_skew_minutes": 5,
        "max_comparison_skew_minutes": 5,
        "odds_tick": 0.01,
    },
    "risk": {
        "kelly_multiplier": 0.50,
        "max_cluster_fraction": 0.30,
        "max_total_fraction": 0.60,
        "tail_odds": 5.00,
        "max_tail_fraction": 0.10,
        "max_value_picks": 6,
    },
    "action": {
        "min_market_ev": -0.05,
        "relative_min_market_ev": -0.18,
        "relative_min_ratio_mid": 0.03,
        "relative_min_ratio_low": 0.00,
        "stake_units": 1,
        "max_picks": 1,
    },
    "parlay": {
        "max_value_legs": 2,
        "max_relative_action_legs": 2,
    },
}


INPUT_SCHEMA = {
    "title": "Betman price-gap portfolio input",
    "type": "object",
    "required": [
        "snapshot_kst",
        "scope",
        "weekly_cycle",
        "board",
        "open_exposure",
        "candidates",
        "parlays",
    ],
    "properties": {
        "snapshot_kst": "ISO-8601 timestamp ending in +09:00",
        "scope": sorted(SCOPES),
        "weekly_cycle": {
            "required": [
                "id",
                "cycle_start_kst",
                "cycle_end_kst",
                "ledger_path",
                "ledger_entries",
                "ledger_snapshot_sha256",
                "verified_at_kst",
            ],
            "ledger_entry_required": [
                "id",
                "kind",
                "occurred_at_kst",
                "amount",
                "decision_id",
                "source",
            ],
            "comment": (
                "Current Monday-to-Monday canonical ledger slice. The allocator "
                "derives action availability; callers cannot assert it."
            ),
        },
        "open_exposure": {
            "required": ["tickets"],
            "comment": (
                "Existing unsettled tickets. The allocator derives total, tail, "
                "event, and correlation-cluster exposure from ticket detail."
            ),
            "ticket_required": [
                "id",
                "decision_id",
                "sleeve",
                "weekly_cycle_id",
                "purchased_at_kst",
                "stake",
                "odds",
                "event_ids",
                "correlation_cluster_ids",
            ],
        },
        "board": {
            "required": [
                "source_url",
                "round_id",
                "captured_at_kst",
                "window_start_kst",
                "window_end_kst",
                "snapshot_sha256",
                "official_event_count",
                "official_market_row_count",
                "sections",
                "parlay_groups",
                "rows",
            ],
            "row_required": [
                "row_id",
                "section_id",
                "event_id",
                "competition_id",
                "participants",
                "scheduled_start_kst",
                "sport",
                "league",
                "market_type",
                "line",
                "settlement_rule",
                "status",
                "close_time_kst",
                "outcome_labels",
                "outcome_odds",
                "odds_observed_at_kst",
                "purchase_modes",
                "parlay_group_ids",
                "selection_dispositions",
                "selection_disposition_reasons",
                "disposition",
                "disposition_reason",
            ],
            "section_required": [
                "id",
                "source_url",
                "captured_at_kst",
                "official_market_row_count",
                "rows_sha256",
            ],
            "parlay_group_required": [
                "id",
                "min_legs",
                "max_legs",
                "odds_rule",
                "odds_tick",
                "max_combined_odds",
            ],
            "snapshot_sha256_comment": (
                "SHA-256 of rows encoded as canonical UTF-8 JSON with sorted keys "
                "and separators (',', ':')."
            ),
        },
        "candidate_required": [
            "id",
            "board_row_id",
            "event_id",
            "competition_id",
            "participants",
            "scheduled_start_kst",
            "correlation_cluster_ids",
            "origin",
            "label",
            "sport",
            "market_type",
            "line",
            "settlement_rule",
            "selection_key",
            "betman_odds",
            "betman_url",
            "betman_odds_observed_at_kst",
            "close_time_kst",
            "status",
            "status_observed_at_kst",
            "status_source_url",
            "market_quotes",
        ],
        "quote_required": [
            "provider_id",
            "provider_group",
            "provider_event_id",
            "source_url",
            "observed_at_kst",
            "event_id",
            "competition_id",
            "participants",
            "scheduled_start_kst",
            "sport",
            "market_type",
            "line",
            "settlement_rule",
            "outcome_labels",
            "outcome_odds",
            "selection_index",
        ],
        "parlay_required": [
            "id",
            "origin",
            "label",
            "leg_ids",
            "parlay_group_id",
            "betman_odds",
            "betman_url",
            "betman_odds_observed_at_kst",
        ],
    },
    "supported_markets": {
        market: sorted(outcomes) for market, outcomes in SUPPORTED_MARKETS.items()
    },
    "policy": POLICY,
    "policy_version": POLICY_VERSION,
}


def finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def nonnegative_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def token(value: Any, name: str) -> str:
    result = nonempty_string(value, name)
    if TOKEN_RE.fullmatch(result) is None:
        raise ValueError(f"{name} must use lowercase [a-z0-9_] tokens")
    return result


def http_url(value: Any, name: str) -> str:
    result = nonempty_string(value, name)
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an HTTP(S) URL")
    return result


def provider_domain(value: str) -> str:
    host = (urlparse(value).hostname or "").lower().removeprefix("www.")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    public_two_part = {"co.uk", "com.au", "co.jp", "co.kr"}
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in public_two_part else suffix


def is_official_betman_url(value: str) -> bool:
    host = (urlparse(value).hostname or "").lower()
    return host == "betman.co.kr" or host.endswith(".betman.co.kr")


def kst_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(hours=9):
        raise ValueError(f"{name} must use KST +09:00")
    return parsed


def age_minutes(now: datetime, observed: datetime) -> float:
    return (now - observed).total_seconds() / 60.0


def live_age_reason(age: float, label: str) -> str | None:
    if age < -POLICY["price"]["future_tolerance_minutes"]:
        return f"future_{label}"
    if age > POLICY["price"]["max_live_age_minutes"]:
        return f"stale_{label}"
    return None


def validate_fields(
    value: dict[str, Any], required: set[str], allowed: set[str], name: str
) -> None:
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - allowed)
    if missing:
        raise ValueError(f"{name} missing fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(unknown)}")


BOARD_FIELDS = {
    "source_url",
    "round_id",
    "captured_at_kst",
    "window_start_kst",
    "window_end_kst",
    "snapshot_sha256",
    "official_event_count",
    "official_market_row_count",
    "sections",
    "parlay_groups",
    "rows",
}
ROW_FIELDS = {
    "row_id",
    "section_id",
    "event_id",
    "competition_id",
    "participants",
    "scheduled_start_kst",
    "sport",
    "league",
    "market_type",
    "line",
    "settlement_rule",
    "status",
    "close_time_kst",
    "outcome_labels",
    "outcome_odds",
    "odds_observed_at_kst",
    "purchase_modes",
    "parlay_group_ids",
    "selection_dispositions",
    "selection_disposition_reasons",
    "disposition",
    "disposition_reason",
}
SECTION_FIELDS = {
    "id",
    "source_url",
    "captured_at_kst",
    "official_market_row_count",
    "rows_sha256",
}
PARLAY_GROUP_FIELDS = {
    "id",
    "min_legs",
    "max_legs",
    "odds_rule",
    "odds_tick",
    "max_combined_odds",
}
CANDIDATE_FIELDS = set(INPUT_SCHEMA["properties"]["candidate_required"])
QUOTE_FIELDS = set(INPUT_SCHEMA["properties"]["quote_required"])
PARLAY_FIELDS = set(INPUT_SCHEMA["properties"]["parlay_required"])
ROOT_FIELDS = {
    "snapshot_kst",
    "scope",
    "weekly_cycle",
    "board",
    "open_exposure",
    "candidates",
    "parlays",
}
OPEN_EXPOSURE_FIELDS = {"tickets"}
WEEKLY_CYCLE_FIELDS = {
    "id",
    "cycle_start_kst",
    "cycle_end_kst",
    "ledger_path",
    "ledger_entries",
    "ledger_snapshot_sha256",
    "verified_at_kst",
}
LEDGER_ENTRY_FIELDS = {
    "id",
    "kind",
    "occurred_at_kst",
    "amount",
    "decision_id",
    "source",
}
LEDGER_ENTRY_OPTIONAL_FIELDS = {"occurred_at_precision"}
LEDGER_FILE_FIELDS = {"version", "entries"}
OPEN_TICKET_FIELDS = {
    "id",
    "decision_id",
    "sleeve",
    "weekly_cycle_id",
    "purchased_at_kst",
    "stake",
    "odds",
    "event_ids",
    "correlation_cluster_ids",
}


def canonical_rows_sha256(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalized_line(value: Any, name: str) -> float | None:
    if value is None:
        return None
    return finite_number(value, name)


def validate_identifier_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list")
    result = [nonempty_string(item, name) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique identifiers")
    return result


def validate_participants(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name} must contain exactly two participants")
    result = [nonempty_string(item, name) for item in value]
    if len(set(result)) != 2:
        raise ValueError(f"{name} must contain two distinct participants")
    return result


def validate_market(market_type: str, line: float | None) -> set[str]:
    if market_type not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported market_type: {market_type}")
    if market_type in {"1x2", "moneyline"} and line is not None:
        raise ValueError(f"{market_type} line must be null")
    if market_type == "total":
        if line is None:
            raise ValueError("total line must be numeric")
        doubled = line * 2
        if not math.isclose(doubled, round(doubled)) or int(round(doubled)) % 2 == 0:
            raise ValueError(
                "only half-point totals without a push state are supported"
            )
    return SUPPORTED_MARKETS[market_type]


def validate_board(
    board: Any, now: datetime, scope: str
) -> tuple[
    dict[str, dict[str, Any]],
    str,
    datetime,
    dict[str, dict[str, Any]],
]:
    if not isinstance(board, dict):
        raise ValueError("board must be an object")
    validate_fields(board, BOARD_FIELDS, BOARD_FIELDS, "board")
    source_url = http_url(board["source_url"], "board source_url")
    if not is_official_betman_url(source_url):
        raise ValueError("board capture requires an official Betman board URL")
    nonempty_string(board["round_id"], "board round_id")
    captured = kst_datetime(board["captured_at_kst"], "board captured_at_kst")
    reason = live_age_reason(age_minutes(now, captured), "board")
    if reason:
        raise ValueError(reason)
    window_start = kst_datetime(board["window_start_kst"], "window_start_kst")
    window_end = kst_datetime(board["window_end_kst"], "window_end_kst")
    if window_start > window_end:
        raise ValueError("board deadline window start must not exceed end")
    event_count = nonnegative_integer(
        board["official_event_count"], "official_event_count"
    )
    row_count = nonnegative_integer(
        board["official_market_row_count"], "official_market_row_count"
    )
    parlay_groups_input = board["parlay_groups"]
    if not isinstance(parlay_groups_input, list):
        raise ValueError("board parlay_groups must be a list")
    parlay_groups: dict[str, dict[str, Any]] = {}
    for item in parlay_groups_input:
        if not isinstance(item, dict):
            raise ValueError("every parlay group must be an object")
        validate_fields(
            item,
            PARLAY_GROUP_FIELDS,
            PARLAY_GROUP_FIELDS,
            "parlay group",
        )
        group_id = nonempty_string(item["id"], "parlay group id")
        min_legs = nonnegative_integer(item["min_legs"], "parlay group min_legs")
        max_legs = nonnegative_integer(item["max_legs"], "parlay group max_legs")
        odds_rule = nonempty_string(item["odds_rule"], "parlay group odds_rule")
        if odds_rule not in {"exact_product", "round_half_up", "floor"}:
            raise ValueError("unsupported parlay group odds_rule")
        odds_tick_input = item["odds_tick"]
        if odds_rule == "exact_product":
            if odds_tick_input is not None:
                raise ValueError("exact_product parlay groups require null odds_tick")
            odds_tick = None
        else:
            odds_tick = finite_number(odds_tick_input, "parlay group odds_tick")
            if odds_tick <= 0:
                raise ValueError("parlay group odds_tick must be positive")
        max_odds_input = item["max_combined_odds"]
        if max_odds_input is None:
            max_combined_odds = None
        else:
            max_combined_odds = finite_number(
                max_odds_input, "parlay group max_combined_odds"
            )
            if max_combined_odds <= 1:
                raise ValueError("parlay group max_combined_odds must exceed 1")
        if min_legs < 2 or max_legs < min_legs:
            raise ValueError("parlay group leg limits are invalid")
        if group_id in parlay_groups:
            raise ValueError("parlay group IDs must be unique")
        parlay_groups[group_id] = {
            "min_legs": min_legs,
            "max_legs": max_legs,
            "odds_rule": odds_rule,
            "odds_tick": odds_tick,
            "max_combined_odds": max_combined_odds,
        }

    sections_input = board["sections"]
    if not isinstance(sections_input, list) or not sections_input:
        raise ValueError("board sections must be a non-empty list")
    sections: dict[str, dict[str, Any]] = {}
    for item in sections_input:
        if not isinstance(item, dict):
            raise ValueError("every board section must be an object")
        validate_fields(item, SECTION_FIELDS, SECTION_FIELDS, "board section")
        section_id = nonempty_string(item["id"], "board section id")
        section_url = http_url(item["source_url"], "board section source_url")
        if not is_official_betman_url(section_url):
            raise ValueError("board capture requires official Betman section URLs")
        section_captured = kst_datetime(
            item["captured_at_kst"], "board section captured_at_kst"
        )
        section_age_reason = live_age_reason(
            age_minutes(now, section_captured), "board_section"
        )
        if section_age_reason:
            raise ValueError(section_age_reason)
        section_count = nonnegative_integer(
            item["official_market_row_count"],
            "section official_market_row_count",
        )
        rows_sha256 = nonempty_string(item["rows_sha256"], "section rows_sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", rows_sha256):
            raise ValueError("section rows_sha256 must be a lowercase SHA-256 digest")
        if section_id in sections:
            raise ValueError("board section IDs must be unique")
        sections[section_id] = {
            "official_market_row_count": section_count,
            "rows_sha256": rows_sha256,
        }
    rows_input = board["rows"]
    if not isinstance(rows_input, list):
        raise ValueError("board rows must be a list")
    expected_hash = nonempty_string(board["snapshot_sha256"], "snapshot_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("snapshot_sha256 must be a lowercase SHA-256 hex digest")
    actual_hash = canonical_rows_sha256(rows_input)
    if expected_hash != actual_hash:
        raise ValueError("board snapshot_sha256 does not match rows")

    rows: dict[str, dict[str, Any]] = {}
    events: set[str] = set()
    event_contracts: dict[str, tuple[str, tuple[str, str], str]] = {}
    contract_event_ids: dict[tuple[str, tuple[str, str], str], str] = {}
    for item in rows_input:
        if not isinstance(item, dict):
            raise ValueError("every board row must be an object")
        validate_fields(item, ROW_FIELDS, ROW_FIELDS, "board row")
        row_id = nonempty_string(item["row_id"], "row_id")
        section_id = nonempty_string(item["section_id"], "row section_id")
        if section_id not in sections:
            raise ValueError("board row references an unknown section_id")
        event_id = nonempty_string(item["event_id"], "event_id")
        competition_id = nonempty_string(item["competition_id"], "row competition_id")
        participants = validate_participants(item["participants"], "row participants")
        scheduled_start = kst_datetime(
            item["scheduled_start_kst"], "row scheduled_start_kst"
        )
        event_contract = (
            competition_id,
            (participants[0], participants[1]),
            scheduled_start.isoformat(),
        )
        previous_contract = event_contracts.get(event_id)
        if previous_contract is not None and previous_contract != event_contract:
            raise ValueError("board event_id has inconsistent event identity")
        previous_event_id = contract_event_ids.get(event_contract)
        if previous_event_id is not None and previous_event_id != event_id:
            raise ValueError("board event identity maps to multiple event_id values")
        event_contracts[event_id] = event_contract
        contract_event_ids[event_contract] = event_id
        token(item["sport"], "row sport")
        nonempty_string(item["league"], "row league")
        market_type = token(item["market_type"], "row market_type")
        line = normalized_line(item["line"], "row line")
        token(item["settlement_rule"], "row settlement_rule")
        labels = item["outcome_labels"]
        odds = item["outcome_odds"]
        if not isinstance(labels, list) or len(labels) < 2:
            raise ValueError("row outcome_labels must contain at least two outcomes")
        normalized_labels = [token(label, "row outcome label") for label in labels]
        if len(normalized_labels) != len(set(normalized_labels)):
            raise ValueError("row outcome labels must be unique")
        if (
            market_type in SUPPORTED_MARKETS
            and set(normalized_labels) != SUPPORTED_MARKETS[market_type]
        ):
            raise ValueError("supported board market must contain every known outcome")
        if not isinstance(odds, list) or len(odds) != len(normalized_labels):
            raise ValueError("row outcome_odds must align with outcome_labels")
        normalized_odds = [finite_number(odd, "row outcome odds") for odd in odds]
        if any(odd <= 1.0 for odd in normalized_odds):
            raise ValueError("row outcome odds must be greater than 1")
        odds_observed = kst_datetime(
            item["odds_observed_at_kst"], "row odds_observed_at_kst"
        )
        row_age_reason = live_age_reason(age_minutes(now, odds_observed), "board_price")
        if row_age_reason:
            raise ValueError(row_age_reason)
        close_time = kst_datetime(item["close_time_kst"], "row close_time_kst")
        if not window_start <= close_time <= window_end:
            raise ValueError("board row close time is outside the deadline window")
        purchase_modes_input = item["purchase_modes"]
        if not isinstance(purchase_modes_input, list) or not purchase_modes_input:
            raise ValueError("row purchase_modes must be a non-empty list")
        purchase_modes = [
            nonempty_string(mode, "row purchase mode") for mode in purchase_modes_input
        ]
        if len(purchase_modes) != len(set(purchase_modes)) or not set(
            purchase_modes
        ).issubset(PURCHASE_MODES):
            raise ValueError("row purchase_modes are invalid")
        group_ids_input = item["parlay_group_ids"]
        if not isinstance(group_ids_input, list):
            raise ValueError("row parlay_group_ids must be a list")
        group_ids = [
            nonempty_string(group_id, "row parlay group id")
            for group_id in group_ids_input
        ]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("row parlay_group_ids must be unique")
        if not set(group_ids).issubset(parlay_groups):
            raise ValueError("row references an unknown parlay group")
        if ("parlay" in purchase_modes) != bool(group_ids):
            raise ValueError("parlay purchase mode and parlay_group_ids must agree")
        if item["status"] not in STATUSES:
            raise ValueError("invalid row status")
        if item["disposition"] not in DISPOSITIONS:
            raise ValueError("invalid row disposition")
        selection_dispositions = item["selection_dispositions"]
        if not isinstance(selection_dispositions, dict) or set(
            selection_dispositions
        ) != set(normalized_labels):
            raise ValueError(
                "selection_dispositions must account for every row outcome"
            )
        if any(
            disposition not in DISPOSITIONS
            for disposition in selection_dispositions.values()
        ):
            raise ValueError("invalid selection disposition")
        selection_reasons = item["selection_disposition_reasons"]
        if not isinstance(selection_reasons, dict) or set(selection_reasons) != set(
            normalized_labels
        ):
            raise ValueError(
                "selection_disposition_reasons must cover every row outcome"
            )
        for selection_reason in selection_reasons.values():
            nonempty_string(selection_reason, "selection disposition reason")
        has_shortlist = "shortlist" in selection_dispositions.values()
        if (item["disposition"] == "shortlist") != has_shortlist:
            raise ValueError(
                "row disposition must be shortlist exactly when a selection "
                "is shortlisted"
            )
        if scope == "board-wide" and item["status"] == "open":
            if has_shortlist and any(
                disposition != "shortlist"
                for disposition in selection_dispositions.values()
            ):
                raise ValueError(
                    "board-wide comparable rows must shortlist every outcome"
                )
            if "dominated" in selection_dispositions.values():
                raise ValueError(
                    "board-wide capture cannot pre-label open outcomes as dominated"
                )
        market_supported = market_type in SUPPORTED_MARKETS
        if market_supported:
            try:
                validate_market(market_type, line)
            except ValueError as exc:
                if has_shortlist:
                    raise ValueError(str(exc)) from exc
                market_supported = False
        if not market_supported and has_shortlist:
            raise ValueError("unsupported board market cannot be shortlisted")
        nonempty_string(item["disposition_reason"], "row disposition_reason")
        if row_id in rows:
            raise ValueError("board row_id values must be unique")
        rows[row_id] = item
        events.add(event_id)
    if len(rows) != row_count or len(events) != event_count:
        raise ValueError("official counts do not reconcile to board rows")
    if (
        sum(section["official_market_row_count"] for section in sections.values())
        != row_count
    ):
        raise ValueError("section row counts do not reconcile to board total")
    for section_id, section in sections.items():
        section_rows = [row for row in rows_input if row["section_id"] == section_id]
        if len(section_rows) != section["official_market_row_count"]:
            raise ValueError("section row count does not match captured rows")
        if canonical_rows_sha256(section_rows) != section["rows_sha256"]:
            raise ValueError("section rows_sha256 does not match captured rows")
    return rows, actual_hash, captured, parlay_groups


def validate_open_exposure(value: Any, now: datetime) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("open_exposure must be an object")
    validate_fields(
        value,
        OPEN_EXPOSURE_FIELDS,
        OPEN_EXPOSURE_FIELDS,
        "open_exposure",
    )
    tickets_input = value["tickets"]
    if not isinstance(tickets_input, list):
        raise ValueError("open_exposure tickets must be a list")
    tickets: list[dict[str, Any]] = []
    ticket_ids: set[str] = set()
    total_stake = 0
    tail_stake = 0
    by_event: dict[str, int] = {}
    by_cluster: dict[str, int] = {}
    for item in tickets_input:
        if not isinstance(item, dict):
            raise ValueError("every open exposure ticket must be an object")
        validate_fields(item, OPEN_TICKET_FIELDS, OPEN_TICKET_FIELDS, "open ticket")
        ticket_id = nonempty_string(item["id"], "open ticket id")
        if ticket_id in ticket_ids:
            raise ValueError("open ticket IDs must be unique")
        ticket_ids.add(ticket_id)
        decision_id = nonempty_string(item["decision_id"], "open ticket decision_id")
        sleeve = nonempty_string(item["sleeve"], "open ticket sleeve")
        if sleeve not in {"value", "action", "manual"}:
            raise ValueError("open ticket sleeve is invalid")
        action_id_match = re.fullmatch(
            r"action-(\d{4}-\d{2}-\d{2})-([0-9a-f]{20})", decision_id
        )
        if (action_id_match is not None) != (sleeve == "action"):
            raise ValueError(
                "canonical action decision IDs and the action sleeve must agree"
            )
        weekly_cycle_id = nonempty_string(
            item["weekly_cycle_id"], "open ticket weekly_cycle_id"
        )
        try:
            parsed_cycle_id = datetime.strptime(weekly_cycle_id, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(
                "open ticket weekly_cycle_id must be a YYYY-MM-DD Monday date"
            ) from exc
        if parsed_cycle_id.weekday() != 0:
            raise ValueError("open ticket weekly_cycle_id must be a Monday date")
        purchased_at = kst_datetime(
            item["purchased_at_kst"], "open ticket purchased_at_kst"
        )
        if purchased_at > now:
            raise ValueError("future_open_ticket_purchase")
        derived_cycle_id = (
            (purchased_at - timedelta(days=purchased_at.weekday())).date().isoformat()
        )
        if weekly_cycle_id != derived_cycle_id:
            raise ValueError(
                "open ticket weekly_cycle_id must equal the Monday derived from "
                "purchased_at_kst"
            )
        if action_id_match is not None and action_id_match.group(1) != derived_cycle_id:
            raise ValueError(
                "action decision ID cycle must match the ticket purchase cycle"
            )
        stake = nonnegative_integer(item["stake"], "open ticket stake")
        if stake <= 0:
            raise ValueError("open ticket stake must be positive")
        if stake % DEFAULT_UNIT != 0:
            raise ValueError("open ticket stake must use the KRW 1,000 unit")
        if sleeve == "action" and stake != DEFAULT_UNIT:
            raise ValueError("open action ticket stake must be KRW 1,000")
        odds = finite_number(item["odds"], "open ticket odds")
        if odds <= 1.0:
            raise ValueError("open ticket odds must be greater than 1")
        event_ids = validate_identifier_list(item["event_ids"], "open ticket event_ids")
        cluster_ids = validate_identifier_list(
            item["correlation_cluster_ids"],
            "open ticket correlation_cluster_ids",
        )
        if not set(event_ids).issubset(cluster_ids):
            raise ValueError(
                "open ticket correlation clusters must include every event_id"
            )
        total_stake += stake
        if odds >= POLICY["risk"]["tail_odds"]:
            tail_stake += stake
        for event_id in event_ids:
            by_event[event_id] = by_event.get(event_id, 0) + stake
        for cluster_id in cluster_ids:
            by_cluster[cluster_id] = by_cluster.get(cluster_id, 0) + stake
        tickets.append(
            {
                "id": ticket_id,
                "decision_id": decision_id,
                "sleeve": sleeve,
                "weekly_cycle_id": weekly_cycle_id,
                "purchased_at_kst": purchased_at.isoformat(),
                "stake": stake,
                "odds": odds,
                "event_ids": event_ids,
                "correlation_cluster_ids": cluster_ids,
            }
        )
    return {
        "tickets": tickets,
        "total_stake": total_stake,
        "tail_stake": tail_stake,
        "by_event": by_event,
        "by_cluster": by_cluster,
    }


def validate_ledger_entries(value: Any, now: datetime) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("ledger entries must be a list")
    entry_ids: set[str] = set()
    decision_ids: set[str] = set()
    entries: list[dict[str, Any]] = []
    previous_time: datetime | None = None
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("every ledger entry must be an object")
        validate_fields(
            item,
            LEDGER_ENTRY_FIELDS,
            LEDGER_ENTRY_FIELDS | LEDGER_ENTRY_OPTIONAL_FIELDS,
            "ledger entry",
        )
        entry_id = nonempty_string(item["id"], "ledger entry id")
        if entry_id in entry_ids:
            raise ValueError("ledger entry IDs must be unique")
        entry_ids.add(entry_id)
        kind = nonempty_string(item["kind"], "ledger entry kind")
        if kind not in {"deposit", "action_fallback", "action_ticket"}:
            raise ValueError("ledger entry kind is unsupported")
        occurred = kst_datetime(item["occurred_at_kst"], "ledger occurred_at_kst")
        if occurred > now:
            raise ValueError("future_ledger_entry")
        if previous_time is not None and occurred < previous_time:
            raise ValueError("ledger entries must be append-ordered by time")
        previous_time = occurred
        amount = nonnegative_integer(item["amount"], "ledger entry amount")
        source = nonempty_string(item["source"], "ledger entry source")
        decision_input = item["decision_id"]
        if kind == "deposit":
            if decision_input is not None:
                raise ValueError("deposit ledger entries require null decision_id")
            if amount != 10_000:
                raise ValueError("replenishment deposit must be KRW 10,000")
            decision_id = None
        elif kind == "action_fallback":
            decision_id = nonempty_string(decision_input, "action fallback decision_id")
            if decision_id in decision_ids:
                raise ValueError("action decision IDs must be unique")
            decision_ids.add(decision_id)
            if amount != DEFAULT_UNIT:
                raise ValueError("action fallback ledger amount must be KRW 1,000")
            action_match = re.fullmatch(
                r"action-(\d{4}-\d{2}-\d{2})-([0-9a-f]{20})", decision_id
            )
            derived_cycle = (
                (occurred - timedelta(days=occurred.weekday())).date().isoformat()
            )
            if action_match is None or action_match.group(1) != derived_cycle:
                raise ValueError("ledger action decision ID must match its cycle")
        else:
            # Backward compatibility for historical ledgers created before the
            # canonical action_fallback kind and decision-ID format were added.
            decision_id = nonempty_string(decision_input, "action ticket decision_id")
            if decision_id in decision_ids:
                raise ValueError("action decision IDs must be unique")
            decision_ids.add(decision_id)
            if amount != DEFAULT_UNIT:
                raise ValueError("action ticket ledger amount must be KRW 1,000")
        normalized_entry = {
            "id": entry_id,
            "kind": kind,
            "occurred_at_kst": occurred.isoformat(),
            "amount": amount,
            "decision_id": decision_id,
            "source": source,
        }
        if "occurred_at_precision" in item:
            precision = nonempty_string(
                item["occurred_at_precision"], "ledger occurred_at_precision"
            )
            if precision not in {"date", "datetime"}:
                raise ValueError("ledger occurred_at_precision is invalid")
            normalized_entry["occurred_at_precision"] = precision
        entries.append(normalized_entry)
    return entries


def validate_weekly_cycle(
    value: Any, now: datetime, open_exposure: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("weekly_cycle must be an object")
    validate_fields(value, WEEKLY_CYCLE_FIELDS, WEEKLY_CYCLE_FIELDS, "weekly_cycle")
    cycle_id = nonempty_string(value["id"], "weekly cycle id")
    cycle_start = kst_datetime(value["cycle_start_kst"], "cycle_start_kst")
    cycle_end = kst_datetime(value["cycle_end_kst"], "cycle_end_kst")
    expected_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    expected_end = expected_start + timedelta(days=7)
    if cycle_start != expected_start or cycle_end != expected_end:
        raise ValueError("weekly cycle must be the current KST Monday-to-Monday window")
    if cycle_id != expected_start.date().isoformat():
        raise ValueError("weekly cycle id must equal the current Monday date")

    ledger_path = Path(nonempty_string(value["ledger_path"], "ledger_path")).resolve()
    if ledger_path != WORKSPACE_LEDGER_PATH.resolve():
        raise ValueError("weekly ledger_path must be the canonical workspace ledger")
    ledger_bytes = ledger_path.read_bytes()
    ledger_hash = nonempty_string(
        value["ledger_snapshot_sha256"], "ledger_snapshot_sha256"
    )
    if not re.fullmatch(r"[0-9a-f]{64}", ledger_hash):
        raise ValueError("ledger_snapshot_sha256 must be a lowercase SHA-256 digest")
    if hashlib.sha256(ledger_bytes).hexdigest() != ledger_hash:
        raise ValueError("ledger_snapshot_sha256 does not match the workspace ledger")
    ledger_document = json.loads(ledger_bytes.decode("utf-8"))
    if not isinstance(ledger_document, dict):
        raise ValueError("workspace ledger must be an object")
    validate_fields(
        ledger_document, LEDGER_FILE_FIELDS, LEDGER_FILE_FIELDS, "workspace ledger"
    )
    if ledger_document["version"] != 1:
        raise ValueError("unsupported workspace ledger version")
    all_entries = validate_ledger_entries(ledger_document["entries"], now)
    current_entries = [
        item
        for item in all_entries
        if cycle_start
        <= kst_datetime(item["occurred_at_kst"], "ledger occurred_at_kst")
        < cycle_end
    ]
    supplied_entries = validate_ledger_entries(value["ledger_entries"], now)
    if supplied_entries != current_entries:
        raise ValueError(
            "weekly ledger_entries must equal the current-cycle workspace ledger slice"
        )

    verified = kst_datetime(value["verified_at_kst"], "weekly cycle verified_at_kst")
    if verified > now:
        raise ValueError("future_weekly_cycle_ledger_verification")
    reason = live_age_reason(age_minutes(now, verified), "weekly_cycle_ledger")
    if reason:
        raise ValueError(reason)
    if current_entries and verified < max(
        kst_datetime(item["occurred_at_kst"], "ledger occurred_at_kst")
        for item in current_entries
    ):
        raise ValueError("weekly ledger verification predates a ledger entry")

    deposit_total = sum(
        item["amount"] for item in current_entries if item["kind"] == "deposit"
    )
    action_decision_ids = [
        item["decision_id"]
        for item in current_entries
        if item["kind"] in {"action_fallback", "action_ticket"}
    ]
    open_action_decision_ids = {
        ticket["decision_id"]
        for ticket in open_exposure["tickets"]
        if ticket["sleeve"] == "action" and ticket["weekly_cycle_id"] == cycle_id
    }
    if open_action_decision_ids - set(action_decision_ids):
        raise ValueError(
            "workspace ledger omits a current-cycle open action ticket decision"
        )
    return {
        "id": cycle_id,
        "cycle_start_kst": cycle_start.isoformat(),
        "cycle_end_kst": cycle_end.isoformat(),
        "ledger_path": str(ledger_path),
        "ledger_entry_count": len(current_entries),
        "confirmed_deposit_total": deposit_total,
        "action_fallback_available": deposit_total > 0 and not action_decision_ids,
        "action_fallback_used_decision_ids": action_decision_ids,
        "open_action_decision_ids": sorted(open_action_decision_ids),
        "ledger_snapshot_sha256": ledger_hash,
        "verified_at_kst": verified.isoformat(),
    }


def validate_shared_board_row_evidence(
    candidates: list[dict[str, Any]],
) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate["board_row_id"], []).append(candidate)
    shared_candidate_fields = {
        "betman_url",
        "betman_odds_observed_at_kst",
        "close_time_kst",
        "status",
        "status_observed_at_kst",
        "status_source_url",
    }
    shared_quote_fields = QUOTE_FIELDS - {"selection_index"}
    for row_id, row_candidates in grouped.items():
        if len(row_candidates) < 2:
            continue
        reference = row_candidates[0]
        reference_quotes = sorted(
            (
                {field: quote[field] for field in shared_quote_fields}
                for quote in reference["market_quotes"]
            ),
            key=lambda quote: quote["provider_id"],
        )
        for candidate in row_candidates[1:]:
            if any(
                candidate[field] != reference[field]
                for field in shared_candidate_fields
            ):
                raise ValueError(
                    f"candidates for board row {row_id} must share "
                    "cutoff/status evidence"
                )
            candidate_quotes = sorted(
                (
                    {field: quote[field] for field in shared_quote_fields}
                    for quote in candidate["market_quotes"]
                ),
                key=lambda quote: quote["provider_id"],
            )
            if candidate_quotes != reference_quotes:
                raise ValueError(
                    f"candidates for board row {row_id} must share "
                    "complete-market quotes"
                )


def quote_probability(
    quote: Any, candidate: dict[str, Any], now: datetime
) -> dict[str, Any]:
    if not isinstance(quote, dict):
        raise ValueError("market quote must be an object")
    validate_fields(quote, QUOTE_FIELDS, QUOTE_FIELDS, "market quote")
    provider_id = token(quote["provider_id"], "provider_id")
    provider_group = token(quote["provider_group"], "provider_group")
    provider_event_id = nonempty_string(quote["provider_event_id"], "provider_event_id")
    source_url = http_url(quote["source_url"], "market quote source_url")
    if is_official_betman_url(source_url):
        raise ValueError("market consensus quotes must exclude Betman target prices")
    observed = kst_datetime(quote["observed_at_kst"], "quote observed_at_kst")
    for field in (
        "event_id",
        "competition_id",
        "sport",
        "market_type",
        "line",
        "settlement_rule",
    ):
        if quote[field] != candidate[field]:
            raise ValueError(f"market quote {field} must match candidate")
    quote_participants = validate_participants(
        quote["participants"], "market quote participants"
    )
    if quote_participants != candidate["participants"]:
        raise ValueError("market quote participants must match candidate")
    quote_start = kst_datetime(
        quote["scheduled_start_kst"], "market quote scheduled_start_kst"
    )
    candidate_start = kst_datetime(
        candidate["scheduled_start_kst"], "candidate scheduled_start_kst"
    )
    if quote_start != candidate_start:
        raise ValueError("market quote scheduled_start_kst must match candidate")

    labels = quote["outcome_labels"]
    odds = quote["outcome_odds"]
    selection_index = quote["selection_index"]
    if not isinstance(labels, list) or not isinstance(odds, list):
        raise ValueError("outcome labels and odds must be lists")
    normalized_labels = [token(item, "outcome label") for item in labels]
    if len(normalized_labels) != len(set(normalized_labels)):
        raise ValueError("outcome labels must be unique")
    if len(odds) != len(normalized_labels):
        raise ValueError("outcome odds must align with outcome labels")
    if (
        isinstance(selection_index, bool)
        or not isinstance(selection_index, int)
        or not 0 <= selection_index < len(odds)
    ):
        raise ValueError("selection_index must identify one outcome")
    if normalized_labels[selection_index] != candidate["selection_key"]:
        raise ValueError("selected outcome must match candidate selection_key")
    expected = validate_market(candidate["market_type"], candidate["line"])
    if set(normalized_labels) != expected:
        raise ValueError("quote must contain every known market outcome")

    decimal_odds = [finite_number(item, "market odds") for item in odds]
    if any(item <= 1.0 for item in decimal_odds):
        raise ValueError("market odds must be greater than 1")
    inverse = [1.0 / item for item in decimal_odds]
    return {
        "provider_id": provider_id,
        "provider_group": provider_group,
        "provider_event_id": provider_event_id,
        "provider_domain": provider_domain(source_url),
        "source_url": source_url,
        "observed_at_kst": observed.isoformat(),
        "observed_at": observed,
        "age_minutes": age_minutes(now, observed),
        "fair_probability": inverse[selection_index] / sum(inverse),
    }


def evidence_grade(source_count: int, dispersion: float, max_age: float) -> str:
    if source_count >= 3 and dispersion <= 0.04 and max_age <= 2:
        return "A"
    if source_count >= 2 and dispersion <= 0.08 and max_age <= 5:
        return "B"
    return "C"


def full_kelly(probability: float, decimal_odds: float) -> float:
    return max(0.0, (decimal_odds * probability - 1.0) / (decimal_odds - 1.0))


def normalized_market_probability(
    outcome_odds: list[Any], selected_index: int
) -> tuple[float, float]:
    decimal_odds = [finite_number(value, "board outcome odds") for value in outcome_odds]
    if any(value <= 1.0 for value in decimal_odds):
        raise ValueError("board outcome odds must be greater than 1")
    inverse = [1.0 / value for value in decimal_odds]
    inverse_sum = sum(inverse)
    return inverse[selected_index] / inverse_sum, 1.0 / inverse_sum


def ceil_odds_tick(value: float) -> float:
    tick = POLICY["price"]["odds_tick"]
    return round(math.ceil((value - 1e-12) / tick) * tick, 2)


def minimum_acceptable_odds(
    mid_probability: float, low_probability: float
) -> float | None:
    if mid_probability <= 0 or low_probability <= 0:
        return None
    market_floor = (1.0 + POLICY["price"]["min_market_ev"]) / mid_probability
    robust_floor = 1.0 / low_probability
    return ceil_odds_tick(max(market_floor, robust_floor))


def minimum_odds_for_full_kelly_unit(
    low_probability: float, equity_at_cost: int, unit: int
) -> float | None:
    required_fraction = unit / equity_at_cost
    if low_probability <= required_fraction:
        return None
    return ceil_odds_tick(
        (1.0 - required_fraction) / (low_probability - required_fraction)
    )


def official_parlay_odds(raw_product: float, group: dict[str, Any]) -> float:
    capped = raw_product
    if group["max_combined_odds"] is not None:
        capped = min(capped, group["max_combined_odds"])
    rule = group["odds_rule"]
    if rule == "exact_product":
        return capped
    tick = group["odds_tick"]
    scaled = capped / tick
    if rule == "floor":
        return math.floor(scaled + 1e-12) * tick
    return math.floor(scaled + 0.5 + 1e-12) * tick


def evaluate_candidate(
    candidate: Any,
    row: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("every candidate must be an object")
    validate_fields(candidate, CANDIDATE_FIELDS, CANDIDATE_FIELDS, "candidate")
    for field in (
        "id",
        "board_row_id",
        "event_id",
        "competition_id",
        "label",
    ):
        nonempty_string(candidate[field], field)
    if candidate["origin"] not in ORIGINS:
        raise ValueError("invalid candidate origin")
    sport = token(candidate["sport"], "sport")
    market_type = token(candidate["market_type"], "market_type")
    settlement_rule = token(candidate["settlement_rule"], "settlement_rule")
    selection_key = token(candidate["selection_key"], "selection_key")
    correlation_cluster_ids = validate_identifier_list(
        candidate["correlation_cluster_ids"], "correlation_cluster_ids"
    )
    if candidate["event_id"] not in correlation_cluster_ids:
        raise ValueError("correlation_cluster_ids must include event_id")
    participants = validate_participants(
        candidate["participants"], "candidate participants"
    )
    scheduled_start = kst_datetime(
        candidate["scheduled_start_kst"], "candidate scheduled_start_kst"
    )
    row_participants = validate_participants(row["participants"], "row participants")
    row_start = kst_datetime(row["scheduled_start_kst"], "row scheduled_start_kst")
    if participants != row_participants:
        raise ValueError("candidate participants must match board row")
    if scheduled_start != row_start:
        raise ValueError("candidate scheduled_start_kst must match board row")
    line = normalized_line(candidate["line"], "line")
    candidate["line"] = line
    if selection_key not in validate_market(market_type, line):
        raise ValueError("selection_key is invalid for market_type")
    for field in (
        "event_id",
        "competition_id",
        "sport",
        "market_type",
        "line",
        "settlement_rule",
        "status",
    ):
        if candidate[field] != row[field]:
            raise ValueError(f"candidate {field} must match board row")

    odds = finite_number(candidate["betman_odds"], "betman_odds")
    if odds <= 1.0:
        raise ValueError("betman_odds must be greater than 1")
    betman_url = http_url(candidate["betman_url"], "betman_url")
    if not is_official_betman_url(betman_url):
        raise ValueError("candidate betman_url must use the official Betman domain")
    status_url = http_url(candidate["status_source_url"], "status_source_url")
    betman_observed = kst_datetime(
        candidate["betman_odds_observed_at_kst"], "betman_odds_observed_at_kst"
    )
    row_labels = [token(label, "row outcome label") for label in row["outcome_labels"]]
    if selection_key not in row_labels:
        raise ValueError("candidate selection_key must exist in board row outcomes")
    row_price = finite_number(
        row["outcome_odds"][row_labels.index(selection_key)],
        "selected board row odds",
    )
    betman_normalized_probability, betman_market_payout_rate = (
        normalized_market_probability(
            row["outcome_odds"], row_labels.index(selection_key)
        )
    )
    if not math.isclose(odds, row_price, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("candidate betman_odds must match the board row price")
    if betman_observed != kst_datetime(
        row["odds_observed_at_kst"], "row odds_observed_at_kst"
    ):
        raise ValueError(
            "candidate Betman observation must match the board row snapshot"
        )
    status_observed = kst_datetime(
        candidate["status_observed_at_kst"], "status_observed_at_kst"
    )
    close_time = kst_datetime(candidate["close_time_kst"], "close_time_kst")
    if close_time != kst_datetime(row["close_time_kst"], "row close_time_kst"):
        raise ValueError("candidate close time must match the board row")
    if candidate["status"] not in STATUSES:
        raise ValueError("invalid candidate status")

    quotes = candidate["market_quotes"]
    if not isinstance(quotes, list) or len(quotes) < 2:
        raise ValueError("market_quotes must contain at least two providers")
    quote_results = [quote_probability(item, candidate, now) for item in quotes]
    provider_ids = [item["provider_id"] for item in quote_results]
    provider_groups = [item["provider_group"] for item in quote_results]
    provider_domains = [item["provider_domain"] for item in quote_results]
    source_urls = [item["source_url"] for item in quote_results]
    for values, label in (
        (provider_ids, "provider IDs"),
        (provider_groups, "provider groups"),
        (provider_domains, "provider domains"),
        (source_urls, "source URLs"),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"market quotes must use unique {label}")

    probabilities = [item["fair_probability"] for item in quote_results]
    mid_probability = statistics.median(probabilities)
    dispersion = max(probabilities) - min(probabilities)
    haircut = max(POLICY["price"]["base_probability_haircut"], dispersion / 2.0)
    low_probability = max(0.0, mid_probability - haircut)
    market_ev = mid_probability * odds - 1.0
    robust_ev = low_probability * odds - 1.0
    relative_ratio_mid = mid_probability / betman_normalized_probability - 1.0
    relative_ratio_low = low_probability / betman_normalized_probability - 1.0
    quote_max_age = max(item["age_minutes"] for item in quote_results)
    quote_min_age = min(item["age_minutes"] for item in quote_results)
    price_observation_skew = max(
        abs((item["observed_at"] - betman_observed).total_seconds()) / 60.0
        for item in quote_results
    )

    reasons: list[str] = []
    for age, label in (
        (age_minutes(now, betman_observed), "betman_price"),
        (age_minutes(now, status_observed), "status"),
        (quote_max_age, "market"),
    ):
        reason = live_age_reason(age, label)
        if reason:
            reasons.append(reason)
    if quote_min_age < -POLICY["price"]["future_tolerance_minutes"]:
        reasons.append("future_market")
    if candidate["status"] != "open":
        reasons.append(f"status_{candidate['status']}")
    if now >= close_time:
        reasons.append("closed")
    if dispersion > POLICY["price"]["max_probability_dispersion"]:
        reasons.append("market_disagreement")
    if price_observation_skew > POLICY["price"]["max_price_observation_skew_minutes"]:
        reasons.append("price_observation_skew")

    return {
        "id": candidate["id"],
        "economic_key": (f"single|{candidate['board_row_id']}|{selection_key}"),
        "board_row_id": candidate["board_row_id"],
        "kind": "single",
        "event_ids": [candidate["event_id"]],
        "competition_id": candidate["competition_id"],
        "participants": participants,
        "scheduled_start_kst": scheduled_start.isoformat(),
        "correlation_cluster_ids": correlation_cluster_ids,
        "origin": candidate["origin"],
        "label": candidate["label"],
        "sport": sport,
        "market_type": market_type,
        "line": line,
        "settlement_rule": settlement_rule,
        "selection_key": selection_key,
        "odds": odds,
        "betman_url": betman_url,
        "betman_odds_observed_at_kst": betman_observed.isoformat(),
        "close_time_kst": close_time.isoformat(),
        "status": candidate["status"],
        "status_source_url": status_url,
        "status_observed_at_kst": status_observed.isoformat(),
        "purchase_modes": list(row["purchase_modes"]),
        "parlay_group_ids": list(row["parlay_group_ids"]),
        "purchase_reasons": (
            [] if "single" in row["purchase_modes"] else ["single_not_available"]
        ),
        "source_urls": source_urls,
        "quote_observed_at_kst": sorted(
            item["observed_at_kst"] for item in quote_results
        ),
        "source_count": len(quote_results),
        "break_even_probability": 1.0 / odds,
        "betman_normalized_probability": betman_normalized_probability,
        "betman_market_payout_rate": betman_market_payout_rate,
        "market_probability": mid_probability,
        "probability_low": low_probability,
        "probability_haircut": haircut,
        "probability_dispersion": dispersion,
        "price_observation_skew_minutes": price_observation_skew,
        "evidence_grade": evidence_grade(len(quote_results), dispersion, quote_max_age),
        "market_ev": market_ev,
        "robust_ev": robust_ev,
        "relative_ratio_mid": relative_ratio_mid,
        "relative_ratio_low": relative_ratio_low,
        "full_kelly": full_kelly(low_probability, odds),
        "minimum_acceptable_odds": minimum_acceptable_odds(
            mid_probability, low_probability
        ),
        "evidence_reasons": sorted(set(reasons)),
    }


def evaluate_parlay(
    parlay: Any,
    candidates: dict[str, dict[str, Any]],
    parlay_groups: dict[str, dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(parlay, dict):
        raise ValueError("every parlay must be an object")
    validate_fields(parlay, PARLAY_FIELDS, PARLAY_FIELDS, "parlay")
    parlay_id = nonempty_string(parlay["id"], "parlay id")
    label = nonempty_string(parlay["label"], "parlay label")
    parlay_group_id = nonempty_string(parlay["parlay_group_id"], "parlay group id")
    if parlay_group_id not in parlay_groups:
        raise ValueError("parlay references an unknown official parlay group")
    if parlay["origin"] not in ORIGINS:
        raise ValueError("invalid parlay origin")
    legs = parlay["leg_ids"]
    if not isinstance(legs, list) or len(legs) < 2:
        raise ValueError("parlay leg_ids must contain at least two legs")
    if len(legs) != len(set(legs)):
        raise ValueError("parlay leg_ids must be unique")
    try:
        leg_items = [candidates[item] for item in legs]
    except KeyError as exc:
        raise ValueError(f"unknown parlay leg: {exc.args[0]}") from exc
    odds = finite_number(parlay["betman_odds"], "parlay betman_odds")
    if odds <= 1.0:
        raise ValueError("parlay betman_odds must be greater than 1")
    betman_url = http_url(parlay["betman_url"], "parlay betman_url")
    if not is_official_betman_url(betman_url):
        raise ValueError("parlay betman_url must use the official Betman domain")
    observed = kst_datetime(
        parlay["betman_odds_observed_at_kst"],
        "parlay betman_odds_observed_at_kst",
    )
    event_ids = [item["event_ids"][0] for item in leg_items]
    leg_cluster_sets = [set(item["correlation_cluster_ids"]) for item in leg_items]
    correlation_cluster_ids = sorted(set().union(*leg_cluster_sets))
    reasons = {reason for item in leg_items for reason in item["evidence_reasons"]}
    price_age_reason = live_age_reason(age_minutes(now, observed), "betman_price")
    if price_age_reason:
        reasons.add(price_age_reason)
    shared_cluster = any(
        leg_cluster_sets[left] & leg_cluster_sets[right]
        for left in range(len(leg_cluster_sets))
        for right in range(left + 1, len(leg_cluster_sets))
    )
    if len(event_ids) != len(set(event_ids)) or shared_cluster:
        reasons.add("correlated_or_same_event")
    purchase_reasons: list[str] = []
    group_limits = parlay_groups[parlay_group_id]
    if (
        any(
            parlay_group_id not in item["parlay_group_ids"]
            or "parlay" not in item["purchase_modes"]
            for item in leg_items
        )
        or not group_limits["min_legs"] <= len(leg_items) <= group_limits["max_legs"]
    ):
        purchase_reasons.append("parlay_not_available")
    raw_product_odds = math.prod(item["odds"] for item in leg_items)
    expected_official_odds = official_parlay_odds(raw_product_odds, group_limits)
    comparison_tolerance = (
        1e-6 if group_limits["odds_tick"] is None else group_limits["odds_tick"] / 1_000
    )
    if not math.isclose(
        odds,
        expected_official_odds,
        rel_tol=1e-9,
        abs_tol=comparison_tolerance,
    ):
        purchase_reasons.append("official_parlay_odds_mismatch")
    parlay_price_skew = max(
        abs(
            (
                observed
                - kst_datetime(
                    item["betman_odds_observed_at_kst"],
                    "leg betman_odds_observed_at_kst",
                )
            ).total_seconds()
        )
        / 60.0
        for item in leg_items
    )
    parlay_market_skew = max(
        abs(
            (
                observed - kst_datetime(quote_time, "leg quote observed_at_kst")
            ).total_seconds()
        )
        / 60.0
        for item in leg_items
        for quote_time in item["quote_observed_at_kst"]
    )
    parlay_price_skew = max(parlay_price_skew, parlay_market_skew)
    if parlay_price_skew > POLICY["price"]["max_price_observation_skew_minutes"]:
        reasons.add("price_observation_skew")

    mid_probability = math.prod(item["market_probability"] for item in leg_items)
    low_probability = math.prod(item["probability_low"] for item in leg_items)
    betman_normalized_probability = math.prod(
        item["betman_normalized_probability"] for item in leg_items
    )
    betman_market_payout_rate = math.prod(
        item["betman_market_payout_rate"] for item in leg_items
    )
    market_ev = mid_probability * odds - 1.0
    robust_ev = low_probability * odds - 1.0
    relative_ratio_mid = mid_probability / betman_normalized_probability - 1.0
    relative_ratio_low = low_probability / betman_normalized_probability - 1.0
    return {
        "id": parlay_id,
        "economic_key": "parlay|"
        + "+".join(sorted(item["economic_key"] for item in leg_items)),
        "kind": "parlay",
        "event_ids": event_ids,
        "correlation_cluster_ids": correlation_cluster_ids,
        "leg_ids": list(legs),
        "leg_count": len(legs),
        "parlay_group_id": parlay_group_id,
        "official_odds_rule": group_limits["odds_rule"],
        "official_raw_product_odds": raw_product_odds,
        "expected_official_odds": expected_official_odds,
        "origin": parlay["origin"],
        "label": label,
        "odds": odds,
        "betman_url": betman_url,
        "betman_odds_observed_at_kst": observed.isoformat(),
        "close_time_kst": min(item["close_time_kst"] for item in leg_items),
        "source_urls": sorted(
            {url for item in leg_items for url in item["source_urls"]}
        ),
        "break_even_probability": 1.0 / odds,
        "betman_normalized_probability": betman_normalized_probability,
        "betman_market_payout_rate": betman_market_payout_rate,
        "market_probability": mid_probability,
        "probability_low": low_probability,
        "probability_haircut": mid_probability - low_probability,
        "probability_dispersion": max(
            item["probability_dispersion"] for item in leg_items
        ),
        "price_observation_skew_minutes": parlay_price_skew,
        "evidence_grade": max(
            (item["evidence_grade"] for item in leg_items),
            key={"A": 0, "B": 1, "C": 2}.get,
        ),
        "market_ev": market_ev,
        "robust_ev": robust_ev,
        "relative_ratio_mid": relative_ratio_mid,
        "relative_ratio_low": relative_ratio_low,
        "full_kelly": full_kelly(low_probability, odds),
        "leg_full_kelly": [item["full_kelly"] for item in leg_items],
        "minimum_acceptable_odds": minimum_acceptable_odds(
            mid_probability, low_probability
        ),
        "evidence_reasons": sorted(reasons),
        "purchase_reasons": purchase_reasons,
        "all_legs_value": all(
            item["market_ev"] >= POLICY["price"]["min_market_ev"]
            and item["robust_ev"] >= POLICY["price"]["min_robust_ev"]
            and not item["evidence_reasons"]
            for item in leg_items
        ),
        "all_legs_relative_action": all(
            item["market_ev"] >= POLICY["action"]["relative_min_market_ev"]
            and item["relative_ratio_mid"]
            >= POLICY["action"]["relative_min_ratio_mid"]
            and item["relative_ratio_low"]
            >= POLICY["action"]["relative_min_ratio_low"]
            and not item["evidence_reasons"]
            for item in leg_items
        ),
    }


def floor_unit(amount: float, unit: int) -> int:
    return int(math.floor(amount / unit) * unit)


def rounded_metrics(item: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "break_even_probability",
        "betman_normalized_probability",
        "betman_market_payout_rate",
        "market_probability",
        "probability_low",
        "probability_haircut",
        "probability_dispersion",
        "price_observation_skew_minutes",
        "official_raw_product_odds",
        "expected_official_odds",
        "market_ev",
        "robust_ev",
        "relative_ratio_mid",
        "relative_ratio_low",
        "full_kelly",
    }
    return {
        key: round(value, 6) if key in fields and isinstance(value, float) else value
        for key, value in item.items()
        if key not in {"all_legs_value", "all_legs_relative_action"}
    }


def actionable_minimum_odds(
    item: dict[str, Any], equity_at_cost: int, unit: int
) -> float | None:
    if item["kind"] != "single":
        return None
    ev_floor = item["minimum_acceptable_odds"]
    unit_floor = minimum_odds_for_full_kelly_unit(
        item["probability_low"], equity_at_cost, unit
    )
    if ev_floor is None or unit_floor is None:
        return None
    return ceil_odds_tick(max(ev_floor, unit_floor))


def rejection(
    item: dict[str, Any],
    reasons: list[str],
    equity_at_cost: int,
    unit: int,
) -> dict[str, Any]:
    return {
        "id": item["id"],
        "kind": item["kind"],
        "origin": item["origin"],
        "label": item["label"],
        "odds": round(item["odds"], 4),
        "break_even_probability": round(item["break_even_probability"], 6),
        "market_ev": round(item["market_ev"], 6),
        "robust_ev": round(item["robust_ev"], 6),
        "betman_normalized_probability": round(
            item["betman_normalized_probability"], 6
        ),
        "betman_market_payout_rate": round(item["betman_market_payout_rate"], 6),
        "relative_ratio_mid": round(item["relative_ratio_mid"], 6),
        "relative_ratio_low": round(item["relative_ratio_low"], 6),
        "ev_price_floor": item["minimum_acceptable_odds"],
        "minimum_acceptable_odds": actionable_minimum_odds(item, equity_at_cost, unit),
        "reasons": sorted(set(reasons)),
    }


def value_reasons(item: dict[str, Any]) -> list[str]:
    reasons = list(item["evidence_reasons"]) + list(item["purchase_reasons"])
    if item["market_ev"] < POLICY["price"]["min_market_ev"]:
        reasons.append("market_ev")
    if item["robust_ev"] < POLICY["price"]["min_robust_ev"]:
        reasons.append("robust_ev")
    if item["kind"] == "parlay":
        if item["leg_count"] > POLICY["parlay"]["max_value_legs"]:
            reasons.append("too_many_value_legs")
        if not item["all_legs_value"]:
            reasons.append("leg_not_value")
    return sorted(set(reasons))


def is_relative_action(item: dict[str, Any]) -> bool:
    if item["market_ev"] < POLICY["action"]["relative_min_market_ev"]:
        return False
    if item["relative_ratio_mid"] < POLICY["action"]["relative_min_ratio_mid"]:
        return False
    if item["relative_ratio_low"] < POLICY["action"]["relative_min_ratio_low"]:
        return False
    if item["kind"] == "parlay":
        return (
            item["leg_count"] <= POLICY["parlay"]["max_relative_action_legs"]
            and item["all_legs_relative_action"]
        )
    return True


def allocation_value_reasons(
    item: dict[str, Any], equity_at_cost: int, unit: int
) -> list[str]:
    reasons = value_reasons(item)
    if item["kind"] == "parlay" and any(
        equity_at_cost * leg_kelly < unit for leg_kelly in item["leg_full_kelly"]
    ):
        reasons.append("leg_unbettable_min_unit")
    return sorted(set(reasons))


def pick_payload(
    item: dict[str, Any], stake: int, bankroll: int, unit_override: bool
) -> dict[str, Any]:
    fraction = stake / bankroll
    if fraction >= 1.0:
        log_growth = -math.inf
    else:
        log_growth = item["probability_low"] * math.log(
            1.0 + fraction * (item["odds"] - 1.0)
        ) + (1.0 - item["probability_low"]) * math.log(1.0 - fraction)
    payload = rounded_metrics(item)
    payload["ev_price_floor"] = payload.pop("minimum_acceptable_odds")
    payload["minimum_acceptable_odds"] = actionable_minimum_odds(
        item, bankroll, DEFAULT_UNIT
    )
    payload.update(
        {
            "stake": stake,
            "maximum_loss": stake,
            "minimum_unit_override": unit_override,
            "estimated_market_profit": round(stake * item["market_ev"], 2),
            "estimated_robust_profit": round(stake * item["robust_ev"], 2),
            "estimated_log_growth": round(log_growth, 8),
        }
    )
    return payload


def allocate_value(
    instruments: list[dict[str, Any]],
    bankroll: int,
    unit: int,
    purchase_ceiling: int,
    open_exposure: dict[str, Any],
) -> dict[str, Any]:
    existing_total = open_exposure["total_stake"]
    existing_tail = open_exposure["tail_stake"]
    equity_at_cost = bankroll + existing_total
    total_cap = min(
        floor_unit(purchase_ceiling, unit),
        max(
            0,
            floor_unit(equity_at_cost * POLICY["risk"]["max_total_fraction"], unit)
            - existing_total,
        ),
    )
    cluster_cap = floor_unit(
        equity_at_cost * POLICY["risk"]["max_cluster_fraction"], unit
    )
    tail_cap = floor_unit(equity_at_cost * POLICY["risk"]["max_tail_fraction"], unit)
    exposure: dict[str, int] = dict(open_exposure["by_event"])
    cluster_exposure: dict[str, int] = dict(open_exposure["by_cluster"])
    new_tail_stake = 0
    picks: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    qualified: list[dict[str, Any]] = []
    for item in instruments:
        reasons = allocation_value_reasons(item, equity_at_cost, unit)
        if reasons:
            rejected.append(rejection(item, reasons, equity_at_cost, unit))
        else:
            qualified.append(item)
    qualified.sort(
        key=lambda item: (
            -item["robust_ev"],
            -item["market_ev"],
            -item["odds"],
            item["kind"],
            item["economic_key"],
        )
    )

    remaining = total_cap
    for item in qualified:
        if len(picks) >= POLICY["risk"]["max_value_picks"]:
            rejected.append(rejection(item, ["max_value_picks"], equity_at_cost, unit))
            continue
        full_kelly_amount = equity_at_cost * item["full_kelly"]
        if full_kelly_amount < unit:
            rejected.append(
                rejection(item, ["unbettable_min_unit"], equity_at_cost, unit)
            )
            continue
        half_target = floor_unit(
            full_kelly_amount * POLICY["risk"]["kelly_multiplier"], unit
        )
        unit_override = half_target < unit
        target = unit if unit_override else half_target
        is_tail = item["odds"] >= POLICY["risk"]["tail_odds"]
        tail_room = tail_cap - existing_tail - new_tail_stake if is_tail else math.inf
        cluster_room = min(
            cluster_cap - cluster_exposure.get(cluster_id, 0)
            for cluster_id in item["correlation_cluster_ids"]
        )
        stake = min(target, cluster_room, tail_room, remaining)
        stake = floor_unit(stake, unit)
        if stake < unit:
            cap_reasons = []
            if cluster_room < unit:
                cap_reasons.append("cluster_open_exposure_cap")
            if remaining < unit:
                cap_reasons.append("total_open_exposure_cap")
            if tail_room < unit:
                cap_reasons.append("tail_open_exposure_cap")
            rejected.append(
                rejection(
                    item,
                    cap_reasons or ["exposure_cap"],
                    equity_at_cost,
                    unit,
                )
            )
            continue
        pick = pick_payload(item, stake, equity_at_cost, unit_override)
        picks.append(pick)
        remaining -= stake
        if is_tail:
            new_tail_stake += stake
        for event_id in item["event_ids"]:
            exposure[event_id] = exposure.get(event_id, 0) + stake
        for cluster_id in item["correlation_cluster_ids"]:
            cluster_exposure[cluster_id] = cluster_exposure.get(cluster_id, 0) + stake

    return {
        "decision": "BET_PRICE_VALUE" if picks else "PASS",
        "equity_at_cost": equity_at_cost,
        "existing_open_stake": existing_total,
        "existing_open_tail_stake": existing_tail,
        "total_stake": sum(item["stake"] for item in picks),
        "reserve": bankroll - sum(item["stake"] for item in picks),
        "maximum_loss": sum(item["stake"] for item in picks),
        "event_exposure_after": exposure,
        "cluster_exposure_after": cluster_exposure,
        "total_open_exposure_after": existing_total
        + sum(item["stake"] for item in picks),
        "tail_open_exposure_after": existing_tail + new_tail_stake,
        "picks": picks,
        "rejected": rejected,
        "accounted_instruments": len(picks) + len(rejected),
    }


def allocate_action(
    instruments: list[dict[str, Any]],
    bankroll: int,
    unit: int,
    purchase_ceiling: int,
    action_fallback_available: bool,
    open_exposure: dict[str, Any],
) -> dict[str, Any]:
    existing_total = open_exposure["total_stake"]
    existing_tail = open_exposure["tail_stake"]
    equity_at_cost = bankroll + existing_total
    cluster_cap = floor_unit(
        equity_at_cost * POLICY["risk"]["max_cluster_fraction"], unit
    )
    total_cap = floor_unit(equity_at_cost * POLICY["risk"]["max_total_fraction"], unit)
    tail_cap = floor_unit(equity_at_cost * POLICY["risk"]["max_tail_fraction"], unit)
    exposure = dict(open_exposure["by_event"])
    cluster_exposure = dict(open_exposure["by_cluster"])

    def pass_result(reason: str, **extra: Any) -> dict[str, Any]:
        result = {
            "decision": "PASS",
            "reason": reason,
            "equity_at_cost": equity_at_cost,
            "existing_open_stake": existing_total,
            "existing_open_tail_stake": existing_tail,
            "total_stake": 0,
            "reserve": bankroll,
            "event_exposure_after": exposure,
            "cluster_exposure_after": cluster_exposure,
            "total_open_exposure_after": existing_total,
            "tail_open_exposure_after": existing_tail,
            "picks": [],
        }
        result.update(extra)
        return result

    if not action_fallback_available:
        return pass_result("action_unavailable_this_cycle")
    stake = unit * POLICY["action"]["stake_units"]
    if min(bankroll, purchase_ceiling) < stake:
        return pass_result("below_action_unit")
    if existing_total + stake > total_cap:
        return pass_result("total_open_exposure_cap")

    def risk_reason(item: dict[str, Any]) -> str | None:
        if not all(
            cluster_exposure.get(cluster_id, 0) + stake <= cluster_cap
            for cluster_id in item["correlation_cluster_ids"]
        ):
            return "cluster_open_exposure_cap"
        if (
            item["odds"] >= POLICY["risk"]["tail_odds"]
            and existing_tail + stake > tail_cap
        ):
            return "tail_open_exposure_cap"
        return None

    eligible = [
        item
        for item in instruments
        if not item["evidence_reasons"]
        and not item["purchase_reasons"]
        and (
            item["market_ev"] >= POLICY["action"]["min_market_ev"]
            or is_relative_action(item)
        )
        and risk_reason(item) is None
    ]
    eligible.sort(
        key=lambda item: (
            0 if is_relative_action(item) else 1,
            0 if item["kind"] == "single" else 1,
            -item["relative_ratio_low"],
            -item["market_ev"],
            item["economic_key"],
        )
    )
    if not eligible:
        purchasable_evidence = [
            item
            for item in instruments
            if not item["evidence_reasons"] and not item["purchase_reasons"]
        ]
        if not purchasable_evidence:
            return pass_result("no_purchasable_action")
        risk_eligible = [
            item for item in purchasable_evidence if risk_reason(item) is None
        ]
        best = sorted(
            risk_eligible,
            key=lambda item: (
                -item["market_ev"],
                -item["odds"],
                item["economic_key"],
            ),
        )
        if not risk_eligible:
            blocked_reasons = {risk_reason(item) for item in purchasable_evidence}
            return pass_result(sorted(blocked_reasons - {None})[0])
        return pass_result(
            "action_loss_floor",
            entertainment_override_candidate=(
                rejection(
                    best[0],
                    ["below_action_loss_floor"],
                    equity_at_cost,
                    unit,
                )
                if best
                else None
            ),
        )
    selected = eligible[0]
    relative_selected = is_relative_action(selected)
    pick = pick_payload(selected, stake, equity_at_cost, False)
    pick["value_claim"] = False
    pick["positive_ev_claim"] = False
    pick["relative_value_claim"] = relative_selected
    pick["action_basis"] = (
        "betman_relative_value" if relative_selected else "absolute_loss_floor"
    )
    pick["action_warning"] = (
        "Best Betman-relative ticket among eligible singles; normalized relative "
        "value does not remove Betman's margin and absolute market EV may be negative."
        if relative_selected
        else "Highest-ranked evaluated action ticket; market EV may be negative "
        "and this is not a profit claim."
    )
    for event_id in selected["event_ids"]:
        exposure[event_id] = exposure.get(event_id, 0) + stake
    for cluster_id in selected["correlation_cluster_ids"]:
        cluster_exposure[cluster_id] = cluster_exposure.get(cluster_id, 0) + stake
    tail_after = existing_tail
    if selected["odds"] >= POLICY["risk"]["tail_odds"]:
        tail_after += stake
    return {
        "decision": "BET_ACTION",
        "reason": (
            "betman_relative_one_unit"
            if relative_selected
            else "preconsented_one_unit_fallback"
        ),
        "equity_at_cost": equity_at_cost,
        "existing_open_stake": existing_total,
        "existing_open_tail_stake": existing_tail,
        "total_stake": stake,
        "reserve": bankroll - stake,
        "maximum_loss": stake,
        "event_exposure_after": exposure,
        "cluster_exposure_after": cluster_exposure,
        "total_open_exposure_after": existing_total + stake,
        "tail_open_exposure_after": tail_after,
        "picks": [pick],
    }


def best_price_trigger(
    instruments: list[dict[str, Any]],
    bankroll: int,
    unit: int,
    purchase_ceiling: int,
    open_exposure: dict[str, Any],
) -> dict[str, Any] | None:
    equity_at_cost = bankroll + open_exposure["total_stake"]
    total_cap = floor_unit(equity_at_cost * POLICY["risk"]["max_total_fraction"], unit)
    available_total_room = min(
        floor_unit(purchase_ceiling, unit),
        max(0, total_cap - open_exposure["total_stake"]),
    )
    if available_total_room < unit:
        return None
    cluster_cap = floor_unit(
        equity_at_cost * POLICY["risk"]["max_cluster_fraction"], unit
    )
    tail_cap = floor_unit(equity_at_cost * POLICY["risk"]["max_tail_fraction"], unit)
    candidates: list[tuple[dict[str, Any], float]] = []
    for item in instruments:
        if item["kind"] != "single":
            continue
        permanent_reasons = set(
            allocation_value_reasons(item, equity_at_cost, unit)
        ) - {"market_ev", "robust_ev"}
        if permanent_reasons:
            continue
        if any(
            cluster_cap - open_exposure["by_cluster"].get(cluster_id, 0) < unit
            for cluster_id in item["correlation_cluster_ids"]
        ):
            continue
        price_floor = item["minimum_acceptable_odds"]
        kelly_floor = minimum_odds_for_full_kelly_unit(
            item["probability_low"], equity_at_cost, unit
        )
        if not isinstance(price_floor, (int, float)) or kelly_floor is None:
            continue
        trigger = ceil_odds_tick(max(price_floor, kelly_floor))
        if (
            trigger >= POLICY["risk"]["tail_odds"]
            and open_exposure["tail_stake"] + unit > tail_cap
        ):
            continue
        candidates.append((item, trigger))
    if not candidates:
        return None
    selected, trigger = min(
        candidates,
        key=lambda pair: (
            pair[1] / pair[0]["odds"],
            pair[0]["economic_key"],
        ),
    )
    return {
        "id": selected["id"],
        "label": selected["label"],
        "current_odds": round(selected["odds"], 4),
        "minimum_acceptable_odds": trigger,
        "close_time_kst": selected["close_time_kst"],
    }


def load_and_evaluate(
    path: Path, now: datetime
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    str,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input must be an object")
    validate_fields(payload, ROOT_FIELDS, ROOT_FIELDS, "input")
    if payload["scope"] not in SCOPES:
        raise ValueError("invalid scope")
    snapshot = kst_datetime(payload["snapshot_kst"], "snapshot_kst")
    reason = live_age_reason(age_minutes(now, snapshot), "snapshot")
    if reason:
        raise ValueError(reason)
    open_exposure = validate_open_exposure(payload["open_exposure"], now)
    weekly_cycle = validate_weekly_cycle(payload["weekly_cycle"], now, open_exposure)
    rows, board_hash, _, parlay_groups = validate_board(
        payload["board"], now, payload["scope"]
    )
    candidate_input = payload["candidates"]
    if not isinstance(candidate_input, list):
        raise ValueError("candidates must be a list")
    if any(not isinstance(item, dict) for item in candidate_input):
        raise ValueError("every candidate must be an object")
    for item in candidate_input:
        validate_fields(item, CANDIDATE_FIELDS, CANDIDATE_FIELDS, "candidate")
    candidate_ids = [
        nonempty_string(item.get("id"), "candidate id") for item in candidate_input
    ]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate IDs must be unique")
    candidate_economic_keys = [
        (
            nonempty_string(item["board_row_id"], "candidate board_row_id"),
            nonempty_string(item["selection_key"], "candidate selection_key"),
        )
        for item in candidate_input
    ]
    if len(candidate_economic_keys) != len(set(candidate_economic_keys)):
        raise ValueError(
            "duplicate board-row selection: merge shared user/assistant picks "
            "into one candidate with origin=both"
        )
    if payload["scope"] == "comparison":
        origins = {item["origin"] for item in candidate_input}
        covers_user = bool(origins & {"user", "both"})
        covers_assistant = bool(origins & {"assistant", "both"})
        if not (covers_user and covers_assistant):
            raise ValueError(
                "comparison scope requires both user and assistant provenance; "
                "use origin=both for one shared selection"
            )
    shortlist_selection_keys = {
        (row_id, selection_key)
        for row_id, row in rows.items()
        for selection_key, disposition in row["selection_dispositions"].items()
        if disposition == "shortlist"
    }
    if set(candidate_economic_keys) != shortlist_selection_keys:
        raise ValueError(
            "shortlisted board selections and candidates must match exactly"
        )
    if payload["scope"] == "comparison" and len(candidate_input) >= 2:
        observed_times = [
            kst_datetime(
                item["betman_odds_observed_at_kst"],
                "comparison betman_odds_observed_at_kst",
            )
            for item in candidate_input
        ]
        comparison_skew = (
            max(observed_times) - min(observed_times)
        ).total_seconds() / 60.0
        if comparison_skew > POLICY["price"]["max_comparison_skew_minutes"]:
            raise ValueError("comparison_price_observation_skew")
    evaluated_candidates = {
        item["id"]: evaluate_candidate(item, rows[item["board_row_id"]], now)
        for item in candidate_input
    }
    if payload["scope"] == "comparison" and len(evaluated_candidates) >= 2:
        quote_times = [
            kst_datetime(quote_time, "comparison quote observed_at_kst")
            for item in evaluated_candidates.values()
            for quote_time in item["quote_observed_at_kst"]
        ]
        quote_skew = (max(quote_times) - min(quote_times)).total_seconds() / 60.0
        if quote_skew > POLICY["price"]["max_comparison_skew_minutes"]:
            raise ValueError("comparison_market_observation_skew")
    validate_shared_board_row_evidence(candidate_input)
    parlay_input = payload["parlays"]
    if not isinstance(parlay_input, list):
        raise ValueError("parlays must be a list")
    evaluated_parlays = [
        evaluate_parlay(item, evaluated_candidates, parlay_groups, now)
        for item in parlay_input
    ]
    parlay_ids = [item["id"] for item in evaluated_parlays]
    if len(parlay_ids) != len(set(parlay_ids)) or set(parlay_ids) & set(candidate_ids):
        raise ValueError("instrument IDs must be unique")
    instruments = list(evaluated_candidates.values()) + evaluated_parlays
    economic_keys = [item["economic_key"] for item in instruments]
    if len(economic_keys) != len(set(economic_keys)):
        raise ValueError(
            "duplicate economic instrument: merge shared proposals with origin=both"
        )
    open_selection_count = sum(
        len(row["outcome_labels"]) for row in rows.values() if row["status"] == "open"
    )
    evaluated_open_selection_count = sum(
        1 for item in candidate_input if rows[item["board_row_id"]]["status"] == "open"
    )
    unevaluated_selection_count = max(
        0, open_selection_count - evaluated_open_selection_count
    )
    coverage_fraction = (
        1.0
        if open_selection_count == 0
        else evaluated_open_selection_count / open_selection_count
    )
    expected_value_parlays: set[tuple[str, str, str]] = set()
    evaluated_candidate_items = sorted(
        evaluated_candidates.values(), key=lambda item: item["id"]
    )
    for left_index, left in enumerate(evaluated_candidate_items):
        for right in evaluated_candidate_items[left_index + 1 :]:
            if set(left["event_ids"]) & set(right["event_ids"]):
                continue
            if set(left["correlation_cluster_ids"]) & set(
                right["correlation_cluster_ids"]
            ):
                continue
            shared_groups = set(left["parlay_group_ids"]) & set(
                right["parlay_group_ids"]
            )
            for group_id in shared_groups:
                group = parlay_groups[group_id]
                if group["min_legs"] <= 2 <= group["max_legs"]:
                    first_id, second_id = sorted((left["id"], right["id"]))
                    expected_value_parlays.add((first_id, second_id, group_id))
    evaluated_value_parlays = {
        (*sorted(item["leg_ids"]), item["parlay_group_id"])
        for item in evaluated_parlays
        if item["leg_count"] == 2
    }
    missing_value_parlays = expected_value_parlays - evaluated_value_parlays
    value_parlay_search_complete = not missing_value_parlays
    if (
        payload["scope"] == "board-wide"
        and unevaluated_selection_count == 0
        and value_parlay_search_complete
    ):
        claim_scope = "full-board"
    elif payload["scope"] == "board-wide" and unevaluated_selection_count == 0:
        claim_scope = "selection-only"
    else:
        claim_scope = "evaluated-only"
    coverage = {
        "open_selection_count": open_selection_count,
        "evaluated_open_selection_count": evaluated_open_selection_count,
        "unevaluated_selection_count": unevaluated_selection_count,
        "evaluated_fraction": round(coverage_fraction, 6),
        "expected_value_parlay_count": len(expected_value_parlays),
        "evaluated_value_parlay_count": len(
            expected_value_parlays & evaluated_value_parlays
        ),
        "missing_value_parlay_count": len(missing_value_parlays),
        "value_parlay_search_complete": value_parlay_search_complete,
        "claim_scope": claim_scope,
    }
    return (
        payload,
        instruments,
        board_hash,
        open_exposure,
        weekly_cycle,
        coverage,
    )


def run(args: argparse.Namespace, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(KST)
    if now.utcoffset() != timedelta(hours=9):
        raise ValueError("trusted runtime now must use KST")
    bankroll = nonnegative_integer(args.bankroll, "bankroll")
    unit = nonnegative_integer(args.unit, "unit")
    if unit != DEFAULT_UNIT:
        raise ValueError("unit must be the verified KRW 1,000 ticket unit")
    if args.mode not in {"value", "portfolio"}:
        raise ValueError("mode must be value or portfolio")
    target_stake = None
    if args.target_stake is not None:
        target_stake = nonnegative_integer(args.target_stake, "target_stake")
        if target_stake <= 0:
            raise ValueError("target_stake must be positive")
    if bankroll < unit:
        return {
            "decision": "WAIT_FOR_DEPOSIT",
            "policy_version": POLICY_VERSION,
            "as_of_kst": now.isoformat(),
            "bankroll": bankroll,
            "unit": unit,
            "total_stake": 0,
            "reserve": bankroll,
        }
    (
        payload,
        instruments,
        board_hash,
        open_exposure,
        weekly_cycle,
        coverage,
    ) = load_and_evaluate(args.input, now)
    purchase_ceiling = min(
        bankroll, target_stake if target_stake is not None else bankroll
    )
    value = allocate_value(instruments, bankroll, unit, purchase_ceiling, open_exposure)
    action = None
    decision = value["decision"]
    if not value["picks"] and args.mode == "portfolio":
        action = allocate_action(
            instruments,
            bankroll,
            unit,
            purchase_ceiling,
            weekly_cycle["action_fallback_available"],
            open_exposure,
        )
        decision = action["decision"]
        if action["picks"]:
            decision_material = {
                "policy_version": POLICY_VERSION,
                "weekly_cycle_id": weekly_cycle["id"],
                "ledger_snapshot_sha256": weekly_cycle["ledger_snapshot_sha256"],
                "board_snapshot_sha256": board_hash,
                "economic_key": action["picks"][0]["economic_key"],
                "odds": action["picks"][0]["odds"],
            }
            action["decision_id"] = (
                f"action-{weekly_cycle['id']}-"
                + hashlib.sha256(
                    json.dumps(
                        decision_material,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()[:20]
            )
            action["decision_material"] = decision_material
    return {
        "decision": decision,
        "mode": args.mode,
        "policy_version": POLICY_VERSION,
        "as_of_kst": now.isoformat(),
        "input_snapshot_kst": payload["snapshot_kst"],
        "scope": payload["scope"],
        "board_url": payload["board"]["source_url"],
        "board_snapshot_sha256": board_hash,
        "coverage": coverage,
        "bankroll": bankroll,
        "equity_at_cost": bankroll + open_exposure["total_stake"],
        "open_exposure": open_exposure,
        "unit": unit,
        "purchase_ceiling": purchase_ceiling,
        "weekly_cycle": weekly_cycle,
        "value": value,
        "action": action,
        "best_price_trigger": (
            None
            if value["picks"] or (action is not None and action["picks"])
            else best_price_trigger(
                instruments,
                bankroll,
                unit,
                purchase_ceiling,
                open_exposure,
            )
        ),
        "policy": POLICY,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("--bankroll", type=int)
    parser.add_argument("--unit", type=int, default=DEFAULT_UNIT)
    parser.add_argument("--mode", choices=("value", "portfolio"), default="value")
    parser.add_argument("--target-stake", type=int)
    parser.add_argument("--schema", action="store_true")
    args = parser.parse_args()
    if not args.schema and (args.input is None or args.bankroll is None):
        parser.error("input and --bankroll are required unless --schema is used")
    return args


if __name__ == "__main__":
    try:
        parsed = parse_args()
        result = INPUT_SCHEMA if parsed.schema else run(parsed)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: {exc}") from exc
