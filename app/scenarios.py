"""Qualification scenario analysis over unplayed fixtures.

Given the completed matches and, for every unplayed fixture, a finite
list of candidate scores, every combination of results is enumerated
(up to ``MAX_COMBINATIONS``) and the ranking rules from
:mod:`app.standings` are applied to each one. For every team this
yields the best and worst rank it can still reach; each extreme is
backed by a witness, i.e. one complete set of results for the unplayed
fixtures that realises it.

Classification against ``top_k``:
- ``guaranteed``: the worst reachable rank is still within the top k.
- ``possible``: the team can finish outside the top k, but its best
  reachable rank is within it.
- ``eliminated``: even the best reachable rank is outside the top k.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from math import prod
from typing import Protocol, Sequence

from .standings import (
    MatchLike,
    StandingsValidationError,
    compute_standings,
    validate_input,
)

MAX_COMBINATIONS = 100_000

STATUS_GUARANTEED = "guaranteed"
STATUS_POSSIBLE = "possible"
STATUS_ELIMINATED = "eliminated"


class ScoreLike(Protocol):
    home_score: int
    away_score: int


class FixtureLike(Protocol):
    home: str
    away: str
    candidates: Sequence[ScoreLike]


@dataclass(frozen=True)
class PlayedResult:
    """An unplayed fixture resolved to one concrete score."""

    home: str
    away: str
    home_score: int
    away_score: int


@dataclass
class TeamScenario:
    """Rank extremes for one team, each with a witnessing set of results."""

    team: str
    status: str
    best_rank: int
    worst_rank: int
    best_witness: list[PlayedResult] = field(default_factory=list)
    worst_witness: list[PlayedResult] = field(default_factory=list)


def classify(best_rank: int, worst_rank: int, top_k: int) -> str:
    """Map a team's rank extremes to its qualification status."""
    if worst_rank <= top_k:
        return STATUS_GUARANTEED
    if best_rank <= top_k:
        return STATUS_POSSIBLE
    return STATUS_ELIMINATED


def validate_remaining(
    teams: Sequence[str],
    matches: Sequence[MatchLike],
    fixtures: Sequence[FixtureLike],
) -> None:
    """Reject unplayed fixtures that are inconsistent with the input.

    A fixture may not involve unknown teams, may not be a self match and
    its pairing may not duplicate a completed match or another unplayed
    fixture (home/away swapped counts as the same pairing). Every
    fixture must carry at least one candidate score and no candidate
    score may be negative.
    """
    known = set(teams)
    seen_pairs = {frozenset((match.home, match.away)) for match in matches}
    for fixture in fixtures:
        if fixture.home == fixture.away:
            raise StandingsValidationError(
                f"team '{fixture.home}' cannot play against itself"
            )
        for side in (fixture.home, fixture.away):
            if side not in known:
                raise StandingsValidationError(f"unknown team: '{side}'")
        pair = frozenset((fixture.home, fixture.away))
        if pair in seen_pairs:
            raise StandingsValidationError(
                f"duplicate matchup: '{fixture.home}' vs '{fixture.away}'"
            )
        seen_pairs.add(pair)
        if not fixture.candidates:
            raise StandingsValidationError(
                f"fixture '{fixture.home}' vs '{fixture.away}' "
                "must list at least one candidate score"
            )
        for candidate in fixture.candidates:
            if candidate.home_score < 0 or candidate.away_score < 0:
                raise StandingsValidationError(
                    "candidate scores must not be negative"
                )


def compute_scenarios(
    teams: Sequence[str],
    matches: Sequence[MatchLike],
    top_k: int,
    fixtures: Sequence[FixtureLike],
) -> tuple[list[TeamScenario], int]:
    """Enumerate every combination of candidate results.

    Returns the per-team scenarios, ordered by best rank, then worst
    rank, then team id, together with the number of combinations that
    were evaluated.
    """
    if top_k < 1:
        raise StandingsValidationError("top_k must be at least 1")
    validate_input(teams, matches)
    validate_remaining(teams, matches, fixtures)

    candidate_lists = [list(fixture.candidates) for fixture in fixtures]
    total = prod(len(candidates) for candidates in candidate_lists)
    if total > MAX_COMBINATIONS:
        raise StandingsValidationError(
            f"too many combinations: {total} exceeds the limit of "
            f"{MAX_COMBINATIONS}"
        )

    played = list(matches)
    best_rank = {team: len(teams) + 1 for team in teams}
    worst_rank = {team: 0 for team in teams}
    best_witness: dict[str, list[PlayedResult]] = {team: [] for team in teams}
    worst_witness: dict[str, list[PlayedResult]] = {team: [] for team in teams}

    for combo in product(*candidate_lists):
        results = [
            PlayedResult(
                fixture.home,
                fixture.away,
                candidate.home_score,
                candidate.away_score,
            )
            for fixture, candidate in zip(fixtures, combo)
        ]
        ordered, _ = compute_standings(teams, played + results)
        for index, team in enumerate(ordered):
            rank = index + 1
            if rank < best_rank[team]:
                best_rank[team] = rank
                best_witness[team] = results
            if rank > worst_rank[team]:
                worst_rank[team] = rank
                worst_witness[team] = results

    scenarios = [
        TeamScenario(
            team=team,
            status=classify(best_rank[team], worst_rank[team], top_k),
            best_rank=best_rank[team],
            worst_rank=worst_rank[team],
            best_witness=best_witness[team],
            worst_witness=worst_witness[team],
        )
        for team in teams
    ]
    scenarios.sort(key=lambda s: (s.best_rank, s.worst_rank, s.team))
    return scenarios, total
