"""Run against the 100k dataset (tests/seed_bulk.py): prints plan check + timing for every explain case.
Usage: python tests/check_explain.py        (BASE_URL env var if the app is not on port 8000)"""
import os
import re
import time
import urllib.request

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")
CASES = ["attendance_list?page=1&page_size=100", "attendance_list?page=500&page_size=100",
         "attendance_list?emp_code=EMP0010&date_from=2026-03-01&date_to=2026-03-31",
         "attendance_list?status=PRESENT&date_from=2026-03-01&date_to=2026-03-31",
         "employee_monthly?emp_code=EMP0010&month=2026-03",
         "department_summary?month=2026-03", "department_summary?month=2026-03&department=Sales",
         "late_leaderboard?month=2026-03&limit=10", "late_leaderboard?month=2026-03&department=Sales",
         "department_trend?department=Sales&from=2026-01-05&to=2026-04-05"]

for case in CASES:
    start = time.time()
    with urllib.request.urlopen(f"{BASE}/admin/explain/{case}", timeout=120) as r:
        text = r.read().decode()
    ms = round((time.time() - start) * 1000)
    ok = "IXSCAN" in text and "COLLSCAN" not in text
    docs = re.findall(r'"totalDocsExamined":\s*(\d+)', text)
    print(f"{'OK ' if ok else 'BAD'} {case:<75} {ms:>5} ms  totalDocsExamined={docs}")