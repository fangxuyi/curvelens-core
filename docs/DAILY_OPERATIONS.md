# Daily report freshness and email recovery

## September 18, 2026 incident

The September 19 overnight invocations for WTI, Gold, Corn and Silver acquired
September 17 FINAL bulletins. They completed that older date successfully, but
never started September 18. The jobs took their target from the acquired file
instead of keeping an independent settlement target. September 18's invocations
had similarly received September 16 bulletins. Successful historical resumption
therefore concealed a missing daily report.

The Gmail sends were independently rejected by automatic approval review. A
connected mailbox, successful manual sends, and a plugin permission setting did
not prove that the unattended report send was authorized. The rejection cited
full internal report/diagnostic payloads and insufficient accepted destination
authorization. Never bypass a rejected send with another transport.

The approved acquisition path subsequently returned internally verified
September 18 PRELIMINARY bulletins for all four deployments. Available records
establish what each invocation received, but do not distinguish delayed upstream
publication from CDN behavior. No evidence establishes a corrupt local download.
CME describes publication times as approximate:
<https://www.cmegroup.com/market-data/daily-bulletin.html>.

## Host integration

Use `agent/run_controller.py` as the restricted adapter to
`agent/analysis_orchestrator.py`. It accepts explicit product selection, a bounded
set of controller operations and ISO dates. It forwards `--expected-date` on
daily commands, preserves subprocess exit status and retains the macOS sandbox
preflight. It permits neither restart nor learning promotion/activation.

An existing approved machine-local launcher may delegate to this tracked file
using `runpy.run_path(..., run_name="__main__")`. Keep its approved command path
and browser/provider permission boundary. Do not copy independent workflow logic
into local launchers. Code deployment alone does not update saved job prompts.

For the four CME morning deployments, freeze the last CME business day strictly
before the invocation's New York date, using the repository's existing
`ccvm.reference.exchange_calendar.is_business_day` calendar. This is a deployment
target policy, not a claim that the publisher has already released the file.
Record and retain the target across resumption. If a special session requires an
unconfigured override, report `TARGET_DATE_UNRESOLVED` rather than guessing.

After verifying the PDF's internal date and product, call:

```bash
CCVM_PRODUCT=wti ccvm/.venv/bin/python agent/run_controller.py start \
  --date 2026-09-17 --expected-date 2026-09-18
```

An older source returns `AWAITING_TRADE_DATE` before any workflow mutation; a newer
source returns `UNEXPECTED_TRADE_DATE`. Neither can authorize a successful report
email. Preserve historical reports and input vintages. The current once-daily
configuration has no retry window: do not revive the historical half-hour cadence
or silently add background retries. A later acquisition requires another
authorized invocation. The user approved moving the four existing morning jobs
to 05:00 America/New_York, Tuesday–Saturday. Verify saved host settings separately;
this document does not install a schedule.

## Authorized email contract

The deployment's saved task must identify the approved sender, sole recipient,
recurrence and payload scope. Addresses and receipts stay in local configuration,
not source control. Send through the connected Gmail plugin only.

1. Verify the authenticated mailbox and search Sent for the unique invocation ID.
2. Require a completed validated workflow and matching source/target dates before
   sending the full `analysis.md`. Include its limitations, actual date, target
   date, run time and delivery ID. Retain preliminary-data labels.
3. Keep filesystem paths, raw diagnostics, workflow packets and tool logs local.
   A blocked/failed run gets a concise observed status, not an older full report.
4. Record success only with a confirmed Gmail message ID. For ambiguous results,
   search Sent before retrying. Preserve a rejected payload locally and request
   the specific missing approval; do not treat an app permission setting as an
   override of automatic approval review.
5. Deliver before bounded historical learning. Keep its evaluation cutoff fixed,
   dispatch only controller-returned actions, report deferred dates/errors
   separately, and do not send a second email solely because learning finishes.

Manual recovery validates that recovery send only. The next scheduled invocation
must confirm unattended delivery with its own Gmail receipt before operational
readiness is claimed. A prompt cannot send email after the host crashes or usage
is exhausted; no independent failure-email service is configured here.

## Recovery integrity checks

The controller checks persisted product, trade date, packet identity and canonical
manifest location before resuming. A saved COMPLETE flag alone is insufficient:
the four canonical report files must exist and be nonempty, and analysis.json
must identify the same product, date and packet. Newly finalized reports also
record SHA-256 digests for all four files; changed content blocks completion and
notification preparation. Older reports retain identity/presence checks and
explicitly report `legacy_identity_and_presence`; they are not retroactively
assigned trusted digests. Unknown workflow phases fail with a structured error.

Report files are published with atomic replacement. Completion is saved only
after all reports and their digests are available; an interrupted finalization
can resume from READY_TO_FINALIZE. A preparation process that exits unsuccessfully
cannot claim that its packets are ready.

The repository notification outbox rejects corrupt ledgers instead of silently
discarding delivery history. Acknowledgment saves receipts before removing queued
items, and pending listings exclude acknowledged IDs. These protections cover the
repository outbox; the host Gmail integration must still follow the separate Sent
search and receipt contract above. Atomic writes do not provide a concurrent-send
lock: only one delivery worker may operate a given product outbox at a time.

The shared GitHub test workflow runs on pull requests and main-branch pushes. It
includes stale-date, wrong-product, missing/altered-report, interrupted-acknowledgment
and corrupt-ledger regressions, plus scorecard identity checks for all six products.
Branch protection is a separate repository setting; adding this workflow does not
make its status a required merge check. External publication delays, host outages,
usage limits and provider authorization failures remain operational dependencies.
