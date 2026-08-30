# Ex-ante decision record

When the user authorizes recording, create one immutable Markdown or JSON record
per recommendation snapshot under `/Users/jinwook/Desktop/betmen/decisions/`.
Otherwise return the same fields in chat without writing. Use KST in the filename
and fields.

Required header fields:

- decision ID and creation time
- skill policy version
- scope: `board-wide`, `candidate-only`, or `comparison`
- Betman round, board URL, official event/row counts, snapshot hash
- evaluated/open selection counts, fraction, and `claim_scope`
- deadline window, UI-section URLs/counts/hashes, and official parlay groups
- available cash, every pending ticket's decision ID/sleeve/purchase-cycle ID and
  immutable purchase time, derived total/event/cluster/tail exposure, current
  Monday-to-Monday cycle bounds, canonical workspace ledger path/current slice/file
  hash, and allocator-derived fallback availability/used decision IDs
- purchase ceiling and KRW 1,000 unit

Required candidate fields:

- candidate ID, official board-row ID, canonical event ID, competition ID,
  ordered participants, scheduled KST start, market identifiers, and
  correlation-cluster IDs
- `proposed_by`: `user`, `assistant`, or `both`
- selection, settlement rule, deadline, status
- single/parlay purchase modes and official parlay-group IDs
- Betman price and observation time
- complete comparable-market source URLs, provider event IDs, and observation times
- break-even, `p_mid`, `p_low`, dispersion, market EV, robust EV
- policy decision and every rejection/invalidation reason
- proposed stake and event-cluster exposure

Required recommendation fields:

- final tickets, reserve, and maximum loss
- rejected shadow candidates
- EV-only price floors and actionable minimum prices including the one-unit
  full-Kelly constraint
- whether any ticket is action-only

After purchase confirmation, append the actual price and stake. For an action-only
purchase also append its deterministic action decision ID to the current-cycle
ledger before another allocation. After the event,
append the result, payout, closing price, CLV, proper scores, and P&L. Never rewrite
the original prediction fields; append a timestamped amendment for corrections.
