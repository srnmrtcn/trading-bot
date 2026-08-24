# Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single, Basic-Auth-protected page that shows the service is alive: last activity time/health, simulated equity + a small history chart, open paper positions, and the most recent scenarios.

**Architecture:** A Flask app (`src/web.py`) runs in the process's main thread, replacing the `time.sleep(60)` loop that currently keeps `main.py` alive while `BackgroundScheduler` does the real work in its own background thread — the two never share a DB session, only the `session_factory`. Pure query functions live in `src/dashboard_data.py`, kept separate from the Flask route so they're testable without an HTTP client.

**Tech Stack:** Same as the rest of the project — Python 3.9, SQLAlchemy 2.0 ORM, `Decimal` for money. New: `flask` (pulls in `werkzeug`, used directly for Basic Auth password hashing).

**Spec:** `docs/superpowers/specs/2026-08-24-web-dashboard-design.md`

## Global Constraints

- Python 3.9 compatibility — every file using `X | None` / `list[X]` style annotations MUST start with `from __future__ import annotations`.
- All price/equity values stay `Decimal` end-to-end; only formatted for display inside the Jinja template (`"%.2f"|format(value)` — verified to work with `Decimal`).
- Health thresholds: `< 90 minutes` since the last `FetchLog.finished_at` → `"healthy"`; `90 minutes – 4 hours` → `"delayed"`; `> 4 hours` or no `FetchLog` rows at all → `"stopped"`.
- Equity history: the last 50 closed `PaperPosition` rows, oldest first.
- Auth: `BASIC_AUTH_USER` / `BASIC_AUTH_PASS_HASH` env vars, checked with `werkzeug.security.check_password_hash` — no plaintext password stored anywhere.
- No new tables, no background jobs — every dashboard number comes from a live query issued during the HTTP request, on its own DB session.
- A DB query failure on the dashboard route must render a friendly error page (HTTP 200), never a 500, and must never affect the scheduler thread.
- UI copy is in Turkish, matching the rest of this project's user-facing text (README, log summaries' domain vocabulary).

---

## File Structure

```
src/
├── config.py                  (modify)  add get_basic_auth_credentials()
├── dashboard_data.py           (new)     pure query functions for the dashboard
├── web.py                      (new)     Flask app: auth + the "/" route
├── templates/
│   └── dashboard.html          (new)     single-page Jinja2 template, inline CSS
└── main.py                     (modify)  run_forever() serves the app instead of sleeping
tests/
├── test_config.py               (modify)  Basic Auth credential tests
├── test_dashboard_data.py       (new)
├── test_web.py                  (new)
└── test_main.py                 (modify)  run_forever() wiring tests
requirements.txt                 (modify)  add flask
.env.example                     (modify)  add BASIC_AUTH_USER, BASIC_AUTH_PASS_HASH, PORT
README.md                        (modify)  document the dashboard
```

---

### Task 1: Basic Auth config helper

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.get_basic_auth_credentials() -> tuple[str, str]` — returns `(user, pass_hash)`; raises `config.ConfigError` if either `BASIC_AUTH_USER` or `BASIC_AUTH_PASS_HASH` is unset.

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_config.py`:

```python
def test_get_basic_auth_credentials_reads_env_vars(monkeypatch):
    monkeypatch.setenv("BASIC_AUTH_USER", "admin")
    monkeypatch.setenv("BASIC_AUTH_PASS_HASH", "pbkdf2:sha256:600000$abc$def")
    assert config.get_basic_auth_credentials() == ("admin", "pbkdf2:sha256:600000$abc$def")


def test_get_basic_auth_credentials_raises_when_user_unset(monkeypatch):
    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.setenv("BASIC_AUTH_PASS_HASH", "hash")
    with pytest.raises(config.ConfigError):
        config.get_basic_auth_credentials()


def test_get_basic_auth_credentials_raises_when_pass_hash_unset(monkeypatch):
    monkeypatch.setenv("BASIC_AUTH_USER", "admin")
    monkeypatch.delenv("BASIC_AUTH_PASS_HASH", raising=False)
    with pytest.raises(config.ConfigError):
        config.get_basic_auth_credentials()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: FAIL (`AttributeError: module 'src.config' has no attribute 'get_basic_auth_credentials'`)

- [ ] **Step 3: Write minimal implementation**

Add to the end of `src/config.py`:

```python
def get_basic_auth_credentials() -> tuple[str, str]:
    user = os.environ.get("BASIC_AUTH_USER")
    pass_hash = os.environ.get("BASIC_AUTH_PASS_HASH")
    if not user or not pass_hash:
        raise ConfigError("BASIC_AUTH_USER and BASIC_AUTH_PASS_HASH environment variables must be set")
    return user, pass_hash
