"""End-to-end checks for the 4 new endpoints against the dataset of tests/seed_test.py.

1) MONGO_DB=attendance_test python tests/seed_test.py
2) MONGO_DB=attendance_test uvicorn app.main:app --port 8000      (separate terminal)
3) python tests/check_all.py                                      (BASE_URL env var if not port 8000)
"""
import json
import os
import urllib.error
import urllib.request

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")
passed = failed = 0


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = passed + ok, failed + (not ok)
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"\n      {detail}"))


def lb_rows(items):
    return [(i["rank"], i["emp_code"], i["name"], i["department"], i["total_late_minutes"], i["late_count"]) for i in items]


def trend_rows(items):
    return [(i["date"], i["is_working_day"], i["headcount"], i["present_count"], i["late_count"],
             i["attendance_rate"], i["moving_avg_7d"]) for i in items]


def expect_leaderboard(query, want):
    status, body = get("/analytics/leaderboard/late?" + query)
    got = lb_rows(body["items"]) if status == 200 else body
    check(f"leaderboard?{query}", status == 200 and got == want, f"status={status}\n      got ={got}\n      want={want}")


def expect_trend(path, want):
    status, body = get(path)
    got = trend_rows(body["items"]) if status == 200 else body
    check(path, status == 200 and got == want, f"status={status}\n      got ={got}\n      want={want}")


def expect_status(path, code):
    status, body = get(path)
    check(f"{path} -> {code}", status == code, f"got {status}: {body}")


A, B, C, D, E = ((1, "EMP0001", "Asha", "Engineering", 90, 3), (2, "EMP0002", "Bala", "Engineering", 60, 1),
                 (2, "EMP0004", "Dev", "Sales", 60, 2), (4, "EMP0007", "Gita", "Ops", 30, 1),
                 (5, "EMP0003", "Chitra", "Engineering", 11, 1))

TREND_ENG = [
    ("2026-07-10", True, 2, 1.5, 0, 0.75, 0.75),
    ("2026-07-11", False, 2, 1.0, 1, None, 0.75),      # Saturday: record counts, no rate
    ("2026-07-12", False, 2, 0.0, 0, None, 0.75),      # Sunday, no records: row still exists
    ("2026-07-13", True, 2, 0.0, 0, 0.0, 0.375),       # working day with NO records: rate 0.0
    ("2026-07-14", True, 2, 1.0, 0, 0.5, 0.4167),
    ("2026-07-15", True, 3, 3.0, 0, 1.0, 0.5625),      # Chitra joins: headcount 2 -> 3
    ("2026-07-16", True, 3, 2.0, 1, 0.6667, 0.5833),
    ("2026-07-17", True, 3, 0.5, 0, 0.1667, 0.4667),   # window drops 07-10 (0.75) and 07-11..12 are null
    ("2026-07-18", False, 3, 0.0, 0, None, 0.4667),
    ("2026-07-19", False, 3, 0.0, 0, None, 0.4667),
]
TREND_SALES = [
    ("2026-07-31", True, 2, 0.0, 0, 0.0, 0.0),
    ("2026-08-01", False, 3, 0.0, 0, None, 0.0),       # Farid joins on Saturday: headcount 3
    ("2026-08-02", False, 3, 0.0, 0, None, 0.0),
]
SUMMARY = [
    {"department": "Engineering", "headcount": 3, "present_days": 11.0, "avg_work_hours": 7.94, "late_count": 5,
     "total_late_minutes": 161, "leave_count": 1, "on_duty_count": 1},
    {"department": "Ops", "headcount": 1, "present_days": 1.0, "avg_work_hours": 8.17, "late_count": 1,
     "total_late_minutes": 30, "leave_count": 0, "on_duty_count": 0},
    {"department": "Sales", "headcount": 2, "present_days": 1.0, "avg_work_hours": 6.34, "late_count": 2,
     "total_late_minutes": 60, "leave_count": 0, "on_duty_count": 0},
]


