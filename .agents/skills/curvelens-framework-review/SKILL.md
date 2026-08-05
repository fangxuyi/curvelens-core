---
name: curvelens-framework-review
description: Run or resume the repository-wide CurveLens framework improvement review across all visible product runtimes. Use when asked to reflect on product learning memory, identify generalizable workflow improvements, review repeated agent or validator failures, or produce PR-gated framework suggestions without changing the framework.
---

# CurveLens Framework Review

This is one repository-wide review loop, not one loop per product agent. It reads
only the product runtimes visible in the current checkout. Do not set
`CCVM_PRODUCT`, read product runbooks, or operate a daily market workflow.

1. Read the root `AGENTS.md` and start or resume the durable review:

   ```bash
   ccvm/.venv/bin/python agent/framework_review.py start --date YYYY-MM-DD
   ```

2. Parse the JSON result. On `FRAMEWORK_REVIEW_REQUIRED`, spawn exactly one
   `curvelens_framework_reviewer` with only the returned task file. Wait for its
   response; the worker must modify only its assigned response path.
3. Advance once:

   ```bash
   ccvm/.venv/bin/python agent/framework_review.py advance --date YYYY-MM-DD
   ```

4. Repeat steps 2-3 through `FRAMEWORK_REVIEW_COMPLETE`. Corrections are bounded.
   Stop and report the exact detail on `FRAMEWORK_REVIEW_BLOCKED` or
   `FRAMEWORK_REVIEW_ERROR`. Never use `--restart` unless the user explicitly
   requests a fresh review.
5. Report the generated `framework_suggestions.json` and
   `framework_suggestions.md`, suggestion IDs, source signal IDs, and whether no
   changes were proposed.

The deterministic packet keeps product-local narrative observations and suggested
adjustments out of shared review. It may route a structured learning pattern only
when the same non-neutral scope appears across products. Repeated workflow failures
may also enter review, but the reviewer must distinguish a shared code, prompt, or
validation issue from product configuration, product knowledge, insufficient
evidence, or a fix already merged.

Every suggestion is advisory. Do not implement it, modify framework files, create
git state, schedule the review, or promote learning. A separate explicit user
request must select a suggestion for implementation, testing, and a reviewed PR.
