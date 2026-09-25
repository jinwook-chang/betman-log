# Betman project

- Keep README concise: top accounting summary, daily records table, and short recording principles only.
  Do not append purchase/settlement memos or chronological update blockquotes.
  Put detailed provenance, odds assumptions, and reconciliation notes in the relevant daily record.

- Resolve all records, ledger and skill paths from this repository, never a user's home path.
- Run Python with `uv run --no-project python`; run sports data with the uv-managed
  `sports-skills` CLI. If missing, install with `uv tool install --python 3.12 sports-skills`.
  Do not use pip. Do not upgrade or reinstall an available runtime during ordinary queries.
- Betman official pages use IAB through the available CUA tools. Read their current
  API documentation, reuse the matching tab, and use public pages without requiring login.
  Login is only needed when the actual requested page blocks access.
- A purchase confirmation is evidence of purchase: do not recheck the user's account.
  Use supplied receipt odds first. Per the user's 2026-09-25 confirmation, if the user
  bought immediately after a Betman price lookup and reports no different odds, treat
  that lookup price as the user-confirmed purchase odds; cite the lookup and do not
  call it receipt-verified. An explicitly reported changed price overrides the lookup.
  If the timing or matching lookup is unclear, keep the purchase odds unknown.
- The approved prospective-review workflow permits recommendation research snapshots
  under `decisions/` only. Recommendations do not authorize purchases, accounting
  writes, commits, pushes or scheduled monitoring. An explicit
  record-and-push request covers normal completion when the user later supplies a missing
  amount or receipt. Do not ask for the same authorization again.
- Record every confirmed ticket, including manual choices. Missing amount/odds remain
  unknown, not zero; record known fields and explain excluded totals.
- Before record commits run the accounting checker described in
  `.agents/skills/betman-value-picker/references/recording.md`, plus `git diff --check`.
- Push only when asked, to the existing main branch unless the user requests otherwise.
  Stage explicit task files and preserve unrelated changes. Do not force-push.
