"""End-to-end tests for the ranking decision tree endpoint."""
from __future__ import annotations

import random

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FINAL_CRITERIA = ["overall_goal_difference", "overall_goals_for", "team_id"]


def post(teams, matches):
    return client.post(
        "/standings-explanation",
        json={
            "teams": teams,
            "matches": [
                {"home": h, "away": a, "home_score": hs, "away_score": as_}
                for h, a, hs, as_ in matches
            ],
        },
    )


def standings_teams(response):
    return [row["team"] for row in response.json()["standings"]]


def iter_nodes(tree):
    """Yield every decision node in tree (pre-order)."""
    for group in tree["points_groups"]:
        stack = [group["decision"]]
        while stack:
            node = stack.pop()
            yield node
            for partition in reversed(node["partitions"]):
                if partition["child"] is not None:
                    stack.append(partition["child"])


def root_members(tree):
    """Concatenate the root point-group members: must equal the table."""
    return [team for group in tree["points_groups"] for team in group["teams"]]


def assert_tree_invariants(tree, teams):
    groups = tree["points_groups"]

    # Root groups are ordered by descending points, partition all teams once.
    assert [group["points"] for group in groups] == sorted(
        (group["points"] for group in groups), reverse=True
    )
    root_order = root_members(tree)
    assert sorted(root_order) == sorted(teams)
    assert len(root_order) == len(set(root_order)) == len(teams)

    for group in groups:
        assert group["teams"] == group["decision"]["members"]

    for node in iter_nodes(tree):
        # members == concatenation of partition teams, without duplicates.
        partition_teams = [
            team
            for partition in node["partitions"]
            for team in partition["teams"]
        ]
        assert partition_teams == node["members"]
        assert len(node["members"]) == len(set(node["members"]))
        # table rows share the member order and carry the key figures.
        assert [row["team"] for row in node["table"]] == node["members"]
        table_by_team = {row["team"]: row for row in node["table"]}

        for partition in node["partitions"]:
            for team in partition["teams"]:
                row = table_by_team[team]
                assert row["points"] == partition["points"]
                assert row["goal_difference"] == partition["goal_difference"]
                assert row["goals_for"] == partition["goals_for"]

            if len(partition["teams"]) == 1:
                assert partition["child"] is None
                assert partition["tie_break"] is None
            elif partition["child"] is not None:
                # Partial separation: subgroup is a strict subset and the
                # child order feeds the partition order directly.
                assert partition["child"]["members"] == partition["teams"]
                assert len(partition["teams"]) < len(node["members"])
                assert partition["tie_break"] is None
            else:
                # Whole node inseparable: terminal overall-criteria ruling.
                assert partition["tie_break"] is not None
                assert partition["teams"] == partition["tie_break"]["order"]
                assert partition["tie_break"]["criteria"] == FINAL_CRITERIA
                tb_table = partition["tie_break"]["table"]
                assert [row["team"] for row in tb_table] == partition["teams"]


def test_matches_standings_endpoint_for_basic_case():
    teams = ["alpha", "bravo", "charlie"]
    matches = [
        ("alpha", "bravo", 2, 1),
        ("bravo", "charlie", 1, 1),
        ("alpha", "charlie", 0, 0),
    ]
    explanation = post(teams, matches)
    standings = client.post(
        "/standings", json={"teams": teams, "matches": [
            {"home": h, "away": a, "home_score": hs, "away_score": as_}
            for h, a, hs, as_ in matches
        ]}
    )
    assert explanation.status_code == 200
    assert explanation.json()["standings"] == standings.json()["standings"]

    tree = explanation.json()
    assert root_members(tree) == standings_teams(explanation)
    assert_tree_invariants(tree, teams)

    # alpha/charlie/bravo share the point groups 4/2/1 respectively.
    groups = tree["points_groups"]
    assert [(g["points"], g["teams"]) for g in groups] == [
        (4, ["alpha"]),
        (2, ["charlie"]),
        (1, ["bravo"]),
    ]


