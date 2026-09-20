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
