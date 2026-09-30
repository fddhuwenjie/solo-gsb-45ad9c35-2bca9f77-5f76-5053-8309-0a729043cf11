"""FastAPI application exposing the round-robin standings endpoint."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .scenarios import compute_scenarios
from .schemas import (
    DecisionPartitionOut,
    FinalVerdictRowOut,
    MatchIn,
    MiniLeagueRowOut,
    PointsGroupOut,
    QualificationRequest,
    QualificationResponse,
    RankingExplanationOut,
    RoundRobinScheduleRequest,
    RoundRobinScheduleResponse,
    ScenarioEntry,
    ScheduledMatchOut,
    ScheduleRound,
    StandingEntry,
    StandingsExplanationResponse,
    StandingsRequest,
    StandingsResponse,
)
from .scheduling import ScheduleValidationError, compute_schedule
from .standings import (
    DecisionPartition,
    PointsGroup,
    StandingsValidationError,
    compute_standings,
    compute_standings_explained,
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
        "POST /standings/explanation returns the same table together "
        "with the machine-readable ranking decision tree. "
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


def _standing_entries(ordered, stats) -> list[StandingEntry]:
    """Build /standings rows from the shared final order and stats."""
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


def _mini_rows_out(members, mini_table) -> list[MiniLeagueRowOut]:
    """Serialize a mini-league table, rows ordered by the group's order."""
    return [
        MiniLeagueRowOut(
            team=team,
            points=mini_table[team].points,
            goal_difference=mini_table[team].goal_difference,
            goals_for=mini_table[team].goals_for,
        )
        for team in members
    ]


def _partition_out(partition: DecisionPartition) -> DecisionPartitionOut:
    """Serialize one node of the ranking decision tree."""
    return DecisionPartitionOut(
        kind=partition.kind,
        members=list(partition.members),
        team=partition.members[0] if partition.kind == "single" else None,
        partitions=(
            [_partition_out(child) for child in partition.partitions]
            if partition.partitions is not None
            else None
        ),
        mini_table=(
            _mini_rows_out(partition.members, partition.mini_table)
            if partition.mini_table is not None
            else None
        ),
        final_table=(
            [
                FinalVerdictRowOut(
                    team=row.team_id,
                    goal_difference=row.goal_difference,
                    goals_for=row.goals_for,
                )
                for row in partition.final_table
            ]
            if partition.final_table is not None
            else None
        ),
    )


def _points_group_out(group: PointsGroup) -> PointsGroupOut:
    return PointsGroupOut(
        points=group.points,
        members=list(group.members),
        mini_table=_mini_rows_out(group.members, group.mini_table),
        partitions=[_partition_out(partition) for partition in group.partitions],
    )


@app.post(
    "/standings",
    response_model=StandingsResponse,
    responses={422: {"description": "Invalid teams or matches"}},
)
def post_standings(payload: StandingsRequest) -> StandingsResponse:
    """Compute the league table for the given teams and completed matches."""
    ordered, stats = compute_standings(payload.teams, payload.matches)
    return StandingsResponse(standings=_standing_entries(ordered, stats))


@app.post(
    "/standings/explanation",
    response_model=StandingsExplanationResponse,
    responses={422: {"description": "Invalid teams or matches"}},
)
def post_standings_explanation(
    payload: StandingsRequest,
) -> StandingsExplanationResponse:
    """Compute the table and explain every ranking decision.

    The ``standings`` rows are identical to POST /standings for the same
    request. ``explanation`` groups teams by total points and records,
    for each group, the head-to-head mini-league (points, goal
    difference, goals scored) and its ordered partitions. Blocks still
    tied after a mini-league carry a recursively rebuilt mini-league;
    blocks the mini-league cannot separate at all carry the final
    verdict (overall goal difference, overall goals scored, team id).
    The members listed across the nodes, read in order, are exactly the
    final standings.
    """
    ordered, stats, groups = compute_standings_explained(
        payload.teams, payload.matches
    )
    explanation = RankingExplanationOut(
        members=list(ordered),
        groups=[_points_group_out(group) for group in groups],
    )
    return StandingsExplanationResponse(
        standings=_standing_entries(ordered, stats),
        explanation=explanation,
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
