from __future__ import annotations

from dataclasses import dataclass

from datetime import timedelta
from decimal import Decimal

from src.integrity import TIMEFRAME_DELTAS
from src.outcome_evaluator import evaluate_outcome
from src.paper_trading_config import TAKER_FEE_RATE
from src.indicators import compute_ema, compute_rsi, detect_confluence_in_window
from src.scenario_runner import SCENARIO_LOOKBACK, _window_rejection
from src.scenario_builder import MAX_EXPIRY_HOURS, MIN_STOP_PCT, build_scenario
from src.scenario_signal import (
    CONFLUENCE_WINDOW,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    MIN_CANDLES,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    RSI_PERIOD,
    VOLUME_LOOKBACK,
    VOLUME_MULTIPLIER,
    SignalResult,
    evaluate_signal,
)


# How long rule C waits for the EMA/volume confirmation to follow the RSI
# cross. The shipped rule effectively allows 0 — it demands both on one
# candle — which is what closes the funnel.
DELAYED_CONFIRM_WINDOW = 15


@dataclass
class FunnelCounts:
    evaluated: int = 0
    rsi_cross_up: int = 0
    confluence_bullish: int = 0
    signal_long: int = 0
    # Candidate rule B: RSI cross-up while the trend is already up.
    signal_long_trend_aligned: int = 0
    # Candidate rule C: confirmation arrives after the bounce, not with it.
    signal_long_delayed_confirm: int = 0


def _crossed_up_recently(rsi: list, window: int) -> bool:
    """Did RSI cross up through the oversold line within the last `window`
    closed candles? Read off the RSI series itself, so no state has to be
    carried between evaluation points."""
    for index in range(len(rsi) - 1, max(len(rsi) - 1 - window, 0), -1):
        current, previous = rsi[index], rsi[index - 1]
        if current is None or previous is None:
            continue
        if previous < RSI_OVERSOLD <= current:
            return True
    return False


def analyze_symbol(klines: list) -> FunnelCounts:
    counts = FunnelCounts()
    step = TIMEFRAME_DELTAS["1h"]
    for end in range(MIN_CANDLES, len(klines) + 1):
        window = klines[max(0, end - SCENARIO_LOOKBACK):end]
        # The same gate production applies before reading any indicator, so
        # this table and the backtest describe one strategy, not two.
        if _window_rejection(window, "1h", window[-1]["open_time"] + step) is not None:
            continue
        counts.evaluated += 1

        closes = [row["close"] for row in window]
        volumes = [row["volume"] for row in window]

        rsi = compute_rsi(closes, RSI_PERIOD)
        current, previous = rsi[-1], rsi[-2]
        rsi_up = (
            current is not None and previous is not None
            and previous < RSI_OVERSOLD <= current
        )
        if rsi_up:
            counts.rsi_cross_up += 1

        ema_fast = compute_ema(closes, EMA_FAST_PERIOD)
        ema_slow = compute_ema(closes, EMA_SLOW_PERIOD)
        crossover = detect_confluence_in_window(
            ema_fast, ema_slow,
            volumes, VOLUME_LOOKBACK, VOLUME_MULTIPLIER, CONFLUENCE_WINDOW,
        )
        trend_up = (
            ema_fast[-1] is not None and ema_slow[-1] is not None
            and ema_fast[-1] > ema_slow[-1]
        )
        if rsi_up and trend_up:
            counts.signal_long_trend_aligned += 1

        if crossover == "bullish" and _crossed_up_recently(rsi, DELAYED_CONFIRM_WINDOW):
            counts.signal_long_delayed_confirm += 1
        if crossover == "bullish":
            counts.confluence_bullish += 1

        # Delegated rather than reimplemented: the whole point is to measure
        # the production rule, so any drift here would measure a fiction.
        signal = evaluate_signal(window)
        if signal is not None and signal.direction == "long":
            counts.signal_long += 1
    return counts


def resolve_draft(draft, future_klines: list):
    """Score a draft against the candles that followed it: `(status, r_multiple)`.

    R is measured in units of the trade's own risk, so scenarios with wildly
    different stop distances stay comparable.
    """
    step = TIMEFRAME_DELTAS["1h"]
    # Unfinished scenarios are dropped, not scored — see the test.
    if not future_klines or future_klines[-1]["open_time"] + step < draft.expires_at:
        return None

    risk = abs(draft.entry_price - draft.stop_price)
    outcome = evaluate_outcome(
        draft.direction, draft.target_price, draft.stop_price,
        draft.expires_at, future_klines, draft.expires_at + step,
    )
    status, resolved_at = outcome
    if status == "hit_stop":
        return DraftOutcome(status, Decimal("-1"), draft.stop_price, resolved_at)
    if status == "hit_target":
        reward = abs(draft.target_price - draft.entry_price)
        return DraftOutcome(status, reward / risk, draft.target_price, resolved_at)

    # Expired: worth its unrealised move at the last candle that closed
    # before expiry, mirroring paper_position_closer's exit rule.
    before_expiry = [row for row in future_klines if row["open_time"] < draft.expires_at]
    if not before_expiry:
        # A data gap swallowed the whole life of the scenario: there is no
        # candle to read an exit price from, so it cannot be scored.
        return None
    exit_price = before_expiry[-1]["close"]
    move = (
        exit_price - draft.entry_price if draft.direction == "long"
        else draft.entry_price - exit_price
    )
    return DraftOutcome(status, move / risk, exit_price, resolved_at)


