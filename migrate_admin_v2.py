"""
Database migration for Admin v2 enhancements.
Adds new columns to existing tables and creates new tables.
Safe to run multiple times — uses IF NOT EXISTS / column existence checks.
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()

from flask import Flask
from database import db, init_database
from sqlalchemy import text, inspect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create minimal app
app = Flask(__name__)
app.secret_key = 'migration-secret-key'
init_database(app)

# Import models to register them
from models import (
    College, CollegeCutoff, User, UploadedFile, ImportJob,
    AuditLog, BackupHistory, LoginHistory, ManualCutoffEntry
)


def column_exists(table, column):
    """Check if a column exists in the given table."""
    try:
        inspector = inspect(db.engine)
        cols = [c['name'] for c in inspector.get_columns(table)]
        return column in cols
    except Exception:
        return False


def run_migration():
    """Run all schema migrations."""
    with app.app_context():
        # ── 1. College table extensions ──
        college_columns = [
            ('college_code', "VARCHAR(20) UNIQUE"),
            ('college_type', "VARCHAR(50)"),
            ('district', "VARCHAR(100)"),
            ('university', "VARCHAR(200)"),
            ('address', "TEXT"),
            ('website', "VARCHAR(500)"),
            ('naac_grade', "VARCHAR(10)"),
            ('is_autonomous', "BOOLEAN DEFAULT FALSE"),
            ('status', "VARCHAR(20) DEFAULT 'active'"),
        ]
        for col_name, col_type in college_columns:
            if not column_exists('colleges', col_name):
                try:
                    db.session.execute(text(
                        f"ALTER TABLE colleges ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info(f"✅ Added column colleges.{col_name}")
                except Exception as e:
                    db.session.rollback()
                    logger.warning(f"⚠️  Could not add colleges.{col_name}: {e}")

        # Add index for college_code
        try:
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_colleges_code ON colleges(college_code)"
            ))
        except Exception:
            pass

        db.session.commit()

        # ── 2. Create login_history table ──
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS login_history (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    email VARCHAR(200) NOT NULL,
                    ip_address VARCHAR(45),
                    user_agent VARCHAR(255),
                    success BOOLEAN NOT NULL DEFAULT FALSE,
                    failure_reason VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_login_history_user ON login_history(user_id)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_login_history_email ON login_history(email)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_login_history_created ON login_history(created_at)"
            ))
            logger.info("✅ Created login_history table")
        except Exception as e:
            logger.warning(f"⚠️  Could not create login_history: {e}")

        # ── 3. Create manual_cutoff_entries table ──
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS manual_cutoff_entries (
                    id SERIAL PRIMARY KEY,
                    year INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    exam_type VARCHAR(20) NOT NULL DEFAULT 'MHT-CET',
                    college_code VARCHAR(20) NOT NULL,
                    college_name VARCHAR(300) NOT NULL,
                    course_code VARCHAR(20) NOT NULL,
                    course_name VARCHAR(200) NOT NULL,
                    category VARCHAR(20) NOT NULL,
                    rank INTEGER,
                    percentile FLOAT,
                    gender VARCHAR(10) DEFAULT 'Gender-Neutral',
                    opening_rank INTEGER,
                    closing_rank INTEGER,
                    seats_available INTEGER,
                    branch VARCHAR(200),
                    entered_by INTEGER REFERENCES users(id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_mce_year ON manual_cutoff_entries(year)"
            ))
            logger.info("✅ Created manual_cutoff_entries table")
        except Exception as e:
            logger.warning(f"⚠️  Could not create manual_cutoff_entries: {e}")

        db.session.commit()

        # ── 4. Verify all tables exist ──
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        logger.info(f"📋 Database tables: {len(tables)}")
        for t in tables:
            cols = [c['name'] for c in inspector.get_columns(t)]
            logger.info(f"   • {t}: {len(cols)} columns")

        logger.info("✅ Migration complete")


if __name__ == '__main__':
    run_migration()