"""Reference oracle for virtual planning-answer regression data.

This module is NOT a product implementation and NOT a product test.
It recomputes expected answers from tests/fixtures/planning_rules.json
using only the standard library, so the fixture stays independently
checkable. Product calculation code does not exist yet; do not quote
these results as product behavior verification.
"""

from decimal import Decimal, getcontext

getcontext().prec = 28

MET = "met"
NOT_MET = "not_met"
NEEDS_REVIEW = "needs_review"


def check_closed_range(min_value, max_value):
    """Reject inverted closed ranges; open bounds are never estimated."""
    if min_value is not None and max_value is not None:
        if min_value > max_value:
            raise ValueError("min > max is rejected")
    return True


def age_status(age, base_max, service_months=None, service_range=None,
               service_unknown=False, extension_allowed=False, cap_years=None):
    """Judge an age upper bound with military extension in months.

    extension_allowed must come from explicit policy evidence; the
    default is no extension (base limit only). cap_years must be the
    caller-supplied per-policy evidence value; None means the cap is
    unclear and an extension-dependent pass cannot be confirmed.
    Extension uses exact months / 12 and never rounds months up to
    whole years. A fully satisfied range is met, a fully failed range
    is not_met, and only boundary-straddling or unknown cases are
    needs_review.
    """
    if age is None:
        return NEEDS_REVIEW
    if not extension_allowed:
        return MET if age <= base_max else NOT_MET
    if service_unknown:
        low = high = None
    elif service_range is not None:
        low, high = service_range
        check_closed_range(low, high)
    elif service_months is not None:
        low = high = service_months
    else:
        low = high = None
    if low is not None and low < 0:
        raise ValueError("negative service months")
    if high is not None and high < 0:
        raise ValueError("negative service months")

    def ext(months):
        value = Decimal(base_max) + Decimal(months) / Decimal(12)
        if cap_years is not None:
            cap = Decimal(base_max) + Decimal(cap_years)
            if value > cap:
                value = cap
        return value

    # Certain by the lower bound alone: no extension needed.
    if age <= base_max:
        return MET
    # Fully unknown service: only the cap upper bound can still fail it.
    if low is None and high is None:
        if cap_years is not None:
            if Decimal(age) > Decimal(base_max) + Decimal(cap_years):
                return NOT_MET
        return NEEDS_REVIEW
    # Open upper bound (e.g. over-N months): minimal extension decides met,
    # the known cap decides not_met; otherwise it stays needs_review.
    if low is not None and high is None:
        if cap_years is not None:
            if Decimal(age) <= ext(low):
                return MET
            if Decimal(age) > Decimal(base_max) + Decimal(cap_years):
                return NOT_MET
        return NEEDS_REVIEW
    # Open lower bound with known upper: only a failure past the
    # absolute maximum can be confirmed.
    if low is None and high is not None:
        absolute_max = Decimal(base_max) + Decimal(high) / Decimal(12)
        if Decimal(age) > absolute_max:
            return NOT_MET
        return NEEDS_REVIEW
    # Both bounds known: pass needs the whole range to satisfy the limit,
    # failure needs the whole range to miss it.
    absolute_max = Decimal(base_max) + Decimal(high) / Decimal(12)
    if Decimal(age) > absolute_max:
        return NOT_MET
    if cap_years is not None:
        capped_max = min(absolute_max, Decimal(base_max) + Decimal(cap_years))
        if Decimal(age) > capped_max:
            return NOT_MET
        if Decimal(age) <= ext(low):
            return MET
    return NEEDS_REVIEW


def per_policy_cap(base_max, service_months, cap_years):
    """Apply a per-policy extension cap and return the extended limit."""
    if cap_years is None:
        raise ValueError("policy cap evidence required")
    extended = Decimal(base_max) + Decimal(service_months) / Decimal(12)
    cap = Decimal(base_max) + Decimal(cap_years)
    return min(extended, cap)


def band_vs_threshold(band_min, band_max, threshold, inclusive=True):
    """Judge an income band against a policy income threshold.

    The policy operator is explicit: <= when inclusive, otherwise <.
    A wholly passing/failing closed band is decisive; a band crossing
    that operator's boundary or lacking necessary bounds needs review.
    """
    check_closed_range(band_min, band_max)
    if band_min is None and band_max is None:
        return NEEDS_REVIEW
    limit = Decimal(threshold)
    if (
        band_max is not None
        and (Decimal(band_max) <= limit if inclusive else Decimal(band_max) < limit)
    ):
        return MET
    if band_min is not None and (
        Decimal(band_min) > limit if inclusive else Decimal(band_min) >= limit
    ):
        return NOT_MET
    return NEEDS_REVIEW


