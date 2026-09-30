"""Pydantic schemas for the standings API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .scheduling import MAX_ROUNDS, MAX_TEAMS


class MatchIn(BaseModel):
    """A completed match between two teams."""

    home: str = Field(min_length=1, examples=["alpha"])
    away: str = Field(min_length=1, examples=["bravo"])
    home_score: int = Field(ge=0, examples=[2])
    away_score: int = Field(ge=0, examples=[1])


class StandingsRequest(BaseModel):
    """Teams participating in the round-robin and the completed matches."""

    teams: list[str] = Field(min_length=1, examples=[["alpha", "bravo", "charlie"]])
    matches: list[MatchIn] = Field(default_factory=list)


class StandingEntry(BaseModel):
    """One row of the final table."""

    rank: int
    team: str
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int


class StandingsResponse(BaseModel):
    standings: list[StandingEntry]


class MiniLeagueRowOut(BaseModel):
    """One team's line in a head-to-head mini-league table."""

    team: str
    points: int
    goal_difference: int
    goals_for: int


class TieBreakRowOut(BaseModel):
    """One team's line in the final overall-criteria tie-break."""

    team: str
    goal_difference: int
    goals_for: int


class FinalTieBreakOut(BaseModel):
    """Terminal ruling for a subgroup the mini-leagues cannot separate.

    ``order`` lists the teams in their final relative order, produced by
    the named ``criteria`` applied successively (overall goal
    difference, overall goals scored, then team id); ``table`` carries
    the underlying overall figures in that same order.
    """

    order: list[str]
    table: list[TieBreakRowOut]
    criteria: list[Literal["overall_goal_difference", "overall_goals_for", "team_id"]]


class DecisionPartitionOut(BaseModel):
    """One ordered block formed by a mini-league key.

    Every team in the block shares mini-league ``points``,
    ``goal_difference`` and ``goals_for``. A multi-team block carries
    either a rebuilt ``child`` mini-league (partial separation) or a
    ``tie_break`` ruling (the node could not separate anyone).
    """

    teams: list[str]
    points: int
    goal_difference: int
    goals_for: int
    child: "DecisionNodeOut | None" = None
    tie_break: FinalTieBreakOut | None = None


class DecisionNodeOut(BaseModel):
    """A head-to-head ruling for one point-tied group (or subgroup).

    ``members`` is the final relative order contributed by this node;
    concatenating every node's members in tree order reproduces the
    standings. ``table`` are the mini-league lines in that same order
    and ``partitions`` are the ordered key-equal blocks.
    """

    members: list[str]
    table: list[MiniLeagueRowOut]
    partitions: list[DecisionPartitionOut]


class PointsGroupOut(BaseModel):
    """All teams sharing one total-points value, with its ruling node."""

    points: int
    teams: list[str]
    decision: DecisionNodeOut


class StandingsExplanationResponse(BaseModel):
    """The same table as /standings plus the ranking decision tree."""

    standings: list[StandingEntry]
    points_groups: list[PointsGroupOut]


DecisionPartitionOut.model_rebuild()


class ScoreCandidate(BaseModel):
    """One possible score for an unplayed fixture."""

    home_score: int = Field(ge=0, examples=[2])
    away_score: int = Field(ge=0, examples=[1])


class RemainingFixture(BaseModel):
    """An unplayed fixture together with its finite candidate scores."""

    home: str = Field(min_length=1, examples=["delta"])
    away: str = Field(min_length=1, examples=["alpha"])
    candidates: list[ScoreCandidate] = Field(min_length=1)


class QualificationRequest(BaseModel):
    """Completed matches plus candidate scores for every unplayed fixture."""

    teams: list[str] = Field(min_length=1, examples=[["alpha", "bravo", "charlie"]])
    matches: list[MatchIn] = Field(default_factory=list)
    top_k: int = Field(ge=1, examples=[2])
    remaining: list[RemainingFixture] = Field(default_factory=list)


class ScenarioEntry(BaseModel):
    """Best/worst reachable rank for one team, each with a witness.

    A witness is a complete set of results for the unplayed fixtures
    (one entry per fixture, in request order) under which the team
    finishes at the corresponding extreme rank.
    """

    team: str
    status: Literal["guaranteed", "possible", "eliminated"]
    best_rank: int
    worst_rank: int
    best_witness: list[MatchIn]
    worst_witness: list[MatchIn]


class QualificationResponse(BaseModel):
    top_k: int
    combinations: int
    scenarios: list[ScenarioEntry]


class TeamUnavailability(BaseModel):
    """The rounds a team cannot play in (1-based round numbers)."""

    team: str = Field(min_length=1, examples=["alpha"])
    unavailable_rounds: list[int] = Field(
        default_factory=list, examples=[[2, 4]]
    )


class FixedMatchIn(BaseModel):
    """A match pinned to a specific round with a fixed home/away order."""

    home: str = Field(min_length=1, examples=["alpha"])
    away: str = Field(min_length=1, examples=["bravo"])
    round: int = Field(ge=1, le=MAX_ROUNDS, examples=[1])


class RoundRobinScheduleRequest(BaseModel):
    """Teams, total rounds, unavailability windows and pinned matches."""

    teams: list[str] = Field(
        min_length=2,
        max_length=MAX_TEAMS,
        examples=[["alpha", "bravo", "charlie", "delta"]],
    )
    rounds: int = Field(
        ge=1,
        le=MAX_ROUNDS,
        description=(
            "Total number of rounds; must be at least the single round-robin "
            "minimum (n-1 for even n, n for odd n) and at most "
            f"{MAX_ROUNDS}."
        ),
        examples=[3],
    )
    unavailability: list[TeamUnavailability] = Field(default_factory=list)
    fixed_matches: list[FixedMatchIn] = Field(default_factory=list)


class ScheduledMatchOut(BaseModel):
    """One scheduled match in a round."""

    home: str
    away: str


class ScheduleRound(BaseModel):
    """All matches of one round, sorted by (home id, away id)."""

    round: int
    matches: list[ScheduledMatchOut]


class RoundRobinScheduleResponse(BaseModel):
    teams: list[str]
    rounds: int
    schedule: list[ScheduleRound]
