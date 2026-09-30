"""FastAPI application exposing the round-robin standings endpoint."""
from __future__ import annotations

from typing import Sequence

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .scenarios import compute_scenarios
from .schemas import (
    DecisionNodeOut,
    DecisionPartitionOut,
    FinalTieBreakOut,
    MatchIn,
    MiniLeagueRowOut,
    PointsGroupOut,
    QualificationRequest,
    QualificationResponse,
    RoundRobinScheduleRequest,
    RoundRobinScheduleResponse,
    ScenarioEntry,
    ScheduledMatchOut,
    ScheduleRound,
    StandingEntry,
    StandingsExplanationResponse,
    StandingsRequest,
    StandingsResponse,
    TieBreakRowOut,
)
from .scheduling import ScheduleValidationError, compute_schedule
from .standings import (
    DecisionNode,
    FinalTieBreak,
    StandingsValidationError,
    TeamStats,
    compute_explanation,
    compute_standings,
)

app = FastAPI(
    title="Round-Robin Standings API",
    version="1.3.0",
    description=(
        "Compute a round-robin league table from completed matches. "
        "Win = 3 points, draw = 1. Ties are broken by head-to-head "
        "mini-league (points, goal difference, goals scored, rebuilt "
        "recursively for still-tied subgroups), then overall goal "
        "difference, overall goals scored and team id. "
        "POST /standings-explanation returns the same table together "
        "with a machine-readable decision tree narrating every tie-break. "
        "POST /qualification-scenarios enumerates the candidate scores "
        "of the remaining fixtures and reports each team's best and "
        "worst reachable rank (guaranteed / possible / eliminated "
        "against top_k), backed by witnessing result sets. "
        "POST /round-robin-schedules generates the lexicographically "
        "smallest feasible single round-robin schedule that respects "
        "unavailable rounds, fixed matches, home/away balance and the "
        "no-three-consecutive-games-on-one-side rule."
    ),
)


