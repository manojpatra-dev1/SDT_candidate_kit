"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import json
import calendar
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Optional

from bson import json_util      
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True, serverSelectionTimeoutMS=3000)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

INDEXES = {
        "employees": [
        ([("emp_code", 1)], {"unique": True}),
        ([("department", 1), ("emp_code", 1)], {}),
        ([("joined_on", 1)], {}),
        ([("department", 1), ("joined_on", 1)], {}),
    ],
    "attendance_logs": [
        ([("emp_code", 1), ("date", 1)], {"unique": True}),
        ([("date", -1), ("emp_code", 1)], {}),
        ([("status", 1), ("date", -1), ("emp_code", 1)], {}),
        ([("emp_code", 1), ("punch_in", -1)], {}),
    ],
}


@asynccontextmanager
async def lifespan(_):
    for collection, specs in INDEXES.items():
        for keys, options in specs:
            db[collection].create_index(keys, **options)
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
GRACE_SECONDS, MIN_OVERTIME, HALF_DAY_HOURS = 600, 30, 4.5
PRESENT_STATUSES = ["PRESENT", "WFH", "ON_DUTY"]

def from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc)


def to_ms(dt: datetime) -> int:
    return round(dt.timestamp() * 1000)

def instant(ms: int | None) -> datetime:
    return from_ms(ms) if ms is not None else datetime.now(timezone.utc).replace(microsecond=0)


def month_bounds(month: str) -> tuple[date, date]:
    first = date.fromisoformat(f"{month}-01")
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]

def round_half_up(x, places: int = 2) -> float:
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP))


def shift_window(punch_in: datetime, shift_start: str, shift_end: str):
    local, start_t, end_t = punch_in.astimezone(IST), time.fromisoformat(shift_start), time.fromisoformat(shift_end)
    overnight, day = shift_end <= shift_start, local.date()
    if overnight and local.time() < end_t:
        day -= timedelta(days=1)
    end_day = day + timedelta(days=int(overnight))
    return day.isoformat(), datetime.combine(day, start_t, IST), datetime.combine(end_day, end_t, IST)


def derived(emp: dict, punch_in: datetime, punch_out: datetime | None = None) -> dict:
    date, start, end = shift_window(punch_in, emp["shift_start"], emp["shift_end"])
    late_secs = (punch_in - start).total_seconds()
    out = {"date": date, "late_minutes": int(late_secs // 60) if late_secs > GRACE_SECONDS else 0,
           "work_hours": None, "overtime_minutes": 0, "half_day": False}
    if punch_out:
        hours = round_half_up(Decimal(int((punch_out - punch_in).total_seconds())) / 3600)
        overtime = int((punch_out - end).total_seconds() // 60)
        out.update(work_hours=hours, half_day=hours < HALF_DAY_HOURS,
                   overtime_minutes=overtime if overtime >= MIN_OVERTIME else 0)
    return out


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
def _valid_date(value: str) -> str:
    date.fromisoformat(value)
    return value

def _valid_month(value: str) -> str:
    date.fromisoformat(f"{value}-01")
    return value

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Department = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


EmpCode = Annotated[str, StringConstraints(pattern=r"^EMP\d{4,6}$")]
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
HHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
DateStr = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"), AfterValidator(_valid_date)]
Month = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), AfterValidator(_valid_month)]
EpochMs = Annotated[int, Field(strict=True, ge=10**11, le=4_102_444_800_000)]
Status = Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
PresenceStatus = Literal["PRESENT", "WFH", "ON_DUTY"]


