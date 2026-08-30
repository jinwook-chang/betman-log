---
name: betman-value-picker
description: Audit Betman Sports Toto prices through the signed-in in-app browser and build an aggressive but evidence-labeled portfolio using sport-specific data and external market evidence. Use for current or deadline-scoped pick requests, user-versus-assistant pick comparisons, weekly KRW 10,000 bankroll allocation, singles or parlays, best-available non-strict fallback, result settlement, and post-result strategy audits.
---

# Betman Price-Value Picker

Treat this as a market-consensus price-gap scanner and decision logger, not a
clairvoyant winner model. Separate these questions every time:

1. Which outcome is more likely?
2. Is the Betman price high enough for that probability?

Never call a side `패` or `승 확정` from narrative judgment. State the estimated
probability, break-even probability, price threshold, and uncertainty. One win or
loss updates the scorecard but never proves or disproves a method.

Analyze and allocate only. Purchase, ledger edits, commits, and pushes require the
user to ask for those actions separately.

## Required tool orchestration

For every Betman board, price, deadline, purchase-mode, or official-result task,
use `browser:control-in-app-browser` and select the persistent `iab` binding. The
in-app browser is the sole browser surface for this skill: do not switch to Chrome,
web search, or a different browser when it is unavailable or signed out. Reuse the
current Betman tab when it matches the task; otherwise open the exact official URL
in IAB. If authentication blocks access, ask the user to sign in there.

For the established workspace, treat an open-ended request such as `지금 살만한
픽` as `board-wide` unless the user explicitly narrows it. Optimize that full-board
path without weakening freshness:

- Reuse the matching current-round IAB tab and reload it once at the start; do not
  close and recreate it between recurring Betman requests.
- Expand the applicable board once, then extract all rows in one bulk DOM read.
  Do not click, snapshot, or parse rows one at a time when the page exposes them
  together.
- Never reuse a prior Betman price, deadline, status, or purchase mode in a new
  recommendation. A cache may retain only non-authoritative parsing structure or
  external event-ID mappings; recapture every official row on every request.
- Run independent sport-data and external-market queries concurrently, grouped by
  sport and date, instead of issuing one query per row sequentially.
- Finish the comparison inside the five-minute snapshot window. If it expires,
  bulk-recapture the whole board and rerun ranking; refreshing only the previous
  finalists cannot support a `best available` claim.
- Preserve the current-round Betman tab for the next expected request. If browser
  finalization is required, keep that tab as `handoff`; clean up only research or
  duplicate tabs.

Use the project-local Machina sports skills only as routed evidence helpers:

- sport-specific `*-data` skills: fixture identity, schedules, structured form,
  injuries, lineups, and results;
- `markets`, read-only `polymarket`, and read-only `kalshi`: external prices and
  market-implied probabilities when the exact event and market can be matched;
- `betting`: odds conversion, de-vigging, EV, Kelly, and parlay arithmetic only;
- `sports-news`: recent context or a veto for stale/mismatched evidence, never a
  numeric probability.

Run their Python CLI through the `uv tool`-managed `sports-skills` executable.
Never install or repair this runtime with `pip`; follow the workspace `AGENTS.md`.

Betman IAB observations remain authoritative for what can actually be bought and
at what price. A sport-data score is not automatically a Betman settlement result;
apply the captured Betman market and settlement rule. Never count an orchestrator
and its underlying venue as independent sources. Do not invoke `machina`,
`world-cup`, `polymarket-trading`, or any wallet/order command from this workflow.

Read [machina-integration.md](references/machina-integration.md) before collecting
the board, routing a sport query, or converting sports-skills output into allocator
evidence.

## Established workspace profile

For `/Users/jinwook/Desktop/betmen`:

- Treat Monday KRW 10,000 replenishment as capital only after it is confirmed and
  dated. Never count a future deposit.
- Use the verified KRW 1,000 ticket unit.
- Treat the user's requested spend as a ceiling. Do not fill it with negative-EV
  tickets merely to reach a round number.
- The user accepts aggressive variance and a clearly labeled one-unit fallback.
  Use `portfolio` mode for that established request; call it best available only
  when output coverage is `full-board`. It remains an entertainment expense, not
  a profit claim.
- In `portfolio` mode, distinguish absolute price value from Betman-relative
  value. The latter removes the current Betman row margin only for ranking and may
  still have negative absolute EV; spend it only through the weekly one-unit
  action allowance.

## 1. Freeze the task scope

Choose and report exactly one scope:

- `board-wide`: inspect every open Betman row in the requested round or deadline
  window; only this scope may claim `best available`.
- `candidate-only`: evaluate a named selection without claiming full-board rank.
- `comparison`: evaluate user and assistant candidates at the same timestamp and
  board state. If the user supplies a pick later, rerun the assistant side at that
  later price rather than comparing stale snapshots.

Default an unqualified recommendation request to `board-wide` for the established
workspace. Use `candidate-only` only when the user names or clearly limits the
candidate set.