def test_head_to_head_partitions_three_way_tie():
    # delta tops the table on 9 points; alpha/bravo/charlie all have 3,
    # and their three-team mini-league separates them by goal difference
    # (bravo +2, alpha 0, charlie -1) into three singleton partitions.
    teams = ["alpha", "bravo", "charlie", "delta"]
    matches = [
        ("alpha", "bravo", 1, 0),
        ("charlie", "alpha", 1, 0),
        ("bravo", "charlie", 2, 0),
        ("delta", "alpha", 5, 0),
        ("delta", "bravo", 1, 0),
        ("delta", "charlie", 1, 0),
    ]
    response = post(teams, matches)
    assert response.status_code == 200
    tree = response.json()
    assert standings_teams(response) == ["delta", "bravo", "alpha", "charlie"]
    assert root_members(tree) == ["delta", "bravo", "alpha", "charlie"]
    assert_tree_invariants(tree, teams)

    tied = next(g for g in tree["points_groups"] if g["points"] == 3)
    partitions = tied["decision"]["partitions"]
    assert [p["teams"] for p in partitions] == [["bravo"], ["alpha"], ["charlie"]]
    assert partitions[0]["points"] == 3
    assert partitions[0]["goal_difference"] == 1
    assert partitions[1]["goal_difference"] == 0
    assert partitions[1]["goals_for"] == 1
    assert partitions[2]["goal_difference"] == -1
    assert partitions[2]["goals_for"] == 1


def test_recursive_subgroup_carries_rebuilt_mini_league():
    teams = ["a", "b", "c", "d"]
    matches = [
        ("a", "b", 2, 1),
        ("c", "a", 1, 0),
        ("b", "c", 1, 0),
        ("a", "d", 1, 0),
        ("b", "d", 5, 0),
        ("c", "d", 1, 0),
    ]
    response = post(teams, matches)
    assert response.status_code == 200
    tree = response.json()
    assert standings_teams(response) == ["a", "b", "c", "d"]
    assert root_members(tree) == ["a", "b", "c", "d"]
    assert_tree_invariants(tree, teams)

    group = tree["points_groups"][0]
    assert group["points"] == 6
    partitions = group["decision"]["partitions"]
    # a/b stay tied on the first mini-league (3 pts, GD 0, GF 2); c drops
    # out with fewer goals, then a/b are re-decided by their mutual match.
    assert [p["teams"] for p in partitions] == [["a", "b"], ["c"]]
    ab_partition, c_partition = partitions
    assert ab_partition["child"] is not None
    child = ab_partition["child"]
    assert child["members"] == ["a", "b"]
    assert [(row["team"], row["points"]) for row in child["table"]] == [
        ("a", 3),
        ("b", 0),
    ]
    assert [p["teams"] for p in child["partitions"]] == [["a"], ["b"]]
    assert c_partition["child"] is None


def test_terminal_tie_break_records_overall_criteria():
    # Everything ends 1-1: the three-team mini-league separates nothing,
    # so the final overall ruling (all stats equal here) sorts by team id.
    teams = ["charlie", "alpha", "bravo"]
    matches = [
        ("alpha", "bravo", 1, 1),
        ("alpha", "charlie", 1, 1),
        ("bravo", "charlie", 1, 1),
    ]
    response = post(teams, matches)
    assert response.status_code == 200
    tree = response.json()
    assert standings_teams(response) == ["alpha", "bravo", "charlie"]
    assert_tree_invariants(tree, teams)

    node = tree["points_groups"][0]["decision"]
    assert len(node["partitions"]) == 1
    partition = node["partitions"][0]
    assert partition["teams"] == ["alpha", "bravo", "charlie"]
    tie_break = partition["tie_break"]
    assert tie_break["order"] == ["alpha", "bravo", "charlie"]
    assert tie_break["criteria"] == FINAL_CRITERIA
    assert [row["goal_difference"] for row in tie_break["table"]] == [0, 0, 0]
    assert [row["goals_for"] for row in tie_break["table"]] == [2, 2, 2]


def test_terminal_tie_break_uses_overall_goal_difference():
    # alpha and bravo draw each other and both beat charlie; the two-way
    # mini-league (the draw) separates nothing, overall GD does.
    teams = ["alpha", "bravo", "charlie"]
    matches = [
        ("alpha", "bravo", 1, 1),
        ("alpha", "charlie", 3, 1),
        ("bravo", "charlie", 2, 0),
    ]
    response = post(teams, matches)
    assert response.status_code == 200
    tree = response.json()
    assert standings_teams(response) == ["alpha", "bravo", "charlie"]
    assert_tree_invariants(tree, teams)

    partition = tree["points_groups"][0]["decision"]["partitions"][0]
    rows = {row["team"]: row for row in partition["tie_break"]["table"]}
    assert rows["alpha"]["goal_difference"] == 2
    assert rows["bravo"]["goal_difference"] == 2
    assert rows["alpha"]["goals_for"] == 4
    assert rows["bravo"]["goals_for"] == 3


