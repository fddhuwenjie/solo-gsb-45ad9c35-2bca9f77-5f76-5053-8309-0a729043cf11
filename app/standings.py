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

The same pass builds both the final ranking and the machine-readable
decision tree (:func:`compute_explanation`): every tree node records the
exact ordered list of members it contributes to the table, so flattening
the tree reproduces the standings without a second sort.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class _MiniRow:
    points: int = 0
    goal_difference: int = 0
    goals_for: int = 0


@dataclass(frozen=True)
class MiniLeagueRow:
    """One team's line in a head-to-head mini-league table."""

    team: str
    points: int
    goal_difference: int
    goals_for: int


@dataclass(frozen=True)
class TieBreakRow:
    """One team's line in the final overall-criteria tie-break."""

    team: str
    goal_difference: int
    goals_for: int


FinalCriterion = Literal["overall_goal_difference", "overall_goals_for", "team_id"]

FINAL_CRITERIA: tuple[FinalCriterion, ...] = (
    "overall_goal_difference",
    "overall_goals_for",
    "team_id",
)


@dataclass
class FinalTieBreak:
    """The terminal ruling for a subgroup the mini-leagues cannot split.

    ``order`` is the final ranking of the subgroup produced by applying
    overall goal difference, overall goals scored and then team id; it
    is the same list stored on the owning partition.
    """

    order: list[str]
    table: list[TieBreakRow]
    criteria: tuple[FinalCriterion, ...] = FINAL_CRITERIA


@dataclass
class DecisionPartition:
    """One ordered block produced by a node's mini-league.

    Teams in the same partition share mini-league points, goal
    difference and goals scored. Exactly one of ``child`` (the block was
    partially separated and a fresh mini-league was rebuilt for it) or
    ``tie_break`` (the whole node was inseparable) is set for a
    multi-team block; both are ``None`` for a singleton block.
    """

    teams: list[str]
    points: int
    goal_difference: int
    goals_for: int
    child: "DecisionNode | None" = None
    tie_break: FinalTieBreak | None = None


@dataclass
class DecisionNode:
    """A head-to-head ruling over one point-tied group (or subgroup).

    ``members`` is the final ranking contributed by this node, in
    order; concatenating every node's members in tree order reproduces
    the final standings. ``table`` lists the mini-league line of each
    member in that same order. ``partitions`` are the ordered blocks
    formed by the mini-league key (points, goal difference, goals
    scored); their concatenation equals ``members``.
    """

    members: list[str]
    table: list[MiniLeagueRow]
    partitions: list[DecisionPartition] = field(default_factory=list)


@dataclass
class PointsGroup:
    """All teams sharing one total-points value, with its ruling tree."""

    points: int
    teams: list[str]
    decision: DecisionNode


@dataclass
class Explanation:
    """The ordered point groups forming the root of the decision tree."""

    points_groups: list[PointsGroup]


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


def _build_decision_node(
    group: Sequence[str],
    matches: Sequence[MatchLike],
    stats: dict[str, TeamStats],
) -> DecisionNode:
    """Build the head-to-head ruling node for a point-tied group.

    The node's ``members`` list is the group's final ranking; it is the
    sole ordering produced here and feeds both the standings and the
    tree, so the two cannot drift apart.
    """
    table = _mini_league(group, matches)
    ordered = sorted(group, key=lambda t: _mini_key(table, t))

    partitions: list[DecisionPartition] = []
    members: list[str] = []
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and _mini_key(table, ordered[j]) == _mini_key(
            table, ordered[i]
        ):
            j += 1
        block = ordered[i:j]
        row = table[block[0]]
        if len(block) == 1:
            # Separated outright by the mini-league key.
            partition = DecisionPartition(
                teams=list(block),
                points=row.points,
                goal_difference=row.goal_difference,
                goals_for=row.goals_for,
            )
        elif len(block) == len(group):
            # The mini-league separates nothing: final overall criteria.
            final_order = _overall_order(block, stats)
            tie_break = FinalTieBreak(
                order=list(final_order),
                table=[
                    TieBreakRow(
                        team=team,
                        goal_difference=stats[team].goal_difference,
                        goals_for=stats[team].goals_for,
                    )
                    for team in final_order
                ],
            )
            partition = DecisionPartition(
                teams=list(final_order),
                points=row.points,
                goal_difference=row.goal_difference,
                goals_for=row.goals_for,
                tie_break=tie_break,
            )
        else:
            # Partially separated: rebuild the mini-league for the
            # still-tied subgroup using only their mutual matches.
            child = _build_decision_node(block, matches, stats)
            partition = DecisionPartition(
                teams=list(child.members),
                points=row.points,
                goal_difference=row.goal_difference,
                goals_for=row.goals_for,
                child=child,
            )
        partitions.append(partition)
        members.extend(partition.teams)
        i = j

    return DecisionNode(
        members=members,
        table=[
            MiniLeagueRow(
                team=team,
                points=table[team].points,
                goal_difference=table[team].goal_difference,
                goals_for=table[team].goals_for,
            )
            for team in members
        ],
        partitions=partitions,
    )


def _aggregate_stats(
    teams: Sequence[str], matches: Sequence[MatchLike]
) -> dict[str, TeamStats]:
    """Accumulate the overall table from the completed matches."""
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
    return stats


def _compute(
    teams: Sequence[str], matches: Sequence[MatchLike]
) -> tuple[list[str], dict[str, TeamStats], list[PointsGroup]]:
    """Validate, aggregate and rank, producing standings and tree together."""
    validate_input(teams, matches)
    stats = _aggregate_stats(teams, matches)

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
        node = _build_decision_node(group, matches, stats)
        ordered.extend(node.members)
        groups.append(
            PointsGroup(points=stats[group[0]].points, teams=node.members, decision=node)
        )
        i = j
    return ordered, stats, groups


def compute_standings(
    teams: Sequence[str], matches: Sequence[MatchLike]
) -> tuple[list[str], dict[str, TeamStats]]:
    """Return the teams in final ranking order together with their stats."""
    ordered, stats, _ = _compute(teams, matches)
    return ordered, stats


def compute_explanation(
    teams: Sequence[str], matches: Sequence[MatchLike]
) -> tuple[list[str], dict[str, TeamStats], Explanation]:
    """Return the ranking, stats and the head-to-head decision tree.

    The ranking is identical to :func:`compute_standings` because both
    are read from the same computation; the tree's members concatenated
    in order reproduce the returned ranking.
    """
    ordered, stats, groups = _compute(teams, matches)
    return ordered, stats, Explanation(points_groups=groups)
