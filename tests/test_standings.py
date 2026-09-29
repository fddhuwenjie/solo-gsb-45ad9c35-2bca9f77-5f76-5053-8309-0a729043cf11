"""End-to-end tests for the standings API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(teams, matches):
    response = client.post(
        "/standings",
        json={
            "teams": teams,
            "matches": [
                {"home": h, "away": a, "home_score": hs, "away_score": as_}
                for h, a, hs, as_ in matches
            ],
        },
    )
    return response


def ordered_teams(response):
    return [row["team"] for row in response.json()["standings"]]


def test_basic_table_and_stats():
    response = post(
        ["alpha", "bravo", "charlie"],
        [
            ("alpha", "bravo", 2, 1),
            ("bravo", "charlie", 1, 1),
            ("alpha", "charlie", 0, 0),
        ],
    )
    assert response.status_code == 200
    standings = response.json()["standings"]
    assert [row["team"] for row in standings] == ["alpha", "charlie", "bravo"]
    assert [row["rank"] for row in standings] == [1, 2, 3]

    alpha = standings[0]
    assert alpha == {
        "rank": 1,
        "team": "alpha",
        "played": 2,
        "wins": 1,
        "draws": 1,
        "losses": 0,
        "goals_for": 2,
        "goals_against": 1,
        "goal_difference": 1,
        "points": 4,
    }
    charlie = standings[1]
    assert charlie["draws"] == 2
    assert charlie["points"] == 2
    bravo = standings[2]
    assert bravo["losses"] == 1
    assert bravo["goal_difference"] == -1
    assert bravo["points"] == 1


def test_head_to_head_beats_overall_goal_difference():
    # alpha and bravo both on 6 points; alpha won their direct match even
    # though bravo's overall goal difference is better.
    response = post(
        ["alpha", "bravo", "charlie", "delta"],
        [
            ("alpha", "bravo", 2, 1),
            ("alpha", "charlie", 4, 0),
            ("delta", "alpha", 3, 0),
            ("bravo", "charlie", 1, 0),
            ("bravo", "delta", 1, 0),
            ("charlie", "delta", 1, 0),
        ],
    )
    assert response.status_code == 200
    assert ordered_teams(response) == ["alpha", "bravo", "charlie", "delta"]
    standings = response.json()["standings"]
    # delta has the better overall goal difference (+1 vs -4) but lost the
    # direct match against charlie, so charlie ranks higher.
    assert standings[2]["team"] == "charlie"
    assert standings[2]["goal_difference"] == -4
    assert standings[3]["team"] == "delta"
    assert standings[3]["goal_difference"] == 1


def test_mini_league_goal_difference_orders_three_way_tie():
    # alpha/bravo/charlie all on 3 points; mini-league goal difference
    # decides, even though overall goal difference would rank charlie
    # above alpha.
    response = post(
        ["alpha", "bravo", "charlie", "delta"],
        [
            ("alpha", "bravo", 1, 0),
            ("charlie", "alpha", 1, 0),
            ("bravo", "charlie", 2, 0),
            ("delta", "alpha", 5, 0),
            ("delta", "bravo", 1, 0),
            ("delta", "charlie", 1, 0),
        ],
    )
    assert response.status_code == 200
    assert ordered_teams(response) == ["delta", "bravo", "alpha", "charlie"]


def test_tied_subgroup_gets_a_fresh_mini_league():
    # a/b/c all on 6 points. First mini-league: c drops out (fewer goals),
    # a and b remain tied and are separated by a rebuilt mini-league
    # containing only a vs b, which a won. Overall goal difference would
    # have favoured b (+5 vs +1).
    response = post(
        ["a", "b", "c", "d"],
        [
            ("a", "b", 2, 1),
            ("c", "a", 1, 0),
            ("b", "c", 1, 0),
            ("a", "d", 1, 0),
            ("b", "d", 5, 0),
            ("c", "d", 1, 0),
        ],
    )
    assert response.status_code == 200
    assert ordered_teams(response) == ["a", "b", "c", "d"]


def test_overall_goal_difference_breaks_remaining_tie():
    response = post(
        ["alpha", "bravo", "charlie"],
        [
            ("alpha", "bravo", 1, 1),
            ("alpha", "charlie", 2, 0),
            ("bravo", "charlie", 1, 0),
        ],
    )
    assert response.status_code == 200
    assert ordered_teams(response) == ["alpha", "bravo", "charlie"]


def test_overall_goals_scored_breaks_remaining_tie():
    response = post(
        ["alpha", "bravo", "charlie"],
        [
            ("alpha", "bravo", 1, 1),
            ("alpha", "charlie", 3, 1),
            ("bravo", "charlie", 2, 0),
        ],
    )
    assert response.status_code == 200
    standings = response.json()["standings"]
    assert standings[0]["team"] == "alpha"
    assert standings[0]["goal_difference"] == standings[1]["goal_difference"]
    assert standings[0]["goals_for"] > standings[1]["goals_for"]


def test_team_id_is_the_final_tiebreak():
    response = post(
        ["charlie", "bravo", "alpha"],
        [
            ("bravo", "alpha", 1, 1),
            ("alpha", "charlie", 2, 1),
            ("bravo", "charlie", 2, 1),
        ],
    )
    assert response.status_code == 200
    standings = response.json()["standings"]
    assert standings[0]["points"] == standings[1]["points"]
    assert standings[0]["goals_for"] == standings[1]["goals_for"]
    assert ordered_teams(response)[:2] == ["alpha", "bravo"]


def test_no_matches_orders_by_team_id_with_zero_stats():
    response = post(["charlie", "alpha", "bravo"], [])
    assert response.status_code == 200
    standings = response.json()["standings"]
    assert [row["team"] for row in standings] == ["alpha", "bravo", "charlie"]
    assert [row["rank"] for row in standings] == [1, 2, 3]
    assert all(
        row["played"] == 0 and row["points"] == 0 and row["goal_difference"] == 0
        for row in standings
    )


def test_unknown_team_is_rejected():
    response = post(["alpha", "bravo"], [("alpha", "charlie", 1, 0)])
    assert response.status_code == 422
    assert "unknown team" in response.json()["detail"]


def test_self_match_is_rejected():
    response = post(["alpha", "bravo"], [("alpha", "alpha", 1, 0)])
    assert response.status_code == 422
    assert "itself" in response.json()["detail"]


def test_duplicate_matchup_is_rejected():
    response = post(
        ["alpha", "bravo"],
        [("alpha", "bravo", 1, 0), ("alpha", "bravo", 2, 2)],
    )
    assert response.status_code == 422
    assert "duplicate matchup" in response.json()["detail"]


def test_reversed_fixture_still_counts_as_duplicate():
    response = post(
        ["alpha", "bravo"],
        [("alpha", "bravo", 1, 0), ("bravo", "alpha", 0, 1)],
    )
    assert response.status_code == 422
    assert "duplicate matchup" in response.json()["detail"]


def test_duplicate_team_ids_are_rejected():
    response = client.post(
        "/standings", json={"teams": ["alpha", "alpha"], "matches": []}
    )
    assert response.status_code == 422
    assert "duplicate team" in response.json()["detail"]


def test_negative_scores_are_rejected():
    response = post(["alpha", "bravo"], [("alpha", "bravo", -1, 0)])
    assert response.status_code == 422


def test_empty_team_list_is_rejected():
    response = client.post("/standings", json={"teams": [], "matches": []})
    assert response.status_code == 422


def test_openapi_documentation_is_served():
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    assert "/standings" in spec.json()["paths"]
    assert client.get("/docs").status_code == 200
