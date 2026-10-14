"""Generates ~100,000 attendance records (for the explain / speed check). Wipes the TEST database first.
Usage: MONGO_DB=attendance_bulk python tests/seed_bulk.py     then start the app on that DB and run tests/check_explain.py
"""
import os
import random
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)[os.getenv("MONGO_DB", "attendance_bulk")]
if "bulk" not in db.name and "test" not in db.name:   # safety: never wipe a real database by accident
    raise SystemExit(f"Refusing to wipe '{db.name}': set MONGO_DB to a name containing 'bulk' (e.g. attendance_bulk)")
random.seed(7)
depts = ["Engineering", "Sales", "Ops", "HR", "Finance", "Support"]
db.employees.delete_many({}); db.attendance_logs.delete_many({})
emps = [{"emp_code": f"EMP{i:04d}", "name": f"Emp {i}", "email": f"e{i}@example.com", "department": depts[i % 6],
         "shift_start": "09:30", "shift_end": "18:30", "joined_on": "2026-01-05",
         "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)} for i in range(1, 301)]
db.employees.insert_many(emps)
IST = timezone(timedelta(hours=5, minutes=30))
logs, day = [], date(2026, 1, 5)
while len(logs) < 100_000:
    if day.weekday() < 5:
        for e in emps:
            late = random.choice([0, 0, 0, 12, 25, 45])
            t_in = datetime.combine(day, datetime.min.time(), IST).replace(hour=9, minute=30) + timedelta(minutes=late)
            logs.append({"emp_code": e["emp_code"], "date": day.isoformat(), "status": "PRESENT",
                         "punch_in": t_in.astimezone(timezone.utc), "punch_out": (t_in + timedelta(hours=9)).astimezone(timezone.utc),
                         "work_hours": 9.0, "late_minutes": late, "overtime_minutes": 0, "half_day": False, "history": []})
    day += timedelta(days=1)
db.attendance_logs.insert_many(logs)
print(f"loaded {len(emps)} employees and {len(logs)} logs into '{db.name}' (last day {day})")