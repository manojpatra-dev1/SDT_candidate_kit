"""Loads a small hand-made dataset with the awkward cases (ties, weekend record, mid-month joiner,
employee with no logs, future joiner, overnight shift, unknown employee, other month) into a TEST database.

Usage:   MONGO_DB=attendance_test python tests/seed_test.py        (Windows PowerShell: $env:MONGO_DB="attendance_test")
It DELETES everything in 'employees' and 'attendance_logs' of that database - never point it at real data.
"""
import os
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "09:30", "18:30"
NIGHT = "22:00", "06:00"


def employee(code, name, dept, joined, shift=DAY):
    return {"emp_code": code, "name": name, "email": f"{name.lower()}@example.com", "department": dept,
            "shift_start": shift[0], "shift_end": shift[1], "joined_on": joined,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}


def ist(day, hm, plus_days=0):
    d = datetime.fromisoformat(f"{day}T{hm}:00").replace(tzinfo=IST)
    return (d + timedelta(days=plus_days)).astimezone(timezone.utc)


def log(code, day, pin=None, pout=None, status="PRESENT", shift=DAY, out_next_day=False):
    """Builds a stored record; the derived fields are computed here with the rules R2-R5 (independent of app code)."""
    doc = {"emp_code": code, "date": day, "status": status, "punch_in": None, "punch_out": None,
           "work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": []}
    if pin is None:
        return doc
    t_in = ist(day, pin)
    late_s = (t_in - ist(day, shift[0])).total_seconds()
    doc.update(punch_in=t_in, late_minutes=int(late_s // 60) if late_s > 600 else 0)
    if pout:
        t_out = ist(day, pout, 1 if out_next_day else 0)
        secs = int((t_out - t_in).total_seconds())
        hours = round((secs * 100 + 1800) // 3600) / 100          # half-up to 2 decimals using integers
        end = ist(day, shift[1], 1 if shift[1] <= shift[0] else 0)
        over = int((t_out - end).total_seconds() // 60)
        doc.update(punch_out=t_out, work_hours=hours, half_day=hours < 4.5, overtime_minutes=over if over >= 30 else 0)
    return doc


EMPLOYEES = [
    employee("EMP0001", "Asha", "Engineering", "2026-01-05"),
    employee("EMP0002", "Bala", "Engineering", "2026-01-05"),
    employee("EMP0003", "Chitra", "Engineering", "2026-07-15"),   # mid-month joiner
    employee("EMP0004", "Dev", "Sales", "2026-01-05"),
    employee("EMP0005", "Esha", "Sales", "2026-01-05"),          # no logs at all
    employee("EMP0006", "Farid", "Sales", "2026-08-01"),         # joins after July
    employee("EMP0007", "Gita", "Ops", "2026-01-05", NIGHT),     # overnight shift 22:00-06:00
]

LOGS = [
    # --- leaderboard: totals 90 / 60 / 60 / 30 / 11  => ranks 1, 2, 2, 4, 5
    log("EMP0001", "2026-07-01", "10:10", "18:30"),                                  # late 40
    log("EMP0001", "2026-07-02", "10:00", "18:35"),                                  # late 30
    log("EMP0002", "2026-07-01", "10:30", "18:30"),                                  # late 60
    log("EMP0004", "2026-07-03", "09:50", "18:30"),                                  # late 20 (Fri)
    log("EMP0004", "2026-07-04", "10:10", "14:10"),                                  # late 40 on a SATURDAY, half day
    log("EMP0007", "2026-07-01", "22:30", "06:40", shift=NIGHT, out_next_day=True),  # late 30, overnight, overtime 40
    log("EMP0003", "2026-07-16", "09:41", "18:30"),                                  # late 11
    log("EMP0003", "2026-07-15", "09:40", "18:30"),                                  # exactly 10:00 -> NOT late
    log("EMP9999", "2026-07-01", "10:20", "18:30"),                                  # unknown employee: ignored
    log("EMP0001", "2026-06-30", "11:09", "18:30"),                                  # other month: ignored (late 99)
    # --- Engineering trend 2026-07-10 .. 2026-07-19
    log("EMP0001", "2026-07-10", "09:30", "18:30"),
    log("EMP0002", "2026-07-10", "09:30", "13:00", status="WFH"),                    # half day (3.5 h) -> 0.5
    log("EMP0001", "2026-07-11", "09:50", "18:00"),                                  # Saturday record, late 20
    log("EMP0001", "2026-07-14", "09:30", "18:30"),
    log("EMP0002", "2026-07-14", status="ABSENT"),
    log("EMP0001", "2026-07-15", "09:30", "18:30", status="ON_DUTY"),
    log("EMP0002", "2026-07-15", "09:30", "18:30"),
    log("EMP0001", "2026-07-16", "09:30", "18:30"),
    log("EMP0002", "2026-07-16", status="LEAVE"),
    log("EMP0003", "2026-07-17", "09:30", "13:30"),                                  # half day (4.0 h)
]

if __name__ == "__main__":
    from dotenv import load_dotenv
    from pymongo import MongoClient
    load_dotenv()
    db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)[os.getenv("MONGO_DB", "attendance_test")]
    if "test" not in db.name:   # safety: never wipe a real database by accident
        raise SystemExit(f"Refusing to wipe '{db.name}': set MONGO_DB to a name containing 'test' (e.g. attendance_test)")
    for name, docs in (("employees", EMPLOYEES), ("attendance_logs", LOGS)):
        db[name].delete_many({})
        db[name].insert_many([dict(d) for d in docs])
        print(f"loaded {len(docs):>2} documents into '{db.name}.{name}'")