def main():
    print("== Leaderboard (ties 1,2,2,4,5; weekend late counted; June / unknown employee ignored)")
    expect_leaderboard("month=2026-07", [A, B, C, D, E])
    expect_leaderboard("month=2026-07&limit=1", [A])
    expect_leaderboard("month=2026-07&limit=2", [A, B, C])          # tie at the cutoff: both rank-2 rows stay
    expect_leaderboard("month=2026-07&limit=3", [A, B, C])          # rank 3 does not exist -> still 3 rows
    expect_leaderboard("month=2026-07&limit=4", [A, B, C, D])
    expect_leaderboard("month=2026-07&department=Engineering", [A, B, (3,) + E[1:]])   # ranked inside the department
    expect_leaderboard("month=2026-07&department=Sales", [(1,) + C[1:]])
    expect_leaderboard("month=2026-07&department=Nope", [])
    expect_leaderboard("month=2026-08", [])
    for bad in ("month=2026-13", "month=2026-07&limit=0", "month=2026-07&limit=51", "limit=5"):
        expect_status("/analytics/leaderboard/late?" + bad, 422)

    print("== Trend (gap fill, weekends, no-record day, joiner, moving average)")
    expect_trend("/analytics/departments/Engineering/trend?from=2026-07-10&to=2026-07-19", TREND_ENG)
    expect_trend("/analytics/departments/Sales/trend?from=2026-07-31&to=2026-08-02", TREND_SALES)
    expect_trend("/analytics/departments/Engineering/trend?from=2026-07-15&to=2026-07-15",
                 [("2026-07-15", True, 3, 3.0, 0, 1.0, 1.0)])
    status, body = get("/analytics/departments/Engineering/trend?from=2026-04-01&to=2026-06-30")   # 91 days, no data
    check("trend 91 days, no records -> 91 rows", status == 200 and len(body["items"]) == 91, f"{status}")
    status, body = get("/analytics/departments/Engineering/trend?from=2026-04-01&to=2026-07-01")   # exactly 92 days
    check("trend exactly 92 days -> 92 rows", status == 200 and len(body["items"]) == 92, f"{status}")
    expect_status("/analytics/departments/Nope/trend?from=2026-07-10&to=2026-07-19", 404)
    expect_status("/analytics/departments/Engineering/trend?from=2026-07-19&to=2026-07-10", 422)
    expect_status("/analytics/departments/Engineering/trend?from=2026-04-01&to=2026-07-02", 422)   # 93 days
    expect_status("/analytics/departments/Engineering/trend?from=2026-02-30&to=2026-03-01", 422)
    expect_status("/analytics/departments/Engineering/trend?from=2026-07-10", 422)

    print("== Department summary (step 9, same dataset)")
    status, body = get("/analytics/departments/summary?month=2026-07")
    check("summary 2026-07", status == 200 and body["items"] == SUMMARY, f"{status}\n      got ={body}\n      want={SUMMARY}")

    print("== Explain: every endpoint must show IXSCAN and no COLLSCAN")
    for path in ("attendance_list?date_from=2026-07-01&date_to=2026-07-31&page=1&page_size=20",
                 "attendance_list", "attendance_list?emp_code=EMP0001", "attendance_list?status=LEAVE",
                 "employee_monthly?emp_code=EMP0001&month=2026-07",
                 "department_summary?month=2026-07", "department_summary?month=2026-07&department=Sales",
                 "late_leaderboard?month=2026-07&limit=5", "late_leaderboard?month=2026-07&department=Sales",
                 "department_trend?department=Engineering&from=2026-07-10&to=2026-07-19"):
        status, body = get("/admin/explain/" + path)
        text = json.dumps(body)
        check(f"explain/{path}", status == 200 and "IXSCAN" in text and "COLLSCAN" not in text,
              f"status={status} IXSCAN={'IXSCAN' in text} COLLSCAN={'COLLSCAN' in text}")
    for bad in ("nonsense", "employee_monthly?month=2026-07", "department_trend?department=Engineering&from=2026-07-10"):
        expect_status("/admin/explain/" + bad, 422)

    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()