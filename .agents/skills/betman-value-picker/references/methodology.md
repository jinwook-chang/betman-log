# Price and portfolio methodology

## Contents

1. Market probability
2. Conservative price test
3. Stake sizing
4. Parlays and high odds
5. User-versus-assistant scoring
6. Policy review
7. Betman-relative value
8. Source orchestration

## 1. Market probability

For provider `j` and complete market outcomes `k`, remove the provider margin:

`q[j,k] = (1 / odds[j,k]) / sum(1 / odds[j,*])`

Use the median selected-outcome probability across independent provider groups as
`p_mid`. Provider count is not confidence; copied feeds, stale prices, and large
disagreement are separate data-quality failures.
Exclude Betman and every Betman subdomain from provider consensus because the target
price cannot serve as independent evidence about itself.

Join markets only when the canonical competition, ordered participants, scheduled
KST start, market, line, and settlement rule agree. Preserve provider-native event
IDs so a same-name or rescheduled event cannot silently enter the consensus.

Measure `dispersion = max(q) - min(q)` and use:

`haircut = max(base_haircut, dispersion / 2)`

`p_low = max(0, p_mid - haircut)`

The allocator's versioned `POLICY` is authoritative for the base haircut,
dispersion limit, freshness, and thresholds.

## 2. Conservative price test

For Betman decimal odds `d`:

- break-even probability: `1 / d`
- market-implied return: `p_mid * d - 1`
- conservative return: `p_low * d - 1`

`BET_PRICE_VALUE` requires both the market and conservative thresholds. This is a
cross-market price signal, not independent proof that `p_low` is the true
probability.

The actionable invalidation price is the highest price floor required by the
market-return test, conservative nonnegative-return test, and conservative
full-Kelly support for one KRW 1,000 unit, rounded upward to the next Betman tick.

## 3. Stake sizing

Use the conservative Kelly proxy:

`full_kelly = max(0, (d * p_low - 1) / (d - 1))`

Apply the policy fraction and risk caps to `equity_at_cost = spendable cash +
unsettled stake`. Derive existing total, longshot, event, and correlation-cluster
exposure from the individual unsettled tickets. A parlay stake counts once against
total exposure and in full against every included event and correlation cluster.
The longshot cap is aggregate across all qualifying open tickets, not per ticket.

With the KRW 1,000 unit:

1. Round fractional-Kelly stakes down to units.
2. If half-Kelly is below one unit but conservative full Kelly supports at least
   one unit, allow exactly one unit and label the override.
3. If conservative full Kelly is below one unit, return
   `unbettable_min_unit`; do not convert a tiny edge into a 10% bankroll wager.

The purchase request is a ceiling. Reserve is a valid allocation.

The one-unit action allowance is derived by directly loading and hashing the
workspace's canonical ledger, not a caller-provided boolean or self-hashed slice.
Its cycle is KST Monday-to-Monday, it requires a confirmed KRW 10,000 replenishment,
rejects future entries, binds open action tickets and action IDs to their derived
purchase cycle, and becomes unavailable after a purchased action decision ID
appears in that cycle.

## 4. Parlays and high odds

For demonstrably independent events:

`p_joint_mid = product(p_mid_leg)`

`p_joint_low = product(p_low_leg)`

Every value-parlay leg must pass the price and KRW 1,000 minimum-unit tests
individually, the combined conservative return must pass, and the value branch
permits at most two legs. A same-game or otherwise correlated parlay needs a
validated joint model; shared correlation-cluster IDs block marginal-probability
multiplication.

Purchase feasibility is separate from value. A combo-only row may be evaluated as
a leg but never emitted as a single. All legs must share a captured official
parlay-group ID, satisfy that group's leg limits, and use a contemporaneous official
combination price. Under the supported official product rule, the entered combined
odds must equal the captured Betman leg-price product after the group's declared
tick rounding or flooring and maximum-odds cap; an arbitrary or typoed 40x number
is rejected.

Odds at or above the policy tail threshold receive a tighter stake cap. Three or
more legs may appear only as a one-unit action candidate. Never add legs merely to
manufacture a target payout.

## 5. User-versus-assistant scoring

Freeze `proposed_by` and `decision_by_policy` before the match. When candidates
differ, score both as flat-unit shadow bets at the same timestamp even if only one
is purchased. Shared picks count as ties.

Primary evaluation:

- de-vigged closing-line value (outcome independent);
- Brier and log score from frozen probabilities;
- flat KRW 1,000 ROI for selection quality;
- policy-sized ROI, expected log growth, and drawdown for portfolio quality.

Hit rate is descriptive only because a 1.30 and a 4.00 selection have different
base rates. Do not declare either source superior before 100 independent decisions;
report uncertainty even then.

## 6. Policy review

Results settle scores but do not change thresholds immediately. Review on a fixed
quarterly or 100-independent-decision cadence. Preserve rejected candidates to
avoid selection bias, use walk-forward comparisons, and shrink segment estimates
toward the market baseline. A small losing segment creates a probation flag, not
an irreversible quarantine.

## 7. Betman-relative value

Absolute EV remains the profit claim. For each complete Betman market, separately
remove Betman's current row margin:

`q_betman[k] = (1 / betman_odds[k]) / sum(1 / betman_odds[*])`

`betman_payout_rate = 1 / sum(1 / betman_odds[*])`

Compare the external consensus with that normalized allocation:

`relative_ratio_mid = p_mid / q_betman - 1`

`relative_ratio_low = p_low / q_betman - 1`

A positive relative ratio means Betman shaved that outcome less than the other
outcomes in the same row, relative to external consensus. It does **not** mean the
raw Betman price has positive expected value. Report raw `market_ev` beside every
relative metric and set `positive_ev_claim=false` for an action pick.

In `portfolio` mode, a one-unit action may survive when either the older absolute
loss-floor test passes or the conservative relative-ratio thresholds pass. The
relative branch also has a wider but explicit absolute-loss floor so a highly
taxed row cannot win merely by internal normalization. It remains limited by the
weekly action ledger and all exposure caps.

For independent parlays, multiply the leg-level normalized probabilities and
payout rates. Require each leg to clear the relative test and limit the branch to
two legs. Because payout drag and probability error compound, prefer an eligible
single over an eligible parlay; use a parlay only when singles cannot be purchased
or the user explicitly asks to analyze that product. Never describe a relative
parlay as a way to overcome house margin.

## 8. Source orchestration

The IAB Betman board is the target market, not an independent probability source.
Machina `markets` is an orchestrator, so its DraftKings, Kalshi, or Polymarket
components retain those provider identities and are not counted again when the
same venue is queried directly. Sport-data statistics and ClubElo forecasts are
separate contextual/model evidence and cannot be serialized as `market_quotes` in
the v4 allocator.

Prediction-market prices qualify only for an exactly matched, sufficiently liquid
contract with compatible settlement terms. Normalize complete outcome prices and
preserve bid/ask or last-trade provenance. A stale or one-sided probability is a
sanity check, not consensus evidence. See
[machina-integration.md](machina-integration.md) for routing and safety rules.
