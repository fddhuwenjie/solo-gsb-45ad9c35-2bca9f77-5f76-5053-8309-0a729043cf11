"""End-to-end tests for the round-robin schedule API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(body):
    return client.post("/round-robin-schedules", json=body)


def flatten(body):
    return [
        (round_["round"], match["home"], match["away"])
        for round_ in body["schedule"]
        for match in round_["matches"]
    ]


def assert_valid_schedule(body, teams, rounds, unavailability=(), fixed=()):
    assert body["teams"] == teams
    assert body["rounds"] == rounds
    assert len(body["schedule"]) == rounds

    blocked = {entry["team"]: set(entry["unavailable_rounds"])
               for entry in unavailability}
    seen = set()
    sides = {team: [] for team in teams}
    for round_ in body["schedule"]:
        r = round_["round"]
        # Matches are sorted by (home, away).
        pairs = [(m["home"], m["away"]) for m in round_["matches"]]
        assert pairs == sorted(pairs)
        teams_in_round = set()
        for match in round_["matches"]:
            home, away = match["home"], match["away"]
            assert home in teams and away in teams
            assert r not in blocked.get(home, set())
            assert r not in blocked.get(away, set())
            pair = frozenset((home, away))
            assert pair not in seen
            seen.add(pair)
            assert home not in teams_in_round and away not in teams_in_round
            teams_in_round |= {home, away}
            sides[home].append((r, "H"))
            sides[away].append((r, "A"))

    # Every pair played exactly once.
    assert len(seen) == len(teams) * (len(teams) - 1) // 2

    n = len(teams)
    for team in teams:
        games = sorted(sides[team])
        assert len(games) == n - 1
        homes = sum(side == "H" for _r, side in games)
        aways = n - 1 - homes
        assert abs(homes - aways) <= 1
        for i in range(2, len(games)):
            assert not (
                games[i][1] == games[i - 1][1] == games[i - 2][1]
            )

    for fixed_match in fixed:
        assert any(
            round_["round"] == fixed_match["round"]
            and any(
                match["home"] == fixed_match["home"]
                and match["away"] == fixed_match["away"]
                for match in round_["matches"]
            )
            for round_ in body["schedule"]
        )


def test_two_team_single_round():
    response = post({"teams": ["alpha", "bravo"], "rounds": 1})
    assert response.status_code == 200
    body = response.json()
    assert_valid_schedule(body, ["alpha", "bravo"], 1)
    assert body["schedule"][0]["round"] == 1
    assert body["schedule"][0]["matches"] == [
        {"home": "alpha", "away": "bravo"}
    ]


def test_four_team_minimum_schedule_is_lexicographically_smallest():
    teams = ["alpha", "bravo", "charlie", "delta"]
    response = post({"teams": teams, "rounds": 3})
    assert response.status_code == 200
    body = response.json()
    assert_valid_schedule(body, teams, 3)
    rounds = [
        [(m["home"], m["away"]) for m in round_["matches"]]
        for round_ in body["schedule"]
    ]
    # Lex smallest: round 1 starts with alpha at home vs the smallest id.
    assert rounds[0][0] == ("alpha", "bravo")
    # No idle rounds possible at the minimum for an even number of teams.
    assert all(len(round_) == 2 for round_ in rounds)


def test_odd_number_of_teams_uses_bye_rounds():
    teams = ["alpha", "bravo", "charlie", "delta", "echo"]
    response = post({"teams": teams, "rounds": 5})
    assert response.status_code == 200
    body = response.json()
    assert_valid_schedule(body, teams, 5)
    # Five teams: two matches per round, one bye each round.
    assert all(
        len(round_["matches"]) == 2 for round_ in body["schedule"]
    )


def test_extra_rounds_produce_empty_rounds_first_in_lex_order():
    teams = ["alpha", "bravo", "charlie"]
    response = post({"teams": ["alpha", "bravo", "charlie"], "rounds": 5})
    assert response.status_code == 200
    body = response.json()
    assert_valid_schedule(body, teams, 5)
    # The empty round sorts before any non-empty round, so the lex
    # smallest schedule leaves the earliest possible round(s) idle.
    counts = [len(round_["matches"]) for round_ in body["schedule"]]
    assert counts[0] == 0
    assert counts[1] == 0


def test_unavailable_rounds_are_respected():
    body = {
        "teams": ["alpha", "bravo", "charlie", "delta"],
        "rounds": 5,
        "unavailability": [
            {"team": "alpha", "unavailable_rounds": [1]},
            {"team": "bravo", "unavailable_rounds": [2, 4]},
        ],
    }
    response = post(body)
    assert response.status_code == 200
    result = response.json()
    assert_valid_schedule(
        result,
        body["teams"],
        5,
        unavailability=body["unavailability"],
    )


def test_fixed_match_keeps_round_and_home_away():
    body = {
        "teams": ["alpha", "bravo", "charlie", "delta"],
        "rounds": 4,
        "fixed_matches": [
            {"home": "delta", "away": "alpha", "round": 1},
            {"home": "bravo", "away": "charlie", "round": 2},
        ],
    }
    response = post(body)
    assert response.status_code == 200
    result = response.json()
    assert_valid_schedule(
        result, body["teams"], 4, fixed=body["fixed_matches"]
    )
    round_one = result["schedule"][0]["matches"]
    assert {"home": "delta", "away": "alpha"} in round_one
    # No reversed home/away copy of the fixed pairing exists.
    assert not any(
        m["home"] == "alpha" and m["away"] == "delta"
        for round_ in result["schedule"]
        for m in round_["matches"]
    )


def test_result_is_stable_for_the_same_request():
    body = {
        "teams": ["alpha", "bravo", "charlie", "delta", "echo"],
        "rounds": 7,
        "unavailability": [
            {"team": "charlie", "unavailable_rounds": [3, 6]}
        ],
        "fixed_matches": [{"home": "echo", "away": "alpha", "round": 5}],
    }
    first = post(body).json()
    second = post(body).json()
    assert first == second


def test_rounds_below_minimum_is_rejected():
    # Four teams need at least 3 rounds.
    response = post({"teams": ["a", "b", "c", "d"], "rounds": 2})
    assert response.status_code == 422
    assert "at least 3" in response.json()["detail"]


def test_rounds_above_the_single_round_robin_minimum_are_allowed():
    # Extra rounds simply create idle rounds; n=2 needs just one round.
    response = post({"teams": ["a", "b"], "rounds": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["schedule"][0]["matches"] == []
    assert body["schedule"][1]["matches"] == [
        {"home": "a", "away": "b"}
    ]


def test_rounds_above_maximum_is_rejected():
    response = post({"teams": ["a", "b"], "rounds": 13})
    assert response.status_code == 422


def test_too_few_teams_is_rejected():
    response = post({"teams": ["solo"], "rounds": 1})
    assert response.status_code == 422


def test_more_than_eight_teams_is_rejected():
    response = post(
        {"teams": [f"t{i}" for i in range(9)], "rounds": 8}
    )
    assert response.status_code == 422


def test_duplicate_team_ids_are_rejected():
    response = post({"teams": ["a", "a", "b"], "rounds": 3})
    assert response.status_code == 422
    assert "duplicate team" in response.json()["detail"]


def test_unknown_team_in_unavailability_is_rejected():
    response = post(
        {
            "teams": ["a", "b"],
            "rounds": 1,
            "unavailability": [
                {"team": "ghost", "unavailable_rounds": [1]}
            ],
        }
    )
    assert response.status_code == 422
    assert "unknown team" in response.json()["detail"]


def test_unknown_team_in_fixed_match_is_rejected():
    response = post(
        {
            "teams": ["a", "b", "c"],
            "rounds": 3,
            "fixed_matches": [{"home": "a", "away": "ghost", "round": 1}],
        }
    )
    assert response.status_code == 422


def test_fixed_match_self_pair_is_rejected():
    response = post(
        {
            "teams": ["a", "b", "c"],
            "rounds": 3,
            "fixed_matches": [{"home": "a", "away": "a", "round": 1}],
        }
    )
    assert response.status_code == 422


def test_duplicate_fixed_matchup_is_rejected():
    response = post(
        {
            "teams": ["a", "b", "c", "d"],
            "rounds": 4,
            "fixed_matches": [
                {"home": "a", "away": "b", "round": 1},
                {"home": "b", "away": "a", "round": 2},
            ],
        }
    )
    assert response.status_code == 422
    assert "duplicate fixed matchup" in response.json()["detail"]


def test_two_fixed_matches_sharing_a_team_in_one_round_are_rejected():
    response = post(
        {
            "teams": ["a", "b", "c", "d"],
            "rounds": 4,
            "fixed_matches": [
                {"home": "a", "away": "b", "round": 1},
                {"home": "a", "away": "c", "round": 1},
            ],
        }
    )
    assert response.status_code == 422
    assert "play twice" in response.json()["detail"]


def test_fixed_match_in_unavailable_round_is_rejected():
    response = post(
        {
            "teams": ["a", "b", "c"],
            "rounds": 3,
            "unavailability": [
                {"team": "a", "unavailable_rounds": [2]}
            ],
            "fixed_matches": [{"home": "a", "away": "b", "round": 2}],
        }
    )
    assert response.status_code == 422
    assert "unavailable" in response.json()["detail"]


def test_unavailable_round_outside_range_is_rejected():
    response = post(
        {
            "teams": ["a", "b", "c"],
            "rounds": 3,
            "unavailability": [
                {"team": "a", "unavailable_rounds": [4]}
            ],
        }
    )
    assert response.status_code == 422
    assert "outside" in response.json()["detail"]


def test_team_unavailable_in_too_many_rounds_is_rejected():
    response = post(
        {
            "teams": ["a", "b", "c"],
            "rounds": 3,
            "unavailability": [
                {"team": "a", "unavailable_rounds": [1, 2, 3]}
            ],
        }
    )
    assert response.status_code == 422
    assert "too many rounds" in response.json()["detail"]


def test_pair_with_no_common_available_round_is_rejected():
    # a is unavailable in rounds 1-2 and b in rounds 3-4, so a and b
    # share no open round even though each team has enough open rounds
    # overall to play its two games.
    response = post(
        {
            "teams": ["a", "b", "c"],
            "rounds": 4,
            "unavailability": [
                {"team": "a", "unavailable_rounds": [1, 2]},
                {"team": "b", "unavailable_rounds": [3, 4]},
            ],
        }
    )
    assert response.status_code == 422
    assert "no available round remains" in response.json()["detail"]


def test_infeasible_fixed_home_streak_returns_422():
    # Three fixed home games for alpha in consecutive rounds force a
    # run of three home games; no schedule can satisfy the streak rule.
    response = post(
        {
            "teams": ["a", "b", "c", "d", "e", "f", "g", "h"],
            "rounds": 7,
            "fixed_matches": [
                {"home": "a", "away": "b", "round": 1},
                {"home": "a", "away": "c", "round": 2},
                {"home": "a", "away": "d", "round": 3},
            ],
        }
    )
    assert response.status_code == 422
    assert "no feasible schedule" in response.json()["detail"]


def test_openapi_documents_the_schedule_endpoint():
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/round-robin-schedules" in paths
    operation = paths["/round-robin-schedules"]["post"]
    assert "RoundRobinScheduleRequest" in str(operation["requestBody"])
    assert "RoundRobinScheduleResponse" in str(operation["responses"])
    assert "422" in operation["responses"]
