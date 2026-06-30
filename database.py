"""Database for SCANNOW — supports SQLite (local) and PostgreSQL (Render)."""
import os, json
from config import DB_PATH, USE_POSTGRES, DATABASE_URL

def get_conn():
    if USE_POSTGRES:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    os.makedirs(os.path.dirname(DB_PATH) if DB_PATH else ".", exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def dict_row(conn):
    if USE_POSTGRES:
        from psycopg2.extras import RealDictCursor
        return conn.cursor(cursor_factory=RealDictCursor)
    return conn.cursor()

def init_db():
    conn = get_conn(); cur = dict_row(conn)
    if USE_POSTGRES:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                id SERIAL PRIMARY KEY,
                status TEXT DEFAULT 'running',
                started_at TIMESTAMPTZ DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                new_leads INTEGER DEFAULT 0,
                total_leads INTEGER DEFAULT 0,
                raw_text_count INTEGER DEFAULT 0,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS leads (
                id SERIAL PRIMARY KEY,
                scan_id INTEGER REFERENCES scans(id),
                name TEXT,
                group_name TEXT,
                tenant_type TEXT DEFAULT 'Expat',
                budget TEXT DEFAULT 'N/A',
                urgency TEXT DEFAULT 'Now',
                location TEXT DEFAULT 'Bangkok',
                profile_id TEXT,
                posted_date TEXT,
                contact TEXT DEFAULT '',
                full_text TEXT DEFAULT '',
                has_raw_text INTEGER DEFAULT 0,
                post_url TEXT,
                is_new INTEGER DEFAULT 1,
                dedup_key TEXT UNIQUE,
                first_seen TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_leads_dedup_pg ON leads(dedup_key);
        """)
    else:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT DEFAULT 'running',
                started_at TEXT,
                finished_at TEXT,
                new_leads INTEGER DEFAULT 0,
                total_leads INTEGER DEFAULT 0,
                raw_text_count INTEGER DEFAULT 0,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER REFERENCES scans(id),
                name TEXT, group_name TEXT, tenant_type TEXT DEFAULT 'Expat',
                budget TEXT DEFAULT 'N/A', urgency TEXT DEFAULT 'Now',
                location TEXT DEFAULT 'Bangkok', profile_id TEXT,
                posted_date TEXT, contact TEXT DEFAULT '',
                full_text TEXT DEFAULT '', has_raw_text INTEGER DEFAULT 0,
                post_url TEXT, is_new INTEGER DEFAULT 1,
                dedup_key TEXT UNIQUE, first_seen TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_leads_dedup ON leads(dedup_key);
        """)
    conn.commit(); conn.close()

def create_scan():
    conn = get_conn(); cur = dict_row(conn)
    if USE_POSTGRES:
        cur.execute("INSERT INTO scans (status) VALUES ('running') RETURNING id")
        scan_id = cur.fetchone()["id"]
    else:
        from datetime import datetime, timezone
        cur.execute("INSERT INTO scans (status, started_at) VALUES ('running', ?)", (datetime.now(timezone.utc).isoformat(),))
        scan_id = cur.lastrowid
    conn.commit(); conn.close(); return scan_id

def finish_scan(scan_id, new_count, total_count, raw_count, error=None):
    conn = get_conn(); cur = dict_row(conn)
    status = "error" if error else "completed"
    if USE_POSTGRES:
        cur.execute("UPDATE scans SET status=%s, finished_at=NOW(), new_leads=%s, total_leads=%s, raw_text_count=%s, error=%s WHERE id=%s",
                    (status, new_count, total_count, raw_count, error, scan_id))
    else:
        from datetime import datetime, timezone
        cur.execute("UPDATE scans SET status=?, finished_at=?, new_leads=?, total_leads=?, raw_text_count=?, error=? WHERE id=?",
                    (status, datetime.now(timezone.utc).isoformat(), new_count, total_count, raw_count, error, scan_id))
    conn.commit(); conn.close()

def upsert_leads(scan_id, leads):
    """Insert new leads, update existing. Returns count of new leads."""
    conn = get_conn(); cur = dict_row(conn)
    new_count = 0
    now_val = "NOW()" if USE_POSTGRES else "?"
    now_param = None if USE_POSTGRES else __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

    for lead in leads:
        dk = lead.get("dk", "")
        if not dk: continue
        if USE_POSTGRES:
            cur.execute("SELECT id, is_new FROM leads WHERE dedup_key=%s", (dk,))
        else:
            cur.execute("SELECT id, is_new FROM leads WHERE dedup_key=?", (dk,))
        existing = cur.fetchone()
        if existing:
            if USE_POSTGRES:
                cur.execute("""UPDATE leads SET group_name=%s, budget=%s, urgency=%s, contact=%s,
                    full_text=%s, has_raw_text=%s, post_url=%s, is_new=0 WHERE dedup_key=%s""",
                    (lead.get("g",""), lead.get("b",""), lead.get("u",""), lead.get("c",""),
                     lead.get("f",""), 1 if lead.get("ft") else 0, lead.get("url",""), dk))
            else:
                cur.execute("""UPDATE leads SET group_name=?, budget=?, urgency=?, contact=?,
                    full_text=?, has_raw_text=?, post_url=?, is_new=0 WHERE dedup_key=?""",
                    (lead.get("g",""), lead.get("b",""), lead.get("u",""), lead.get("c",""),
                     lead.get("f",""), 1 if lead.get("ft") else 0, lead.get("url",""), dk))
        else:
            if USE_POSTGRES:
                cur.execute("""INSERT INTO leads (scan_id, name, group_name, tenant_type, budget, urgency,
                    location, profile_id, posted_date, contact, full_text, has_raw_text,
                    post_url, is_new, dedup_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s)""",
                    (scan_id, lead.get("n",""), lead.get("g",""), lead.get("t","Expat"),
                     lead.get("b",""), lead.get("u",""), lead.get("l","Bangkok"),
                     lead.get("p",""), lead.get("d",""), lead.get("c",""),
                     lead.get("f",""), 1 if lead.get("ft") else 0, lead.get("url",""), dk))
            else:
                cur.execute("""INSERT INTO leads (scan_id, name, group_name, tenant_type, budget, urgency,
                    location, profile_id, posted_date, contact, full_text, has_raw_text,
                    post_url, is_new, dedup_key, first_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                    (scan_id, lead.get("n",""), lead.get("g",""), lead.get("t","Expat"),
                     lead.get("b",""), lead.get("u",""), lead.get("l","Bangkok"),
                     lead.get("p",""), lead.get("d",""), lead.get("c",""),
                     lead.get("f",""), 1 if lead.get("ft") else 0, lead.get("url",""), dk, now_param))
            new_count += 1
    conn.commit(); conn.close(); return new_count

def get_recent_scans(limit=10):
    conn = get_conn(); cur = dict_row(conn)
    if USE_POSTGRES:
        cur.execute("SELECT * FROM scans ORDER BY started_at DESC LIMIT %s", (limit,))
    else:
        cur.execute("SELECT * FROM scans ORDER BY started_at DESC LIMIT ?", (limit,))
    rows = cur.fetchall(); conn.close()
    return [dict(r) for r in rows]

def get_leads(search="", status_filter="", limit=100):
    conn = get_conn(); cur = dict_row(conn)
    if USE_POSTGRES:
        q = "SELECT * FROM leads WHERE 1=1"; p = []
        if search: q += " AND (name ILIKE %s OR group_name ILIKE %s OR full_text ILIKE %s)"; p.extend([f"%{search}%"]*3)
        if status_filter == "new": q += " AND is_new=1"
        q += " ORDER BY first_seen DESC LIMIT %s"; p.append(limit)
        cur.execute(q, p)
    else:
        q = "SELECT * FROM leads WHERE 1=1"; p = []
        if search: q += " AND (name LIKE ? OR group_name LIKE ? OR full_text LIKE ?)"; p.extend([f"%{search}%"]*3)
        if status_filter == "new": q += " AND is_new=1"
        q += " ORDER BY first_seen DESC LIMIT ?"; p.append(limit)
        cur.execute(q, p)
    rows = cur.fetchall(); conn.close()
    return [dict(r) for r in rows]

def get_new_leads():
    conn = get_conn(); cur = dict_row(conn)
    if USE_POSTGRES:
        cur.execute("SELECT * FROM leads WHERE is_new=1 ORDER BY first_seen")
    else:
        cur.execute("SELECT * FROM leads WHERE is_new=1 ORDER BY first_seen")
    rows = cur.fetchall(); conn.close()
    return [dict(r) for r in rows]

def mark_leads_notified(limit=50):
    conn = get_conn(); cur = dict_row(conn)
    if USE_POSTGRES:
        cur.execute("UPDATE leads SET is_new=0 WHERE id IN (SELECT id FROM leads WHERE is_new=1 LIMIT %s)", (limit,))
    else:
        cur.execute("UPDATE leads SET is_new=0 WHERE id IN (SELECT id FROM leads WHERE is_new=1 LIMIT ?)", (limit,))
    conn.commit(); conn.close()

def get_stats():
    conn = get_conn(); cur = dict_row(conn)
    cur.execute("SELECT COUNT(*) FROM leads"); total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM leads WHERE is_new=1"); new_leads = cur.fetchone()[0]
    if USE_POSTGRES:
        cur.execute("SELECT COUNT(DISTINCT group_name) FROM leads")
    else:
        cur.execute("SELECT COUNT(DISTINCT group_name) FROM leads")
    groups = cur.fetchone()[0]
    if USE_POSTGRES:
        cur.execute("SELECT finished_at FROM scans WHERE status='completed' ORDER BY finished_at DESC LIMIT 1")
    else:
        cur.execute("SELECT finished_at FROM scans WHERE status='completed' ORDER BY finished_at DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return {"total_leads": total, "new_leads": new_leads, "unique_groups": groups, "last_scan": row[0] if row else None}

def reset_all():
    """Delete all leads and scans (for fresh start)."""
    conn = get_conn(); cur = conn.cursor()
    if USE_POSTGRES:
        cur.execute("DELETE FROM leads"); cur.execute("DELETE FROM scans")
    else:
        cur.execute("DELETE FROM leads"); cur.execute("DELETE FROM scans")
    conn.commit(); conn.close()