def test_no_matches_falls_back_to_team_id_for_everyone():
    teams = ["charlie", "alpha", "bravo"]
    response = post(teams, [])
    assert response.status_code == 200
    tree = response.json()
    assert standings_teams(response) == ["alpha", "bravo", "charlie"]
    assert root_members(tree) == ["alpha", "bravo", "charlie"]
    assert_tree_invariants(tree, teams)

    group = tree["points_groups"][0]
    assert group["points"] == 0
    partition = group["decision"]["partitions"][0]
    assert partition["tie_break"]["order"] == ["alpha", "bravo", "charlie"]
    assert all(
        row["points"] == 0
        and row["goal_difference"] == 0
        and row["goals_for"] == 0
        for row in group["decision"]["table"]
    )


def test_single_team_is_a_singleton_group():
    response = post(["solo"], [])
    assert response.status_code == 200
    tree = response.json()
    group = tree["points_groups"][0]
    assert group["points"] == 0
    node = group["decision"]
    assert node["members"] == ["solo"]
    partition = node["partitions"][0]
    assert partition["teams"] == ["solo"]
    assert partition["child"] is None
    assert partition["tie_break"] is None
    assert_tree_invariants(tree, ["solo"])


def test_validation_errors_match_standings_semantics():
    cases = [
        (["alpha", "bravo"], [("alpha", "charlie", 1, 0)], "unknown team"),
        (["alpha", "bravo"], [("alpha", "alpha", 1, 0)], "itself"),
        (
            ["alpha", "bravo"],
            [("alpha", "bravo", 1, 0), ("bravo", "alpha", 0, 1)],
            "duplicate matchup",
        ),
        (["alpha", "alpha"], [], "duplicate team"),
    ]
    for teams, matches, message in cases:
        response = post(teams, matches)
        assert response.status_code == 422
        assert message in response.json()["detail"]

    # Pydantic-level rejections use the same shape as /standings.
    for payload in (
        {"teams": [], "matches": []},
        {
            "teams": ["alpha", "bravo"],
            "matches": [
                {"home": "alpha", "away": "bravo", "home_score": -1, "away_score": 0}
            ],
        },
    ):
        response = client.post("/standings-explanation", json=payload)
        assert response.status_code == 422


def test_openapi_documents_the_tree_schema():
    spec = client.get("/openapi.json").json()
    path = spec["paths"]["/standings-explanation"]["post"]
    assert "decision tree" in path["description"]
    schema_name = path["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].rsplit("/", 1)[-1]
    assert schema_name == "StandingsExplanationResponse"
    components = spec["components"]["schemas"]
    node = components["DecisionNodeOut"]
    assert set(node["properties"]) == {"members", "table", "partitions"}
    partition = components["DecisionPartitionOut"]
    assert {"teams", "points", "goal_difference", "goals_for", "child", "tie_break"} <= set(
        partition["properties"]
    )
    tie_break = components["FinalTieBreakOut"]
    assert tie_break["properties"]["criteria"]["items"]["enum"] == FINAL_CRITERIA


def test_tree_matches_standings_across_random_tournaments():
    rng = random.Random(20260930)
    pool = ["t0", "t1", "t2", "t3", "t4", "t5"]
    for case in range(60):
        n = rng.randint(1, len(pool))
        teams = pool[:n]
        pairs = [(teams[i], teams[j]) for i in range(n) for j in range(i + 1, n)]
        rng.shuffle(pairs)
        # Keep an arbitrary subset of the fixtures so ties actually occur.
        played = pairs[: rng.randint(0, len(pairs))]
        matches = [
            (
                home,
                away,
                rng.choice([0, 0, 1, 2, 3]),
                rng.choice([0, 0, 1, 2, 3]),
            )
            for home, away in played
        ]
        response = post(teams, matches)
        assert response.status_code == 200, case
        body = response.json()

        standings_response = client.post(
            "/standings",
            json={
                "teams": teams,
                "matches": [
                    {"home": h, "away": a, "home_score": hs, "away_score": as_}
                    for h, a, hs, as_ in matches
                ],
            },
        )
        assert body["standings"] == standings_response.json()["standings"], case

        order = [row["team"] for row in body["standings"]]
        assert root_members(body) == order, case
        assert_tree_invariants(body, teams)
