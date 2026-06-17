"""
Migration: Add approval workflow columns to import_jobs & college_cutoffs.

This is a one-time migration that synchronizes the PostgreSQL schema with
the ImportJob and CollegeCutoff SQLAlchemy models.

Safe to run multiple times — uses IF NOT EXISTS / column-exists checks.
"""
import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_URL = (
    os.environ.get("NEON_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://localhost/college_recommendation"
)


def _clean_db_url(raw: str) -> str:
    """Strip psql wrapper and unsupported params."""
    import re
    url = raw.strip().strip("'\"")
    if url.lower().startswith("psql "):
        url = url[5:].strip("'\"")
    url = re.sub(r'[&?]channel_binding=[^&]*', '', url)
    if 'neon.tech' in url and 'sslmode' not in url:
        url += ('&' if '?' in url else '?') + 'sslmode=require'
    return url


def get_columns(conn, table: str) -> set[str]:
    """Return set of column names for a given table."""
    import psycopg2.extras
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,),
    )
    cols = {row[0] for row in cur.fetchall()}
    cur.close()
    return cols


def run_migration():
    """Execute the schema migration against the target database."""
    import psycopg2.extras

    db_url = _clean_db_url(DB_URL)
    logger.info("Connecting to database …")
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # ── 1. import_jobs — add approval columns ──────────────────────────
        ij_cols = get_columns(conn, "import_jobs")
        logger.info(f"import_jobs existing columns: {sorted(ij_cols)}")

        migrates_ij = [
            ("approval_status", "VARCHAR(20)", "NULL"),
            ("approved_by", "INTEGER", "NULL"),
            ("approved_at", "TIMESTAMP", "NULL"),
            ("rejection_reason", "TEXT", "NULL"),
            ("uploaded_by", "INTEGER", "NULL"),
        ]

        for col_name, col_type, col_default in migrates_ij:
            if col_name not in ij_cols:
                default_clause = f"DEFAULT {col_default}" if col_default != "NULL" else ""
                sql = f"ALTER TABLE import_jobs ADD COLUMN {col_name} {col_type} {default_clause}"
                cur.execute(sql)
                logger.info(f"✅ Added column {col_name} to import_jobs")

        # ── 2. college_cutoffs — ensure columns exist with correct defaults ─
        cc_cols = get_columns(conn, "college_cutoffs")
        logger.info(f"college_cutoffs existing columns: {sorted(cc_cols)}")

        migrates_cc = [
            ("approval_status", "VARCHAR(20)", "'pending_approval'"),
            ("approved_at", "TIMESTAMP", "NULL"),
            ("approved_by", "INTEGER", "NULL"),
        ]

        for col_name, col_type, col_default in migrates_cc:
            if col_name not in cc_cols:
                default_clause = f"DEFAULT {col_default}" if col_default != "NULL" else ""
                sql = f"ALTER TABLE college_cutoffs ADD COLUMN {col_name} {col_type} {default_clause}"
                cur.execute(sql)
                logger.info(f"✅ Added column {col_name} to college_cutoffs")
            else:
                # Column exists — ensure default is 'pending_approval', NOT 'approved'
                if col_name == "approval_status":
                    cur.execute(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_name = 'college_cutoffs' AND column_name = 'approval_status'"
                    )
                    row = cur.fetchone()
                    current_default = row[0] if row else None
                    if current_default and "'approved'" in current_default:
                        cur.execute(
                            "ALTER TABLE college_cutoffs ALTER COLUMN approval_status "
                            "SET DEFAULT 'pending_approval'"
                        )
                        logger.info("✅ Fixed college_cutoffs.approval_status default → pending_approval")

        conn.commit()
        logger.info("✅ Migration completed successfully")

    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Migration failed: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run_migration()