`comparison` must contain both provenances. Use at least one `origin=user` and one
`origin=assistant`, or one shared selection with `origin=both`; otherwise downgrade
to `candidate-only` rather than pretending a comparison occurred.

Always include a user-supplied candidate in the shortlist. A single official board
row may have multiple candidate selections, so compare opposing user and assistant
sides without inventing duplicate board rows. Apply the same price, evidence, and
exposure tests; never add or subtract probability merely because of provenance.
If both propose the same board-row selection, represent it once with `origin=both`;
never double the economic exposure. Break numerical ties by the canonical economic
instrument key, not input order, display label, or proposer.

## 2. Reconcile spendable cash

Read `README.md` and unsettled daily records.

`cash = confirmed deposits + settled payouts/refunds - all purchased stakes`

Track every pending ticket separately under `open_exposure.tickets`, including its
decision ID, `value`/`action`/`manual` sleeve, immutable purchase time, derived
purchase-cycle Monday ID, stake, odds, event IDs, and correlation-cluster IDs. The
allocator derives totals and
cross-checks current-cycle open action tickets against the weekly ledger; never
hand-enter aggregate exposure. A parlay stake counts once in total exposure
and in full against every leg's event and correlation cluster. Do not subtract it
twice. The allocator sizes against `equity_at_cost = cash + pending stake` while
pending tickets consume total, cluster, and aggregate-longshot caps. Also capture
the canonical current-cycle slice from
`/Users/jinwook/Desktop/betmen/ledger.json`. It runs from KST Monday 00:00 to the
next Monday and contains the confirmed KRW 10,000 replenishment plus any purchased
action-fallback decision IDs. The allocator loads that workspace file directly,
hashes it, rejects future entries, binds action IDs to their purchase cycle, and
derives availability; caller-authored entries alone are never evidence. When an
action ticket is bought, append its deterministic action decision ID to that file
before another run. If the file is missing or mismatched, the fallback fails
closed. If cash is below KRW 1,000, return `WAIT_FOR_DEPOSIT`.

## 3. Capture the complete official board in IAB

Use the signed-in Betman board in IAB. For `오늘 마감`, include every row closing
on the current KST date; for a round, include every open row.

- Expand every sport, date, league, market, `조합`, and `한경기` view.
- Record the Betman round and exact deadline window. Capture every UI section from
  an official `betman.co.kr` URL with its official row count and per-section row
  hash; the section totals must reconcile to the board total.
- Normalize every row with canonical event ID, competition ID, ordered
  participants, scheduled KST start, sport, league, market, line, settlement rule,
  deadline, status, every Betman outcome price and observation time, whether it is
  purchasable as `single` and/or `parlay`, official parlay-group IDs, and one
  explained disposition per outcome: `shortlist`, `expired`, `no_comparable`,
  `insufficient_evidence`, or `dominated`. A supported market must contain every
  known outcome; every shortlisted outcome must have exactly one candidate.
- In `board-wide`, if one outcome of an open supported row has comparable evidence,
  shortlist and evaluate every outcome from that complete market. Do not pre-label
  an open outcome `dominated`; that is an allocator result. Use `no_comparable` or
  `insufficient_evidence` for an entire row that cannot be priced.
- Selection coverage is not ticket coverage. For every captured official parlay
  group, enumerate and evaluate every feasible independent two-leg combination
  among shortlisted legs. If any are missing, output is `selection-only` and may
  not support a full-board `PASS` or best-ticket claim.
- Compute the canonical row-list SHA-256 required by the allocator. Shortlist
  candidate prices must match those hashed row prices exactly.
- Never describe a partial page as the full slate.

The allocator can prove that a capture is internally consistent; it cannot prove
that the browser operator did not omit a UI section. If the signed-in board and all
section counts were not actually inspected, downgrade to `candidate-only` and do
not say `best available`, even if a caller-supplied JSON claims `board-wide`.

## 4. Build reproducible price evidence

For every shortlist row capture the current Betman price and observation time,
deadline, status, and direct URLs. Capture complete odds for the exact same event,
competition, ordered participants, scheduled start, market, line, selection, and
settlement rule from at least two independent provider groups on different
domains. Preserve each provider's own event ID; matching team names alone are not
an event join.
The comparable providers must be external to Betman; never feed the target Betman
price back into its own market-consensus estimate.

Keep the board, Betman price, status, weekly ledger verification, and every
comparable-market observation no more than five minutes old, and keep every market
observation within five minutes of the Betman price.
In `comparison` scope, user and assistant Betman price observations must also be
within five minutes of one another; otherwise refresh both sides.
Opposing selections on the same official row must reuse the same complete-market
quote snapshots; only the selected outcome index may differ.

De-vig complete markets. Also normalize the complete Betman row separately to
measure relative allocation after its structural margin, but never feed that
normalized Betman probability into external consensus. Do not use previews, tips,
team form, injuries, or an LLM
opinion as a numeric probability. Narrative evidence may veto stale or mismatched
data; it may not manufacture an edge. The v4 allocator intentionally accepts only
market-consensus probabilities; adding a predictive model requires a separately
versioned, out-of-sample calibrated extension.