```

`src/config.py` doesn't currently have `from __future__ import annotations` at all (it's a 15-line file with plain annotations) — leave it as-is, the return type `tuple[str, str]` is fine at module scope since it's only evaluated once at import time and `tuple[str, str]` works as a runtime subscript on Python 3.9.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: PASS (5 tests total)

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add Basic Auth credential config helper"
```

---

### Task 2: Dashboard data queries

**Files:**
- Create: `src/dashboard_data.py`
- Test: `tests/test_dashboard_data.py`

**Interfaces:**
- Consumes: `db.models.FetchLog, PaperPosition, Scenario`; `paper_equity.current_equity`; `timeutil.utc_now`
- Produces:
  - `dashboard_data.SystemHealth(last_activity: datetime | None, status: str)` — `status` is `"healthy" | "delayed" | "stopped"`
  - `dashboard_data.get_system_health(session, now: datetime = None) -> SystemHealth`
  - `dashboard_data.EquitySummary(current: Decimal, history: list[tuple[datetime, Decimal]])`
  - `dashboard_data.get_equity_summary(session) -> EquitySummary`
  - `dashboard_data.equity_sparkline_points(history: list[tuple[datetime, Decimal]], width: int = 200, height: int = 40) -> str | None` — space-separated `"x,y"` pairs for an SVG `<polyline>`; `None` if `history` has fewer than 2 points
  - `dashboard_data.get_open_positions(session) -> list[PaperPosition]` — `status == "open"`, `opened_at` DESC
  - `dashboard_data.get_recent_scenarios(session, limit: int = 10) -> list[Scenario]` — `created_at` DESC

- [ ] **Step 1: Write the failing tests**

`tests/test_dashboard_data.py`:

```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.dashboard_data import (
    equity_sparkline_points,
    get_equity_summary,
    get_open_positions,
    get_recent_scenarios,
    get_system_health,
)
from src.db.models import FetchLog, PaperPosition, Scenario
from src.paper_trading_config import STARTING_EQUITY


def _log(finished_at):
    return FetchLog(
        symbol="BTCUSDT", timeframe="1h", status="success",
        started_at=finished_at, finished_at=finished_at,
    )


def _scenario(id_, symbol="BTCUSDT", created_at=None, status="pending",
              calibrated_confidence=None, confidence_score=Decimal("0.7")):
    created_at = created_at or datetime(2026, 1, 1)
    return Scenario(
        id=id_, symbol=symbol, direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=confidence_score,
        created_at=created_at, expires_at=created_at + timedelta(hours=24),
        status=status, calibrated_confidence=calibrated_confidence,
    )


def _closed_position(scenario_id, symbol, closed_at, equity_after):
    return PaperPosition(
        scenario_id=scenario_id, symbol=symbol, direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=closed_at - timedelta(hours=1), status="closed",
        closed_at=closed_at, exit_price=Decimal("110"), realized_pnl=equity_after - STARTING_EQUITY,
        equity_before=STARTING_EQUITY, equity_after=equity_after,
    )


def _open_position(scenario_id, symbol, opened_at):
    return PaperPosition(
        scenario_id=scenario_id, symbol=symbol, direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=opened_at, status="open",
    )


# --- get_system_health ---

def test_get_system_health_reports_stopped_when_no_runs_exist(db_session):
    health = get_system_health(db_session)
    assert health.last_activity is None
    assert health.status == "stopped"


def test_get_system_health_reports_healthy_for_recent_activity(db_session):
    now = datetime(2026, 1, 1, 12, 0)
    db_session.add(_log(now - timedelta(minutes=10)))
    db_session.commit()
    assert get_system_health(db_session, now=now).status == "healthy"


def test_get_system_health_reports_delayed_between_thresholds(db_session):
    now = datetime(2026, 1, 1, 12, 0)
    db_session.add(_log(now - timedelta(minutes=91)))
    db_session.commit()
    assert get_system_health(db_session, now=now).status == "delayed"


def test_get_system_health_reports_stopped_past_four_hours(db_session):
    now = datetime(2026, 1, 1, 12, 0)
    db_session.add(_log(now - timedelta(hours=4, minutes=1)))
    db_session.commit()
    assert get_system_health(db_session, now=now).status == "stopped"


def test_get_system_health_uses_the_most_recent_run(db_session):
    now = datetime(2026, 1, 1, 12, 0)
    db_session.add(_log(now - timedelta(hours=5)))
    db_session.add(_log(now - timedelta(minutes=5)))
    db_session.commit()
    assert get_system_health(db_session, now=now).status == "healthy"


# --- get_equity_summary ---

def test_get_equity_summary_returns_starting_equity_with_no_history(db_session):
    summary = get_equity_summary(db_session)
    assert summary.current == STARTING_EQUITY
    assert summary.history == []


def test_get_equity_summary_returns_current_equity_and_ordered_history(db_session):
    t0 = datetime(2026, 1, 1)
    db_session.add(_scenario(1, created_at=t0))
    db_session.add(_scenario(2, created_at=t0 + timedelta(hours=1)))
    db_session.commit()
    db_session.add(_closed_position(2, "BTCUSDT", t0 + timedelta(hours=2), Decimal("10200")))
    db_session.add(_closed_position(1, "BTCUSDT", t0 + timedelta(hours=1), Decimal("10100")))
    db_session.commit()

    summary = get_equity_summary(db_session)

    assert summary.current == Decimal("10200")
    assert [equity for _, equity in summary.history] == [Decimal("10100"), Decimal("10200")]


def test_get_equity_summary_caps_history_at_50_points(db_session):
    t0 = datetime(2026, 1, 1)
    for i in range(60):
        scenario_id = i + 1
        db_session.add(_scenario(scenario_id, created_at=t0 + timedelta(hours=i)))
    db_session.commit()
    for i in range(60):
        db_session.add(_closed_position(i + 1, "BTCUSDT", t0 + timedelta(hours=i), STARTING_EQUITY + i))
    db_session.commit()

    summary = get_equity_summary(db_session)

    assert len(summary.history) == 50
    assert summary.history[-1][1] == STARTING_EQUITY + 59


# --- equity_sparkline_points ---

def test_equity_sparkline_points_returns_none_for_fewer_than_two_points():
    assert equity_sparkline_points([]) is None
    assert equity_sparkline_points([(datetime(2026, 1, 1), Decimal("10000"))]) is None


def test_equity_sparkline_points_scales_into_the_given_box():
    history = [
        (datetime(2026, 1, 1), Decimal("100")),
        (datetime(2026, 1, 2), Decimal("200")),
    ]
    assert equity_sparkline_points(history, width=100, height=50) == "0.0,50.0 100.0,0.0"


def test_equity_sparkline_points_handles_flat_history():
    history = [
        (datetime(2026, 1, 1), Decimal("100")),
        (datetime(2026, 1, 2), Decimal("100")),
    ]
    # No spread between low and high: both points sit on the bottom edge.
    assert equity_sparkline_points(history, width=100, height=50) == "0.0,50.0 100.0,50.0"


# --- get_open_positions ---

def test_get_open_positions_returns_only_open_ones_newest_first(db_session):
    db_session.add(_scenario(1, symbol="BTCUSDT"))
    db_session.add(_scenario(2, symbol="ETHUSDT", created_at=datetime(2026, 1, 1, 1)))
    db_session.commit()
    db_session.add(_open_position(1, "BTCUSDT", datetime(2026, 1, 1, 0)))
    db_session.add(_open_position(2, "ETHUSDT", datetime(2026, 1, 1, 2)))
    db_session.add(_closed_position(1, "BTCUSDT", datetime(2026, 1, 1, 3), Decimal("10100")))
    db_session.commit()

    positions = get_open_positions(db_session)

    assert [position.symbol for position in positions] == ["ETHUSDT"]


# --- get_recent_scenarios ---

def test_get_recent_scenarios_returns_newest_first_and_respects_limit(db_session):
    for i in range(3):
        db_session.add(_scenario(i + 1, symbol=f"SYM{i}", created_at=datetime(2026, 1, 1, i)))
    db_session.commit()

    scenarios = get_recent_scenarios(db_session, limit=2)

    assert [scenario.symbol for scenario in scenarios] == ["SYM2", "SYM1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dashboard_data.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.dashboard_data'`)

