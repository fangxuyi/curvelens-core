"""Keep an invocation's settlement target separate from its acquired trade date."""
from __future__ import annotations

from datetime import date


def trade_date_guard(actual: str, expected: str | None) -> dict | None:
    """Return a blocker for a mismatched target, without touching runtime state.

    The caller verifies the source's internal date and supplies the target from
    the deployment's settlement/publication policy. Neither wall-clock time nor
    delivery history proves that a particular settlement should be available.
    Omitting the target preserves explicit historical operations.
    """
    if expected is None:
        return None
    actual_date, expected_date = date.fromisoformat(actual), date.fromisoformat(expected)
    if actual_date == expected_date:
        return None
    stale = actual_date < expected_date
    return {
        "result": "AWAITING_TRADE_DATE" if stale else "UNEXPECTED_TRADE_DATE",
        "date": actual_date.isoformat(),
        "expected_trade_date": expected_date.isoformat(),
        "freshness": "stale" if stale else "ahead_of_target",
        "actions": [],
        "delivery_queued": False,
        "detail": (
            f"Source trade date {actual_date} does not match invocation target "
            f"{expected_date}. No workflow or report delivery was prepared. "
            + ("Reacquire through the approved path within the existing retry window."
               if stale else "Check the source date and invocation target before continuing.")
        ),
    }