Read [methodology.md](references/methodology.md) when interpreting probabilities,
staking, longshots, parlays, CLV, or historical performance.

## 5. Run the allocator

Print the live input contract:

```bash
python3 scripts/allocate_bankroll.py --schema
```

Use profit-seeking price-value mode:

```bash
python3 scripts/allocate_bankroll.py candidates.json \
  --bankroll 10000 --mode value
```

Use the established non-strict, one-unit fallback only when requested or already
consented in context:

```bash
python3 scripts/allocate_bankroll.py candidates.json \
  --bankroll 10000 --mode portfolio
```

Add `--target-stake` for a purchase ceiling. Weekly action availability belongs in
the required ledger-backed input; there is no permissive CLI default.
Price-wait triggers are emitted only for purchasable singles and only when the
purchase ceiling still allows one unit; a parlay requires leg-level repricing and
is never represented by an invented standalone target price. If an action ticket
is selected, its price-wait trigger is suppressed so two alternatives cannot be
mistaken for simultaneous purchases.

Interpret `BET_PRICE_VALUE` as a conservative cross-market price discrepancy,
not proof of predictive advantage. The allocator:

- uses the median de-vigged market probability;
- subtracts a source-disagreement haircut before sizing;
- compares every timestamp with the actual KST runtime clock;
- uses half-Kelly on the probability haircut, with correlation-cluster, total, and
  aggregate-longshot exposure caps;
- rounds to KRW 1,000 only when conservative full Kelly supports at least one
  unit;
- allows at most two independent value legs in a parlay, requires every leg to
  clear the value and minimum-unit tests independently, and blocks shared
  correlation clusters;
- never emits a `조합만` row as a single, and accepts a parlay only when every leg
  belongs to the same captured official parlay group, its leg limits pass, and the
  combined odds match that group's captured product, tick-rounding/floor, and cap
  rule;
- treats three-plus-leg parlays as action-only and rejects same-event/correlated
  products without a validated joint model;
- permits one KRW 1,000 action fallback per weekly deposit cycle when either the
  absolute loss floor or the conservative Betman-relative test survives;
- reports the current row payout rate, normalized Betman probability, absolute
  market EV, and relative ratios together, and prefers an eligible single over a
  relative-value parlay because margin and probability error compound.

High odds are neither rewarded nor rejected by themselves. A 40x ticket must pass
the same joint-probability and minimum-unit tests; otherwise it belongs only in
the explicitly labeled action sleeve.

## 6. Refresh, log, and report

Immediately before reporting, refresh the Betman price in IAB, comparable prices,
deadline, and status; regenerate the snapshot and rerun. Discard stale output if
the price falls below `minimum_acceptable_odds`.

For recommendation work, return the fields of an ex-ante decision record before
kickoff using [decision-log.md](references/decision-log.md). Save it under the
workspace only when the user separately asks to record it. Include both user and
assistant candidates, including rejected shadow picks. Do not mark a ticket
purchased until the user confirms it. Corrections are timestamped amendments, not
silent hindsight edits.

Return in Korean:

1. `결론`: `가격가치`, `액션 1유닛`, `가격대기`, or `패스`
2. `사용자 픽 대조`: same-time verdict for every supplied user pick
3. `가격 근거`: Betman odds, break-even, current-row payout rate and normalized
   probability, market midpoint/haircut estimate, absolute market/robust EV,
   Betman-relative mid/low ratios, disagreement, evidence grade
4. `배분`: tickets, event-cluster exposure, total stake, reserve, maximum loss
5. `무효화 조건`: minimum acceptable odds, deadline, status, source staleness
6. `출처·시각`: direct URLs, KST runtime, board count, snapshot hash

Also report `평가 커버리지` as evaluated/open selections plus evaluated/expected
two-leg value parlays. Say `전체 발매판 중 최고` only when
`coverage.claim_scope=full-board`. `selection-only` means all outcomes were priced
but legal parlay tickets were not exhaustively searched; `evaluated-only` means the
selection board itself was partial. Internal JSON consistency still cannot prove
that the operator actually opened every signed-in UI section.

If only the fallback survives, say plainly that it may have negative expectation.
Never claim that the method maximizes profit without prospective calibration.

## 7. Learn without hindsight

On a result request, settle the ledger and append result, closing price, CLV,
Brier/log score, flat-unit ROI, policy-sized ROI, and drawdown when the necessary
ex-ante fields exist. Aggregate by independent event, not ticket count, so duplicate
singles and parlay legs do not inflate the sample.

Do not backfill missing historical probabilities or provenance. Review policy only
after 100 independent logged decisions or on a fixed quarterly cadence. A losing
streak triggers an audit, not a permanent market ban. Read
[legacy-audit.md](references/legacy-audit.md) only when discussing the pre-v3
record.