class EmployeeIn(BaseModel):
    emp_code: EmpCode
    name: Name
    email: Email
    department: Department
    shift_start: HHMM = "09:30"
    shift_end: HHMM = "18:30"
    joined_on: DateStr

    @model_validator(mode="after")
    def shifts_differ(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMs] = None
    status: PresenceStatus = "PRESENT"

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=200)]
RegularizedBy = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[Status] = None
    punch_in: Optional[EpochMs] = None
    punch_out: Optional[EpochMs] = None
    reason: Reason
    regularized_by: RegularizedBy    

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMs] = None    


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
def employee_out(doc: dict) -> dict:
    return {**{k: v for k, v in doc.items() if k != "_id"}, "created_at": to_ms(doc["created_at"])}


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(503, "MongoDB unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = {**body.model_dump(), "created_at": datetime.now(timezone.utc).replace(microsecond=0)}
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return employee_out(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {} if department is None else {"department": department}
    cursor = db.employees.find(q).sort("emp_code", 1).skip((page - 1) * page_size).limit(page_size)
    return {"items": [employee_out(d) for d in cursor], "total": db.employees.count_documents(q),
            "page": page, "page_size": page_size}


ATTENDANCE_SORT = [("date", -1), ("emp_code", 1)]


def attendance_out(d: dict) -> dict:
    ms = lambda v: None if v is None else to_ms(v)
    changes = lambda h: {k: {f: ms(v) if k in ("punch_in", "punch_out") else v for f, v in c.items()}
                         for k, c in h["changes"].items()}
    return {"emp_code": d["emp_code"], "date": d["date"], "status": d["status"],
            "punch_in": ms(d.get("punch_in")), "punch_out": ms(d.get("punch_out")),
            "work_hours": d.get("work_hours"), "late_minutes": d.get("late_minutes", 0),
            "overtime_minutes": d.get("overtime_minutes", 0), "half_day": d.get("half_day", False),
            "history": [{"at": to_ms(h["at"]), "by": h["by"], "reason": h["reason"], "changes": changes(h)}
                        for h in d.get("history", [])]}


def attendance_filter(emp_code, date_from, date_to, status) -> dict:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be <= date_to")
    date_range = {k: v for k, v in (("$gte", date_from), ("$lte", date_to)) if v}
    return {k: v for k, v in (("emp_code", emp_code), ("status", status), ("date", date_range)) if v}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    at = instant(body.punched_at)
    doc = {"emp_code": body.emp_code, "status": body.status, "punch_in": at, "punch_out": None,
           **derived(emp, at), "history": []}
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return attendance_out(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    out = instant(body.punched_at)
    record = emp and db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": out}}, sort=[("punch_in", -1)])
    if not record:
        raise HTTPException(404, "employee or punch-in not found")
    if not 0 < (out - record["punch_in"]).total_seconds() <= 24 * 3600:
        raise HTTPException(422, "punched_at must be after punch_in and within 24 hours")
    d = derived(emp, record["punch_in"], out)
    updated = db.attendance_logs.find_one_and_update(
        {"emp_code": body.emp_code, "date": record["date"], "punch_out": None},
        {"$set": {"punch_out": out, **{k: d[k] for k in ("work_hours", "overtime_minutes", "half_day")}}},
        return_document=ReturnDocument.AFTER)
    if not updated:
        raise HTTPException(409, "already punched out")
    return attendance_out(updated)

FIELDS = ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")
NO_WORK = {"work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: DateStr, body: RegularizeIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    record = emp and db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "employee or record not found")
    status = body.status or record["status"]
    away = status in ("ABSENT", "LEAVE")
    if away and (body.punch_in is not None or body.punch_out is not None):
        raise HTTPException(422, "ABSENT/LEAVE records cannot have punch times")
    punch_in = None if away else from_ms(body.punch_in) if body.punch_in is not None else record.get("punch_in")
    punch_out = None if away else from_ms(body.punch_out) if body.punch_out is not None else record.get("punch_out")
    if not away:
        if punch_in is None:
            raise HTTPException(422, "a presence status needs a punch_in")
        if punch_out and not 0 < (punch_out - punch_in).total_seconds() <= 24 * 3600:
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
        if body.punch_in is not None and derived(emp, punch_in)["date"] != date:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")
    new = {"status": status, "punch_in": punch_in, "punch_out": punch_out,
           **(NO_WORK if away else {k: v for k, v in derived(emp, punch_in, punch_out).items() if k != "date"})}
    old = {"punch_in": None, "punch_out": None, **NO_WORK, **{k: record[k] for k in FIELDS if k in record}}
    changes = {k: {"from": old[k], "to": new[k]} for k in FIELDS if new[k] != old[k]}
    if not {"status", "punch_in", "punch_out"} & changes.keys():
        raise HTTPException(422, "request changes nothing")
    entry = {"at": datetime.now(timezone.utc).replace(microsecond=0), "by": body.regularized_by,
             "reason": body.reason, "changes": changes}
    seen = len(record.get("history", []))
    updated = db.attendance_logs.find_one_and_update(
        {"emp_code": emp_code, "date": date, "status": old["status"], "punch_in": old["punch_in"],
         "punch_out": old["punch_out"], f"history.{seen}": {"$exists": False}},
        {"$set": new, "$push": {"history": entry}}, return_document=ReturnDocument.AFTER)
    if not updated:
        raise HTTPException(409, "record was modified concurrently, retry")
    return attendance_out(updated)

def monthly_pipeline(emp_code: str, first: date, last: date) -> list:
    weekdays = [d.isoformat() for d in days_between(first, last) if d.weekday() < 5]
    late = {"$ifNull": ["$late_minutes", 0]}
    count = lambda cond: {"$sum": {"$cond": [cond, 1, 0]}}
    present = {"$and": [{"$in": ["$status", PRESENT_STATUSES]}, {"$in": ["$date", weekdays]}]}
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [present, {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}},
            "leave_days": count({"$eq": ["$status", "LEAVE"]}),
            "late_count": count({"$gt": [late, 0]}),
            "total_late_minutes": {"$sum": late},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        }},
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: Month):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    first, last = month_bounds(month)
    joined = date.fromisoformat(emp["joined_on"])
    working_days = sum(d.weekday() < 5 for d in days_between(max(first, joined), last))
    stats = next(db.attendance_logs.aggregate(monthly_pipeline(emp_code, first, last)), {})
    present = Decimal(str(stats.get("present_days", 0)))
    return {"emp_code": emp_code, "month": month, "working_days": working_days,
            "present_days": round_half_up(present),
            "leave_days": stats.get("leave_days", 0), "late_count": stats.get("late_count", 0),
            "total_late_minutes": stats.get("total_late_minutes", 0),
            "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
            "attendance_pct": round_half_up(present * 100 / working_days) if working_days else None}


def department_pipeline(first: date, last: date, department: str | None) -> list:
    weekdays = [d.isoformat() for d in days_between(first, last) if d.weekday() < 5]
    late = {"$ifNull": ["$late_minutes", 0]}
    count = lambda cond: {"$sum": {"$cond": [cond, 1, 0]}}
    is_present = {"$in": ["$status", PRESENT_STATUSES]}
    timed = {"$and": [is_present, {"$isNumber": "$work_hours"}]}
    per_employee = [
        {"$match": {"date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [{"$and": [is_present, {"$in": ["$date", weekdays]}]},
                                                {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}},
            "work_cents": {"$sum": {"$cond": [timed, {"$round": [{"$multiply": ["$work_hours", 100]}, 0]}, 0]}},
            "work_n": count(timed),
            "late_count": count({"$gt": [late, 0]}),
            "total_late_minutes": {"$sum": late},
            "leave_count": count({"$eq": ["$status", "LEAVE"]}),
            "on_duty_count": count({"$eq": ["$status", "ON_DUTY"]}),
        }},
    ]
    employees = {"joined_on": {"$lte": last.isoformat()}, **({} if department is None else {"department": department})}
    return [
        {"$match": employees},
        {"$lookup": {"from": "attendance_logs", "localField": "emp_code", "foreignField": "emp_code",
                     "pipeline": per_employee, "as": "s"}},
        {"$set": {"s": {"$arrayElemAt": ["$s", 0]}}},
        {"$group": {
            "_id": "$department", "headcount": {"$sum": 1},
            **{k: {"$sum": f"$s.{k}"} for k in ("present_days", "work_cents", "work_n", "late_count",
                                               "total_late_minutes", "leave_count", "on_duty_count")},
        }},
        {"$sort": {"_id": 1}},
    ]


