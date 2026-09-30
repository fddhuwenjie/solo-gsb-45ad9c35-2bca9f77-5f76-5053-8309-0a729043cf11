"""Targeted tests for POST /standings/explanation.

The endpoint must return the exact same table as /standings and a
machine-readable decision tree whose node members, concatenated in
order, reproduce that table without a second sorting pass.
"""
from __future__ import annotations

import json
import random

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post_explanation(teams, matches):
    return client.post(
        "/standings/explanation",
        json={
            "teams": teams,
            "matches": [
                {"home": h, "away": a, "home_score": hs, "away_score": as_}
                for h, a, hs, as_ in matches
            ],
        },
    )


def post_standings(teams, matches):
    return client.post(
        "/standings",
        json={
            "teams": teams,
            "matches": [
                {"home": h, "away": a, "home_score": hs, "away_score": as_}
                for h, a, hs, as_ in matches
            ],
        },
    )


def flatten_partitions(partitions):
    members = []
    for partition in partitions:
        if partition["kind"] == "single":
            assert partition["members"] == [partition["team"]]
            assert partition["partitions"] is None
            assert partition["mini_table"] is None
            assert partition["final_table"] is None
            members.append(partition["team"])
        elif partition["kind"] == "mini_league":
            assert partition["team"] is None
            assert partition["partitions"] is not None
            assert partition["mini_table"] is not None
            assert partition["final_table"] is None
            members.extend(flatten_partitions(partition["partitions"]))
            assert members[-len(partition["members"]):] == partition["members"]
        else:
            assert partition["kind"] == "final"
            assert partition["team"] is None
            assert partition["partitions"] is None
            assert partition["mini_table"] is None
            assert partition["final_table"] is not None
            rows = partition["final_table"]
            assert [row["team"] for row in rows] == partition["members"]
            # The final verdict is ordered by overall GD, then GF, then id.
            for earlier, later in zip(rows, rows[1:]):
                assert (
                    -earlier["goal_difference"],
                    -earlier["goals_for"],
                    earlier["team"],
                ) <= (
                    -later["goal_difference"],
                    -later["goals_for"],
                    later["team"],
                )
            members.extend(partition["members"])
    return members


def flatten_explanation(explanation):
    """Return the tree order and check every node is structurally sound."""
    members = []
    for group in explanation["groups"]:
        group_order = flatten_partitions(group["partitions"])
        assert group_order == group["members"]
        assert [row["team"] for row in group["mini_table"]] == group["members"]
        members.extend(group["members"])
    assert members == explanation["members"]
    # Groups are strictly descending by total points.
    points = [group["points"] for group in explanation["groups"]]
    assert points == sorted(points, reverse=True)
    assert len(set(points)) == len(points)
    return members


def test_explanation_table_is_identical_to_standings():
    teams = ["alpha", "bravo", "charlie"]
    matches = [
        ("alpha", "bravo", 2, 1),
        ("bravo", "charlie", 1, 1),
        ("alpha", "charlie", 0, 0),
    ]
    explanation_response = post_explanation(teams, matches)
    standings_response = post_standings(teams, matches)
    assert explanation_response.status_code == 200
    assert explanation_response.json()["standings"] == standings_response.json()[
        "standings"
    ]


def test_explanation_tree_concatenates_into_the_final_table():
    response = post_explanation(
        ["alpha", "bravo", "charlie"],
        [
            ("alpha", "bravo", 2, 1),
            ("bravo", "charlie", 1, 1),
            ("alpha", "charlie", 0, 0),
        ],
    )
    body = response.json()
    tree_order = flatten_explanation(body["explanation"])
    assert tree_order == [row["team"] for row in body["standings"]]
    assert [row["rank"] for row in body["standings"]] == [1, 2, 3]