def is_compatible(members, conflict_pairs):
    """True when no conflicting pair is fully contained in members."""
    member_set = set(members)
    for a, b in conflict_pairs:
        if a in member_set and b in member_set:
            return False
    return True


def all_maximal_sets(eligible, conflict_pairs):
    """Enumerate maximal independent sets by backtracking (small inputs)."""
    eligible = list(eligible)
    conflict_set = {tuple(sorted(p)) for p in conflict_pairs}
    maximal = []

    def conflicts_with(candidate, chosen):
        for other in chosen:
            if tuple(sorted((candidate, other))) in conflict_set:
                return True
        return False

    def backtrack(index, chosen):
        if index == len(eligible):
            maximal.append(tuple(sorted(chosen)))
            return
        candidate = eligible[index]
        if not conflicts_with(candidate, chosen):
            backtrack(index + 1, chosen + [candidate])
        backtrack(index + 1, chosen)

    backtrack(0, [])
    result = []
    for combo in maximal:
        combo_set = set(combo)
        extendable = False
        for policy in eligible:
            if policy in combo_set:
                continue
            if is_compatible(combo_set | {policy}, conflict_pairs):
                extendable = True
                break
        if not extendable:
            result.append(combo)
    return sorted(set(result))


def rank_portfolios(eligible, weights, conflict_pairs, limit=3):
    """Rank maximal sets by period total, then fewer members, then name."""
    portfolios = []
    for combo in all_maximal_sets(eligible, conflict_pairs):
        total = sum(weights[p] for p in combo)
        portfolios.append((total, combo))
    portfolios.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [
        {"members": list(combo), "total_won": total}
        for total, combo in portfolios[:limit]
    ]


def best_value_branch_and_bound(weights, conflict_pairs):
    """Maximum-weight independent set without 2^n enumeration.

    Returns (best_value, visited_nodes). The bound at each node is the
    current value plus every remaining policy that is still compatible
    with the chosen set, which prunes the 20-clique regression case
    to a tiny search instead of 2^21 subsets.
    """
    nodes = sorted(weights, key=lambda n: -weights[n])
    conflict_set = {tuple(sorted(p)) for p in conflict_pairs}
    best = 0
    visited = [0]
    # nonlocal write target for nested backtrack

    def compatible_with_chosen(candidate, chosen_set):
        for other in chosen_set:
            if tuple(sorted((candidate, other))) in conflict_set:
                return False
        return True

    def bound_value(start, chosen_set):
        extra = 0
        for i in range(start, len(nodes)):
            node = nodes[i]
            if compatible_with_chosen(node, chosen_set):
                extra += weights[node]
        return extra

    def backtrack(start, current, chosen_set):
        nonlocal best
        visited[0] += 1
        if current > best:
            best = current
        if start >= len(nodes):
            return
        if current + bound_value(start, chosen_set) <= best:
            return
        node = nodes[start]
        if compatible_with_chosen(node, chosen_set):
            chosen_set.add(node)
            backtrack(start + 1, current + weights[node], chosen_set)
            chosen_set.remove(node)
        backtrack(start + 1, current, chosen_set)

    backtrack(0, 0, set())
    return best, visited[0]


def effective_rate(before_total_won, after_total_won, income_from_won, income_to_won):
    """Effective rate from annual support change over annual income rise.

    The rate exists only when annual income strictly increases;
    a flat or decreased income yields None.
    """
    if income_to_won is None or income_from_won is None:
        return None
    income_gap = Decimal(income_to_won) - Decimal(income_from_won)
    if income_gap <= 0:
        return None
    before = Decimal(before_total_won)
    after = Decimal(after_total_won)
    return (before - after) / income_gap * Decimal(100)


def present_value(cashflows, annual_rate):
    """Discount cashflows at actual payment months; None rate stays None."""
    if annual_rate is None:
        return None
    rate = Decimal(str(annual_rate))
    total = Decimal(0)
    for month, amount in cashflows:
        factor = (Decimal(1) + rate) ** (Decimal(month) / Decimal(12))
        total += Decimal(amount) / factor
    return total


def monthly_cashflows(monthly_amount_won, months, first_month=1):
    """Expand a monthly benefit into (month, amount) pairs."""
    return [(m, monthly_amount_won) for m in range(first_month, first_month + months)]


def maturity_cashflow(total_won, maturity_month):
    """Maturity benefit pays the whole accrued amount at maturity month."""
    return [(maturity_month, total_won)]