- [ ] **Step 3: Write minimal implementation**

`src/dashboard_data.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FetchLog, PaperPosition, Scenario
from src.paper_equity import current_equity
from src.timeutil import utc_now

HEALTHY_THRESHOLD = timedelta(minutes=90)
DELAYED_THRESHOLD = timedelta(hours=4)

EQUITY_HISTORY_LIMIT = 50
RECENT_SCENARIOS_LIMIT = 10


@dataclass
class SystemHealth:
    last_activity: datetime | None
    status: str


@dataclass
class EquitySummary:
    current: Decimal
    history: list[tuple[datetime, Decimal]]


def get_system_health(session, now: datetime = None) -> SystemHealth:
    now = now if now is not None else utc_now()
    last_activity = (
        session.query(FetchLog.finished_at)
        .order_by(FetchLog.finished_at.desc())
        .limit(1)
        .scalar()
    )
    if last_activity is None:
        return SystemHealth(last_activity=None, status="stopped")

    age = now - last_activity
    if age < HEALTHY_THRESHOLD:
        status = "healthy"
    elif age < DELAYED_THRESHOLD:
        status = "delayed"
    else:
        status = "stopped"
    return SystemHealth(last_activity=last_activity, status=status)


def get_equity_summary(session) -> EquitySummary:
    rows = (
        session.query(PaperPosition.closed_at, PaperPosition.equity_after)
        .filter(PaperPosition.status == "closed")
        .order_by(PaperPosition.closed_at.asc(), PaperPosition.id.asc())
        .all()
    )
    history = [(closed_at, equity_after) for closed_at, equity_after in rows][-EQUITY_HISTORY_LIMIT:]
    return EquitySummary(current=current_equity(session), history=history)


def equity_sparkline_points(history: list[tuple[datetime, Decimal]], width: int = 200, height: int = 40) -> str | None:
    if len(history) < 2:
        return None

    values = [float(equity) for _, equity in history]
    low, high = min(values), max(values)
    span = high - low or 1.0
    step = width / (len(values) - 1)

    points = []
    for index, value in enumerate(values):
        x = index * step
        y = height - ((value - low) / span) * height
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def get_open_positions(session) -> list[PaperPosition]:
    return (
        session.query(PaperPosition)
        .filter(PaperPosition.status == "open")
        .order_by(PaperPosition.opened_at.desc())
        .all()
    )


def get_recent_scenarios(session, limit: int = RECENT_SCENARIOS_LIMIT) -> list[Scenario]:
    return (
        session.query(Scenario)
        .order_by(Scenario.created_at.desc())
        .limit(limit)
        .all()
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dashboard_data.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dashboard_data.py tests/test_dashboard_data.py
git commit -m "feat: add dashboard query functions"
```

---

### Task 3: Flask app and dashboard template

**Files:**
- Create: `src/web.py`
- Create: `src/templates/dashboard.html`
- Test: `tests/test_web.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: everything produced in Task 2 (`dashboard_data.get_system_health`, `get_equity_summary`, `equity_sparkline_points`, `get_open_positions`, `get_recent_scenarios`)
- Produces: `web.create_app(session_factory, auth_user: str, auth_pass_hash: str) -> Flask` — a Flask app with a single `GET /` route, Basic-Auth-protected

- [ ] **Step 1: Add flask to requirements and install it**

Add to `requirements.txt`:

```
flask>=3.0,<4.0
```

Run: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing tests**

`tests/test_web.py`:

```python
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from src.db.models import PaperPosition, Scenario
from src.web import create_app

AUTH_USER = "admin"
AUTH_PASSWORD = "s3cret"
AUTH_PASS_HASH = generate_password_hash(AUTH_PASSWORD)


def _client(db_session):
    app = create_app(lambda: db_session, AUTH_USER, AUTH_PASS_HASH)
    return app.test_client()


def test_dashboard_requires_auth(db_session):
    response = _client(db_session).get("/")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


def test_dashboard_rejects_wrong_password(db_session):
    response = _client(db_session).get("/", auth=(AUTH_USER, "wrong"))
    assert response.status_code == 401


def test_dashboard_rejects_wrong_username(db_session):
    response = _client(db_session).get("/", auth=("nobody", AUTH_PASSWORD))
    assert response.status_code == 401


def test_dashboard_renders_with_correct_credentials(db_session):
    response = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD))
    assert response.status_code == 200
    assert "Dashboard" in response.get_data(as_text=True)