def test_points_groups_have_mini_league_rows_in_ranking_order():
    # a/b/c tied on 6 points; d alone on 0.
    response = post_explanation(
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
    body = response.json()
    groups = body["explanation"]["groups"]
    assert [group["points"] for group in groups] == [6, 0]
    tied, lone = groups
    assert tied["members"] == ["a", "b", "c"]
    assert {
        (row["team"], row["points"], row["goal_difference"], row["goals_for"])
        for row in tied["mini_table"]
    } == {
        ("a", 3, 0, 2),
        ("b", 3, 0, 2),
        ("c", 3, 0, 1),
    }
    # A singleton points group still carries its (empty) mini-league row.
    assert lone["members"] == ["d"]
    assert lone["mini_table"] == [
        {"team": "d", "points": 0, "goal_difference": 0, "goals_for": 0}
    ]
    assert lone["partitions"] == [
        {
            "kind": "single",
            "members": ["d"],
            "team": "d",
            "partitions": None,
            "mini_table": None,
            "final_table": None,
        }
    ]


def test_recursive_mini_league_is_reported_as_nested_node():
    # First mini-league drops c out; a vs b stay tied and a fresh
    # mini-league (a beat b) separates them.
    response = post_explanation(
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
    body = response.json()
    assert flatten_explanation(body["explanation"]) == [
        row["team"] for row in body["standings"]
    ]
    tied_group = body["explanation"]["groups"][0]
    nested, single_c = tied_group["partitions"]
    assert nested["kind"] == "mini_league"
    assert nested["members"] == ["a", "b"]
    assert [
        (row["team"], row["points"]) for row in nested["mini_table"]
    ] == [("a", 3), ("b", 0)]
    assert [p["kind"] for p in nested["partitions"]] == ["single", "single"]
    assert [p["team"] for p in nested["partitions"]] == ["a", "b"]
    assert single_c == {
        "kind": "single",
        "members": ["c"],
        "team": "c",
        "partitions": None,
        "mini_table": None,
        "final_table": None,
    }


def test_top_level_final_verdict_records_overall_criteria():
    # a/b/c draw every mutual match; the whole points group falls through
    # to overall goal difference (built against d).
    response = post_explanation(
        ["a", "b", "c", "d"],
        [
            ("a", "b", 1, 1),
            ("b", "c", 1, 1),
            ("a", "c", 1, 1),
            ("a", "d", 5, 0),
            ("b", "d", 2, 0),
            ("c", "d", 1, 0),
        ],
    )
    body = response.json()
    assert [row["team"] for row in body["standings"]] == ["a", "b", "c", "d"]
    tied_group = body["explanation"]["groups"][0]
    assert tied_group["points"] == 5
    (partition,) = tied_group["partitions"]
    assert partition["kind"] == "final"
    assert partition["members"] == ["a", "b", "c"]
    assert [
        (row["team"], row["goal_difference"], row["goals_for"])
        for row in partition["final_table"]
    ] == [("a", 5, 7), ("b", 2, 4), ("c", 1, 3)]


def test_nested_final_verdict_inside_partial_separation():
    # a/b/d all on 7 points. Mini-league keys give the ordered blocks
    # [a], [b, d]; b and d drew and matched each other on every
    # mini-league criterion, so their block falls back to overall
    # criteria. Against outsiders e/f, b's overall goal difference beats
    # d's. f and e finish outside the points group.
    response = post_explanation(
        ["a", "b", "d", "e", "f"],
        [
            ("a", "b", 2, 0),
            ("a", "d", 2, 0),
            ("b", "d", 1, 1),
            ("e", "a", 1, 0),
            ("a", "f", 1, 1),
            ("b", "e", 5, 0),
            ("b", "f", 5, 0),
            ("d", "e", 2, 0),
            ("d", "f", 2, 0),
            ("f", "e", 1, 0),
        ],
    )
    body = response.json()
    assert [row["team"] for row in body["standings"]] == [
        "a",
        "b",
        "d",
        "f",
        "e",
    ]
    tied_group = body["explanation"]["groups"][0]
    assert tied_group["points"] == 7
    kinds = [p["kind"] for p in tied_group["partitions"]]
    assert kinds == ["single", "mini_league"]
    assert tied_group["partitions"][0]["team"] == "a"
    nested = tied_group["partitions"][1]
    assert nested["members"] == ["b", "d"]
    assert [
        (row["team"], row["points"]) for row in nested["mini_table"]
    ] == [("b", 1), ("d", 1)]
    (final,) = nested["partitions"]
    assert final["kind"] == "final"
    assert final["members"] == ["b", "d"]
    assert [
        (row["team"], row["goal_difference"]) for row in final["final_table"]
    ] == [("b", 8), ("d", 2)]
    assert flatten_explanation(body["explanation"]) == [
        row["team"] for row in body["standings"]
    ]


def test_no_matches_tree_is_one_group_decided_by_team_id():
    response = post_explanation(["charlie", "alpha", "bravo"], [])
    assert response.status_code == 200
    body = response.json()
    (group,) = body["explanation"]["groups"]
    assert group["points"] == 0
    (partition,) = group["partitions"]
    assert partition["kind"] == "final"
    assert partition["members"] == ["alpha", "bravo", "charlie"]
    assert flatten_explanation(body["explanation"]) == [
        row["team"] for row in body["standings"]
    ]


def test_tree_matches_table_for_random_tournaments():
    rng = random.Random(20260930)
    for trial in range(300):
        n = rng.randint(1, 7)
        teams = [f"t{i:02d}" for i in range(n)]
        matches = []
        for i in range(n):
            for j in range(i + 1, n):
                if rng.random() < 0.7:
                    matches.append(
                        (teams[i], teams[j], rng.randint(0, 4), rng.randint(0, 4))
                    )
        shuffled = teams[:]
        rng.shuffle(shuffled)
        response = post_explanation(shuffled, matches)
        assert response.status_code == 200, f"trial {trial}: {response.text}"
        body = response.json()
        standings_response = post_standings(shuffled, matches)
        assert body["standings"] == standings_response.json()["standings"]
        assert flatten_explanation(body["explanation"]) == [
            row["team"] for row in body["standings"]
        ]


def test_validation_errors_keep_standings_422_semantics():
    cases = [
        {"teams": [], "matches": []},
        {"teams": ["alpha", "alpha"], "matches": []},
        {
            "teams": ["alpha", "bravo"],
            "matches": [
                {"home": "alpha", "away": "ghost", "home_score": 1, "away_score": 0}
            ],
        },
        {
            "teams": ["alpha", "bravo"],
            "matches": [
                {"home": "alpha", "away": "alpha", "home_score": 1, "away_score": 0}
            ],
        },
        {
            "teams": ["alpha", "bravo"],
            "matches": [
                {"home": "alpha", "away": "bravo", "home_score": 1, "away_score": 0},
                {"home": "bravo", "away": "alpha", "home_score": 0, "away_score": 1},
            ],
        },
    ]
    for payload in cases:
        new_response = client.post("/standings/explanation", json=payload)
        old_response = client.post("/standings", json=payload)
        assert new_response.status_code == old_response.status_code == 422, payload
        assert new_response.json() == old_response.json()
    # Pydantic-level validation (negative score) is 422 on both endpoints.
    bad_score = {"teams": ["alpha", "bravo"], "matches": []}
    bad_score["matches"] = [
        {"home": "alpha", "away": "bravo", "home_score": -1, "away_score": 0}
    ]
    assert client.post("/standings/explanation", json=bad_score).status_code == 422


def test_openapi_documents_the_explanation_endpoint():
    spec = client.get("/openapi.json").json()
    path = spec["paths"]["/standings/explanation"]["post"]
    assert "StandingsExplanationResponse" in json.dumps(path)
    assert "422" in path["responses"]
    schema_names = spec["components"]["schemas"]
    for name in (
        "StandingsExplanationResponse",
        "RankingExplanationOut",
        "PointsGroupOut",
        "DecisionPartitionOut",
        "MiniLeagueRowOut",
        "FinalVerdictRowOut",
    ):
        assert name in schema_names
    partition = schema_names["DecisionPartitionOut"]
    kinds = partition["properties"]["kind"]["enum"]
    assert set(kinds) == {"single", "mini_league", "final"}
