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

The explanation tree built by :func:`compute_standings_explained` is the
single source of truth for the ranking: :func:`compute_standings` simply
flattens it, so the two can never drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence


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


@dataclass(frozen=True)
class MiniLeagueValues:
    """One team's line in a head-to-head mini-league."""

    points: int = 0
    goal_difference: int = 0
    goals_for: int = 0


@dataclass(frozen=True)
class FinalVerdictValues:
    """One team's line when overall criteria decide a subgroup."""

    goal_difference: int
    goals_for: int
    team_id: str


@dataclass
class DecisionPartition:
    """One ordered partition produced at a mini-league level.

    ``kind`` selects how the block was resolved:
    - ``"single"``: a block of one team, already decided by this level.
    - ``"mini_league"``: a still-tied block resolved by a fresh mini-league
      rebuilt only from the block's mutual matches.
    - ``"final"``: the mini-league separated nothing at this level; the
      block is ordered by overall goal difference, overall goals scored
      and team id.
    """

    kind: Literal["single", "mini_league", "final"]
    members: list[str]
    partitions: list["DecisionPartition"] | None = None
    mini_table: dict[str, MiniLeagueValues] | None = None
    final_table: list[FinalVerdictValues] | None = None


@dataclass
class PointsGroup:
    """All teams sharing the same total points, in final ranking order."""

    points: int
    members: list[str]
    mini_table: dict[str, MiniLeagueValues]
    partitions: list[DecisionPartition]


def _mini_league(
    group: Sequence[str], matches: Sequence[MatchLike]
) -> dict[str, MiniLeagueValues]:
    """Build a table using only the matches played between members of group."""
    members = set(group)
    table = {team: MiniLeagueValues() for team in group}
    for match in matches:
        if match.home in members and match.away in members:
            home, away = table[match.home], table[match.away]
            home_goals, away_goals = match.home_score, match.away_score
            table[match.home] = MiniLeagueValues(
                home.points
                + (3 if home_goals > away_goals else 1 if home_goals == away_goals else 0),
                home.goal_difference + home_goals - away_goals,
                home.goals_for + home_goals,
            )
            table[match.away] = MiniLeagueValues(
                away.points
                + (3 if away_goals > home_goals else 1 if home_goals == away_goals else 0),
                away.goal_difference + away_goals - home_goals,
                away.goals_for + away_goals,
            )
    return table


def _mini_key(table: dict[str, MiniLeagueValues], team: str) -> tuple[int, int, int]:
    row = table[team]
    return (-row.points, -row.goal_difference, -row.goals_for)


def _final_order(
    group: Sequence[str], stats: dict[str, TeamStats]
) -> tuple[list[str], list[FinalVerdictValues]]:
    """Final tiebreak: overall goal difference, goals scored, team id."""
    rows = [
        FinalVerdictValues(
            goal_difference=stats[team].goal_difference,
            goals_for=stats[team].goals_for,
            team_id=team,
        )
        for team in group
    ]
    rows.sort(key=lambda row: (-row.goal_difference, -row.goals_for, row.team_id))
    return [row.team_id for row in rows], rows


def _build_partitions(
    group: Sequence[str],
    matches: Sequence[MatchLike],
    stats: dict[str, TeamStats],
) -> tuple[list[str], dict[str, MiniLeagueValues], list[DecisionPartition]]:
    """Resolve one point-tied group, returning its in-order members.

    The returned members are the concatenation of the partitions' members,
    i.e. the partitions themselves are the ranking: callers must not sort
    the group independently.
    """
    table = _mini_league(group, matches)
    ordered = sorted(group, key=lambda t: _mini_key(table, t))

    members: list[str] = []
    partitions: list[DecisionPartition] = []
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and _mini_key(table, ordered[j]) == _mini_key(
            table, ordered[i]
        ):
            j += 1
        block = ordered[i:j]
        if len(block) == 1:
            partition = DecisionPartition(kind="single", members=list(block))
        elif len(block) == len(group):
            # The mini-league separates nothing: go to overall criteria.
            final_members, final_table = _final_order(block, stats)
            partition = DecisionPartition(
                kind="final", members=final_members, final_table=final_table
            )
        else:
            # Partially separated: rebuild the mini-league for the
            # still-tied subgroup using only their mutual matches.
            sub_members, sub_table, sub_partitions = _build_partitions(
                block, matches, stats
            )
            partition = DecisionPartition(
                kind="mini_league",
                members=sub_members,
                partitions=sub_partitions,
                mini_table=sub_table,
            )
        members.extend(partition.members)
        partitions.append(partition)
        i = j
    return members, table, partitions


def compute_standings_explained(
    teams: Sequence[str], matches: Sequence[MatchLike]
) -> tuple[list[str], dict[str, TeamStats], list[PointsGroup]]:
    """Return the final order, overall stats and the decision tree.

    The tree lists one :class:`PointsGroup` per distinct total-points
    value (most points first). Concatenating ``members`` across the
    groups (and recursively across partitions) yields exactly the
    returned final order.
    """
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
    groups: list[PointsGroup] = []
    i = 0
    while i < len(by_points):
        j = i + 1
        while (
            j < len(by_points)
            and stats[by_points[j]].points == stats[by_points[i]].points
        ):
            j += 1
        group = by_points[i:j]
        members, mini_table, partitions = _build_partitions(
            group, matches, stats
        )
        groups.append(
            PointsGroup(
                points=stats[group[0]].points,
                members=members,
                mini_table=mini_table,
                partitions=partitions,
            )
        )
        ordered.extend(members)
        i = j
    return ordered, stats, groups


def compute_standings(
    teams: Sequence[str], matches: Sequence[MatchLike]
) -> tuple[list[str], dict[str, TeamStats]]:
    """Return the teams in final ranking order together with their stats."""
    ordered, stats, _ = compute_standings_explained(teams, matches)
    return ordered, stats
