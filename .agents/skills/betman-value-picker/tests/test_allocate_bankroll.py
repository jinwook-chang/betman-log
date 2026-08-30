import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).parents[1] / "scripts" / "allocate_bankroll.py"
SPEC = importlib.util.spec_from_file_location("allocate_bankroll", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

NOW = datetime.fromisoformat("2026-08-02T12:00:00+09:00")
SNAPSHOT = "2026-08-02T11:55:00+09:00"


def quote_set(
    selected_probabilities=(0.57, 0.57, 0.57),
    *,
    event_id="event-a",
    competition_id=None,
    participants=None,
    scheduled_start="2026-08-02T13:00:00+09:00",
    sport="football",
    market_type="1x2",
    line=None,
    settlement_rule="regulation_90",
    selection_key="home",
    observed=SNAPSHOT,
    shared_domain=False,
):
    competition_id = competition_id or f"competition-{event_id}"
    participants = participants or [f"{event_id}-home", f"{event_id}-away"]
    labels = {
        "1x2": ["home", "draw", "away"],
        "moneyline": ["home", "away"],
        "total": ["under", "over"],
    }[market_type]
    selected_index = labels.index(selection_key)
    result = []
    for index, selected_probability in enumerate(selected_probabilities):
        remaining = 1.0 - selected_probability
        probabilities = [remaining / (len(labels) - 1) for _ in labels]
        probabilities[selected_index] = selected_probability
        overround = 1.05
        odds = [1.0 / (value * overround) for value in probabilities]
        domain = (
            f"feed-{index}.same-book.example"
            if shared_domain
            else f"provider-{index}.example"
        )
        result.append(
            {
                "provider_id": f"book_{index}",
                "provider_group": f"group_{index}",
                "provider_event_id": f"provider-{index}-{event_id}",
                "source_url": f"https://{domain}/market",
                "observed_at_kst": observed,
                "event_id": event_id,
                "competition_id": competition_id,
                "participants": participants,
                "scheduled_start_kst": scheduled_start,
                "sport": sport,
                "market_type": market_type,
                "line": line,
                "settlement_rule": settlement_rule,
                "outcome_labels": labels,
                "outcome_odds": odds,
                "selection_index": selected_index,
            }
        )
    return result


def reselect_quotes(quotes, selection_key):
    result = json.loads(json.dumps(quotes))
    for quote in result:
        quote["selection_index"] = quote["outcome_labels"].index(selection_key)
    return result


def candidate(candidate_id="a", event_id="event-a", **overrides):
    value = {
        "id": candidate_id,
        "board_row_id": candidate_id,
        "event_id": event_id,
        "origin": "assistant",
        "label": candidate_id,
        "sport": "football",
        "market_type": "1x2",
        "line": None,
        "settlement_rule": "regulation_90",
        "selection_key": "home",
        "betman_odds": 2.12,
        "betman_url": "https://m.betman.co.kr/game",
        "betman_odds_observed_at_kst": SNAPSHOT,
        "close_time_kst": "2026-08-02T14:00:00+09:00",
        "status": "open",
        "status_observed_at_kst": SNAPSHOT,
        "status_source_url": "https://league.example/status",
    }
    value.update(overrides)
    if "competition_id" not in overrides:
        value["competition_id"] = f"competition-{value['event_id']}"
    if "participants" not in overrides:
        value["participants"] = [
            f"{value['event_id']}-home",
            f"{value['event_id']}-away",
        ]
    if "scheduled_start_kst" not in overrides:
        value["scheduled_start_kst"] = "2026-08-02T13:00:00+09:00"
    if "correlation_cluster_ids" not in overrides:
        value["correlation_cluster_ids"] = [value["event_id"]]
    if "market_quotes" not in overrides:
        value["market_quotes"] = quote_set(
            event_id=value["event_id"],
            competition_id=value["competition_id"],
            participants=value["participants"],
            scheduled_start=value["scheduled_start_kst"],
            sport=value["sport"],
            market_type=value["market_type"],
            line=value["line"],
            settlement_rule=value["settlement_rule"],
            selection_key=value["selection_key"],
        )
    return value


def row_for(item, disposition="shortlist"):
    labels = {
        "1x2": ["home", "draw", "away"],
        "moneyline": ["home", "away"],
        "total": ["under", "over"],
    }[item["market_type"]]
    odds = [2.50 for _ in labels]
    odds[labels.index(item["selection_key"])] = item["betman_odds"]
    return {
        "row_id": item["board_row_id"],
        "section_id": "main",
        "event_id": item["event_id"],
        "competition_id": item["competition_id"],
        "participants": item["participants"],
        "scheduled_start_kst": item["scheduled_start_kst"],
        "sport": item["sport"],
        "league": "Test League",
        "market_type": item["market_type"],
        "line": item["line"],
        "settlement_rule": item["settlement_rule"],
        "status": item["status"],
        "close_time_kst": item["close_time_kst"],
        "outcome_labels": labels,
        "outcome_odds": odds,
        "odds_observed_at_kst": item["betman_odds_observed_at_kst"],
        "purchase_modes": ["single", "parlay"],
        "parlay_group_ids": ["default"],
        "selection_dispositions": {
            label: ("shortlist" if label == item["selection_key"] else "dominated")
            for label in labels
        },
        "selection_disposition_reasons": {
            label: "test fixture reason" for label in labels
        },
        "disposition": disposition,
        "disposition_reason": "test fixture",
    }


def payload(
    candidates,
    parlays=None,
    extra_rows=None,
    snapshot=SNAPSHOT,
    open_exposure=None,
    row_overrides=None,
    action_available=True,
    scope="candidate-only",
):
    rows_by_id = {}
    for item in candidates:
        row = rows_by_id.setdefault(item["board_row_id"], row_for(item))
        selection_index = row["outcome_labels"].index(item["selection_key"])
        row["outcome_odds"][selection_index] = item["betman_odds"]
        row["selection_dispositions"][item["selection_key"]] = "shortlist"
    rows = list(rows_by_id.values())
    for row in rows:
        row.update((row_overrides or {}).get(row["row_id"], {}))
    rows.extend(extra_rows or [])
    section_rows = [row for row in rows if row["section_id"] == "main"]
    ledger_entries = [
        {
            "id": "deposit-2026-07-27",
            "kind": "deposit",
            "occurred_at_kst": "2026-07-27T09:00:00+09:00",
            "amount": 10_000,
            "decision_id": None,
            "source": "records/deposit-test.md",
        }
    ]
    if not action_available:
        ledger_entries.append(
            {
                "id": "action-used-this-cycle",
                "kind": "action_fallback",
                "occurred_at_kst": "2026-08-01T20:00:00+09:00",
                "amount": 1_000,
                "decision_id": "action-2026-07-27-aaaaaaaaaaaaaaaaaaaa",
                "source": "records/action-test.md",
            }
        )
    return {
        "snapshot_kst": snapshot,
        "scope": scope,
        "weekly_cycle": {
            "id": "2026-07-27",
            "cycle_start_kst": "2026-07-27T00:00:00+09:00",
            "cycle_end_kst": "2026-08-03T00:00:00+09:00",
            "ledger_path": "set-by-test-runner",
            "ledger_entries": ledger_entries,
            "ledger_snapshot_sha256": "set-by-test-runner",
            "verified_at_kst": snapshot,
        },
        "open_exposure": open_exposure or {"tickets": []},
        "board": {
            "source_url": "https://m.betman.co.kr/board",
            "round_id": "test-round",
            "captured_at_kst": snapshot,
            "window_start_kst": "2026-08-02T00:00:00+09:00",
            "window_end_kst": "2026-08-02T23:59:59+09:00",
            "snapshot_sha256": MODULE.canonical_rows_sha256(rows),
            "official_event_count": len({row["event_id"] for row in rows}),
            "official_market_row_count": len(rows),
            "sections": [
                {
                    "id": "main",
                    "source_url": "https://m.betman.co.kr/board?section=main",
                    "captured_at_kst": snapshot,
                    "official_market_row_count": len(section_rows),
                    "rows_sha256": MODULE.canonical_rows_sha256(section_rows),
                }
            ],
            "parlay_groups": [
                {
                    "id": "default",
                    "min_legs": 2,
                    "max_legs": 20,
                    "odds_rule": "exact_product",
                    "odds_tick": None,
                    "max_combined_odds": None,
                }
            ],
            "rows": rows,
        },
        "candidates": candidates,
        "parlays": parlays or [],
    }


def parlay(parlay_id, leg_ids, **overrides):
    value = {
        "id": parlay_id,
        "origin": "assistant",
        "label": parlay_id,
        "leg_ids": leg_ids,
        "parlay_group_id": "default",
        "betman_odds": 4.4944,
        "betman_url": "https://m.betman.co.kr/game",
        "betman_odds_observed_at_kst": SNAPSHOT,
    }
    value.update(overrides)
    return value


class AllocationTests(unittest.TestCase):
    def run_payload(self, data, **overrides):
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            if "weekly_cycle" in data:
                ledger_file_entries = data.pop(
                    "_test_ledger_file_entries",
                    data["weekly_cycle"]["ledger_entries"],
                )
                ledger_document = {
                    "version": 1,
                    "entries": ledger_file_entries,
                }
                ledger_bytes = json.dumps(
                    ledger_document, ensure_ascii=False, indent=2
                ).encode("utf-8")
                ledger_path.write_bytes(ledger_bytes)
                data["weekly_cycle"]["ledger_path"] = str(ledger_path)
                data["weekly_cycle"]["ledger_snapshot_sha256"] = hashlib.sha256(
                    ledger_bytes
                ).hexdigest()
            input_path = Path(directory) / "input.json"
            input_path.write_text(json.dumps(data), encoding="utf-8")
            args = SimpleNamespace(
                input=input_path,
                bankroll=10_000,
                unit=MODULE.DEFAULT_UNIT,
                mode="value",
                target_stake=None,
            )
            for key, value in overrides.items():
                setattr(args, key, value)
            canonical_ledger = MODULE.WORKSPACE_LEDGER_PATH
            MODULE.WORKSPACE_LEDGER_PATH = ledger_path
            try:
                return MODULE.run(args, now=NOW)
            finally:
                MODULE.WORKSPACE_LEDGER_PATH = canonical_ledger

    def test_default_purchase_unit_is_one_thousand_won(self):
        self.assertEqual(MODULE.DEFAULT_UNIT, 1_000)

    def test_red_bulls_style_price_gap_is_selected(self):
        item = candidate(origin="user", label="NY Red Bulls win", betman_odds=2.12)
        result = self.run_payload(payload([item]))
        self.assertEqual(result["decision"], "BET_PRICE_VALUE")
        pick = result["value"]["picks"][0]
        self.assertEqual(pick["id"], "a")
        self.assertEqual(pick["origin"], "user")
        self.assertGreater(pick["robust_ev"], 0)
        self.assertEqual(pick["stake"], 1_000)
        self.assertTrue(pick["minimum_unit_override"])

    def test_user_and_assistant_candidates_use_identical_math(self):
        first = candidate("user", "event-user", origin="user")
        second = candidate(
            "assistant",
            "event-assistant",
            origin="assistant",
        )
        result = self.run_payload(payload([first, second], scope="comparison"))
        picks = {item["id"]: item for item in result["value"]["picks"]}
        self.assertEqual(
            picks["user"]["market_probability"],
            picks["assistant"]["market_probability"],
        )
        self.assertEqual(picks["user"]["robust_ev"], picks["assistant"]["robust_ev"])

    def test_comparison_rejects_one_sided_provenance(self):
        item = candidate(origin="assistant")
        with self.assertRaisesRegex(ValueError, "both user and assistant provenance"):
            self.run_payload(payload([item], scope="comparison"))

    def test_shared_pick_is_one_origin_both_instrument(self):
        item = candidate(origin="both", label="shared Red Bulls win")
        result = self.run_payload(payload([item], scope="comparison"))
        self.assertEqual(len(result["value"]["picks"]), 1)
        self.assertEqual(result["value"]["picks"][0]["origin"], "both")

    def test_duplicate_shared_pick_must_be_merged_before_allocation(self):
        first = candidate("user", board_row_id="shared", origin="user")
        second = candidate("assistant", board_row_id="shared", origin="assistant")
        with self.assertRaisesRegex(ValueError, "merge shared user/assistant picks"):
            self.run_payload(payload([first, second], scope="comparison"))

    def test_same_board_market_allows_opposing_user_and_assistant_sides(self):
        shared_quotes = quote_set(
            (0.57, 0.57), event_id="event-shared", selection_key="home"
        )
        home = candidate(
            "home",
            "event-shared",
            board_row_id="shared",
            origin="user",
            selection_key="home",
            market_quotes=shared_quotes,
        )
        away = candidate(
            "away",
            "event-shared",
            board_row_id="shared",
            origin="assistant",
            selection_key="away",
            market_quotes=reselect_quotes(shared_quotes, "away"),
        )
        result = self.run_payload(payload([home, away], scope="comparison"))
        accounted = {item["id"] for item in result["value"]["picks"]} | {
            item["id"] for item in result["value"]["rejected"]
        }
        self.assertEqual(accounted, {"home", "away"})

    def test_opposing_sides_cannot_use_inconsistent_market_evidence(self):
        home = candidate("home", "event-shared", board_row_id="shared", origin="user")
        away = candidate(
            "away",
            "event-shared",
            board_row_id="shared",
            origin="assistant",
            selection_key="away",
            market_quotes=quote_set(
                (0.30, 0.30), event_id="event-shared", selection_key="away"
            ),
        )
        with self.assertRaisesRegex(ValueError, "share complete-market quotes"):
            self.run_payload(payload([home, away], scope="comparison"))

    def test_snapshot_is_checked_against_runtime_clock(self):
        stale = "2026-08-02T10:00:00+09:00"
        item = candidate(
            betman_odds_observed_at_kst=stale,
            status_observed_at_kst=stale,
            market_quotes=quote_set(observed=stale),
        )
        with self.assertRaisesRegex(ValueError, "stale_snapshot"):
            self.run_payload(payload([item], snapshot=stale))

    def test_stale_betman_board_price_invalidates_snapshot(self):
        stale = "2026-08-02T10:00:00+09:00"
        item = candidate(betman_odds_observed_at_kst=stale)
        with self.assertRaisesRegex(ValueError, "stale_board_price"):
            self.run_payload(payload([item]))

    def test_twenty_nine_minute_old_recommendation_snapshot_is_rejected(self):
        stale = "2026-08-02T11:31:00+09:00"
        item = candidate(
            betman_odds_observed_at_kst=stale,
            status_observed_at_kst=stale,
            market_quotes=quote_set(observed=stale),
        )
        with self.assertRaisesRegex(ValueError, "stale_snapshot"):
            self.run_payload(payload([item], snapshot=stale))

    def test_cross_book_and_betman_prices_must_be_near_same_time(self):
        old_quote = "2026-08-02T11:30:00+09:00"
        item = candidate(market_quotes=quote_set(observed=old_quote))
        result = self.run_payload(payload([item]))
        self.assertEqual(result["decision"], "PASS")
        self.assertIn(
            "price_observation_skew", result["value"]["rejected"][0]["reasons"]
        )

    def test_comparison_prices_must_share_a_common_cutoff(self):
        first = candidate("a", "event-a", origin="user")
        second = candidate(
            "b",
            "event-b",
            origin="assistant",
            betman_odds_observed_at_kst="2026-08-02T12:01:30+09:00",
            market_quotes=quote_set(
                event_id="event-b", observed="2026-08-02T12:01:30+09:00"
            ),
        )
        with self.assertRaisesRegex(ValueError, "comparison_price_observation_skew"):
            self.run_payload(payload([first, second], scope="comparison"))

    def test_comparison_market_quotes_share_one_common_time_window(self):
        first = candidate(
            "a",
            "event-a",
            origin="user",
            market_quotes=quote_set(
                event_id="event-a", observed="2026-08-02T11:50:00+09:00"
            ),
        )
        second = candidate(
            "b",
            "event-b",
            origin="assistant",
            market_quotes=quote_set(
                event_id="event-b", observed="2026-08-02T11:56:00+09:00"
            ),
        )
        with self.assertRaisesRegex(ValueError, "comparison_market_observation_skew"):
            self.run_payload(payload([first, second], scope="comparison"))

    def test_provider_domains_must_be_independent(self):
        item = candidate(market_quotes=quote_set(shared_domain=True))
        with self.assertRaisesRegex(ValueError, "provider domains"):
            self.run_payload(payload([item]))

    def test_market_consensus_must_exclude_betman_target_prices(self):
        quotes = quote_set()
        quotes[0]["source_url"] = "https://m.betman.co.kr/market"
        item = candidate(market_quotes=quotes)
        with self.assertRaisesRegex(ValueError, "exclude Betman target prices"):
            self.run_payload(payload([item]))

    def test_market_quote_event_identity_must_match_candidate(self):
        quotes = quote_set()
        quotes[0]["participants"] = ["wrong-home", "wrong-away"]
        item = candidate(market_quotes=quotes)
        with self.assertRaisesRegex(ValueError, "participants must match candidate"):
            self.run_payload(payload([item]))

    def test_market_outlier_creates_disagreement_not_false_confidence(self):
        item = candidate(
            market_quotes=quote_set((0.60, 0.61, 0.20)),
            betman_odds=2.00,
        )
        result = self.run_payload(payload([item]))
        rejection = result["value"]["rejected"][0]
        self.assertIn("market_disagreement", rejection["reasons"])

    def test_bad_low_price_is_rejected_without_low_odds_rule(self):
        item = candidate(betman_odds=1.57, market_quotes=quote_set((0.55, 0.55)))
        result = self.run_payload(payload([item]))
        self.assertEqual(result["decision"], "PASS")
        self.assertIn("market_ev", result["value"]["rejected"][0]["reasons"])

    def test_portfolio_mode_uses_one_unit_action_fallback(self):
        item = candidate(betman_odds=1.95, market_quotes=quote_set((0.50, 0.50)))
        result = self.run_payload(payload([item]), mode="portfolio", bankroll=9_280)
        self.assertEqual(result["decision"], "BET_ACTION")
        self.assertEqual(result["action"]["total_stake"], 1_000)
        self.assertFalse(result["action"]["picks"][0]["value_claim"])
        self.assertFalse(result["action"]["picks"][0]["positive_ev_claim"])
        self.assertTrue(result["action"]["picks"][0]["relative_value_claim"])
        self.assertEqual(
            result["action"]["picks"][0]["action_basis"],
            "betman_relative_value",
        )
        self.assertGreater(
            result["action"]["picks"][0]["relative_ratio_low"], 0
        )
        self.assertLess(
            result["action"]["picks"][0]["betman_market_payout_rate"], 1
        )
        self.assertRegex(
            result["action"]["decision_id"],
            r"^action-2026-07-27-[0-9a-f]{20}$",
        )
        self.assertIsNone(result["best_price_trigger"])

    def test_action_fallback_is_once_per_cycle(self):
        item = candidate(betman_odds=1.95, market_quotes=quote_set((0.50, 0.50)))
        result = self.run_payload(
            payload([item], action_available=False), mode="portfolio"
        )
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["action"]["reason"], "action_unavailable_this_cycle")

    def test_weekly_action_allowance_is_required_not_cli_defaulted(self):
        data = payload([candidate()])
        del data["weekly_cycle"]
        with self.assertRaisesRegex(ValueError, "missing fields: weekly_cycle"):
            self.run_payload(data, mode="portfolio")

    def test_action_loss_floor_requires_explicit_entertainment_override(self):
        item = candidate(betman_odds=1.50, market_quotes=quote_set((0.50, 0.50)))
        result = self.run_payload(payload([item]), mode="portfolio")
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["action"]["reason"], "action_loss_floor")
        self.assertIsNotNone(result["action"]["entertainment_override_candidate"])

    def test_two_leg_relative_metrics_show_compounded_betman_drag(self):
        first = candidate(
            "a",
            "event-a",
            betman_odds=1.80,
            market_quotes=quote_set((0.50, 0.50), event_id="event-a"),
        )
        second = candidate(
            "b",
            "event-b",
            betman_odds=1.80,
            market_quotes=quote_set((0.50, 0.50), event_id="event-b"),
        )
        combo = parlay("p", ["a", "b"], betman_odds=3.24)
        result = self.run_payload(
            payload([first, second], [combo]), mode="portfolio"
        )
        self.assertEqual(result["action"]["picks"][0]["kind"], "single")
        rejected = {item["id"]: item for item in result["value"]["rejected"]}
        self.assertLess(
            rejected["p"]["betman_market_payout_rate"],
            rejected["a"]["betman_market_payout_rate"],
        )
        self.assertLess(rejected["p"]["market_ev"], rejected["a"]["market_ev"])

    def test_high_odds_tiny_kelly_is_unbettable_at_minimum_unit(self):
        item = candidate(
            betman_odds=40.0,
            market_quotes=quote_set((0.036, 0.036, 0.036)),
        )
        result = self.run_payload(payload([item]))
        self.assertEqual(result["decision"], "PASS")
        self.assertIn("unbettable_min_unit", result["value"]["rejected"][0]["reasons"])

    def test_strong_half_point_under_is_not_permanently_quarantined(self):
        item = candidate(
            sport="football",
            market_type="total",
            line=2.5,
            settlement_rule="regulation_90",
            selection_key="under",
            betman_odds=2.10,
            market_quotes=quote_set(
                (0.58, 0.58, 0.58),
                market_type="total",
                line=2.5,
                selection_key="under",
            ),
        )
        result = self.run_payload(payload([item]))
        self.assertEqual(result["decision"], "BET_PRICE_VALUE")

    def test_integer_total_is_rejected_because_push_is_unmodeled(self):
        item = candidate(
            market_type="total",
            line=2.0,
            selection_key="under",
            market_quotes=quote_set(
                (0.58, 0.58), market_type="total", line=2.0, selection_key="under"
            ),
        )
        with self.assertRaisesRegex(ValueError, "half-point totals"):
            self.run_payload(payload([item]))

    def test_two_leg_value_parlay_requires_value_legs(self):
        first = candidate(
            "a",
            "event-a",
            betman_odds=2.00,
            market_quotes=quote_set((0.60, 0.60, 0.60)),
        )
        second = candidate(
            "b",
            "event-b",
            betman_odds=2.00,
            market_quotes=quote_set((0.60, 0.60, 0.60), event_id="event-b"),
        )
        combo = parlay("p", ["a", "b"], betman_odds=4.00)
        result = self.run_payload(payload([first, second], [combo]))
        pick_ids = {item["id"] for item in result["value"]["picks"]}
        self.assertIn("p", pick_ids)

    def test_combo_only_rows_are_never_recommended_as_singles(self):
        first = candidate(
            "a",
            "event-a",
            betman_odds=2.00,
            market_quotes=quote_set((0.60, 0.60, 0.60)),
        )
        second = candidate(
            "b",
            "event-b",
            betman_odds=2.00,
            market_quotes=quote_set((0.60, 0.60, 0.60), event_id="event-b"),
        )
        combo = parlay("p", ["a", "b"], betman_odds=4.00)
        combo_only = {
            "a": {"purchase_modes": ["parlay"], "parlay_group_ids": ["default"]},
            "b": {"purchase_modes": ["parlay"], "parlay_group_ids": ["default"]},
        }
        result = self.run_payload(
            payload([first, second], [combo], row_overrides=combo_only)
        )
        pick_ids = {item["id"] for item in result["value"]["picks"]}
        self.assertEqual(pick_ids, {"p"})
        single_rejections = {
            item["id"]: item["reasons"]
            for item in result["value"]["rejected"]
            if item["kind"] == "single"
        }
        self.assertIn("single_not_available", single_rejections["a"])
        self.assertIn("single_not_available", single_rejections["b"])

    def test_three_leg_parlay_is_action_only_even_when_legs_have_value(self):
        items = []
        for index in range(3):
            event_id = f"event-{index}"
            items.append(
                candidate(
                    str(index),
                    event_id,
                    betman_odds=2.00,
                    market_quotes=quote_set((0.60, 0.60, 0.60), event_id=event_id),
                )
            )
        combo = parlay("p", ["0", "1", "2"], betman_odds=8.00)
        result = self.run_payload(payload(items, [combo]))
        combo_rejection = next(
            item for item in result["value"]["rejected"] if item["id"] == "p"
        )
        self.assertIn("too_many_value_legs", combo_rejection["reasons"])

    def test_value_parlay_cannot_bypass_unbettable_individual_legs(self):
        first = candidate(
            "a",
            "event-a",
            betman_odds=2.10,
            market_quotes=quote_set((0.50, 0.50), event_id="event-a"),
        )
        second = candidate(
            "b",
            "event-b",
            betman_odds=2.10,
            market_quotes=quote_set((0.50, 0.50), event_id="event-b"),
        )
        combo = parlay("p", ["a", "b"], betman_odds=4.41)
        result = self.run_payload(payload([first, second], [combo]))
        rejection = next(
            item for item in result["value"]["rejected"] if item["id"] == "p"
        )
        self.assertIn("leg_unbettable_min_unit", rejection["reasons"])

    def test_parlay_price_must_be_near_every_leg_market_quote(self):
        quote_time = "2026-08-02T11:50:00+09:00"
        first = candidate(
            "a",
            "event-a",
            market_quotes=quote_set(event_id="event-a", observed=quote_time),
        )
        second = candidate(
            "b",
            "event-b",
            market_quotes=quote_set(event_id="event-b", observed=quote_time),
        )
        combo = parlay(
            "p",
            ["a", "b"],
            betman_odds_observed_at_kst="2026-08-02T11:59:00+09:00",
        )
        result = self.run_payload(payload([first, second], [combo]))
        rejection = next(
            item for item in result["value"]["rejected"] if item["id"] == "p"
        )
        self.assertIn("price_observation_skew", rejection["reasons"])

    def test_parlay_odds_must_match_official_product_rule(self):
        first = candidate("a", "event-a")
        second = candidate("b", "event-b", market_quotes=quote_set(event_id="event-b"))
        combo = parlay("p", ["a", "b"], betman_odds=45.00)
        result = self.run_payload(payload([first, second], [combo]))
        rejection = next(
            item for item in result["value"]["rejected"] if item["id"] == "p"
        )
        self.assertIn("official_parlay_odds_mismatch", rejection["reasons"])

    def test_official_parlay_tick_rounding_is_supported(self):
        first = candidate("a", "event-a", betman_odds=2.123)
        second = candidate(
            "b",
            "event-b",
            betman_odds=2.123,
            market_quotes=quote_set(event_id="event-b"),
        )
        combo = parlay("p", ["a", "b"], betman_odds=4.51)
        data = payload([first, second], [combo])
        data["board"]["parlay_groups"][0].update(
            {
                "odds_rule": "round_half_up",
                "odds_tick": 0.01,
                "max_combined_odds": None,
            }
        )
        result = self.run_payload(data)
        parlay_items = [
            *result["value"]["picks"],
            *result["value"]["rejected"],
        ]
        evaluated = next(item for item in parlay_items if item["id"] == "p")
        self.assertNotIn("official_parlay_odds_mismatch", evaluated.get("reasons", []))

    def test_shared_correlation_cluster_blocks_cross_event_parlay(self):
        first = candidate(
            "a",
            "event-a",
            correlation_cluster_ids=["event-a", "shared-team"],
        )
        second = candidate(
            "b",
            "event-b",
            correlation_cluster_ids=["event-b", "shared-team"],
            market_quotes=quote_set(event_id="event-b"),
        )
        combo = parlay("p", ["a", "b"])
        result = self.run_payload(payload([first, second], [combo]))
        rejection = next(
            item for item in result["value"]["rejected"] if item["id"] == "p"
        )
        self.assertIn("correlated_or_same_event", rejection["reasons"])

    def test_same_event_parlay_is_rejected_without_joint_model(self):
        first = candidate("a", "same")
        second = candidate(
            "b",
            "same",
            market_type="total",
            line=2.5,
            selection_key="over",
            market_quotes=quote_set(
                (0.57, 0.57, 0.57),
                event_id="same",
                market_type="total",
                line=2.5,
                selection_key="over",
            ),
        )
        combo = parlay("p", ["a", "b"])
        result = self.run_payload(payload([first, second], [combo]))
        rejection = next(
            item for item in result["value"]["rejected"] if item["id"] == "p"
        )
        self.assertIn("correlated_or_same_event", rejection["reasons"])

    def test_board_hash_must_match_normalized_rows(self):
        data = payload([candidate()])
        data["board"]["snapshot_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.run_payload(data)

    def test_candidate_price_must_match_hashed_board_row(self):
        data = payload([candidate()])
        data["candidates"][0]["betman_odds"] = 2.20
        with self.assertRaisesRegex(ValueError, "must match the board row price"):
            self.run_payload(data)

    def test_board_counts_must_reconcile(self):
        data = payload([candidate()])
        data["board"]["official_market_row_count"] = 2
        with self.assertRaisesRegex(ValueError, "official counts"):
            self.run_payload(data)

    def test_board_section_hash_must_reconcile(self):
        data = payload([candidate()])
        data["board"]["sections"][0]["rows_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "section rows_sha256"):
            self.run_payload(data)

    def test_board_row_must_be_inside_requested_deadline_window(self):
        data = payload([candidate()])
        data["board"]["window_end_kst"] = "2026-08-02T13:00:00+09:00"
        with self.assertRaisesRegex(ValueError, "outside the deadline window"):
            self.run_payload(data)

    def test_supported_market_row_must_include_every_outcome(self):
        data = payload(
            [candidate()],
            row_overrides={
                "a": {
                    "outcome_labels": ["home", "away"],
                    "outcome_odds": [2.12, 2.50],
                    "selection_dispositions": {
                        "home": "shortlist",
                        "away": "dominated",
                    },
                }
            },
        )
        with self.assertRaisesRegex(ValueError, "every known outcome"):
            self.run_payload(data)

    def test_every_shortlisted_selection_requires_a_candidate(self):
        data = payload(
            [candidate()],
            row_overrides={
                "a": {
                    "selection_dispositions": {
                        "home": "shortlist",
                        "draw": "shortlist",
                        "away": "dominated",
                    }
                }
            },
        )
        with self.assertRaisesRegex(ValueError, "must match exactly"):
            self.run_payload(data)

    def test_board_wide_scope_rejects_nonofficial_capture_urls(self):
        data = payload([candidate()])
        data["scope"] = "board-wide"
        data["board"]["source_url"] = "https://example.com/fake-board"
        with self.assertRaisesRegex(ValueError, "official Betman board URL"):
            self.run_payload(data)

    def test_board_wide_comparable_row_must_evaluate_every_outcome(self):
        data = payload([candidate()])
        data["scope"] = "board-wide"
        with self.assertRaisesRegex(ValueError, "shortlist every outcome"):
            self.run_payload(data)

    def test_coverage_distinguishes_subset_from_full_board(self):
        subset = self.run_payload(payload([candidate()]))
        self.assertEqual(subset["coverage"]["open_selection_count"], 3)
        self.assertEqual(subset["coverage"]["evaluated_open_selection_count"], 1)
        self.assertEqual(subset["coverage"]["claim_scope"], "evaluated-only")

        shared_quotes = quote_set(selection_key="home")
        items = [
            candidate(
                selection,
                board_row_id="full-row",
                selection_key=selection,
                market_quotes=reselect_quotes(shared_quotes, selection),
            )
            for selection in ("home", "draw", "away")
        ]
        full = self.run_payload(payload(items, scope="board-wide"))
        self.assertEqual(full["coverage"]["evaluated_fraction"], 1.0)
        self.assertEqual(full["coverage"]["claim_scope"], "full-board")

    def test_combo_only_board_without_parlay_search_cannot_claim_full_board(self):
        items = []
        for event_id, row_id in (("combo-a", "row-a"), ("combo-b", "row-b")):
            shared_quotes = quote_set(event_id=event_id, selection_key="home")
            for selection in ("home", "draw", "away"):
                items.append(
                    candidate(
                        f"{event_id}-{selection}",
                        event_id,
                        board_row_id=row_id,
                        selection_key=selection,
                        market_quotes=reselect_quotes(shared_quotes, selection),
                    )
                )
        data = payload(
            items,
            scope="board-wide",
            row_overrides={
                "row-a": {"purchase_modes": ["parlay"]},
                "row-b": {"purchase_modes": ["parlay"]},
            },
        )
        result = self.run_payload(data)
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["coverage"]["missing_value_parlay_count"], 9)
        self.assertFalse(result["coverage"]["value_parlay_search_complete"])
        self.assertEqual(result["coverage"]["claim_scope"], "selection-only")

    def test_candidate_only_scope_also_requires_official_betman_urls(self):
        data = payload([candidate(betman_url="https://attacker.example/game")])
        data["scope"] = "candidate-only"
        with self.assertRaisesRegex(ValueError, "official Betman domain"):
            self.run_payload(data)

    def test_extra_shortlist_row_without_candidate_is_rejected(self):
        item = candidate()
        extra = row_for(candidate("b", "event-b"))
        data = payload([item], extra_rows=[extra])
        with self.assertRaisesRegex(ValueError, "must match exactly"):
            self.run_payload(data)

    def test_total_value_exposure_is_capped_at_sixty_percent(self):
        items = []
        for index in range(8):
            event_id = f"event-{index}"
            items.append(
                candidate(
                    str(index),
                    event_id,
                    betman_odds=3.00,
                    market_quotes=quote_set((0.70, 0.70, 0.70), event_id=event_id),
                )
            )
        result = self.run_payload(payload(items))
        self.assertLessEqual(result["value"]["total_stake"], 6_000)
        self.assertEqual(result["value"]["reserve"], 4_000)

    def test_tail_exposure_cap_is_aggregate_not_per_ticket(self):
        items = []
        for index in range(6):
            event_id = f"tail-{index}"
            items.append(
                candidate(
                    str(index),
                    event_id,
                    betman_odds=6.00,
                    market_quotes=quote_set((0.30, 0.30, 0.30), event_id=event_id),
                )
            )
        result = self.run_payload(payload(items))
        self.assertEqual(result["value"]["total_stake"], 1_000)
        self.assertEqual(result["value"]["tail_open_exposure_after"], 1_000)

    def test_action_longshot_still_respects_tail_exposure_cap(self):
        item = candidate(
            betman_odds=40.00,
            market_quotes=quote_set((0.025, 0.025)),
        )
        result = self.run_payload(payload([item]), mode="portfolio", bankroll=4_000)
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["action"]["reason"], "tail_open_exposure_cap")

    def test_existing_tail_ticket_consumes_aggregate_tail_room(self):
        item = candidate(
            betman_odds=6.00,
            market_quotes=quote_set((0.30, 0.30, 0.30)),
        )
        data = payload(
            [item],
            open_exposure={
                "tickets": [
                    {
                        "id": "old-tail",
                        "decision_id": "old-tail-decision",
                        "sleeve": "value",
                        "weekly_cycle_id": "2026-07-20",
                        "purchased_at_kst": "2026-07-23T12:00:00+09:00",
                        "stake": 1_000,
                        "odds": 6.00,
                        "event_ids": ["old-tail-event"],
                        "correlation_cluster_ids": ["old-tail-event"],
                    }
                ]
            },
        )
        result = self.run_payload(data, bankroll=9_000)
        self.assertEqual(result["decision"], "PASS")
        self.assertIn(
            "tail_open_exposure_cap", result["value"]["rejected"][0]["reasons"]
        )

    def test_tied_value_order_is_canonical_not_input_order(self):
        items = []
        for label in "gfedcba":
            event_id = f"event-{label}"
            items.append(
                candidate(
                    label,
                    event_id,
                    betman_odds=2.12,
                    market_quotes=quote_set(event_id=event_id),
                )
            )
        first = self.run_payload(payload(items))
        second = self.run_payload(payload(list(reversed(items))))
        first_ids = [item["id"] for item in first["value"]["picks"]]
        second_ids = [item["id"] for item in second["value"]["picks"]]
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(first_ids, list("abcdef"))

    def test_tied_action_order_is_canonical_not_input_order(self):
        first = candidate(
            "a",
            "event-a",
            origin="user",
            betman_odds=1.95,
            market_quotes=quote_set((0.50, 0.50), event_id="event-a"),
        )
        second = candidate(
            "b",
            "event-b",
            origin="assistant",
            betman_odds=1.95,
            market_quotes=quote_set((0.50, 0.50), event_id="event-b"),
        )
        forward = self.run_payload(
            payload([first, second], scope="comparison"), mode="portfolio"
        )
        reverse = self.run_payload(
            payload([second, first], scope="comparison"), mode="portfolio"
        )
        self.assertEqual(forward["action"]["picks"][0]["id"], "a")
        self.assertEqual(reverse["action"]["picks"][0]["id"], "a")

    def test_price_trigger_never_recommends_structurally_invalid_parlay(self):
        items = []
        for index in range(3):
            event_id = f"trigger-{index}"
            items.append(
                candidate(
                    str(index),
                    event_id,
                    betman_odds=1.90,
                    market_quotes=quote_set((0.50, 0.50), event_id=event_id),
                )
            )
        combo = parlay("bad-parlay", ["0", "1", "2"], betman_odds=6.859)
        result = self.run_payload(payload(items, [combo]))
        trigger = result["best_price_trigger"]
        self.assertTrue(trigger is None or trigger["id"] != "bad-parlay")

    def test_price_trigger_respects_subunit_purchase_ceiling(self):
        item = candidate(
            betman_odds=1.57,
            market_quotes=quote_set((0.55, 0.55)),
        )
        result = self.run_payload(payload([item]), target_stake=500)
        self.assertEqual(result["decision"], "PASS")
        self.assertIsNone(result["best_price_trigger"])

    def test_rejected_minimum_odds_includes_one_unit_kelly_floor(self):
        item = candidate(
            betman_odds=7.30,
            market_quotes=quote_set((0.15, 0.15)),
        )
        result = self.run_payload(payload([item]))
        rejection = result["value"]["rejected"][0]
        self.assertIn("unbettable_min_unit", rejection["reasons"])
        self.assertEqual(rejection["ev_price_floor"], 7.15)
        self.assertEqual(rejection["minimum_acceptable_odds"], 22.50)

    def test_existing_open_exposure_reduces_total_value_room(self):
        items = []
        for index in range(4):
            event_id = f"event-{index}"
            items.append(
                candidate(
                    str(index),
                    event_id,
                    betman_odds=3.00,
                    market_quotes=quote_set((0.70, 0.70, 0.70), event_id=event_id),
                )
            )
        data = payload(
            items,
            open_exposure={
                "tickets": [
                    {
                        "id": "old-ticket",
                        "decision_id": "old-value-decision",
                        "sleeve": "value",
                        "weekly_cycle_id": "2026-07-20",
                        "purchased_at_kst": "2026-07-23T12:00:00+09:00",
                        "stake": 4_000,
                        "odds": 2.00,
                        "event_ids": ["old"],
                        "correlation_cluster_ids": ["old"],
                    }
                ]
            },
        )
        result = self.run_payload(data, bankroll=6_000)
        self.assertEqual(result["value"]["equity_at_cost"], 10_000)
        self.assertLessEqual(result["value"]["total_stake"], 2_000)
        self.assertLessEqual(result["value"]["total_open_exposure_after"], 6_000)

    def test_existing_event_exposure_blocks_duplicate_event_action(self):
        item = candidate(
            betman_odds=1.95,
            market_quotes=quote_set((0.50, 0.50)),
        )
        data = payload(
            [item],
            open_exposure={
                "tickets": [
                    {
                        "id": "open-a",
                        "decision_id": "manual-open-a",
                        "sleeve": "manual",
                        "weekly_cycle_id": "2026-07-27",
                        "purchased_at_kst": "2026-08-01T12:00:00+09:00",
                        "stake": 3_000,
                        "odds": 2.00,
                        "event_ids": ["event-a"],
                        "correlation_cluster_ids": ["event-a"],
                    }
                ]
            },
        )
        result = self.run_payload(data, mode="portfolio", bankroll=7_000)
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["action"]["reason"], "cluster_open_exposure_cap")

    def test_bankruptcy_waits_for_next_deposit(self):
        args = SimpleNamespace(
            input=Path("unused.json"),
            bankroll=999,
            unit=1_000,
            mode="portfolio",
            target_stake=None,
        )
        result = MODULE.run(args, now=NOW)
        self.assertEqual(result["decision"], "WAIT_FOR_DEPOSIT")
        self.assertEqual(result["reserve"], 999)

    def test_weekly_cycle_is_derived_from_canonical_current_ledger(self):
        item = candidate(
            betman_odds=1.95,
            market_quotes=quote_set((0.50, 0.50)),
        )
        data = payload([item])
        data["weekly_cycle"]["id"] = "1900-01-01"
        with self.assertRaisesRegex(ValueError, "current Monday date"):
            self.run_payload(data, mode="portfolio")

        data = payload([item])
        data["weekly_cycle"]["ledger_entries"][0]["amount"] = 1
        with self.assertRaisesRegex(ValueError, "replenishment deposit"):
            self.run_payload(data, mode="portfolio")

    def test_caller_cannot_omit_settled_action_from_workspace_ledger(self):
        data = payload([candidate()])
        action_entry = {
            "id": "settled-action-this-cycle",
            "kind": "action_fallback",
            "occurred_at_kst": "2026-08-01T20:00:00+09:00",
            "amount": 1_000,
            "decision_id": "action-2026-07-27-bbbbbbbbbbbbbbbbbbbb",
            "source": "records/settled-action.md",
        }
        data["_test_ledger_file_entries"] = [
            *data["weekly_cycle"]["ledger_entries"],
            action_entry,
        ]
        with self.assertRaisesRegex(ValueError, "workspace ledger slice"):
            self.run_payload(data, mode="portfolio")

    def test_open_action_ticket_cannot_be_omitted_from_weekly_ledger(self):
        item = candidate(
            betman_odds=1.95,
            market_quotes=quote_set((0.50, 0.50)),
        )
        first = self.run_payload(payload([item]), mode="portfolio")
        action_id = first["action"]["decision_id"]
        second = payload(
            [item],
            open_exposure={
                "tickets": [
                    {
                        "id": "pending-action-ticket",
                        "decision_id": action_id,
                        "sleeve": "action",
                        "weekly_cycle_id": "2026-07-27",
                        "purchased_at_kst": "2026-08-02T11:59:00+09:00",
                        "stake": 1_000,
                        "odds": 1.95,
                        "event_ids": ["event-a"],
                        "correlation_cluster_ids": ["event-a"],
                    }
                ]
            },
        )
        with self.assertRaisesRegex(ValueError, "omits a current-cycle open action"):
            self.run_payload(second, mode="portfolio", bankroll=9_000)

    def test_canonical_action_decision_cannot_be_mislabeled_as_value(self):
        item = candidate(
            betman_odds=1.95,
            market_quotes=quote_set((0.50, 0.50)),
        )
        first = self.run_payload(payload([item]), mode="portfolio")
        action_id = first["action"]["decision_id"]
        data = payload(
            [item],
            open_exposure={
                "tickets": [
                    {
                        "id": "mislabeled-action-ticket",
                        "decision_id": action_id,
                        "sleeve": "value",
                        "weekly_cycle_id": "2026-07-27",
                        "purchased_at_kst": "2026-08-02T11:59:00+09:00",
                        "stake": 1_000,
                        "odds": 1.95,
                        "event_ids": ["event-a"],
                        "correlation_cluster_ids": ["event-a"],
                    }
                ]
            },
        )
        with self.assertRaisesRegex(ValueError, "action sleeve must agree"):
            self.run_payload(data, mode="portfolio", bankroll=9_000)

    def test_open_ticket_cycle_id_must_be_a_monday(self):
        data = payload(
            [candidate()],
            open_exposure={
                "tickets": [
                    {
                        "id": "bad-cycle-ticket",
                        "decision_id": "action-2026-07-28-aaaaaaaaaaaaaaaaaaaa",
                        "sleeve": "action",
                        "weekly_cycle_id": "2026-07-28",
                        "purchased_at_kst": "2026-07-28T12:00:00+09:00",
                        "stake": 1_000,
                        "odds": 1.95,
                        "event_ids": ["event-a"],
                        "correlation_cluster_ids": ["event-a"],
                    }
                ]
            },
        )
        with self.assertRaisesRegex(ValueError, "must be a Monday date"):
            self.run_payload(data, mode="portfolio", bankroll=9_000)

    def test_action_decision_id_cannot_be_relabelled_to_a_prior_cycle(self):
        item = candidate(
            betman_odds=1.95,
            market_quotes=quote_set((0.50, 0.50)),
        )
        first = self.run_payload(payload([item]), mode="portfolio")
        data = payload(
            [item],
            open_exposure={
                "tickets": [
                    {
                        "id": "relabelled-cycle-ticket",
                        "decision_id": first["action"]["decision_id"],
                        "sleeve": "action",
                        "weekly_cycle_id": "2026-07-20",
                        "purchased_at_kst": "2026-07-23T12:00:00+09:00",
                        "stake": 1_000,
                        "odds": 1.95,
                        "event_ids": ["event-a"],
                        "correlation_cluster_ids": ["event-a"],
                    }
                ]
            },
        )
        with self.assertRaisesRegex(ValueError, "decision ID cycle must match"):
            self.run_payload(data, mode="portfolio", bankroll=9_000)

    def test_future_weekly_deposit_is_strictly_rejected(self):
        data = payload([candidate()])
        entry = data["weekly_cycle"]["ledger_entries"][0]
        entry["occurred_at_kst"] = "2026-08-02T12:00:01+09:00"
        entry["amount"] = 10_000
        data["weekly_cycle"]["ledger_snapshot_sha256"] = MODULE.canonical_rows_sha256(
            data["weekly_cycle"]["ledger_entries"]
        )
        with self.assertRaisesRegex(ValueError, "future_ledger_entry"):
            self.run_payload(data, mode="portfolio")

    def test_non_integer_target_stake_is_rejected_in_programmatic_use(self):
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            self.run_payload(payload([candidate()]), target_stake=float("nan"))

    def test_non_finite_odds_are_rejected(self):
        item = candidate(betman_odds=float("nan"))
        with self.assertRaisesRegex(ValueError, "finite number"):
            self.run_payload(payload([item]))


if __name__ == "__main__":
    unittest.main()