@app.get("/analytics/departments/summary")
def department_summary(month: Month, department: Optional[str] = None):
    first, last = month_bounds(month)
    items = []
    for r in db.employees.aggregate(department_pipeline(first, last, department)):
        avg = round_half_up(Decimal(r["work_cents"]) / r["work_n"] / 100) if r["work_n"] else None
        items.append({"department": r["_id"], "headcount": r["headcount"],
                      "present_days": round_half_up(Decimal(str(r["present_days"]))), "avg_work_hours": avg,
                      "late_count": r["late_count"], "total_late_minutes": r["total_late_minutes"],
                      "leave_count": r["leave_count"], "on_duty_count": r["on_duty_count"]})
    return {"month": month, "items": items}


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[DateStr] = None,
    date_to: Optional[DateStr] = None,
    status: Optional[Status] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = attendance_filter(emp_code, date_from, date_to, status)
    cursor = db.attendance_logs.find(q).sort(ATTENDANCE_SORT).skip((page - 1) * page_size).limit(page_size)
    return {"items": [attendance_out(d) for d in cursor], "total": db.attendance_logs.count_documents(q),
            "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# Late leaderboard  (R2, R8 - standard competition ranking: 1, 2, 2, 4)
# --------------------------------------------------------------------------- #
def leaderboard_pipeline(first: date, last: date, limit: int, department: str | None) -> list:
    late = {"$ifNull": ["$late_minutes", 0]}
    pipeline = [
        {"$match": {"date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}},
        {"$group": {"_id": "$emp_code",
                    "total_late_minutes": {"$sum": late},
                    "late_count": {"$sum": {"$cond": [{"$gt": [late, 0]}, 1, 0]}}}},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        # logs of employees that do not exist are dropped by the $unwind
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code",
                     "pipeline": [{"$project": {"_id": 0, "name": 1, "department": 1}}], "as": "emp"}},
        {"$unwind": "$emp"},
    ]
    if department is not None:  # filter BEFORE ranking, so ranks are inside the department
        pipeline.append({"$match": {"emp.department": department}})
    pipeline += [
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},  # limit AFTER ranking: ties at the cutoff stay
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$emp.name",
                      "department": "$emp.department", "total_late_minutes": 1, "late_count": 1}},
    ]
    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: Month, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    first, last = month_bounds(month)
    items = list(db.attendance_logs.aggregate(leaderboard_pipeline(first, last, limit, department)))
    return {"month": month, "items": items}