@app.exception_handler(StandingsValidationError)
async def standings_validation_handler(
    request: Request, exc: StandingsValidationError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(ScheduleValidationError)
async def schedule_validation_handler(
    request: Request, exc: ScheduleValidationError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def _standings_entries(
    ordered: Sequence[str], stats: dict[str, TeamStats]
) -> list[StandingEntry]:
    """Build the response rows for the given final ranking order."""
    entries = []
    for index, team in enumerate(ordered):
        row = stats[team]
        entries.append(
            StandingEntry(
                rank=index + 1,
                team=team,
                played=row.played,
                wins=row.wins,
                draws=row.draws,
                losses=row.losses,
                goals_for=row.goals_for,
                goals_against=row.goals_against,
                goal_difference=row.goal_difference,
                points=row.points,
            )
        )
    return entries


def _final_tie_break_out(
    tie_break: FinalTieBreak,
) -> FinalTieBreakOut:
    return FinalTieBreakOut(
        order=list(tie_break.order),
        table=[
            TieBreakRowOut(
                team=row.team,
                goal_difference=row.goal_difference,
                goals_for=row.goals_for,
            )
            for row in tie_break.table
        ],
        criteria=list(tie_break.criteria),
    )


def _decision_node_out(node: DecisionNode) -> DecisionNodeOut:
    partitions = []
    for partition in node.partitions:
        partitions.append(
            DecisionPartitionOut(
                teams=list(partition.teams),
                points=partition.points,
                goal_difference=partition.goal_difference,
                goals_for=partition.goals_for,
                child=(
                    _decision_node_out(partition.child)
                    if partition.child is not None
                    else None
                ),
                tie_break=(
                    _final_tie_break_out(partition.tie_break)
                    if partition.tie_break is not None
                    else None
                ),
            )
        )
    return DecisionNodeOut(
        members=list(node.members),
        table=[
            MiniLeagueRowOut(
                team=row.team,
                points=row.points,
                goal_difference=row.goal_difference,
                goals_for=row.goals_for,
            )
            for row in node.table
        ],
        partitions=partitions,
    )


@app.post(
    "/standings",
    response_model=StandingsResponse,
    responses={422: {"description": "Invalid teams or matches"}},
)
def post_standings(payload: StandingsRequest) -> StandingsResponse:
    """Compute the league table for the given teams and completed matches."""
    ordered, stats = compute_standings(payload.teams, payload.matches)
    return StandingsResponse(standings=_standings_entries(ordered, stats))


@app.post(
    "/standings-explanation",
    response_model=StandingsExplanationResponse,
    responses={422: {"description": "Invalid teams or matches"}},
)
def post_standings_explanation(
    payload: StandingsRequest,
) -> StandingsExplanationResponse:
    """Compute the table and a machine-readable ranking decision tree.

    The request body is the same as POST /standings and ``standings`` in
    the response is the identical table. ``points_groups`` groups teams
    by total points (descending); each group's ``decision`` node lists
    the head-to-head mini-league built only from intra-group matches
    (points, goal difference, goals scored) and the ordered partitions
    it produces. Partitions that remain tied carry a rebuilt child
    mini-league; a group the mini-league cannot split at all carries a
    final tie-break on overall goal difference, overall goals scored and
    team id. Concatenating every node's ``members`` in tree order
    reproduces the returned standings.
    """
    ordered, stats, explanation = compute_explanation(
        payload.teams, payload.matches
    )
    return StandingsExplanationResponse(
        standings=_standings_entries(ordered, stats),
        points_groups=[
            PointsGroupOut(
                points=group.points,
                teams=list(group.teams),
                decision=_decision_node_out(group.decision),
            )
            for group in explanation.points_groups
        ],
    )


@app.post(
    "/qualification-scenarios",
    response_model=QualificationResponse,
    responses={422: {"description": "Invalid teams, matches or remaining fixtures"}},
)
def post_qualification_scenarios(
    payload: QualificationRequest,
) -> QualificationResponse:
    """Enumerate candidate results of the remaining fixtures.

    Every combination of candidate scores is evaluated with the same
    ranking rules as /standings. For each team the best and worst
    reachable rank is reported, together with a witnessing set of
    unplayed results for each extreme, and the team is classified as
    guaranteed / possible / eliminated against top_k.
    """
    scenarios, combinations = compute_scenarios(
        payload.teams, payload.matches, payload.top_k, payload.remaining
    )
    entries = []
    for scenario in scenarios:
        entries.append(
            ScenarioEntry(
                team=scenario.team,
                status=scenario.status,
                best_rank=scenario.best_rank,
                worst_rank=scenario.worst_rank,
                best_witness=[
                    MatchIn(
                        home=result.home,
                        away=result.away,
                        home_score=result.home_score,
                        away_score=result.away_score,
                    )
                    for result in scenario.best_witness
                ],
                worst_witness=[
                    MatchIn(
                        home=result.home,
                        away=result.away,
                        home_score=result.home_score,
                        away_score=result.away_score,
                    )
                    for result in scenario.worst_witness
                ],
            )
        )
    return QualificationResponse(
        top_k=payload.top_k, combinations=combinations, scenarios=entries
    )


@app.post(
    "/round-robin-schedules",
    response_model=RoundRobinScheduleResponse,
    responses={422: {"description": "Invalid request, fixed-match conflict or no feasible schedule"}},
)
def post_round_robin_schedule(
    payload: RoundRobinScheduleRequest,
) -> RoundRobinScheduleResponse:
    """Generate the lexicographically smallest feasible round-robin schedule.

    Every pair of teams plays exactly once and each team plays at most
    once per round. Fixed matches keep their round and home/away sides.
    All other matches avoid the given unavailable rounds, keep each
    team's home/away counts within one of each other, and never give a
    team three consecutive games (in round order, idle rounds skipped)
    all at home or all away. Each round's matches are sorted by home id
    then away id and the schedule is the lexicographically smallest
    round-by-round sequence among all feasible schedules.
    """
    if len(set(payload.teams)) != len(payload.teams):
        raise ScheduleValidationError("duplicate team identifiers are not allowed")

    unavailable = {team: set() for team in payload.teams}
    for entry in payload.unavailability:
        if entry.team not in unavailable:
            raise ScheduleValidationError(f"unknown team: '{entry.team}'")
        unavailable[entry.team].update(entry.unavailable_rounds)

    schedule = compute_schedule(
        payload.teams, payload.rounds, unavailable, payload.fixed_matches
    )
    rounds = [
        ScheduleRound(
            round=index + 1,
            matches=[
                ScheduledMatchOut(home=match.home, away=match.away)
                for match in round_matches
            ],
        )
        for index, round_matches in enumerate(schedule)
    ]
    return RoundRobinScheduleResponse(
        teams=list(payload.teams),
        rounds=payload.rounds,
        schedule=rounds,
    )
