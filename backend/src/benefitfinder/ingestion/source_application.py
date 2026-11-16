"""Recruitment facts from the official API, without a model or invented dates."""

from datetime import date
import re
from collections.abc import Mapping

from benefitfinder.ingestion.decider_pending import _url


def _date(value: str) -> date | None:
    value = value.strip()
    try:
        if re.fullmatch(r"20[0-9]{6}", value):
            return date(int(value[:4]), int(value[4:6]), int(value[6:]))
        match = re.fullmatch(r"(20[0-9]{2})[./-]\s*([0-9]{1,2})[./-]\s*([0-9]{1,2})\.?", value)
        if match:
            return date(*(int(part) for part in match.groups()))
    except ValueError:
        pass
    return None


def source_application(fields: Mapping[str, str], *, as_of: date) -> tuple[dict, tuple[str, ...]]:
    """0057001=specified period, 0057002=continuous, 0057003=closed.

    Business dates never become application endpoints. Budget exhaustion remains
    a stated condition, rather than a model-verification prerequisite.
    """
    application = {
        "status": "unknown", "starts_on": None, "ends_on": None,
        "url": _url(fields.get("aplyUrlAddr", "").strip()),
    }
    raw = fields.get("aplyYmd", "").strip()
    code = fields.get("aplyPrdSeCd", "").strip()
    date_token = r"(?:20[0-9]{6}|20[0-9]{2}[./-]\s*[0-9]{1,2}[./-]\s*[0-9]{1,2}\.?)"
    match = re.fullmatch(rf"\s*({date_token})\s*[~∼～–—]\s*({date_token})\s*", raw)
    start, end = (None, None) if match is None else tuple(_date(part) for part in match.groups())
    if start is not None and end is not None and start <= end:
        application.update(starts_on=start.isoformat(), ends_on=end.isoformat())
        application["status"] = (
            "closed" if code == "0057003" or as_of > end
            else "upcoming" if as_of < start else "open"
        )
        return application, ()
    if code == "0057003":
        application["status"] = "closed"
        return application, ()
    if code == "0057002" and not raw:
        business_end = _date(fields.get("bizPrdEndYmd", ""))
        if business_end is not None and business_end < as_of:
            return application, ("continuous_notice_business_period_ended",)
        application["status"] = "open"
        return application, ()
    return application, ("application_period_not_resolved",)
