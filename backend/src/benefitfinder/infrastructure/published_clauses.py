"""In-memory published-clause adapter for local F13 verification; not a database adapter."""

from benefitfinder.application.policy_explanations import ExplanationRequest, PublishedClause


class InMemoryPublishedClauses:
    def __init__(self, clauses: tuple[PublishedClause, ...]) -> None:
        self._clauses = clauses

    async def search(
        self, request: ExplanationRequest, *, limit: int
    ) -> tuple[PublishedClause, ...]:
        if not 1 <= limit <= 5:
            raise ValueError("invalid_clause_search_limit")
        topics = {key for key, _ in request.conditions} | {"amount", "application"}
        matches = [
            clause
            for clause in self._clauses
            if clause.published
            and clause.policy_id == request.policy_id
            and clause.document_version == request.document_version
            and topics.intersection(clause.topics)
        ]
        matches.sort(key=lambda clause: (-len(topics.intersection(clause.topics)), clause.id))
        return tuple(matches[:limit])