# --------------------------------------------------------------------------- #
# Department trend  ($densify gap fill + $setWindowFields moving average)
# --------------------------------------------------------------------------- #
MAX_TREND_DAYS = 92


def trend_range(date_from: str, date_to: str) -> tuple[date, date]:
    first, last = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if last < first:
        raise HTTPException(422, "'to' must be >= 'from'")
    if (last - first).days + 1 > MAX_TREND_DAYS:
        raise HTTPException(422, f"range must not exceed {MAX_TREND_DAYS} days")
    return first, last


def trend_pipeline(department: str, first: date, last: date) -> list:
    f, t = first.isoformat(), last.isoformat()
    lo = datetime.combine(first, time.min, timezone.utc)
    hi = datetime.combine(last + timedelta(days=1), time.min, timezone.utc)  # $densify upper bound is exclusive
    present_w = {"$cond": [{"$in": ["$$l.status", PRESENT_STATUSES]},
                           {"$cond": [{"$ifNull": ["$$l.half_day", False]}, 0.5, 1]}, 0]}
    late_w = {"$cond": [{"$gt": [{"$ifNull": ["$$l.late_minutes", 0]}, 0]}, 1, 0]}
    last_6_and_me = {"documents": [-6, 0]}
    return [
        {"$match": {"department": department}},
        {"$lookup": {"from": "attendance_logs", "localField": "emp_code", "foreignField": "emp_code",
                     "pipeline": [{"$match": {"date": {"$gte": f, "$lte": t}}},
                                  {"$project": {"_id": 0, "date": 1, "status": 1, "half_day": 1, "late_minutes": 1}}],
                     "as": "logs"}},
        # one "joined" row per employee (clamped into the range) + one row per log
        {"$project": {"_id": 0, "rows": {"$concatArrays": [
            [{"d": {"$min": [{"$max": ["$joined_on", f]}, t]},
              "joined": {"$cond": [{"$lte": ["$joined_on", t]}, 1, 0]}, "present": 0, "late": 0}],
            {"$map": {"input": "$logs", "as": "l",
                      "in": {"d": "$$l.date", "joined": 0, "present": present_w, "late": late_w}}},
        ]}}},
        {"$unwind": "$rows"},
        {"$replaceRoot": {"newRoot": "$rows"}},
        {"$group": {"_id": "$d", "joined": {"$sum": "$joined"}, "present": {"$sum": "$present"},
                    "late": {"$sum": "$late"}}},
        {"$set": {"d": {"$dateFromString": {"dateString": "$_id", "format": "%Y-%m-%d"}}}},
        {"$sort": {"d": 1}},
        {"$densify": {"field": "d", "range": {"step": 1, "unit": "day", "bounds": [lo, hi]}}},
        {"$match": {"d": {"$gte": lo, "$lt": hi}}},
        {"$set": {"joined": {"$ifNull": ["$joined", 0]}, "present": {"$ifNull": ["$present", 0]},
                  "late": {"$ifNull": ["$late", 0]}}},
        {"$setWindowFields": {"sortBy": {"d": 1}, "output": {
            "headcount": {"$sum": "$joined", "window": {"documents": ["unbounded", "current"]}}}}},
        {"$set": {"working": {"$lte": [{"$isoDayOfWeek": "$d"}, 5]}}},
        # rate in 1/10000 units, rounded HALF-UP with exact integer maths (Mongo's $round is banker's rounding)
        {"$set": {"rate_i": {"$cond": [
            {"$and": ["$working", {"$gt": ["$headcount", 0]}]},
            {"$floor": {"$divide": [{"$add": [{"$multiply": [{"$multiply": ["$present", 2]}, 10000]}, "$headcount"]},
                                    {"$multiply": [2, "$headcount"]}]}},
            None]}}},
        {"$set": {"rate_n": {"$cond": [{"$isNumber": "$rate_i"}, 1, 0]}}},
        {"$setWindowFields": {"sortBy": {"d": 1}, "output": {
            "rs": {"$sum": "$rate_i", "window": last_6_and_me},
            "rn": {"$sum": "$rate_n", "window": last_6_and_me}}}},
        {"$project": {
            "_id": 0,
            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$d"}},
            "is_working_day": "$working", "headcount": 1, "present_count": "$present", "late_count": "$late",
            "attendance_rate": {"$cond": [{"$isNumber": "$rate_i"}, {"$divide": ["$rate_i", 10000]}, None]},
            "moving_avg_7d": {"$cond": [{"$gt": ["$rn", 0]},
                                        {"$divide": [{"$floor": {"$divide": [{"$add": [{"$multiply": ["$rs", 2]}, "$rn"]},
                                                                             {"$multiply": [2, "$rn"]}]}}, 10000]},
                                        None]}}},
        {"$sort": {"date": 1}},
    ]


