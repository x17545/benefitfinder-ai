
from __future__ import annotations

from itertools import combinations


def calculate_portfolios(
    eligible: list[str],
    amounts: dict[str, int],
    conflicts: list[list[str]],
    limit: int = 3,
) -> list[dict]:
    """유효한 정책 조합을 계산하고 지원 금액순으로 정렬한다."""

    eligible = list(dict.fromkeys(eligible))
    conflict_sets = [set(pair) for pair in conflicts]

    portfolios = []

    for size in range(1, len(eligible) + 1):
        for members in combinations(eligible, size):
            member_set = set(members)

            if any(
                conflict.issubset(member_set)
                for conflict in conflict_sets
            ):
                continue

            total = sum(amounts[policy] for policy in members)

            portfolios.append({
                "members": list(members),
                "total_won": total,
            })

    # 상위 조합에 포함된 부분 조합은 별도 대안에서 제외
    maximal = []

    for portfolio in portfolios:
        current = set(portfolio["members"])

        if any(
            current < set(other["members"])
            for other in portfolios
        ):
            continue

        maximal.append(portfolio)

    maximal.sort(
        key=lambda item: (
            -item["total_won"],
            -len(item["members"]),
            item["members"],
        )
    )

    return [
        {
            "rank": index,
            **portfolio,
        }
        for index, portfolio in enumerate(maximal[:limit], start=1)
    ]
