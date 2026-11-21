"""Preserve source conditions once while keeping offline cash releases compact."""


def append_source_conditions(payload: dict, conditions: list[dict], rules: list[dict]) -> None:
    """Remove duplicate manual citations, retaining every executable condition."""
    known = {
        (e["location"], e["quote"])
        for condition in payload["benefit"]["eligibility_conditions"]
        for e in condition["evidence"]
    }
    by_id = {condition["id"]: condition for condition in conditions}
    for rule in rules:
        condition = by_id[rule["condition_id"]]
        if rule["kind"] == "manual" and all(
            (e["location"], e["quote"]) in known for e in condition["evidence"]
        ):
            continue
        payload["benefit"]["eligibility_conditions"].append(condition)
        payload["conditions"].append(rule)
        known.update((e["location"], e["quote"]) for e in condition["evidence"])


def reimbursement_rates(text: str, cost_pattern: str) -> set[int]:
    """Read this reimbursement's percentage, excluding prior third-party discounts."""
    from decimal import Decimal
    import re

    rates = set()
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*%", text):
        before = text[max(0, match.start() - 70) : match.start()]
        after = text[match.end() : match.end() + 100]
        if re.search(r"(?:감면|할인|지원\s*받)[^\n]{0,40}경우", after) and re.search(
            r"차액|결제액|실제\s*(?:결제|부담)", after
        ):
            continue
        costs = list(re.finditer(cost_pattern, before))
        thresholds = list(re.finditer(r"소득|중위|자부담|부담률|보증금\s*[x×*]", before))
        if thresholds and (not costs or thresholds[-1].start() > costs[-1].start()):
            continue
        if costs or re.search(r"지원\s*(?:비율|금액|율)\s*[:：]?\s*$", before):
            value = Decimal(match.group(1)) * 100
            if value != int(value) or not 0 < value <= 10000:
                raise ValueError("source_reimbursement_invalid_percentage")
            rates.add(int(value))
    return rates
