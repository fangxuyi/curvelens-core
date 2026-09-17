# Retrospective caching and daily work limits

Daily learning checks up to 30 prior report dates, but unchanged reviews are
reused. Checking a date is deterministic work; it does not itself invoke a model.

## Cache identity

The retrospective cache keeps forecast, outcome, scoring-version and workflow
fingerprints. Workflow-event paths under the selected product data root are
represented relative to that root for the cache fingerprint. Moving a checkout
therefore does not invalidate a review solely because those paths changed.
The original event log and its raw hash remain in the audit artifacts.

Execution timestamps in evaluation records do not constitute new evidence.
Forecast, mobile-selection and investigator evaluations are all fingerprinted;
a newly matured investigator outcome can require review even when the report's
own forecast outcomes have not changed. Other event changes still invalidate
the cache. Report and outcome source hashes remain strict.

Existing legacy reviews are upgraded only if their old fingerprint can be
reproduced and their deterministic evaluations are unchanged. A maintenance
caller may explicitly supply `previous_data_root` to `prepare_retrospective`
for a verified relocation. This reconstructs the old event bytes solely to
check that fingerprint; it does not modify source events or accept changed
scores. Matching responses are archived before rebinding, then pass the normal
response validator. Missing proof requires a fresh review. This is not a daily
analysis entry point or permission to alter an original forecast.

## Daily budget

`learn --date <as-of-date>` reserves at most two distinct review source dates
per product and as-of date, newest first. Reservations are persisted under
`learning/review_dispatch/<as-of-date>.json`, so repeated controller calls do not
unlock another batch. Retries of those dates retain the existing two-correction
limit. The controller emits `review_budget` and `deferred_retrospectives`.

After its dispatched reviews validate, `LEARNING_MEMORY_UPDATED` means the
aggregate memory was rebuilt; it does not mean every historical date was
reviewed. Deferred dates and legacy-record errors must be reported separately.
A separately requested historical catch-up may use the explicit retrospective
workflow. Ordinary daily jobs must not use it to bypass the daily budget.

## Delivery order

Where report delivery is already authorized, deliver the validated daily report
before learning catch-up. Indicate learning is pending if necessary. Deduplicate
by invocation ID and do not send another report merely because learning finished.
This ordering does not grant delivery permission or change its destination.
