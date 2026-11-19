# Clinic attendance

Pulls punch data from the biometric machines across the clinics (via whatever
cloud service they report into), stores it, computes attendance against shift
rules, and serves a dashboard.

Self-contained: it shares no code, config or database with the `sanai` package
beside it. Runtime dependencies are the standard library, Jinja2, uvicorn and
`cryptography`.

```
machines -> vendor cloud -> [ source adapter ] -> punch -> [ rules ] -> day -> dashboard
                                   ^                                     ^
                            API or CSV export                   rebuildable cache
```

## Quick start

```bash
pip install -e .
export ATT_DB_PATH=/var/lib/attendance/attendance.db
export ATT_SECRET_KEY="$(attendance keygen)"     # keep a copy of this
export ATT_WEB_PASSWORD='a long random string'

attendance initdb
attendance clinic-add KB "Karol Bagh" --week-off 6
attendance shift-add GEN General --start 09:00 --end 18:00 --present 7 --full 8
attendance serve                                  # http://127.0.0.1:8898
```

Then add connections from **Connections -> Add connection** in the dashboard,
or on the command line:

```bash
attendance account-add "Karol Bagh" --corp-id ACME --user-id admin --clinic KB
attendance account-test            # says exactly what is wrong, if anything
attendance sync
```

## Data model

| table | what it holds |
|---|---|
| `clinic`, `shift`, `holiday` | reference data you control |
| `account` | one upstream login. Password stored as a Fernet token, never plaintext |
| `employee` | created automatically on first punch; assign clinic/shift afterwards |
| `punch` | raw punches, `UNIQUE(employee_id, punch_at, direction)` |
| `day` | **derived** per-employee-day verdict; safe to delete and recompute |
| `sync_run` | audit of every pull |

Two properties matter:

* **Re-syncing is free.** Punch inserts are `INSERT OR IGNORE` against a
  uniqueness constraint, so re-pulling an overlapping window converges instead
  of double-counting. The scheduled job re-pulls the last 7 days, which picks
  up late edits made in the vendor's portal with no reconciliation logic.
* **`day` is a cache.** `attendance recompute` rebuilds it from `punch`.
  Changing a shift rule does not require re-fetching anything.

Punches are stored as clinic-local wall-clock time (`YYYY-MM-DD HH:MM:SS`).
Every clinic is in one timezone, so this keeps reports, shift maths and the
vendor's own exports in the same frame.

## Attendance rules

Set per shift, in `Settings -> Shifts`:

| field | meaning |
|---|---|
| `start` / `end` | shift window; tick "crosses midnight" for night shifts |
| `grace in` / `grace out` | lateness forgiven inside this many minutes |
| `half day` | minutes needed to earn a half day (default 4h) |
| `present from` | minutes needed for a full day's attendance (default 7h) |
| `full day` | the **overtime baseline** (default 8h) |

`present from` and `full day` are deliberately separate numbers. Collapsing
them into one would label a 7h25 day a half day, which is wrong; overtime
should still only start after a full 8h.

Statuses: `present`, `half_day`, `short`, `incomplete`, `absent`, `week_off`,
`holiday`. Worked time is the sum of paired in/out intervals, **not** the
first-to-last span, so a long midday break is not paid as work. A day with an
unpaired punch becomes `incomplete` rather than silently counting as present.

Computation is clamped to an employee's first ever punch and to today, so a
wide backfill never invents absences for dates before someone was employed.

## Sources

| adapter | use |
|---|---|
| `sources/etimeoffice.py` | the vendor REST API |
| `sources/csvfile.py` | a portal export on disk - backfill, or the whole pipeline when the API is unavailable |

Both share `sources/normalize.py`, which is deliberately tolerant: the vendor
publishes no schema, so column names are matched across known spellings, then
fall back to scanning for anything date-shaped. It handles one row per punch,
separate date/time columns, and one row per employee-day with `INTime`/`OUTTime`
(emitted as two punches, rolling the out-punch to the next day when the shift
crossed midnight). Unparseable rows are skipped, never fatal.

## Notes on the eTimeOffice API

Findings from probing the live server, recorded so this is not re-derived:

* Base `https://api.etimeoffice.com/api`. The root serves `Hello World!` and
  there is no docs page or browsable UI - that is normal, not a fault.
* Endpoints that exist: `DownloadInOutPunchData`, `DownloadPunchData`,
  `DownloadPunchDataMCID`. `DownloadEmpData`, `DownloadAttendanceData` and
  `DownloadLeaveData` return **404** on this build, so computed attendance has
  to be derived locally - which is what `rules.py` does.
* Auth is HTTP Basic where the **username is a composite**:
  `CorporateID:UserID:Password:true`, with the password field left empty.
* **Two different 500s, and telling them apart is the whole diagnosis:**
  * `500` with an empty, type-less body -> rejected at the auth layer.
  * `500` with a JSON .NET exception (`IndexOutOfRangeException`) -> the
    credentials were **accepted**; their controller then failed internally,
    which in practice means API access is not enabled on the subscription.

  `EtimeOfficeSource.classify()` encodes this, so the dashboard says which one
  it is instead of blaming the password. The behaviour is pinned by tests in
  `tests/test_attendance_sources.py`.
* Their server returns full stack traces to unauthenticated callers - a
  misconfiguration on their side, useful here but worth reporting to them.

## Security

* Upstream passwords are encrypted with Fernet under `ATT_SECRET_KEY`. They are
  write-only from the browser: settable and replaceable, never rendered back,
  and the encrypted token never reaches a template (pinned by a test).
* Rotating `ATT_SECRET_KEY` invalidates stored passwords; re-enter each one.
* The dashboard requires a password, sets `HttpOnly`/`SameSite=Lax` session
  cookies, and checks a CSRF token on every state-changing POST.
* The database file is created `0600`; it holds encrypted credentials.
* Bind to `127.0.0.1` and put TLS in front (see `deploy/nginx.conf.example`).

## Deployment

```bash
cp deploy/attendance-web.service /etc/systemd/system/
cp deploy/attendance-sync.{service,timer} /etc/systemd/system/
cp deploy/attendance.env.example /etc/attendance/attendance.env   # then edit
systemctl enable --now attendance-web.service attendance-sync.timer
```

## Commands

| command | does |
|---|---|
| `attendance keygen` | print a new credential-encryption key |
| `attendance initdb` | create or migrate the database |
| `attendance serve` | run the dashboard |
| `attendance clinic-add` / `shift-add` | reference data |
| `attendance account-add` / `account-list` / `account-test` | manage connections |
| `attendance sync [--id N]` | pull punches and recompute |
| `attendance import-csv PATH --clinic KB` | backfill from a portal export |
| `attendance recompute` | rebuild `day` from stored punches |
| `attendance probe CORP USER PASS` | call the API with ad-hoc credentials and dump the reply |

## Tests

```bash
python -m pytest tests/test_attendance_*.py -q
```

Covers the rules engine (grace, breaks, night shifts, unpaired punches,
thresholds), the normalizer against five upstream shapes, the API response
classifier, sync idempotency, the schema migration, and the dashboard's auth,
CSRF and credential handling.