def test_dashboard_shows_an_open_position(db_session):
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=datetime(2026, 1, 1), expires_at=datetime(2026, 1, 2), status="pending",
    )
    db_session.add(scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
    ))
    db_session.commit()

    response = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD))

    assert "BTCUSDT" in response.get_data(as_text=True)


def test_dashboard_shows_empty_state_with_no_data(db_session):
    response = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD))
    body = response.get_data(as_text=True)
    assert "Açık pozisyon yok" in body
    assert "Henüz senaryo yok" in body


def test_dashboard_renders_error_message_when_a_query_fails(db_session):
    with patch("src.web.get_system_health", side_effect=RuntimeError("db down")):
        response = _client(db_session).get("/", auth=(AUTH_USER, AUTH_PASSWORD))

    assert response.status_code == 200
    assert "okunamıyor" in response.get_data(as_text=True)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_web.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.web'`)

- [ ] **Step 4: Write the template**

`src/templates/dashboard.html`:

```html
<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<title>Kripto Analiz Ofisi — Dashboard</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0d1117; color: #e6edf3; margin: 0; padding: 2rem; }
  h1 { font-size: 1.4rem; margin: 0 0 1.5rem; }
  h2 { font-size: 1rem; margin: 0 0 0.75rem; color: #c9d1d9; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1.25rem; margin-bottom: 1.5rem; }
  .badge { display: inline-block; padding: 0.25rem 0.75rem; border-radius: 999px; font-weight: 600; font-size: 0.85rem; }
  .badge.healthy { background: rgba(63, 185, 80, 0.2); color: #3fb950; }
  .badge.delayed { background: rgba(210, 153, 34, 0.2); color: #d29922; }
  .badge.stopped { background: rgba(248, 81, 73, 0.2); color: #f85149; }
  .equity-value { font-size: 2rem; font-weight: 700; margin: 0.5rem 0; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #30363d; }
  th { color: #8b949e; font-weight: 500; }
  .empty { color: #8b949e; font-style: italic; }
  .error { color: #f85149; }
</style>
</head>
<body>
<h1>Kripto Analiz Ofisi — Dashboard</h1>

{% if error %}
<div class="card error">Veri şu an okunamıyor. Servis bir sonraki denemede otomatik toparlanır.</div>
{% else %}

<div class="card">
  <span class="badge {{ health.status }}">
    {% if health.status == "healthy" %}Çalışıyor
    {%- elif health.status == "delayed" %}Gecikmiş
    {%- else %}Durmuş görünüyor
    {%- endif %}
  </span>
  <p>
    {% if health.last_activity %}
      Son aktivite: {{ health.last_activity.strftime("%Y-%m-%d %H:%M UTC") }}
    {% else %}
      Henüz hiç kayıt yok.
    {% endif %}
  </p>
</div>

<div class="card">
  <h2>Paper Portföy Equity</h2>
  <div class="equity-value">{{ "%.2f"|format(equity.current) }}</div>
  {% if sparkline_points %}
  <svg width="200" height="40" viewBox="0 0 200 40">
    <polyline points="{{ sparkline_points }}" fill="none" stroke="#3fb950" stroke-width="2" />
  </svg>
  {% endif %}
</div>

<div class="card">
  <h2>Açık Pozisyonlar</h2>
  {% if open_positions %}
  <table>
    <tr><th>Sembol</th><th>Yön</th><th>Giriş</th><th>Stop</th><th>Hedef</th><th>Açılış</th></tr>
    {% for position in open_positions %}
    <tr>
      <td>{{ position.symbol }}</td>
      <td>{{ position.direction }}</td>
      <td>{{ position.entry_price }}</td>
      <td>{{ position.stop_price }}</td>
      <td>{{ position.target_price }}</td>
      <td>{{ position.opened_at.strftime("%Y-%m-%d %H:%M") }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p class="empty">Açık pozisyon yok.</p>
  {% endif %}
</div>

<div class="card">
  <h2>Son Senaryolar</h2>
  {% if recent_scenarios %}
  <table>
    <tr><th>Sembol</th><th>Yön</th><th>Confidence</th><th>Durum</th><th>Oluşturulma</th></tr>
    {% for scenario in recent_scenarios %}
    <tr>
      <td>{{ scenario.symbol }}</td>
      <td>{{ scenario.direction }}</td>
      <td>{{ "%.2f"|format(scenario.calibrated_confidence if scenario.calibrated_confidence is not none else scenario.confidence_score) }}</td>
      <td>{{ scenario.status }}</td>
      <td>{{ scenario.created_at.strftime("%Y-%m-%d %H:%M") }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p class="empty">Henüz senaryo yok.</p>
  {% endif %}
</div>

{% endif %}
</body>
</html>
```

