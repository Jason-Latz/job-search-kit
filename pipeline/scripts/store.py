"""SQLite store for saved jobs and application tracking.

SQLite because it is a real database with zero setup, ships with Python, and is one file
that survives anything. The file itself is gitignored (a daily-changing binary makes for
useless history); `export_json` writes a readable snapshot that is committed instead, so
the tracker can be rebuilt if the file is ever lost.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
from datetime import datetime, timezone

DB = pathlib.Path(__file__).resolve().parent.parent / "state" / "jobs.db"
EXPORT = pathlib.Path(__file__).resolve().parent.parent / "state" / "tracker-export.json"

# Ordered: this drives the board's columns and the "advance" action.
STATUSES = ["new", "starred", "applied", "interviewing", "offer", "rejected", "passed"]
OPEN_STATUSES = ["starred", "applied", "interviewing", "offer"]

# Editable per-job fields. Added by migration so an existing database is not rebuilt.
EXTRA_COLUMNS = {
    "priority": "INTEGER NOT NULL DEFAULT 0",   # 0-3, shown as dots
    "stage": "TEXT NOT NULL DEFAULT ''",        # phone screen / technical / onsite / final
    "deadline": "TEXT",                         # application deadline
    "next_action": "TEXT NOT NULL DEFAULT ''",
    "next_action_date": "TEXT",
    "contact": "TEXT NOT NULL DEFAULT ''",
    "compensation": "TEXT NOT NULL DEFAULT ''",
}
EDITABLE = set(EXTRA_COLUMNS) | {"notes"}
STAGES = ["", "phone screen", "technical", "take-home", "onsite", "final", "offer call"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  url TEXT PRIMARY KEY, company TEXT, title TEXT, category TEXT, subtype TEXT,
  location TEXT, published_at TEXT, employer_openings INTEGER, source TEXT,
  vertical TEXT, summary TEXT, why TEXT, first_seen TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS tracking (
  url TEXT PRIMARY KEY REFERENCES jobs(url) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'new', notes TEXT NOT NULL DEFAULT '',
  applied_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL, at TEXT NOT NULL
);
-- The full eligible pool, replaced every harvest. The daily digest only surfaces what
-- is new, so without this the several thousand roles already open are unreachable.
CREATE TABLE IF NOT EXISTS pool (
  url TEXT PRIMARY KEY, company TEXT, title TEXT, category TEXT, subtype TEXT,
  location TEXT, published_at TEXT, age_days INTEGER, employer_openings INTEGER,
  source TEXT, vertical TEXT, is_new INTEGER, on_watchlist INTEGER, summary TEXT,
  refreshed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pool_vertical ON pool(vertical);
CREATE INDEX IF NOT EXISTS idx_pool_subtype ON pool(subtype);
CREATE INDEX IF NOT EXISTS idx_pool_age ON pool(age_days);
CREATE INDEX IF NOT EXISTS idx_pool_company ON pool(company);
CREATE INDEX IF NOT EXISTS idx_tracking_status ON tracking(status);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    migrate(con)
    return con


def migrate(con: sqlite3.Connection) -> list[str]:
    """Add any tracking columns this version expects. SQLite has no IF NOT EXISTS for
    ALTER TABLE ADD COLUMN, so compare against the live schema."""
    have = {r["name"] for r in con.execute("PRAGMA table_info(tracking)")}
    added = []
    for name, decl in EXTRA_COLUMNS.items():
        if name not in have:
            con.execute(f"ALTER TABLE tracking ADD COLUMN {name} {decl}")
            added.append(name)
    if added:
        con.commit()
    return added


def upsert_jobs(con: sqlite3.Connection, records) -> int:
    """Insert digest entries. Existing rows keep their tracking state untouched."""
    ts, n = now(), 0
    for r in records:
        url = r.get("url")
        if not url:
            continue
        con.execute("""
            INSERT INTO jobs (url, company, title, category, subtype, location,
                              published_at, employer_openings, source, vertical,
                              summary, why, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
              last_seen=excluded.last_seen,
              summary=COALESCE(NULLIF(excluded.summary,''), jobs.summary),
              why=COALESCE(NULLIF(excluded.why,''), jobs.why)
        """, (url, r.get("company"), r.get("title"), r.get("category"), r.get("subtype"),
              r.get("location"), r.get("published_at"), r.get("employer_openings"),
              r.get("source"), r.get("vertical"), r.get("summary") or "",
              r.get("why") or "", ts, ts))
        con.execute("INSERT OR IGNORE INTO tracking (url, status, updated_at) VALUES (?,?,?)",
                    (url, "new", ts))
        n += 1
    con.commit()
    return n


def replace_pool(con: sqlite3.Connection, records) -> int:
    """Swap in this run's eligible pool atomically, so a browse never sees a half-write."""
    ts = now()
    rows = [(r.get("url"), r.get("company"), r.get("title"), r.get("category"),
             r.get("subtype"), r.get("location"), r.get("published_at"), r.get("age_days"),
             r.get("employer_openings"), r.get("source"), r.get("vertical"),
             1 if r.get("is_new") else 0, 1 if r.get("on_watchlist") else 0,
             (r.get("text") or "")[:400], ts)
            for r in records if r.get("url")]
    with con:
        con.execute("DELETE FROM pool")
        con.executemany("INSERT OR REPLACE INTO pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def query_pool(con: sqlite3.Connection, *, vertical=None, category=None, subtype=None,
               max_age=None, max_openings=None, watchlist=None, q=None, hide_tracked=True,
               limit=60, offset=0) -> dict:
    """Filtered browse over the pool, with each row's tracking status attached."""
    where, args = ["1=1"], []
    if vertical:
        where.append("p.vertical = ?"); args.append(vertical)
    if category:
        where.append("p.category = ?"); args.append(category)
    if subtype:
        where.append("p.subtype = ?"); args.append(subtype)
    if max_age is not None:
        where.append("p.age_days IS NOT NULL AND p.age_days <= ?"); args.append(int(max_age))
    if max_openings is not None:
        where.append("p.employer_openings IS NOT NULL AND p.employer_openings <= ?")
        args.append(int(max_openings))
    if watchlist:
        where.append("p.on_watchlist = 1")
    if q:
        where.append("(p.company LIKE ? OR p.title LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if hide_tracked:
        # Anything already starred, applied to or passed on is not part of catching up.
        where.append("(t.status IS NULL OR t.status = 'new')")
    clause = " AND ".join(where)
    total = con.execute(
        f"SELECT COUNT(*) n FROM pool p LEFT JOIN tracking t ON t.url=p.url "
        f"WHERE {clause}", args).fetchone()["n"]
    rows = con.execute(f"""
        SELECT p.*, t.status FROM pool p LEFT JOIN tracking t ON t.url=p.url
        WHERE {clause}
        ORDER BY (p.vertical = 'health-bio') DESC,
                 (p.subtype IN ('Winter 2027','Spring 2027')) DESC,
                 p.on_watchlist DESC,
                 (p.employer_openings IS NOT NULL AND p.employer_openings <= 60) DESC,
                 p.age_days ASC
        LIMIT ? OFFSET ?""", args + [int(limit), int(offset)]).fetchall()
    return {"total": total, "rows": [dict(r) for r in rows]}


def pool_facets(con: sqlite3.Connection) -> dict:
    """Counts for the filter chips, so the UI never offers an empty filter."""
    def tally(col):
        return {r[col]: r["n"] for r in con.execute(
            f"SELECT {col}, COUNT(*) n FROM pool p LEFT JOIN tracking t ON t.url=p.url "
            f"WHERE ({col} IS NOT NULL AND {col} != '') "
            f"AND (t.status IS NULL OR t.status='new') GROUP BY {col} ORDER BY n DESC")}
    return {"vertical": tally("vertical"), "subtype": tally("subtype"),
            "category": tally("category"),
            "total": con.execute(
                "SELECT COUNT(*) n FROM pool p LEFT JOIN tracking t ON t.url=p.url "
                "WHERE t.status IS NULL OR t.status='new'").fetchone()["n"]}


def star_from_pool(con: sqlite3.Connection, url: str) -> dict:
    """Star a role the daily digest never sent, promoting it out of the pool."""
    row = con.execute("SELECT * FROM pool WHERE url=?", (url,)).fetchone()
    if row is None:
        raise KeyError(url)
    upsert_jobs(con, [{**dict(row), "summary": row["summary"], "why": ""}])
    return set_status(con, url, "starred")


def set_status(con: sqlite3.Connection, url: str, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    row = con.execute("SELECT status FROM tracking WHERE url=?", (url,)).fetchone()
    if row is None:
        raise KeyError(url)
    prev, ts = row["status"], now()
    applied = ", applied_at=COALESCE(applied_at, ?)" if status == "applied" else ""
    args = ([status, ts, ts, url] if applied else [status, ts, url])
    con.execute(f"UPDATE tracking SET status=?, updated_at=?{applied} WHERE url=?", args)
    con.execute("INSERT INTO events (url, from_status, to_status, at) VALUES (?,?,?,?)",
                (url, prev, status, ts))
    con.commit()
    return {"url": url, "from": prev, "to": status}


def set_note(con: sqlite3.Connection, url: str, note: str) -> None:
    con.execute("UPDATE tracking SET notes=?, updated_at=? WHERE url=?", (note, now(), url))
    con.commit()


def update_fields(con: sqlite3.Connection, url: str, fields: dict) -> dict:
    """Patch editable tracking fields. Unknown keys are ignored rather than trusted."""
    patch = {k: v for k, v in fields.items() if k in EDITABLE}
    if not patch:
        return {"updated": []}
    if "priority" in patch:
        patch["priority"] = max(0, min(3, int(patch["priority"] or 0)))
    if patch.get("stage") not in (None, *STAGES) and "stage" in patch:
        patch["stage"] = str(patch["stage"])[:40]
    sets = ", ".join(f"{k}=?" for k in patch)
    with con:
        cur = con.execute(f"UPDATE tracking SET {sets}, updated_at=? WHERE url=?",
                          [*patch.values(), now(), url])
    if cur.rowcount == 0:
        raise KeyError(url)
    return {"updated": sorted(patch)}


def job_detail(con: sqlite3.Connection, url: str) -> dict:
    row = con.execute("""
        SELECT j.*, t.status, t.notes, t.applied_at, t.updated_at, t.priority, t.stage,
               t.deadline, t.next_action, t.next_action_date, t.contact, t.compensation
        FROM jobs j JOIN tracking t ON t.url=j.url WHERE j.url=?""", (url,)).fetchone()
    if row is None:
        raise KeyError(url)
    events = [dict(e) for e in con.execute(
        "SELECT from_status, to_status, at FROM events WHERE url=? ORDER BY id", (url,))]
    return {**dict(row), "events": events}


def stats(con: sqlite3.Connection) -> dict:
    """Headline numbers for the board. Response rate counts applications that went
    anywhere at all, which is the number worth watching while job hunting."""
    c = counts(con)
    applied = con.execute(
        "SELECT COUNT(DISTINCT url) n FROM events WHERE to_status='applied'").fetchone()["n"]
    advanced = con.execute(
        "SELECT COUNT(DISTINCT url) n FROM events WHERE to_status IN ('interviewing','offer')"
    ).fetchone()["n"]
    rejected = con.execute(
        "SELECT COUNT(DISTINCT url) n FROM events WHERE to_status='rejected'").fetchone()["n"]
    return {"counts": c, "applied_total": applied, "advanced": advanced,
            "rejected": rejected,
            "response_rate": round(100 * (advanced + rejected) / applied) if applied else None,
            "interview_rate": round(100 * advanced / applied) if applied else None,
            "tracked_total": sum(c.values())}


def board(con: sqlite3.Connection) -> dict:
    rows = con.execute("""
        SELECT j.*, t.status, t.notes, t.applied_at, t.updated_at, t.priority, t.stage,
               t.deadline, t.next_action, t.next_action_date, t.contact, t.compensation
        FROM jobs j JOIN tracking t ON t.url=j.url
        ORDER BY t.priority DESC, t.updated_at DESC
    """).fetchall()
    out = {s: [] for s in STATUSES}
    for r in rows:
        out[r["status"]].append(dict(r))
    return out


def counts(con: sqlite3.Connection) -> dict:
    return {r["status"]: r["n"] for r in
            con.execute("SELECT status, COUNT(*) n FROM tracking GROUP BY status")}


def export_json(con: sqlite3.Connection) -> pathlib.Path:
    """Readable, committable snapshot. The .db itself is gitignored."""
    data = {
        "exported_at": now(),
        "jobs": [dict(r) for r in con.execute("SELECT * FROM jobs ORDER BY first_seen")],
        "tracking": [dict(r) for r in con.execute("SELECT * FROM tracking ORDER BY url")],
        "events": [dict(r) for r in con.execute("SELECT * FROM events ORDER BY id")],
    }
    EXPORT.write_text(json.dumps(data, indent=2) + "\n")
    return EXPORT


def import_json(con: sqlite3.Connection) -> int:
    """Rebuild the database from the committed snapshot."""
    if not EXPORT.exists():
        return 0
    data = json.loads(EXPORT.read_text())
    for j in data.get("jobs", []):
        cols = ",".join(j)
        con.execute(f"INSERT OR REPLACE INTO jobs ({cols}) VALUES ({','.join('?' * len(j))})",
                    list(j.values()))
    for t in data.get("tracking", []):
        cols = ",".join(t)
        con.execute(f"INSERT OR REPLACE INTO tracking ({cols}) VALUES ({','.join('?' * len(t))})",
                    list(t.values()))
    con.commit()
    return len(data.get("jobs", []))
