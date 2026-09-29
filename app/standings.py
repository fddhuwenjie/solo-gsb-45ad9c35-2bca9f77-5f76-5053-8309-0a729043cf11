"""Core round-robin standings computation.

Ranking rules (applied in order):
1. Total points (win = 3, draw = 1, loss = 0).
2. For teams tied on points: a head-to-head mini-league built only from
   the matches between the tied teams, ordered by mini-league points,
   then mini-league goal difference, then mini-league goals scored.
3. If the mini-league only separates part of the group, a new mini-league
   is built for each subgroup that is still tied (recursively).
4. Teams still tied afterwards are ordered by overall goal difference,
   then overall goals scored, then team id (ascending).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


class MatchLike(Protocol):
    home: str
    away: str
    home_score: int
    away_score: int


class StandingsValidationError(ValueError):
    """Raised when the submitted teams/matches are inconsistent."""


@dataclass
class TeamStats:
    team: str
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


def validate_input(teams: Sequence[str], matches: Sequence[MatchLike]) -> None:
    """Reject unknown teams, self matches and duplicate pairings."""
    if not teams:
        raise StandingsValidationError("at least one team is required")
    if len(set(teams)) != len(teams):
        raise StandingsValidationError("duplicate team identifiers are not allowed")

    known = set(teams)
    seen_pairs: set[frozenset[str]] = set()
    for match in matches:
        if match.home == match.away:
            raise StandingsValidationError(
                f"team '{match.home}' cannot play against itself"
            )
        for side in (match.home, match.away):
            if side not in known:
                raise StandingsValidationError(f"unknown team: '{side}'")
        pair = frozenset((match.home, match.away))
        if pair in seen_pairs:
            raise StandingsValidationError(
                f"duplicate matchup: '{match.home}' vs '{match.away}'"
            )
        seen_pairs.add(pair)


@dataclass
class _MiniRow:
    points: int = 0
    goal_difference: int = 0
    goals_for: int = 0


def _mini_league(
    group: Sequence[str], matches: Sequence[MatchLike]
) -> dict[str, _MiniRow]:
    """Build a table using only the matches played between members of group."""
    members = set(group)
    table = {team: _MiniRow() for team in group}
    for match in matches:
        if match.home in members and match.away in members:
            home, away = table[match.home], table[match.away]
            home.goals_for += match.home_score
            home.goal_difference += match.home_score - match.away_score
            away.goals_for += match.away_score
            away.goal_difference += match.away_score - match.home_score
            if match.home_score > match.away_score:
                home.points += 3
            elif match.home_score < match.away_score:
                away.points += 3
            else:
                home.points += 1
                away.points += 1
    return table


def _mini_key(table: dict[str, _MiniRow], team: str) -> tuple[int, int, int]:
    row = table[team]
    return (-row.points, -row.goal_difference, -row.goals_for)


def _overall_order(group: Sequence[str], stats: dict[str, TeamStats]) -> list[str]:
    """Final tiebreak: overall goal difference, goals scored, team id."""
    return sorted(
        group,
        key=lambda t: (-stats[t].goal_difference, -stats[t].goals_for, t),
    )


def _order_tied_group(
    group: Sequence[str],
    matches: Sequence[MatchLike],
    stats: dict[str, TeamStats],
) -> list[str]:
    """Order a point-tied group via head-to-head mini-leagues.

    Subgroups that remain tied get a fresh mini-league built only from
    their mutual matches; groups that cannot be separated at all fall
    back to the overall criteria.
    """
    if len(group) == 1:
        return list(group)

    table = _mini_league(group, matches)
    ordered = sorted(group, key=lambda t: _mini_key(table, t))

    result: list[str] = []
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and _mini_key(table, ordered[j]) == _mini_key(
            table, ordered[i]
        ):
            j += 1
        block = ordered[i:j]
        if len(block) == 1:
            result.extend(block)
        elif len(block) == len(group):
            # The mini-league separates nothing: go to overall criteria.
            result.extend(_overall_order(block, stats))
        else:
            # Partially separated: rebuild the mini-league for the
            # still-tied subgroup using only their mutual matches.
            result.extend(_order_tied_group(block, matches, stats))
        i = j
    return result


def compute_standings(
    teams: Sequence[str], matches: Sequence[MatchLike]
) -> tuple[list[str], dict[str, TeamStats]]:
    """Return the teams in final ranking order together with their stats."""
    validate_input(teams, matches)

    stats = {team: TeamStats(team=team) for team in teams}
    for match in matches:
        home, away = stats[match.home], stats[match.away]
        home.played += 1
        away.played += 1
        home.goals_for += match.home_score
        home.goals_against += match.away_score
        away.goals_for += match.away_score
        away.goals_against += match.home_score
        if match.home_score > match.away_score:
            home.wins += 1
            home.points += 3
            away.losses += 1
        elif match.home_score < match.away_score:
            away.wins += 1
            away.points += 3
            home.losses += 1
        else:
            home.draws += 1
            away.draws += 1
            home.points += 1
            away.points += 1

    by_points = sorted(teams, key=lambda t: -stats[t].points)
    ordered: list[str] = []
    i = 0
    while i < len(by_points):
        j = i + 1
        while (
            j < len(by_points)
            and stats[by_points[j]].points == stats[by_points[i]].points
        ):
            j += 1
        group = by_points[i:j]
        if len(group) == 1:
            ordered.extend(group)
        else:
            ordered.extend(_order_tied_group(group, matches, stats))
        i = j
    return ordered, stats