def fee_cost_in_r(entry_price: Decimal, exit_price: Decimal, risk: Decimal,
                  fee_rate: Decimal) -> Decimal:
    """What a round trip costs in units of the trade's own risk.

    Charges each leg on the notional actually transacted, the same way
    `paper_position_closer._fees` does, and takes the rate from the shipped
    config rather than restating it — a replay that models costs differently
    from the portfolio it is meant to inform is worse than one that models
    none at all.
    """
    return (entry_price + exit_price) * fee_rate / risk


@dataclass
class DraftOutcome:
    status: str
    r_multiple: Decimal
    exit_price: Decimal
    resolved_at: object = None


@dataclass
class RuleResult:
    signals: int = 0
    no_levels: int = 0
    regime_blocked: int = 0
    filtered: int = 0
    unscored: int = 0
    hit_target: int = 0
    hit_stop: int = 0
    expired: int = 0
    total_r: Decimal = Decimal("0")
    total_fee_r: Decimal = Decimal("0")


def _rule_signal(rule: str, window: list, rsi: list = None):
    """The SignalResult a candidate rule would produce at this window's end.

    "shipped" delegates to production. The candidates reuse the same RSI and
    EMA inputs and differ only in how they combine them.

    `rsi` may be a precomputed slice ending at this window's last candle —
    see `_rsi_for_window`. The RSI gates are evaluated before the EMA ones
    because they are both cheaper and far more selective (~3% of candles), and
    computing an EMA for a window that cannot fire is most of the runtime.
    """
    closes = [row["close"] for row in window]
    if rule == "shipped":
        # Production is still what decides; this only skips calling it when
        # its own RSI precondition cannot hold. `evaluate_signal` returns a
        # signal only on a threshold crossing, so a candle without one would
        # cost a 101-candle Decimal RSI recomputation to learn nothing.
        # Equivalence is pinned candle-by-candle against production in
        # test_the_shipped_rule_short_circuit_never_changes_which_signals_fire.
        if rsi is not None:
            current, previous = rsi[-1], rsi[-2]
            if current is None or previous is None:
                return None
            if not (previous < RSI_OVERSOLD <= current
                    or previous > RSI_OVERBOUGHT >= current):
                return None
        return evaluate_signal(window)

    rsi = rsi if rsi is not None else compute_rsi(closes, RSI_PERIOD)
    current, previous = rsi[-1], rsi[-2]
    if current is None or previous is None:
        return None

    if rule == "trend":
        if not previous < RSI_OVERSOLD <= current:
            return None
        ema_fast = compute_ema(closes, EMA_FAST_PERIOD)
        ema_slow = compute_ema(closes, EMA_SLOW_PERIOD)
        fires = (
            ema_fast[-1] is not None and ema_slow[-1] is not None
            and ema_fast[-1] > ema_slow[-1]
        )
    elif rule == "delayed":
        if not _crossed_up_recently(rsi, DELAYED_CONFIRM_WINDOW):
            return None
        crossover = detect_confluence_in_window(
            compute_ema(closes, EMA_FAST_PERIOD), compute_ema(closes, EMA_SLOW_PERIOD),
            [row["volume"] for row in window], VOLUME_LOOKBACK, VOLUME_MULTIPLIER,
            CONFLUENCE_WINDOW,
        )
        fires = crossover == "bullish"
    else:
        raise ValueError("unknown rule: %r" % rule)

    if not fires:
        return None
    return SignalResult(
        direction="long", entry_price=closes[-1], rsi=current, previous_rsi=previous,
    )


def _rsi_for_window(rsi_series: list, end: int, window_length: int) -> list:
    """The slice of a whole-series RSI that a window of its own would produce.

    Safe because this RSI is a pure sliding window — `rsi[i]` reads only the
    `RSI_PERIOD` closes before `i` and carries nothing forward. EMA is
    recursive from a seed and deliberately stays windowed.
    See `test_rsi_over_a_window_equals_rsi_over_the_whole_series_at_the_same_point`.
    """
    return rsi_series[end - window_length:end]


def passes_risk_filters(draft, min_stop_pct, min_rr) -> bool:
    """Would this draft survive the two guards `build_scenario` is missing?

    Both are opt-in (None disables) so the replay can measure the shipped
    behaviour and a candidate fix side by side.
    """
    risk = abs(draft.entry_price - draft.stop_price)
    if risk == 0:
        return False
    if min_stop_pct is not None and risk / draft.entry_price < min_stop_pct:
        return False
    if min_rr is not None and abs(draft.target_price - draft.entry_price) / risk < min_rr:
        return False
    return True


