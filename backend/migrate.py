"""
Migration script — adds new columns to existing tables.
Safe to run multiple times (uses IF NOT EXISTS / exception handling).
Run with: python migrate.py
"""
import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not configured. Add your Neon Postgres connection string to backend/.env.")

# Parse the connection string for psycopg2
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()


def add_column_if_missing(table, column, col_type, default=None):
    """Add a column to a table only if it doesn't already exist."""
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    if cur.fetchone():
        print(f"  OK: {table}.{column} already exists -- skipping")
        return

    default_clause = f"DEFAULT {default}" if default else ""
    cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {col_type} {default_clause}')
    print(f"  + Added {table}.{column} ({col_type})")


print("\n-- users --------------------------------------------------")
add_column_if_missing("users", "created_at", "TIMESTAMP", "NOW()")
add_column_if_missing("users", "updated_at", "TIMESTAMP")
add_column_if_missing("users", "saturday_policy", "VARCHAR(30)", "'alt_sat_holiday'")
add_column_if_missing("users", "allow_remote_checkin", "BOOLEAN", "FALSE")

print("\n-- attendance_logs ----------------------------------------")
add_column_if_missing("attendance_logs", "checkin_photo_url",    "TEXT")
add_column_if_missing("attendance_logs", "checkin_note",         "VARCHAR(280)")
add_column_if_missing("attendance_logs", "checkin_face_score",   "FLOAT")
add_column_if_missing("attendance_logs", "checkout_photo_url",   "TEXT")
add_column_if_missing("attendance_logs", "checkout_note",        "VARCHAR(280)")
add_column_if_missing("attendance_logs", "checkout_face_score",  "FLOAT")
add_column_if_missing("attendance_logs", "is_manual_override",   "BOOLEAN", "FALSE")
add_column_if_missing("attendance_logs", "override_by",          "UUID")
add_column_if_missing("attendance_logs", "override_at",          "TIMESTAMP")
add_column_if_missing("attendance_logs", "override_note",        "TEXT")
add_column_if_missing("attendance_logs", "is_anomaly_flagged",   "BOOLEAN", "FALSE")
add_column_if_missing("attendance_logs", "anomaly_type",         "VARCHAR(30)")
add_column_if_missing("attendance_logs", "checkin_mood",         "VARCHAR(50)")
add_column_if_missing("attendance_logs", "checkin_mood_note",    "TEXT")
add_column_if_missing("attendance_logs", "checkout_mood",        "VARCHAR(50)")
add_column_if_missing("attendance_logs", "checkout_mood_note",   "TEXT")
add_column_if_missing("users", "preferred_checkin_time", "VARCHAR(10)", "'09:00'")
add_column_if_missing("users", "enable_checkin_reminder", "BOOLEAN", "TRUE")


print("\n-- attendance_intervals -----------------------------------")
cur.execute("""
    CREATE TABLE IF NOT EXISTS attendance_intervals (
        id UUID PRIMARY KEY,
        attendance_log_id UUID NOT NULL REFERENCES attendance_logs(id) ON DELETE CASCADE,
        checkin_time TIMESTAMP NOT NULL,
        checkin_lat FLOAT,
        checkin_lng FLOAT,
        checkin_photo_url TEXT,
        checkin_note VARCHAR(280),
        checkin_face_score FLOAT,
        checkin_mood VARCHAR(50),
        checkin_mood_note TEXT,
        checkout_time TIMESTAMP,
        checkout_lat FLOAT,
        checkout_lng FLOAT,
        checkout_photo_url TEXT,
        checkout_note VARCHAR(280),
        checkout_face_score FLOAT,
        checkout_mood VARCHAR(50),
        checkout_mood_note TEXT,
        duration_hours FLOAT DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT NOW()
    );
""")
print("  OK: Checked/Created table attendance_intervals")

cur.close()
conn.close()
print("\nMigration complete.\n")

