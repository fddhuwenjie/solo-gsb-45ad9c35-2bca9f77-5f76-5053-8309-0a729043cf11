# 循环赛积分榜 API

基于 FastAPI 的循环赛（round-robin）服务。提交参赛队伍与已完赛比分，返回排名及各项统计数据；也可让服务返回一棵**可机读的排名判定树**，说明每一层同分判定是如何分出先后的；也可提交未赛对阵的候选比分，枚举所有赛果组合，分析各队跻身前 K 名的出线形势；还可提交队伍数、总轮数、各队不可参赛轮次和固定比赛，生成满足主客场均衡与连场限制的单循环赛程。

## 排名规则

1. **积分**：胜 3 分、平 1 分、负 0 分。
2. **同分队伍**：仅使用这些队伍之间的比赛建立"小联赛"，依次比较小联赛积分、净胜球、进球数。
3. **部分分出**：若小联赛只分出部分队伍，对仍同分的小组**重新建立小联赛**（递归）再比较。
4. **仍同分**：依次比较总净胜球、总进球数、队伍 ID（字典序升序）。

`POST /standings/explanation` 会把上述每一步判定输出为一棵可机读的判定树（每层的小联赛积分/净胜球/进球、递归重建的子小联赛、最终裁决值），详见下文[排名判定树](#post-standingsexplanation)。

## 校验规则

出现以下情况时返回 `422`：

- 比赛中出现未知队伍（不在 `teams` 列表中）
- 队伍与自己比赛（`home == away`）
- 重复对阵（同一对队伍出现两次，主客互换也算重复；未赛对阵不得与已赛或其他未赛项重复）
- 队伍 ID 重复、空队伍列表、负比分
- 未赛对阵的候选比分为空或含负数
- `top_k` 小于 1
- 候选比分组合数超过 100000

## 赛程生成（POST /round-robin-schedules）

提交 2～8 支队伍、总轮数（1～12）、各队不可参赛轮次（可选）和固定比赛（可选），生成**单循环**赛程。

### 约束

- 每对队伍**必须且只比赛一次**；每队每轮至多一场。
- 总轮数必须介于完成单循环所需的最少轮数（偶数队为 `n-1`，奇数队为 `n`）与 12 之间。
- 固定比赛（`fixed_matches`）**保留指定轮次和主客关系**。
- 其余比赛避开每队的不可参赛轮次（`unavailability`）。
- 每队主场数与客场数之差**不超过 1**。
- 按轮次顺序查看每队的比赛，**不得连续三场同为主场或同为客场**（轮空会打断连续性）。
- 每轮比赛按主队 ID、客队 ID 排序；在所有可行赛程中返回**逐轮序列字典序最小**的方案（空轮排在最前），同一请求始终返回相同结果。

`unavailability` 为列表，每项含 `team` 与 `unavailable_rounds`（1 起始的轮次号）。`fixed_matches` 每项含 `home`、`away`、`round`。固定比赛与不可参赛轮次冲突、同轮重复使用同一支队伍、重复对阵或自对阵均返回 `422`；约束无法同时满足（无可行赛程）时也返回 `422` 并说明原因。

## 启动方式

### Docker Compose（推荐）

```bash
docker compose up --build
```

### Dockerfile

```bash
docker build -t standings-api .
docker run -p 8000:8000 standings-api
```

### 本地开发

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## 访问端口

服务监听 **8000** 端口：

- 积分榜端点：`POST http://localhost:8000/standings`
- 排名判定树端点：`POST http://localhost:8000/standings/explanation`
- 出线形势端点：`POST http://localhost:8000/qualification-scenarios`
- 赛程生成端点：`POST http://localhost:8000/round-robin-schedules`
- Swagger 交互文档：`http://localhost:8000/docs`
- OpenAPI 规范：`http://localhost:8000/openapi.json`

## 请求示例

### POST /standings

```bash
curl -X POST http://localhost:8000/standings \
  -H "Content-Type: application/json" \
  -d '{
    "teams": ["alpha", "bravo", "charlie"],
    "matches": [
      {"home": "alpha", "away": "bravo", "home_score": 2, "away_score": 1},
      {"home": "bravo", "away": "charlie", "home_score": 1, "away_score": 1},
      {"home": "alpha", "away": "charlie", "home_score": 0, "away_score": 0}
    ]
  }'
```

响应：

```json
{
  "standings": [
    {"rank": 1, "team": "alpha", "played": 2, "wins": 1, "draws": 1, "losses": 0,
     "goals_for": 2, "goals_against": 1, "goal_difference": 1, "points": 4},
    {"rank": 2, "team": "charlie", "played": 2, "wins": 0, "draws": 2, "losses": 0,
     "goals_for": 1, "goals_against": 1, "goal_difference": 0, "points": 2},
    {"rank": 3, "team": "bravo", "played": 2, "wins": 0, "draws": 1, "losses": 1,
     "goals_for": 2, "goals_against": 3, "goal_difference": -1, "points": 1}
  ]
}
```

### POST /standings/explanation

请求体与 `/standings` 完全相同（沿用 `teams` 与 `matches`，校验规则与 `422` 语义也一致），响应中的 `standings` 与同一请求发给 `/standings` 的结果**逐字节相同**；此外附带一棵可机读的排名判定树 `explanation`，它是榜单顺序的唯一来源（`/standings` 由同一棵树展平得到，不会另写排序）。

树的结构：

- 顶层 `groups` 先按**总积分**分组（积分降序），每个组给出 `points`、按最终名次排列的 `members`，以及仅由组内比赛计算的 `mini_table`（小联赛 `points` / `goal_difference` / `goals_for`）。
- 每组的 `partitions` 是该小联赛按（小联赛积分、净胜球、进球数）形成的**有序分区**，分区 `members` 顺序拼接即为本组名次。分区有三种 `kind`：
  - `single`：该层已分出的单队（`team` 即该队）。
  - `mini_league`：仍未分开的子组；节点内嵌**仅用该子组相互比赛重建**的小联赛 `mini_table` 与下一层 `partitions`（递归）。
  - `final`：本层小联赛完全无法区分（所有队伍三项相同）；`final_table` 给出总净胜球、总进球、队伍 ID 的最终裁决，行序即名次。
- 顶层 `members` 是所有分区成员顺序拼接的结果，严格等于 `standings` 中的队伍顺序。

下面的例子中 a/b/c 同积 6 分：第一层小联赛把 c 分出（进球少），a 与 b 仍并列，于是只看 a vs b 重建小联赛，a 胜而居首。

```bash
curl -X POST http://localhost:8000/standings/explanation \
  -H "Content-Type: application/json" \
  -d '{
    "teams": ["a", "b", "c", "d"],
    "matches": [
      {"home": "a", "away": "b", "home_score": 2, "away_score": 1},
      {"home": "c", "away": "a", "home_score": 1, "away_score": 0},
      {"home": "b", "away": "c", "home_score": 1, "away_score": 0},
      {"home": "a", "away": "d", "home_score": 1, "away_score": 0},
      {"home": "b", "away": "d", "home_score": 5, "away_score": 0},
      {"home": "c", "away": "d", "home_score": 1, "away_score": 0}
    ]
  }'
```

响应（`standings` 部分与 `/standings` 相同，此处只展示 `explanation`）：

```json
{
  "criterion": "total_points",
  "members": ["a", "b", "c", "d"],
  "groups": [
    {
      "points": 6,
      "members": ["a", "b", "c"],
      "mini_table": [
        {"team": "a", "points": 3, "goal_difference": 0, "goals_for": 2},
        {"team": "b", "points": 3, "goal_difference": 0, "goals_for": 2},
        {"team": "c", "points": 3, "goal_difference": 0, "goals_for": 1}
      ],
      "partitions": [
        {
          "kind": "mini_league",
          "members": ["a", "b"],
          "team": null,
          "partitions": [
            {"kind": "single", "members": ["a"], "team": "a",
             "partitions": null, "mini_table": null, "final_table": null},
            {"kind": "single", "members": ["b"], "team": "b",
             "partitions": null, "mini_table": null, "final_table": null}
          ],
          "mini_table": [
            {"team": "a", "points": 3, "goal_difference": 1, "goals_for": 2},
            {"team": "b", "points": 0, "goal_difference": -1, "goals_for": 1}
          ],
          "final_table": null
        },
        {
          "kind": "single", "members": ["c"], "team": "c",
          "partitions": null, "mini_table": null, "final_table": null
        }
      ]
    },
    {
      "points": 0,
      "members": ["d"],
      "mini_table": [
        {"team": "d", "points": 0, "goal_difference": 0, "goals_for": 0}
      ],
      "partitions": [
        {"kind": "single", "members": ["d"], "team": "d",
         "partitions": null, "mini_table": null, "final_table": null}
      ]
    }
  ]
}
```

当小联赛在某层完全无法区分（例如同组内全部打平）时，对应分区为 `"kind": "final"`，其 `final_table` 按总净胜球、总进球、队伍 ID 排序并逐行给出数值，例如：

```json
{
  "kind": "final",
  "members": ["alpha", "bravo", "charlie"],
  "team": null,
  "partitions": null,
  "mini_table": null,
  "final_table": [
    {"team": "alpha", "goal_difference": 5, "goals_for": 7},
    {"team": "bravo", "goal_difference": 2, "goals_for": 4},
    {"team": "charlie", "goal_difference": 1, "goals_for": 3}
  ]
}
```

### POST /qualification-scenarios

在 `teams`、`matches`（已赛）之外，额外提供：

- `top_k`：出线名额（名次 ≤ top_k 视为出线）。
- `remaining`：未赛对阵列表，每项含 `home`、`away` 和有限的候选比分 `candidates`（至少一个，均非负）。

服务枚举所有候选比分组合（笛卡尔积，超过 100000 时返回 `422`），对每种组合套用与 `/standings` 相同的排名规则，给出每队的**最佳名次** `best_rank` 与**最差名次** `worst_rank`，并据此分类：

- `guaranteed`：最差名次 ≤ top_k，铁定出线；
- `possible`：最差名次 > top_k 但最佳名次 ≤ top_k，仍有可能；
- `eliminated`：最佳名次 > top_k，确定无缘。

每个名次极值都附带一组**完整的未赛赛果**作为见证（`best_witness` / `worst_witness`，与 `remaining` 一一对应、顺序一致），将其与已赛比分一起提交 `/standings` 可复现对应名次。响应按最佳名次、最差名次、队伍 ID 排序，`combinations` 为实际枚举的组合数。

```bash
curl -X POST http://localhost:8000/qualification-scenarios \
  -H "Content-Type: application/json" \
  -d '{
    "teams": ["alpha", "bravo", "charlie", "delta"],
    "matches": [
      {"home": "alpha", "away": "bravo", "home_score": 2, "away_score": 0},
      {"home": "alpha", "away": "charlie", "home_score": 2, "away_score": 0},
      {"home": "bravo", "away": "charlie", "home_score": 1, "away_score": 0}
    ],
    "top_k": 2,
    "remaining": [
      {"home": "delta", "away": "alpha",
       "candidates": [
         {"home_score": 1, "away_score": 0},
         {"home_score": 0, "away_score": 0},
         {"home_score": 0, "away_score": 1}
       ]},
      {"home": "delta", "away": "bravo",
       "candidates": [
         {"home_score": 1, "away_score": 0},
         {"home_score": 0, "away_score": 0},
         {"home_score": 0, "away_score": 1}
       ]},
      {"home": "delta", "away": "charlie",
       "candidates": [
         {"home_score": 1, "away_score": 0},
         {"home_score": 0, "away_score": 0},
         {"home_score": 0, "away_score": 1}
       ]}
    ]
  }'
```

响应（bravo/charlie 的见证从略）：

```json
{
  "top_k": 2,
  "combinations": 27,
  "scenarios": [
    {
      "team": "alpha",
      "status": "guaranteed",
      "best_rank": 1,
      "worst_rank": 2,
      "best_witness": [
        {"home": "delta", "away": "alpha", "home_score": 1, "away_score": 0},
        {"home": "delta", "away": "bravo", "home_score": 0, "away_score": 0},
        {"home": "delta", "away": "charlie", "home_score": 0, "away_score": 0}
      ],
      "worst_witness": [
        {"home": "delta", "away": "alpha", "home_score": 1, "away_score": 0},
        {"home": "delta", "away": "bravo", "home_score": 1, "away_score": 0},
        {"home": "delta", "away": "charlie", "home_score": 1, "away_score": 0}
      ]
    },
    {"team": "delta", "status": "possible", "best_rank": 1, "worst_rank": 4,
     "best_witness": ["..."], "worst_witness": ["..."]},
    {"team": "bravo", "status": "possible", "best_rank": 2, "worst_rank": 3,
     "best_witness": ["..."], "worst_witness": ["..."]},
    {"team": "charlie", "status": "eliminated", "best_rank": 3, "worst_rank": 4,
     "best_witness": ["..."], "worst_witness": ["..."]}
  ]
}
```

### POST /round-robin-schedules

```bash
curl -X POST http://localhost:8000/round-robin-schedules \
  -H "Content-Type: application/json" \
  -d '{
    "teams": ["alpha", "bravo", "charlie", "delta"],
    "rounds": 4,
    "unavailability": [
      {"team": "alpha", "unavailable_rounds": [3]}
    ],
    "fixed_matches": [
      {"home": "delta", "away": "alpha", "round": 1}
    ]
  }'
```

响应（每轮按主队、客队 ID 排序，整体为字典序最小的可行赛程）：

```json
{
  "teams": ["alpha", "bravo", "charlie", "delta"],
  "rounds": 4,
  "schedule": [
    {"round": 1, "matches": [
      {"home": "bravo", "away": "charlie"},
      {"home": "delta", "away": "alpha"}
    ]},
    {"round": 2, "matches": [
      {"home": "alpha", "away": "bravo"}
    ]},
    {"round": 3, "matches": [
      {"home": "charlie", "away": "delta"}
    ]},
    {"round": 4, "matches": [
      {"home": "alpha", "away": "charlie"},
      {"home": "bravo", "away": "delta"}
    ]}
  ]
}
```

固定比赛 `delta`（主）对 `alpha`（客）保留在第 1 轮；`alpha` 不在第 3 轮出赛；每队均为 2 主 2 客（或主客差 1），且不存在连续三场同主或同客。

## 运行测试

```bash
pip install -r requirements.txt
pytest
```

## 项目结构

```
app/
  main.py        # FastAPI 应用与路由
  schemas.py     # 请求/响应模型
  standings.py   # 积分榜计算与校验逻辑
  scenarios.py   # 出线形势枚举与名次极值计算
  scheduling.py  # 单循环赛程生成与可行性回溯
tests/
  test_standings.py
  test_standings_explanation.py
  test_scenarios.py
  test_schedules.py
Dockerfile
docker-compose.yml
requirements.txt
```
