"""Resolve reviewed recruitment dates without reopening an explicit closure."""

from datetime import date


def application_on(
    application: dict[str, str | None], today: date,
) -> dict[str, str | None]:
    result = application.copy()
    starts_on = result["starts_on"]
    ends_on = result["ends_on"]
    if ends_on is not None and date.fromisoformat(ends_on) < today:
        result["status"] = "closed"
    elif result["status"] != "closed" and starts_on is not None:
        if date.fromisoformat(starts_on) > today:
            result["status"] = "upcoming"
        elif result["status"] == "upcoming":
            result["status"] = "open"
    return result