- [ ] **Step 5: Write minimal implementation**

`src/web.py`:

```python
from __future__ import annotations

import logging

from flask import Flask, Response, render_template, request
from werkzeug.security import check_password_hash

from src.dashboard_data import (
    equity_sparkline_points,
    get_equity_summary,
    get_open_positions,
    get_recent_scenarios,
    get_system_health,
)

logger = logging.getLogger("web")


def create_app(session_factory, auth_user: str, auth_pass_hash: str) -> Flask:
    app = Flask(__name__)

    def _authorized() -> bool:
        auth = request.authorization
        return (
            auth is not None
            and auth.username == auth_user
            and check_password_hash(auth_pass_hash, auth.password)
        )

    @app.route("/")
    def dashboard():
        if not _authorized():
            return Response(
                "Authentication required", 401,
                {"WWW-Authenticate": 'Basic realm="Dashboard"'},
            )

        session = session_factory()
        try:
            health = get_system_health(session)
            equity = get_equity_summary(session)
            open_positions = get_open_positions(session)
            recent_scenarios = get_recent_scenarios(session)
        except Exception:
            logger.exception("Failed to load dashboard data")
            return render_template("dashboard.html", error=True)
        finally:
            session.close()

        return render_template(
            "dashboard.html",
            error=False,
            health=health,
            equity=equity,
            sparkline_points=equity_sparkline_points(equity.history),
            open_positions=open_positions,
            recent_scenarios=recent_scenarios,
        )

    return app
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_web.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Commit**

```bash
git add src/web.py src/templates/dashboard.html tests/test_web.py requirements.txt
git commit -m "feat: add Basic-Auth-protected dashboard web app"
```

---

### Task 4: Wire the dashboard into the running service

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Consumes: `config.get_basic_auth_credentials` (Task 1), `web.create_app` (Task 3)
- Produces: no new public interface — `run_forever` now serves the dashboard on the main thread instead of sleeping

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_main.py`:

```python
def test_run_forever_starts_scheduler_serves_dashboard_and_shuts_down_on_exit(monkeypatch):
    calls = []

    class _FakeScheduler:
        def start(self):
            calls.append("scheduler.start")

        def shutdown(self):
            calls.append("scheduler.shutdown")

    class _FakeApp:
        def run(self, host, port):
            calls.append(("app.run", host, port))
            raise KeyboardInterrupt()

    monkeypatch.setattr(main_module, "build_scheduler", lambda session_factory, binance_client: _FakeScheduler())
    monkeypatch.setattr(main_module, "get_basic_auth_credentials", lambda: ("admin", "hash"))
    monkeypatch.setattr(
        main_module, "create_app",
        lambda session_factory, auth_user, auth_pass_hash: _FakeApp(),
    )
    monkeypatch.setenv("PORT", "9000")

    main_module.run_forever(session_factory=lambda: None, binance_client=None)

    assert calls == ["scheduler.start", ("app.run", "0.0.0.0", 9000), "scheduler.shutdown"]


def test_run_forever_defaults_to_port_8000_when_unset(monkeypatch):
    calls = []

    class _FakeScheduler:
        def start(self):
            pass

        def shutdown(self):
            pass

    class _FakeApp:
        def run(self, host, port):
            calls.append(("app.run", host, port))
            raise KeyboardInterrupt()

    monkeypatch.setattr(main_module, "build_scheduler", lambda session_factory, binance_client: _FakeScheduler())
    monkeypatch.setattr(main_module, "get_basic_auth_credentials", lambda: ("admin", "hash"))
    monkeypatch.setattr(
        main_module, "create_app",
        lambda session_factory, auth_user, auth_pass_hash: _FakeApp(),
    )
    monkeypatch.delenv("PORT", raising=False)

    main_module.run_forever(session_factory=lambda: None, binance_client=None)

    assert calls == [("app.run", "0.0.0.0", 8000)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_main.py -v -k run_forever`