def _regime_allows(regime, direction: str) -> bool:
    """Mirrors `process_symbol_scenario`: an undetermined regime blocks every
    direction, not just the one it disagrees with."""
    if regime is None:
        return False
    return regime == ("up" if direction == "long" else "down")


def is_locked(live_until: dict, direction: str, now) -> bool:
    """Is a scenario in this direction still live at `now`?"""
    expiry = live_until.get(direction)
    return expiry is not None and now < expiry


@dataclass
class ScenarioEvent:
    """One outcome of evaluating a rule at a single candle boundary.

    `kind` is "draft" when a scenario was produced, or the reason it was not:
    "regime_blocked" or "no_levels".
    """
    kind: str
    now: object
    direction: str
    draft: object = None
    future: object = None


# Longest a scenario can live, plus slack — bounds the future slice so scoring
# a week-long scenario does not walk two years of candles.
FUTURE_HORIZON = MAX_EXPIRY_HOURS + 10


def iter_scenarios(symbol: str, klines: list, rule: str, regime_at=None, start_after=None):
    """Every scenario `rule` would create over `klines`, in order.

    The single walk shared by the backtest and the walk-forward runner. A
    second copy of this loop is how the funnel table and the backtest came to
    apply different gates, so callers filter the events rather than re-deriving
    them.
    """
    step = TIMEFRAME_DELTAS["1h"]
    rsi_series = compute_rsi([row["close"] for row in klines], RSI_PERIOD)
    for end in range(MIN_CANDLES, len(klines) + 1):
        window = klines[max(0, end - SCENARIO_LOOKBACK):end]
        # The scenario is created just after the signal candle closes, exactly
        # as the hourly job does — never on the candle it was read from.
        now = window[-1]["open_time"] + step
        if start_after is not None and now < start_after:
            continue
        # Same gate production applies: stale, gapped or anomaly-flagged
        # windows are never read for indicators.
        if _window_rejection(window, "1h", now) is not None:
            continue

        signal = _rule_signal(rule, window, _rsi_for_window(rsi_series, end, len(window)))
        if signal is None:
            continue
        if regime_at is not None and not _regime_allows(regime_at(now), signal.direction):
            yield ScenarioEvent("regime_blocked", now, signal.direction)
            continue

        draft = build_scenario(symbol, signal, window, now)
        if draft is None:
            yield ScenarioEvent("no_levels", now, signal.direction)
            continue
        yield ScenarioEvent("draft", now, signal.direction, draft, klines[end:end + FUTURE_HORIZON])


def backtest_symbol(symbol: str, klines: list, rule: str,
                    min_stop_pct=None, min_rr=None, regime_at=None,
                    start_after=None) -> RuleResult:
    """Replay one rule over one symbol.

    `regime_at(now)` supplies BTC's daily regime the way
    `run_scenario_generation` computes it once per run; leave it None to
    measure a rule with the regime gate lifted.

    `start_after` scores only signals at or after that moment, while still
    reading indicator history from before it — the boundary for an
    out-of-sample run.
    """
    result = RuleResult()
    # Mirrors `has_pending_scenario`: one live scenario per direction at a
    # time. Without it a single EMA cross is counted three times, because
    # `detect_confluence_in_window` reports it for CONFLUENCE_WINDOW candles.
    #
    # Held until expiry, where production releases it as soon as the scenario
    # resolves. That is the conservative direction — it can only skip signals,
    # never invent them — and per-trade expectancy, the number this tool exists
    # to compare, is unaffected by how many are skipped.
    live_until = {}
    for event in iter_scenarios(symbol, klines, rule, regime_at, start_after):
        # `regime_blocked` is counted apart from `signals`: the gate rejects
        # the signal outright, so it never becomes one. A signal that finds no
        # support/resistance levels DID fire and is counted.
        if event.kind == "regime_blocked":
            result.regime_blocked += 1
            continue
        if is_locked(live_until, event.direction, event.now):
            continue
        result.signals += 1
        if event.kind == "no_levels":
            result.no_levels += 1
            continue

        draft = event.draft
        if not passes_risk_filters(draft, min_stop_pct, min_rr):
            result.filtered += 1
            continue
        live_until[event.direction] = draft.expires_at

        scored = resolve_draft(draft, event.future)
        if scored is None:
            live_until[event.direction] = draft.expires_at
            result.unscored += 1
            continue
        setattr(result, scored.status, getattr(result, scored.status) + 1)
        result.total_r += scored.r_multiple
        result.total_fee_r += fee_cost_in_r(
            draft.entry_price, scored.exit_price,
            abs(draft.entry_price - draft.stop_price), TAKER_FEE_RATE,
        )
        live_until[event.direction] = scored.resolved_at
    return result
