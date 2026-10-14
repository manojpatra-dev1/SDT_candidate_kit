# Employee Attendance & Analytics API

FastAPI + MongoDB service for employee punch-in/punch-out, manual corrections with an audit trail,
and analytics built on MongoDB aggregation pipelines.

## Requirements

- Python 3.11+
- MongoDB 6.0+ (local Docker or Atlas free tier). The pipelines use `$setWindowFields` and `$densify`.

## Run

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then set MONGO_URI and MONGO_DB
uvicorn app.main:app --port 8000
```

`MONGO_URI` and `MONGO_DB` are read from environment variables (real environment variables win over `.env`).
All indexes are created at startup and the call is idempotent. Open `http://localhost:8000/docs` for the interactive API.

## Status

Every endpoint of the contract is implemented. Nothing is missing.

| Area | Endpoints |
|---|---|
| System | `GET /health` |
| Employees | `POST /employees`, `GET /employees` |
| Attendance | `POST /attendance/punch-in`, `POST /attendance/punch-out`, `GET /attendance`, `PATCH /attendance/{emp_code}/{date}` |
| Analytics | `GET /analytics/employees/{emp_code}/monthly`, `GET /analytics/departments/summary`, `GET /analytics/leaderboard/late`, `GET /analytics/departments/{department}/trend` |
| Admin | `GET /admin/explain/{endpoint}` |

## Design notes

- Analytics are aggregation pipelines and read the **stored** derived values (`work_hours`, `late_minutes`, ...).
- Punch-in, punch-out and regularize are race-safe: a unique index on `(emp_code, date)`, conditional updates,
  and an optimistic check on `history` (the loser gets 409).
- The late leaderboard uses `$rank` (ties give 1, 2, 2, 4) and applies `limit` after ranking.
- The department trend fills missing days with `$densify` and computes the moving average with `$setWindowFields`.
  The range is limited to 92 calendar days (inclusive).
- `attendance_rate` and `moving_avg_7d` are rounded half-up to 4 decimals using integer arithmetic, because
  MongoDB's `$round` rounds half to even. The moving average is taken over the already-rounded daily rates.
- `/admin/explain/{endpoint}` builds the query with the same functions as the real endpoint.

## Tests

`tests/` holds my own test data and checks (they need a running app and a **separate test database**):

```bash
MONGO_DB=attendance_test python tests/seed_test.py        # small dataset with the awkward cases
MONGO_DB=attendance_test uvicorn app.main:app --port 8000
python tests/check_all.py                                 # 37 checks: ties, gap fill, joiners, errors, explain

MONGO_DB=attendance_bulk python tests/seed_bulk.py        # ~100,000 records
MONGO_DB=attendance_bulk uvicorn app.main:app --port 8000
python tests/check_explain.py                             # every explain plan: IXSCAN, no COLLSCAN
```

On Windows cmd use `set MONGO_DB=attendance_test` instead of the `MONGO_DB=...` prefix.
The seed scripts refuse to run on a database whose name does not contain `test` or `bulk`.

See `REVIEW.md` for the code review of the starter and `DECISIONS.md` for the design answers.