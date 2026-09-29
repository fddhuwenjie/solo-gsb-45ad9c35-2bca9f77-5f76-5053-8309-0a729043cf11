"""Single round-robin schedule generation.

Given 2-8 teams, a total number of rounds, per-team unavailable rounds
and optional fixed matches, build a single round-robin schedule: every
pair of teams meets exactly once (with one home and one away side), and
each team plays at most once per round.

Hard constraints:
- Fixed matches keep their round and their home/away assignment.
- No match is placed in a round that one of its sides cannot play.
- For every team the difference between its home and away games is at
  most one.
- Reading a team's games in round order, it may not play three
  consecutive games all at home or all away (idle rounds break the run).

The total number of rounds must be between the minimum needed to finish
a single round-robin (``n - 1`` for even ``n``, ``n`` for odd ``n``)
and ``MAX_ROUNDS``.

Among all feasible schedules the lexicographically smallest round-by-
round sequence is returned. Each round's matches are sorted by home id
and then away id, so an empty round sorts before any match, a round with
a smaller home team sorts first, and so on. The same request always
yields the same schedule.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

MAX_TEAMS = 8
MAX_ROUNDS = 12
HOME = 1
AWAY = -1
IDLE = 0


class ScheduleValidationError(ValueError):
    """Raised when the schedule request is inconsistent or unsolvable."""


class FixedMatchLike(Protocol):
    home: str
    away: str
    round: int


def minimum_rounds(n: int) -> int:
    """Minimum rounds needed so every pair can play at most once per round."""
    return n - 1 if n % 2 == 0 else n


def validate_request(
    teams: Sequence[str],
    rounds: int,
    unavailable: dict[str, set[int]],
    fixed: Sequence[FixedMatchLike],
) -> None:
    """Validate the request independent of the actual search."""
    n = len(teams)
    if not 2 <= n <= MAX_TEAMS:
        raise ScheduleValidationError(
            f"between 2 and {MAX_TEAMS} teams are required, got {n}"
        )
    if len(set(teams)) != n:
        raise ScheduleValidationError("duplicate team identifiers are not allowed")
    if not isinstance(rounds, int) or isinstance(rounds, bool):
        raise ScheduleValidationError("rounds must be an integer")

    needed = minimum_rounds(n)
    if rounds < needed:
        raise ScheduleValidationError(
            f"rounds must be at least {needed} for {n} teams (the "
            f"single round-robin minimum) and at most {MAX_ROUNDS}, "
            f"got {rounds}"
        )
    if rounds > MAX_ROUNDS:
        raise ScheduleValidationError(
            f"rounds must be at most {MAX_ROUNDS}, got {rounds}"
        )

    for team, blocked_rounds in unavailable.items():
        for r in blocked_rounds:
            if not 1 <= r <= rounds:
                raise ScheduleValidationError(
                    f"unavailable round {r} for team '{team}' is outside "
                    f"the range 1..{rounds}"
                )

    fixed_edges: set[int] = set()
    fixed_by_round: dict[int, list[tuple[int, int]]] = {}
    for match in fixed:
        if match.home == match.away:
            raise ScheduleValidationError(
                f"team '{match.home}' cannot play against itself"
            )
        if not 1 <= match.round <= rounds:
            raise ScheduleValidationError(
                f"fixed match '{match.home}' vs '{match.away}' is scheduled "
                f"in round {match.round}, outside the range 1..{rounds}"
            )
        if match.home not in unavailable:
            raise ScheduleValidationError(f"unknown team: '{match.home}'")
        if match.away not in unavailable:
            raise ScheduleValidationError(f"unknown team: '{match.away}'")
        if match.round in unavailable[match.home]:
            raise ScheduleValidationError(
                f"fixed match '{match.home}' vs '{match.away}' in round "
                f"{match.round} conflicts with '{match.home}' being unavailable"
            )
        if match.round in unavailable[match.away]:
            raise ScheduleValidationError(
                f"fixed match '{match.home}' vs '{match.away}' in round "
                f"{match.round} conflicts with '{match.away}' being unavailable"
            )
        a, b = teams.index(match.home), teams.index(match.away)
        edge = (a * n + b) if a < b else (b * n + a)
        if edge in fixed_edges:
            raise ScheduleValidationError(
                f"duplicate fixed matchup: '{match.home}' vs '{match.away}'"
            )
        fixed_edges.add(edge)
        fixed_by_round.setdefault(match.round, []).append((a, b))

    for r, entries in fixed_by_round.items():
        used: set[int] = set()
        for a, b in entries:
            if a in used or b in used:
                raise ScheduleValidationError(
                    f"fixed matches in round {r} force a team to play twice"
                )
            used.add(a)
            used.add(b)

    # Cheap necessary conditions before running the search.
    for team in teams:
        available_rounds = rounds - len(unavailable[team])
        if available_rounds < n - 1:
            raise ScheduleValidationError(
                f"team '{team}' is unavailable in too many rounds: it must "
                f"play {n - 1} games but only {available_rounds} round(s) "
                "are available"
            )
    index = {team: i for i, team in enumerate(teams)}
    for i, home in enumerate(teams):
        for away in teams[i + 1 :]:
            a, b = index[home], index[away]
            edge = a * n + b
            if edge in fixed_edges:
                continue
            if all(
                r in unavailable[home] or r in unavailable[away]
                for r in range(1, rounds + 1)
            ):
                raise ScheduleValidationError(
                    f"no available round remains for '{home}' vs '{away}': "
                    "every round is blocked for one of the teams"
                )


@dataclass(frozen=True)
class ScheduledMatch:
    round: int
    home: str
    away: str


def compute_schedule(
    teams: Sequence[str],
    rounds: int,
    unavailable: dict[str, set[int]],
    fixed: Sequence[FixedMatchLike],
) -> list[list[ScheduledMatch]]:
    """Return the lexicographically smallest feasible schedule.

    The result is a list of ``rounds`` rounds; each round is a list of
    :class:`ScheduledMatch` sorted by (home id, away id). Raises
    :class:`ScheduleValidationError` when no feasible schedule exists.
    """
    validate_request(teams, rounds, unavailable, fixed)
    solver = _Solver(teams, rounds, unavailable, fixed)
    schedule = solver.solve()
    if schedule is None:
        raise ScheduleValidationError(
            "no feasible schedule exists: the unavailable rounds, fixed "
            "matches, home/away balance and no-three-consecutive-games "
            "rule cannot all be satisfied together"
        )
    return schedule


class _Solver:
    """Lex-ordered backtracking solver, fast on realistic instances.

    It enumerates each round's legal matchings (and orientations) in
    lexicographic round-key order, pruning with a cheap placement-only
    feasibility check and enforcing balance/streak locally; a set of
    memoized dead states avoids re-exploring the same (round, remaining
    edges, team state) subtree. Before the search a set of forced
    violations that only involve fixed matches is rejected up front,
    which makes even adversarial fixed-match-heavy unsat instances
    terminate instantly.
    """

    def __init__(
        self,
        teams: Sequence[str],
        rounds: int,
        unavailable: dict[str, set[int]],
        fixed: Sequence[FixedMatchLike],
    ) -> None:
        self.teams = list(teams)
        self.n = len(teams)
        self.R = rounds
        index = {team: i for i, team in enumerate(teams)}

        # Bit r-1 set when the team may play in round r.
        self.avail = [0] * self.n
        for t, team in enumerate(self.teams):
            mask = 0
            for r in range(1, rounds + 1):
                if r not in unavailable[team]:
                    mask |= 1 << (r - 1)
            self.avail[t] = mask

        # Fixed matches keyed by their (lo, hi) edge id.
        self.fixed: dict[int, tuple[int, int, int]] = {}
        for match in fixed:
            a, b = index[match.home], index[match.away]
            lo, hi = (a, b) if a < b else (b, a)
            self.fixed[lo * self.n + hi] = (match.round - 1, a, b)

        self.free_edges = 0
        for lo in range(self.n):
            for hi in range(lo + 1, self.n):
                if lo * self.n + hi not in self.fixed:
                    self.free_edges |= 1 << (lo * self.n + hi)

        # All edges incident to a team (fixed edges included).
        self.incident = [0] * self.n
        for lo in range(self.n):
            for hi in range(lo + 1, self.n):
                bit = 1 << (lo * self.n + hi)
                self.incident[lo] |= bit
                self.incident[hi] |= bit

        # Per-round occupied teams and fixed (home, away) entries.
        self.fixed_busy = [0] * self.n
        self.fixed_at_round: dict[int, list[tuple[int, int]]] = {}
        for edge, (r, ha, aa) in self.fixed.items():
            self.fixed_busy[ha] |= 1 << r
            self.fixed_busy[aa] |= 1 << r
            self.fixed_at_round.setdefault(r, []).append((ha, aa))

        games = self.n - 1
        self.h_min = games // 2
        self.h_max = (games + 1) // 2

        self.fit_memo: dict[tuple[int, int], bool] = {}
        self.dead: set[tuple] = set()

    # ----- public entry point ------------------------------------------------

    def solve(self) -> Optional[list[list[ScheduledMatch]]]:
        if not self._fixed_precheck():
            return None
        played = (0,) * self.n
        homes = (0,) * self.n
        last1 = (IDLE,) * self.n
        last2 = (0,) * self.n
        return self._search(
            0, self.free_edges, played, homes, last1, last2, []
        )

    def _fixed_precheck(self) -> bool:
        """Reject forced violations involving only fixed matches."""
        for t in range(self.n):
            home_rounds: list[int] = []
            away_rounds: list[int] = []
            for edge, (r, ha, aa) in self.fixed.items():
                if ha == t:
                    home_rounds.append(r)
                if aa == t:
                    away_rounds.append(r)
            if len(home_rounds) > self.h_max:
                return False
            if len(away_rounds) > self.n - 1 - self.h_min:
                return False
            for rounds_with_side in (home_rounds, away_rounds):
                ordered = sorted(rounds_with_side)
                for i in range(2, len(ordered)):
                    # Three fixed games on the same side occupying three
                    # consecutive rounds cannot be separated any more.
                    if ordered[i] - ordered[i - 1] == 1 and (
                        ordered[i - 1] - ordered[i - 2] == 1
                    ):
                        return False
        return True

    # ----- main lex search ---------------------------------------------------

    def _edge_ends(self, edge: int) -> tuple[int, int]:
        lo = edge // self.n
        return lo, edge - lo * self.n

    def _search(
        self,
        r: int,
        rem: int,
        played: tuple[int, ...],
        homes: tuple[int, ...],
        last1: tuple[int, ...],
        last2: tuple[int, ...],
        output: list[list[ScheduledMatch]],
    ) -> Optional[list[list[ScheduledMatch]]]:
        if not self._fit(r, rem):
            return None

        if rem == 0:
            tail = self._fixed_tail(r, played, homes, last1, last2)
            if tail is None:
                return None
            return [list(g) for g in output] + tail

        key = (r, rem, played, homes, last1, last2)
        if key in self.dead:
            return None

        fixed_entries = self.fixed_at_round.get(r, [])
        busy_teams = 0
        for ha, aa in fixed_entries:
            busy_teams |= 1 << ha
            busy_teams |= 1 << aa

        options = self._round_options(r, rem, busy_teams, fixed_entries)
        # options come in lex order; apply state filters.
        for pairs, chosen, sides in options:
            advanced = self._apply(
                sides, played, homes, last1, last2
            )
            if advanced is None:
                continue
            n_played, n_homes, n_last1, n_last2 = advanced
            round_matches = [
                ScheduledMatch(r + 1, ha, aa) for ha, aa in pairs
            ]
            output.append(round_matches)
            found = self._search(
                r + 1, rem & ~chosen, n_played, n_homes,
                n_last1, n_last2, output,
            )
            if found is not None:
                return found
            output.pop()

        self.dead.add(key)
        return None

    def _apply(
        self,
        sides: dict[int, int],
        played: tuple[int, ...],
        homes: tuple[int, ...],
        last1: tuple[int, ...],
        last2: tuple[int, ...],
    ) -> Optional[
        tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]
    ]:
        n_played = list(played)
        n_homes = list(homes)
        n_last1 = list(last1)
        n_last2 = list(last2)
        for team, side in sides.items():
            wanted = HOME if side == HOME else AWAY
            if last1[team] == wanted == last2[team]:
                return None
            n_played[team] += 1
            n_last2[team] = n_last1[team]
            n_last1[team] = side
            if side == HOME:
                n_homes[team] += 1
        for t in range(self.n):
            remaining = self.n - 1 - n_played[t]
            if n_homes[t] > self.h_max or n_homes[t] + remaining < self.h_min:
                return None
        return (
            tuple(n_played), tuple(n_homes),
            tuple(n_last1), tuple(n_last2),
        )

    def _fixed_tail(self, r, played, homes, last1, last2):
        """Build/check the fixed-only tail once free edges are exhausted."""
        tail: list[list[ScheduledMatch]] = []
        n_played, n_homes, n_last1, n_last2 = (
            list(played), list(homes), list(last1), list(last2)
        )
        for rr in range(r, self.R):
            sides: dict[int, int] = {}
            entries = self.fixed_at_round.get(rr, [])
            for ha, aa in entries:
                sides[ha] = HOME
                sides[aa] = AWAY
            advanced = self._apply(
                sides, tuple(n_played), tuple(n_homes),
                tuple(n_last1), tuple(n_last2),
            )
            if advanced is None:
                return None
            n_played, n_homes, n_last1, n_last2 = (
                list(advanced[0]), list(advanced[1]),
                list(advanced[2]), list(advanced[3]),
            )
            matches = [
                ScheduledMatch(rr + 1, self.teams[ha], self.teams[aa])
                for ha, aa in entries
            ]
            matches.sort(key=lambda m: (m.home, m.away))
            tail.append(matches)
        return tail

    # ----- per-round option generation --------------------------------------

    def _round_options(
        self,
        r: int,
        rem: int,
        busy_teams: int,
        fixed_entries: list[tuple[int, int]],
    ) -> list[tuple[tuple[tuple[str, str], ...], int, dict[int, int]]]:
        allowed = 0
        edges = rem
        while edges:
            bit = edges & -edges
            edges ^= bit
            lo, hi = self._edge_ends(bit.bit_length() - 1)
            if (
                not ((busy_teams >> lo) & 1)
                and not ((busy_teams >> hi) & 1)
                and (self.avail[lo] >> r) & 1
                and (self.avail[hi] >> r) & 1
            ):
                allowed |= bit

        matchings: list[int] = []
        self._gen_matchings(allowed, matchings)

        fixed_pairs = [
            (self.teams[ha], self.teams[aa]) for ha, aa in fixed_entries
        ]
        fixed_sides = {}
        for ha, aa in fixed_entries:
            fixed_sides[ha] = HOME
            fixed_sides[aa] = AWAY

        options = []
        for mask in matchings:
            edge_list = []
            bits = mask
            while bits:
                bit = bits & -bits
                bits ^= bit
                edge_list.append(self._edge_ends(bit.bit_length() - 1))
            for orient in range(1 << len(edge_list)):
                pairs = list(fixed_pairs)
                sides = dict(fixed_sides)
                for i, (lo, hi) in enumerate(edge_list):
                    if (orient >> i) & 1:
                        pairs.append((self.teams[hi], self.teams[lo]))
                        sides[hi] = HOME
                        sides[lo] = AWAY
                    else:
                        pairs.append((self.teams[lo], self.teams[hi]))
                        sides[lo] = HOME
                        sides[hi] = AWAY
                pairs.sort()
                options.append((tuple(pairs), mask, sides))
        options.sort(key=lambda item: item[0])
        return options

    def _gen_matchings(self, allowed: int, out: list[int]) -> None:
        if allowed == 0:
            out.append(0)
            return
        first_bit = allowed & -allowed
        lo, hi = self._edge_ends(first_bit.bit_length() - 1)
        self._gen_matchings(allowed ^ first_bit, out)
        rest = allowed ^ first_bit
        rest &= ~self.incident[lo]
        rest &= ~self.incident[hi]
        before = len(out)
        self._gen_matchings(rest, out)
        for i in range(before, len(out)):
            out[i] |= first_bit

    # ----- placement-only feasibility ----------------------------------------

    def _fit(self, r: int, rem: int) -> bool:
        key = (r, rem)
        cached = self.fit_memo.get(key)
        if cached is not None:
            return cached
        if rem == 0:
            self.fit_memo[key] = True
            return True
        if r == self.R:
            self.fit_memo[key] = False
            return False

        rounds_left = self.R - r
        open_rounds = (((1 << rounds_left) - 1) << r) & ((1 << self.R) - 1)
        for t in range(self.n):
            need = (rem & self.incident[t]).bit_count()
            supply = (
                open_rounds & self.avail[t] & ~self.fixed_busy[t]
            ).bit_count()
            if need > supply:
                self.fit_memo[key] = False
                return False

        edges = rem
        while edges:
            bit = edges & -edges
            edges ^= bit
            lo, hi = self._edge_ends(bit.bit_length() - 1)
            if not (
                open_rounds
                & self.avail[lo]
                & self.avail[hi]
                & ~self.fixed_busy[lo]
                & ~self.fixed_busy[hi]
            ):
                self.fit_memo[key] = False
                return False

        busy_teams = 0
        for ha, aa in self.fixed_at_round.get(r, []):
            busy_teams |= 1 << ha
            busy_teams |= 1 << aa
        allowed = 0
        edges = rem
        while edges:
            bit = edges & -edges
            edges ^= bit
            lo, hi = self._edge_ends(bit.bit_length() - 1)
            if (
                not ((busy_teams >> lo) & 1)
                and not ((busy_teams >> hi) & 1)
                and (self.avail[lo] >> r) & 1
                and (self.avail[hi] >> r) & 1
            ):
                allowed |= bit

        matchings: list[int] = []
        self._gen_matchings(allowed, matchings)
        matchings.sort(key=lambda mask: -mask.bit_count())
        for mask in matchings:
            if self._fit(r + 1, rem ^ mask):
                self.fit_memo[key] = True
                return True
        self.fit_memo[key] = False
        return False