def trend_row(r: dict) -> dict:
    num = lambda v: None if v is None else float(v)
    return {"date": r["date"], "is_working_day": bool(r["is_working_day"]), "headcount": int(r["headcount"]),
            "present_count": float(r["present_count"]), "late_count": int(r["late_count"]),
            "attendance_rate": num(r["attendance_rate"]), "moving_avg_7d": num(r["moving_avg_7d"])}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_: Annotated[DateStr, Query(alias="from")], to: DateStr):
    first, last = trend_range(from_, to)
    if db.employees.find_one({"department": department}, {"_id": 1}) is None:
        raise HTTPException(404, "department not found")
    rows = db.employees.aggregate(trend_pipeline(department, first, last))
    return {"department": department, "items": [trend_row(r) for r in rows]}




# --------------------------------------------------------------------------- #
# Explain  (same builders as the real endpoints -> the plan you see is the plan they run)
# --------------------------------------------------------------------------- #
ExplainTarget = Literal["attendance_list", "employee_monthly", "department_summary",
                        "late_leaderboard", "department_trend"]


def require(**params):
    missing = [name for name, value in params.items() if value is None]
    if missing:
        raise HTTPException(422, f"missing query parameter(s): {', '.join(missing)}")


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: ExplainTarget,
    emp_code: Optional[str] = None,
    month: Optional[Month] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[DateStr] = None,
    date_to: Optional[DateStr] = None,
    status: Optional[Status] = None,
    from_: Annotated[Optional[DateStr], Query(alias="from")] = None,
    to: Optional[DateStr] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if endpoint == "attendance_list":
        collection = "attendance_logs"
        body = {"find": collection, "filter": attendance_filter(emp_code, date_from, date_to, status),
                "sort": dict(ATTENDANCE_SORT), "skip": (page - 1) * page_size, "limit": page_size}
    else:
        if endpoint == "employee_monthly":
            require(emp_code=emp_code, month=month)
            collection, pipeline = "attendance_logs", monthly_pipeline(emp_code, *month_bounds(month))
        elif endpoint == "department_summary":
            require(month=month)
            collection, pipeline = "employees", department_pipeline(*month_bounds(month), department)
        elif endpoint == "late_leaderboard":
            require(month=month)
            collection, pipeline = "attendance_logs", leaderboard_pipeline(*month_bounds(month), limit, department)
        else:  # department_trend
            require(department=department, **{"from": from_, "to": to})
            collection, pipeline = "employees", trend_pipeline(department, *trend_range(from_, to))
        body = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}
    try:
        raw = db.command({"explain": body, "verbosity": "executionStats"})
    except PyMongoError as exc:
        raise HTTPException(503, f"MongoDB error: {exc}")
    # explain output holds BSON types (ObjectId, Int64, datetime...): convert to plain JSON
    explain = json.loads(json_util.dumps(raw, json_options=json_util.RELAXED_JSON_OPTIONS))
    return {"endpoint": endpoint, "collection": collection, "explain": explain}