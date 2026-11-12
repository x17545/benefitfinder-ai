from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from typing import TypedDict


DEFAULT_TIMEOUT_SECONDS = 60.0


class CalculationLimitExceeded(RuntimeError):
    """Raised when the exact portfolio search cannot finish within its limits."""


class PortfolioResult(TypedDict):
    rank: int
    members: list[str]
    total_won: int


def calculate_portfolios(
    eligible: list[str],
    amounts: dict[str, int],
    conflicts: list[list[str]],
    limit: int = 3,
    *,
    unverified_pairs: Sequence[Sequence[str]] | None = None,
    max_search_nodes: int | None = None,
    timeout_seconds: float | None = DEFAULT_TIMEOUT_SECONDS,
) -> list[PortfolioResult]:
    """Return the highest-ranked maximal compatible portfolios.

    A portfolio is maximal when no other eligible, compatible policy can be
    added to it. Ties are resolved by fewer unverified pairs, fewer policies,
    then lexicographic policy IDs. ``max_search_nodes`` counts recursive graph
    search states. ``None`` disables a limit; the default timeout is 60 seconds.
    """

    if limit < 0:
        raise ValueError("limit must be non-negative")
    if max_search_nodes is not None and max_search_nodes < 0:
        raise ValueError("max_search_nodes must be non-negative")
    if timeout_seconds is not None and timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    if limit == 0:
        return []

    policy_ids = sorted(set(eligible))
    if not policy_ids:
        return []

    try:
        policy_index = {policy_id: index for index, policy_id in enumerate(policy_ids)}
        conflict_adjacency = _build_adjacency(
            policy_index,
            conflicts,
            pair_name="conflicts",
        )
        unverified_adjacency = _build_adjacency(
            policy_index,
            unverified_pairs or (),
            pair_name="unverified_pairs",
        )

        policy_count = len(policy_ids)
        all_policies = (1 << policy_count) - 1
        compatibility = [
            all_policies ^ conflict_adjacency[index] ^ (1 << index)
            for index in range(policy_count)
        ]

        deadline = (
            None if timeout_seconds is None else time.monotonic() + timeout_seconds
        )
        search_nodes = 0
        ranked: list[
            tuple[tuple[int, int, int, tuple[str, ...]], PortfolioResult]
        ] = []

        def ensure_time_remaining() -> None:
            if deadline is not None and time.monotonic() >= deadline:
                raise CalculationLimitExceeded(
                    "portfolio search exceeded timeout_seconds"
                )

        def consume_search_node() -> None:
            nonlocal search_nodes
            search_nodes += 1
            if max_search_nodes is not None and search_nodes > max_search_nodes:
                raise CalculationLimitExceeded(
                    "portfolio search exceeded max_search_nodes"
                )
            ensure_time_remaining()

        def add_maximal_portfolio(member_mask: int) -> None:
            members = tuple(
                policy_ids[index] for index in _bit_indices(member_mask)
            )
            total_won = sum(amounts[policy_id] for policy_id in members)
            unverified_count = _count_pairs(member_mask, unverified_adjacency)
            key = (-total_won, unverified_count, len(members), members)
            result = PortfolioResult(
                rank=0,
                members=list(members),
                total_won=total_won,
            )

            if len(ranked) < limit:
                ranked.append((key, result))
                ranked.sort(key=lambda item: item[0])
            elif key < ranked[-1][0]:
                ranked[-1] = (key, result)
                ranked.sort(key=lambda item: item[0])

        def search(members: int, candidates: int, excluded: int) -> None:
            consume_search_node()

            if candidates == 0 and excluded == 0:
                add_maximal_portfolio(members)
                return

            pivot = _choose_pivot(
                candidates | excluded,
                candidates,
                compatibility,
            )
            pending = candidates & ~compatibility[pivot]

            while pending:
                policy_bit = pending & -pending
                pending ^= policy_bit
                index = policy_bit.bit_length() - 1
                ensure_time_remaining()
                search(
                    members | policy_bit,
                    candidates & compatibility[index],
                    excluded & compatibility[index],
                )
                candidates &= ~policy_bit
                excluded |= policy_bit

        search(0, all_policies, 0)

        return [
            PortfolioResult(
                rank=index,
                members=result["members"],
                total_won=result["total_won"],
            )
            for index, (_, result) in enumerate(ranked, start=1)
        ]
    except MemoryError as error:
        raise CalculationLimitExceeded(
            "portfolio search exceeded available memory"
        ) from error
    except RecursionError as error:
        raise CalculationLimitExceeded(
            "portfolio search exceeded recursion depth"
        ) from error


def _build_adjacency(
    policy_index: dict[str, int],
    pairs: Sequence[Sequence[str]],
    *,
    pair_name: str,
) -> list[int]:
    adjacency = [0] * len(policy_index)

    for pair in pairs:
        pair_ids = tuple(dict.fromkeys(pair))
        if len(pair_ids) != 2:
            raise ValueError(f"{pair_name} must contain distinct policy pairs")

        left = policy_index.get(pair_ids[0])
        right = policy_index.get(pair_ids[1])
        if left is None or right is None:
            continue

        left_bit = 1 << left
        right_bit = 1 << right
        adjacency[left] |= right_bit
        adjacency[right] |= left_bit

    return adjacency


def _choose_pivot(
    choices: int,
    candidates: int,
    compatibility: list[int],
) -> int:
    best_pivot = -1
    best_neighbor_count = -1

    for index in _bit_indices(choices):
        neighbor_count = (candidates & compatibility[index]).bit_count()
        if neighbor_count > best_neighbor_count:
            best_pivot = index
            best_neighbor_count = neighbor_count

    return best_pivot


def _count_pairs(member_mask: int, adjacency: list[int]) -> int:
    pair_endpoints = 0
    for index in _bit_indices(member_mask):
        pair_endpoints += (member_mask & adjacency[index]).bit_count()
    return pair_endpoints // 2


def _bit_indices(bits: int) -> Iterator[int]:
    while bits:
        bit = bits & -bits
        yield bit.bit_length() - 1
        bits ^= bit
