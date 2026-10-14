# REVIEW.md

Review of the starter `app/main.py` (helpers and endpoints). Every item below is fixed in the submitted code.

| # | Where | What is wrong | How you'd notice it | How I fixed it |
|---|---|---|---|---|
| 1 | `from_ms`, `instant` | Instants were not truncated to whole seconds (R1). | Punch-in 09:40:00.900 is 600.9 s after shift start, so `late_minutes` = 10 instead of 0. | `from_ms` uses `ms // 1000`, `instant` uses `microsecond=0`; the truncated value is stored and returned. |
| 2 | `round_half_up`, `derived` (work hours) | Float `round()` is not half-up (R4). | 29250 s = 8.125 h: `round(8.125, 2)` gives 8.12, the correct answer is 8.13. | `Decimal(seconds) / 3600`, quantized with `ROUND_HALF_UP`. |
| 3 | `derived` (late) | Grace must be *strictly more than* 10:00, and minutes count from shift start, not from the end of the grace (R2). | 09:40:00 -> 0, 09:40:01 -> 10, 10:15:59 -> 45. | `late_secs > 600`, then `late_secs // 60`. |
| 4 | `derived` (overtime, half day) | Overtime needs floor and a 30-minute minimum; half day uses the *rounded* hours (R3, R5). | 4.496 h rounds to 4.50, so it is not a half day. | Floor minutes, counted only if `>= 30`; `half_day = rounded_hours < 4.5`. |
| 5 | `shift_window` | Overnight shifts (e.g. 22:00-06:00) need the previous-day rule and a next-day shift end (R1). | Punch-in at 05:30 must belong to yesterday's shift. | If `shift_end <= shift_start` and the IST time is before `shift_end`, use the previous day; the end falls on the next day. |
| 6 | `punch_in` | Check-then-insert races under parallel requests. | Two simultaneous punch-ins both return 201. | Unique index `(emp_code, date)`; `DuplicateKeyError` becomes 409. |
| 7 | `punch_out` | It must close the latest record with `punch_in <= punched_at` (also across midnight), exactly once. | Two simultaneous punch-outs both return 200; an overnight punch-out finds no record. | Lookup sorted by `punch_in` desc (index `(emp_code, punch_in)`); conditional update on `punch_out: None`; no match gives 409; the 24 h check gives 422. |
| 8 | `regularize` | Concurrent corrections can overwrite each other's history. | Two parallel PATCH calls: one history entry is lost. | `$push` (never overwrite), guarded by a filter on the old values and on `history.<n>` not existing; the loser gets 409. |
| 9 | `regularize`, `attendance_out` | History must list only fields that really changed, and times inside `changes` are BSON dates. | Raw datetimes in the JSON response, or unchanged fields listed. | `changes` is built from old vs new values; `punch_in`/`punch_out` inside `changes` are converted to epoch ms. |
| 10 | Mongo client | PyMongo returns naive datetimes, and `timestamp()` then assumes local time. | On a server in IST every epoch value is off by 5.5 h. | `MongoClient(tz_aware=True)`. |
| 11 | Pydantic models | Seconds, floats or strings accepted as epoch ms; a date like 2026-02-30 passes a pattern check. | `punched_at: 1783312500` (seconds) is accepted. | `EpochMs` is a strict int in a valid range; `DateStr` also validates the real calendar date. |
| 12 | `RegularizeIn` | `extra="forbid"` rejects client-sent derived fields with 422, but the contract says to ignore them. | PATCH with `work_hours` in the body returns 422. | `extra="ignore"`. |
| 13 | Analytics | Recomputing from punch times, counting weekend records as present days, or starting from logs (loses employees with no logs). | A Saturday record raises `present_days`; an employee with no logs is missing from the headcount. | Stored values are read; `present_days` counts Mon-Fri only; department pipelines start from `employees` (R7, R9). |
| 14 | Indexes | Only the default `_id` index exists, so list and analytics queries scan the collection. | `/attendance` is slow at 100k records; `COLLSCAN` in explain. | All indexes are created at startup in `lifespan`, idempotently (see `DECISIONS.md`). |

## Examined and judged fine

- `insert_one(doc)` adds `_id` to `doc`, but `attendance_out` and `employee_out` return only whitelisted fields, so `_id` never leaks.
- ISO dates and zero-padded `HH:MM` strings compare correctly as strings (`$gte`/`$lte`, overnight check).
- `create_index` on every start is idempotent.
- `Decimal(str(x))` in `round_half_up` is safe: it quantizes the shortest decimal form of the float.
- `skip`/`limit` paging is fine at 100k records; very deep pages cost O(skip) (see `DECISIONS.md`, point 5).

## Verification

My own checks (`tests/check_all.py`, 37 cases) pass, and `tests/check_explain.py` shows `IXSCAN` and no
`COLLSCAN` for all five explain endpoints on about 100,000 records.