Expected: FAIL (`AttributeError: module 'src.main' has no attribute 'get_basic_auth_credentials'` or similar — the names don't exist in `main_module` yet)

- [ ] **Step 3: Update `src/main.py`**

Change the import block at the top from:

```python
from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler

from src.backfill import run_initial_backfill
from src.binance_client import BinanceClient
from src.config import get_database_url
from src.db.models import Symbol
from src.db.session import create_all_tables, make_engine, make_session_factory
from src.scheduler import build_scheduler
from src.storage import get_kline_time_bounds
from src.symbol_registry import refresh_symbols
```

to:

```python
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from src.backfill import run_initial_backfill
from src.binance_client import BinanceClient
from src.config import get_basic_auth_credentials, get_database_url
from src.db.models import Symbol
from src.db.session import create_all_tables, make_engine, make_session_factory
from src.scheduler import build_scheduler
from src.storage import get_kline_time_bounds
from src.symbol_registry import refresh_symbols
from src.web import create_app
```

(`time` is dropped — its only use was the `sleep(60)` loop being removed below.)

Replace `run_forever`:

```python
def run_forever(session_factory, binance_client) -> None:
    scheduler = build_scheduler(session_factory, binance_client)
    scheduler.start()
    logger.info("Scheduler started, service running")
    auth_user, auth_pass_hash = get_basic_auth_credentials()
    app = create_app(session_factory, auth_user, auth_pass_hash)
    port = int(os.environ.get("PORT", 8000))
    try:
        app.run(host="0.0.0.0", port=port)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: PASS (all tests in the file, including the 2 new ones)

- [ ] **Step 5: Update `.env.example`**

Replace the file's contents with:

```
DATABASE_URL=postgresql+psycopg2://localhost/crypto_office
BASIC_AUTH_USER=admin
# Generate with: python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-password'))"
BASIC_AUTH_PASS_HASH=pbkdf2:sha256:...
PORT=8000
```

- [ ] **Step 6: Update `README.md`**

Add this section after the existing "Paper Test Portföyü" section and before "## Test":

```markdown
## Dashboard

Servis çalışırken `http://localhost:8000` (Railway'de kendi public domain'inde, `PORT` ortam
değişkeni Railway tarafından otomatik enjekte edilir) üzerinden tek sayfalık bir dashboard
sunulur: son aktivite zamanı/sağlık durumu, paper portföyün güncel equity'si ve kısa geçmişi,
açık pozisyonlar ve en son üretilen senaryolar. `BASIC_AUTH_USER` / `BASIC_AUTH_PASS_HASH`
ortam değişkenleriyle korunur — parola hash'i `python3 -c "from werkzeug.security import
generate_password_hash; print(generate_password_hash('...'))"` ile üretilir, düz metin parola
hiçbir yerde saklanmaz. Dashboard verisi her istekte veritabanından canlı okunur; scheduler'dan
bağımsız ayrı bir DB oturumu kullanır, birbirlerini bloklamazlar.
```

- [ ] **Step 7: Run the full test suite**

Run: `python3 -m pytest -v`
Expected: PASS (all tests across the whole project)

- [ ] **Step 8: Commit**

```bash
git add src/main.py tests/test_main.py .env.example README.md
git commit -m "feat: serve the dashboard from the main service process"
```

---

## Post-Plan Notes

- **Railway deployment:** after this plan lands, set `BASIC_AUTH_USER` and `BASIC_AUTH_PASS_HASH` (and `DATABASE_URL`) as Railway environment variables, then generate a public domain for the service — Railway auto-provisions HTTPS on it, no in-app TLS work needed. `PORT` is injected by Railway automatically; the app already reads it via `os.environ.get("PORT", 8000)`.
- **Manual verification** (not automated — needs a live Postgres + a browser): run `python -m src.main` locally with `BASIC_AUTH_USER`/`BASIC_AUTH_PASS_HASH` set, open `http://localhost:8000`, confirm the browser's Basic Auth prompt appears, wrong credentials are rejected, and correct credentials render the health badge / equity / positions / scenarios sections with real data once the scheduler has run at least once.
- Scope explicitly excludes: a second page/route, live/websocket updates, rate-limiting failed auth attempts, and running the web server independently of the scheduler thread (a crash in one currently takes down the other — acceptable per the design's "Hata Yönetimi" section, since the main thread was already the service's liveness signal before this change).
