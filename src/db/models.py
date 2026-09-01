from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint

from src.db.base import Base
from src.timeutil import utc_now


class Symbol(Base):
    __tablename__ = "symbols"

    symbol = Column(String, primary_key=True)
    base_asset = Column(String, nullable=False)
    quote_asset = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    # Nullable is mandatory, not a style choice: sync_missing_columns (see
    # src/db/session.py) only ever ADDs nullable columns to a live database.
    # A NOT NULL column here would be skipped there, and every ORM query
    # against `symbols` would then fail on the deployed service.
    #
    # No Python-side default either: a default would fire on every INSERT that
    # leaves the value unset, making NULL unreachable through the ORM. NULL is
    # a meaningful state here — "not yet classified by the daily symbol
    # refresh" — and is exactly what ALTER TABLE ADD COLUMN leaves on every
    # pre-existing row.
    has_futures_contract = Column(Boolean, nullable=True)
    listed_at = Column(DateTime, nullable=True)
    # utc_now() returns a naive UTC datetime, matching this naive DateTime
    # column. A tz-aware value here would be converted using the server's
    # TimeZone setting on PostgreSQL and could shift what gets stored.
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)


class Kline(Base):
    __tablename__ = "klines"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "open_time", name="uq_kline_symbol_timeframe_open_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    open_time = Column(DateTime, nullable=False)
    open = Column(Numeric(20, 8), nullable=False)
    high = Column(Numeric(20, 8), nullable=False)
    low = Column(Numeric(20, 8), nullable=False)
    close = Column(Numeric(20, 8), nullable=False)
    volume = Column(Numeric(30, 8), nullable=False)
    flagged = Column(Boolean, nullable=False, default=False)


class FetchLog(Base):
    __tablename__ = "fetch_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False)
    error_message = Column(Text, nullable=True)


class Scenario(Base):
    __tablename__ = "scenarios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)
    target_price = Column(Numeric(20, 8), nullable=False)
    stop_price = Column(Numeric(20, 8), nullable=False)
    expected_return_pct = Column(Numeric(10, 6), nullable=False)
    confidence_score = Column(Numeric(5, 4), nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="pending")
    resolved_at = Column(DateTime, nullable=True)
    calibrated_confidence = Column(Numeric(5, 4), nullable=True)
    strategy_version = Column(String, nullable=True)


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=False, unique=True)
    symbol = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)
    stop_price = Column(Numeric(20, 8), nullable=False)
    target_price = Column(Numeric(20, 8), nullable=False)
    risk_amount = Column(Numeric(20, 8), nullable=False)
    position_size = Column(Numeric(20, 8), nullable=False)
    opened_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="open")
    closed_at = Column(DateTime, nullable=True)
    exit_price = Column(Numeric(20, 8), nullable=True)
    realized_pnl = Column(Numeric(20, 8), nullable=True)
    equity_before = Column(Numeric(20, 8), nullable=True)
    equity_after = Column(Numeric(20, 8), nullable=True)
    exit_reason = Column(String, nullable=True)
    strategy_version = Column(String, nullable=True)


class FundingRateHistory(Base):
    __tablename__ = "funding_rate_history"
    __table_args__ = (
        UniqueConstraint("symbol", "funding_time", name="uq_funding_history_symbol_time"),
    )

    # Unlike `funding_rates`, which keeps only the latest print for the gate,
    # this is the append-only event log a closed paper position is charged
    # against: which funding events fell inside the window it was open.
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    funding_time = Column(DateTime, nullable=False)
    funding_rate = Column(Numeric(10, 8), nullable=False)
    mark_price = Column(Numeric(20, 8), nullable=False)


class FundingRate(Base):
    __tablename__ = "funding_rates"

    # One row per symbol: only the latest print matters to the gate, so this
    # is a "last known value" table, not an append-only history.
    symbol = Column(String, primary_key=True)
    funding_rate = Column(Numeric(10, 8), nullable=False)
    fetched_at = Column(DateTime, nullable=False)


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"

    # The portfolio path's own position table, deliberately not PaperPosition.
    # A paper position is a barrier trade: it must carry a stop and a target,
    # and it closes when one of them is touched. This strategy has neither. It
    # holds a basket, rebalances on a clock, and exits because the clock said
    # so. Storing it in PaperPosition would mean inventing a stop_price and a
    # target_price that nothing reads and that every report would then have to
    # explain.
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_version = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)
    position_size = Column(Numeric(20, 8), nullable=False)
    opened_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="open")
    closed_at = Column(DateTime, nullable=True)
    exit_price = Column(Numeric(20, 8), nullable=True)
    # Kept apart rather than folded into one net figure: a book that is
    # profitable before costs and unprofitable after has a fee problem, and a
    # book whose funding line dominates is not really trading momentum any
    # more. Netting them hides both diagnoses.
    gross_pnl = Column(Numeric(20, 8), nullable=True)
    fee_cost = Column(Numeric(20, 8), nullable=True)
    funding_cost = Column(Numeric(20, 8), nullable=True)
    realized_pnl = Column(Numeric(20, 8), nullable=True)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        UniqueConstraint("strategy_version", "as_of", name="uq_portfolio_snapshot_version_time"),
    )

    # Equity is recorded per rebalance rather than per position, because the
    # whole book closes and reopens at once: there is no single position whose
    # exit "is" the new equity the way there is on the paper path.
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_version = Column(String, nullable=False, index=True)
    as_of = Column(DateTime, nullable=False)
    equity = Column(Numeric(20, 8), nullable=False)
    positions_closed = Column(Integer, nullable=False, default=0)
    positions_opened = Column(Integer, nullable=False, default=0)
