"""End-to-end tests for the qualification-scenarios API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import scenarios as scenarios_module
from app.main import app

client = TestClient(app)

TEAMS = ["alpha", "bravo", "charlie", "delta"]
PLAYED = [
    {"home": "alpha", "away": "bravo", "home_score": 2, "away_score": 0},
    {"home": "alpha", "away": "charlie", "home_score": 2, "away_score": 0},
    {"home": "bravo", "away": "charlie", "home_score": 1, "away_score": 0},
]
CANDIDATES = [
    {"home_score": 1, "away_score": 0},
    {"home_score": 0, "away_score": 0},
    {"home_score": 0, "away_score": 1},
]
REMAINING = [
    {"home": "delta", "away": "alpha", "candidates": CANDIDATES},
    {"home": "delta", "away": "bravo", "candidates": CANDIDATES},
    {"home": "delta", "away": "charlie", "candidates": CANDIDATES},
]


def post_scenarios(teams=TEAMS, matches=PLAYED, top_k=2, remaining=REMAINING):
    return client.post(
        "/qualification-scenarios",
        json={
            "teams": teams,
            "matches": matches,
            "top_k": top_k,
            "remaining": remaining,
        },
    )


def entries_by_team(body):
    return {entry["team"]: entry for entry in body["scenarios"]}


def test_rank_extremes_and_statuses():
    # Points after the played matches: alpha 6, bravo 3, charlie 0, delta 0.
    # delta still plays alpha, bravo and charlie (win/draw/loss candidates).
    response = post_scenarios()
    assert response.status_code == 200
    body = response.json()
    assert body["top_k"] == 2
    assert body["combinations"] == 27

    # Scenarios are ordered by best rank, then worst rank, then team id.
    assert [entry["team"] for entry in body["scenarios"]] == [
        "alpha",
        "delta",
        "bravo",
        "charlie",
    ]

    entries = entries_by_team(body)

    # alpha is always at least second: only delta can pass 6 points and
    # bravo loses every tie at 6 against alpha (head-to-head 0-2).
    assert entries["alpha"]["best_rank"] == 1
    assert entries["alpha"]["worst_rank"] == 2
    assert entries["alpha"]["status"] == "guaranteed"

    # delta can win all three (9 points, first) or lose all three (last).
    assert entries["delta"]["best_rank"] == 1
    assert entries["delta"]["worst_rank"] == 4
    assert entries["delta"]["status"] == "possible"

    # bravo reaches second at best (alpha is untouchable on 6 points) and
    # drops to third at worst (charlie loses every tie against bravo).
    assert entries["bravo"]["best_rank"] == 2
    assert entries["bravo"]["worst_rank"] == 3
    assert entries["bravo"]["status"] == "possible"

    # charlie can at most climb to third, so top_k=2 is out of reach.
    assert entries["charlie"]["best_rank"] == 3
    assert entries["charlie"]["worst_rank"] == 4
    assert entries["charlie"]["status"] == "eliminated"


def test_witnesses_realise_the_reported_ranks():
    response = post_scenarios()
    assert response.status_code == 200
    remaining_pairs = {
        frozenset((fixture["home"], fixture["away"])) for fixture in REMAINING
    }
    for entry in response.json()["scenarios"]:
        for witness_key, rank_key in (
            ("best_witness", "best_rank"),
            ("worst_witness", "worst_rank"),
        ):
            witness = entry[witness_key]
            # A witness is a complete set of unplayed results: exactly one
            # result per remaining fixture.
            assert len(witness) == len(REMAINING)
            assert {
                frozenset((match["home"], match["away"])) for match in witness
            } == remaining_pairs
            # Replaying the witness through /standings must reproduce the
            # reported extreme rank.
            standings = client.post(
                "/standings",
                json={"teams": TEAMS, "matches": PLAYED + witness},
            )
            assert standings.status_code == 200
            rank = next(
                row["rank"]
                for row in standings.json()["standings"]
                if row["team"] == entry["team"]
            )
            assert rank == entry[rank_key]


def test_no_remaining_fixtures_yields_the_current_table():
    response = post_scenarios(remaining=[])
    assert response.status_code == 200
    body = response.json()
    assert body["combinations"] == 1
    entries = entries_by_team(body)
    # Current table: alpha 6, bravo 3, delta 0 (GD 0), charlie 0 (GD -3).
    assert entries["alpha"]["best_rank"] == entries["alpha"]["worst_rank"] == 1
    assert entries["bravo"]["best_rank"] == entries["bravo"]["worst_rank"] == 2
    assert entries["delta"]["best_rank"] == entries["delta"]["worst_rank"] == 3
    assert entries["charlie"]["best_rank"] == entries["charlie"]["worst_rank"] == 4
    assert entries["alpha"]["status"] == "guaranteed"
    assert entries["bravo"]["status"] == "guaranteed"
    assert entries["delta"]["status"] == "eliminated"
    assert entries["charlie"]["status"] == "eliminated"
    for entry in body["scenarios"]:
        assert entry["best_witness"] == []
        assert entry["worst_witness"] == []


def test_top_k_covering_all_teams_makes_everyone_guaranteed():
    response = post_scenarios(top_k=4)
    assert response.status_code == 200
    assert all(
        entry["status"] == "guaranteed"
        for entry in response.json()["scenarios"]
    )


def test_remaining_fixture_may_not_duplicate_a_played_match():
    remaining = [
        {"home": "charlie", "away": "alpha", "candidates": CANDIDATES},
    ]
    response = post_scenarios(remaining=remaining)
    assert response.status_code == 422
    assert "duplicate matchup" in response.json()["detail"]


def test_remaining_fixtures_may_not_duplicate_each_other():
    remaining = [
        {"home": "delta", "away": "alpha", "candidates": CANDIDATES},
        {"home": "alpha", "away": "delta", "candidates": CANDIDATES},
    ]
    response = post_scenarios(remaining=remaining)
    assert response.status_code == 422
    assert "duplicate matchup" in response.json()["detail"]


def test_remaining_fixture_with_unknown_team_is_rejected():
    remaining = [
        {"home": "delta", "away": "echo", "candidates": CANDIDATES},
    ]
    response = post_scenarios(remaining=remaining)
    assert response.status_code == 422
    assert "unknown team" in response.json()["detail"]


def test_remaining_self_match_is_rejected():
    remaining = [
        {"home": "delta", "away": "delta", "candidates": CANDIDATES},
    ]
    response = post_scenarios(remaining=remaining)
    assert response.status_code == 422
    assert "itself" in response.json()["detail"]


def test_empty_candidate_list_is_rejected():
    remaining = [{"home": "delta", "away": "alpha", "candidates": []}]
    response = post_scenarios(remaining=remaining)
    assert response.status_code == 422


def test_negative_candidate_score_is_rejected():
    remaining = [
        {
            "home": "delta",
            "away": "alpha",
            "candidates": [{"home_score": -1, "away_score": 0}],
        }
    ]
    response = post_scenarios(remaining=remaining)
    assert response.status_code == 422


def test_top_k_must_be_at_least_one():
    response = post_scenarios(top_k=0)
    assert response.status_code == 422


def test_combination_limit_is_enforced():
    teams = [f"t{i}" for i in range(7)]
    pairs = [("t0", "t1"), ("t2", "t3"), ("t4", "t5"), ("t6", "t0"), ("t1", "t2"), ("t3", "t4")]
    remaining = [
        {
            "home": home,
            "away": away,
            "candidates": [
                {"home_score": goals, "away_score": 0} for goals in range(10)
            ],
        }
        for home, away in pairs
    ]
    # 6 fixtures x 10 candidates = 1,000,000 combinations > 100,000.
    response = post_scenarios(teams=teams, matches=[], top_k=3, remaining=remaining)
    assert response.status_code == 422
    assert "too many combinations" in response.json()["detail"]


def test_combination_count_exactly_at_the_limit_is_allowed(monkeypatch):
    monkeypatch.setattr(scenarios_module, "MAX_COMBINATIONS", 27)
    response = post_scenarios()
    assert response.status_code == 200
    assert response.json()["combinations"] == 27


def test_one_combination_over_the_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(scenarios_module, "MAX_COMBINATIONS", 26)
    response = post_scenarios()
    assert response.status_code == 422
    assert "too many combinations" in response.json()["detail"]


def test_exactly_one_hundred_thousand_combinations_are_allowed():
    # Minimal standings (one fixture, 100,000 candidate scores) to keep
    # the boundary check end-to-end without enumerating 100,000 fixtures.
    body = {
        "teams": ["alpha", "bravo"],
        "matches": [],
        "top_k": 1,
        "remaining": [
            {
                "home": "alpha",
                "away": "bravo",
                "candidates": [
                    {"home_score": goals, "away_score": 0}
                    for goals in range(scenarios_module.MAX_COMBINATIONS)
                ],
            }
        ],
    }
    response = client.post("/qualification-scenarios", json=body)
    assert response.status_code == 200
    assert response.json()["combinations"] == 100_000


def test_one_hundred_thousand_and_one_combinations_are_rejected():
    body = {
        "teams": ["alpha", "bravo"],
        "matches": [],
        "top_k": 1,
        "remaining": [
            {
                "home": "alpha",
                "away": "bravo",
                "candidates": [
                    {"home_score": goals, "away_score": 0}
                    for goals in range(scenarios_module.MAX_COMBINATIONS + 1)
                ],
            }
        ],
    }
    response = client.post("/qualification-scenarios", json=body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "too many combinations" in detail
    assert "100001" in detail


def test_openapi_documents_the_new_endpoint():
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    assert "/qualification-scenarios" in paths
    assert "/standings" in paths
    operation = paths["/qualification-scenarios"]["post"]
    assert "QualificationRequest" in str(operation["requestBody"])
    assert "422" in operation["responses"]
