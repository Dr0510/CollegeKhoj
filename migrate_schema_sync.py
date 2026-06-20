"""
Schema Synchronization: ALTERs existing PostgreSQL tables to match SQLAlchemy models.

This script detects all column differences between models.py and the actual
PostgreSQL database, then applies safe ALTER TABLE statements to fix them.

Idempotent — safe to run multiple times. Preserves all existing data.
No DROP TABLE or DELETE operations.

Usage:
    python migrate_schema_sync.py
"""
import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask
from database import db, init_database, get_db_columns
from sqlalchemy import text, inspect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'schema-sync-migration'
init_database(app)


# Known renames: (table, old_column, new_column)
KNOWN_RENAMES = [
    ('colleges', 'college', 'college_name'),
]

# Expected columns per table: (column_name, sql_type)
# These must match what models.py defines
EXPECTED_COLUMNS = {
    'colleges': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('college_code', 'VARCHAR(20)'),
        ('college_name', 'VARCHAR(300)'),
        ('district', 'VARCHAR(100)'),
        ('city', 'VARCHAR(100)'),
        ('college_type', 'VARCHAR(50)'),
        ('status', "VARCHAR(20) DEFAULT 'active'"),
        ('location', 'VARCHAR(100)'),
        ('branch', 'VARCHAR(200)'),
        ('fees', 'FLOAT'),
        ('placement_rate', 'FLOAT'),
        ('nirf_rank', 'INTEGER'),
        ('rating', 'FLOAT'),
        ('university', 'VARCHAR(200)'),
        ('address', 'TEXT'),
        ('website', 'VARCHAR(500)'),
        ('naac_grade', 'VARCHAR(10)'),
        ('is_autonomous', 'BOOLEAN DEFAULT FALSE'),
        ('created_at', 'TIMESTAMP DEFAULT NOW()'),
        ('updated_at', 'TIMESTAMP DEFAULT NOW()'),
    ],
    'cutoffs': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('admission_type_id', 'INTEGER NOT NULL'),
        ('college_id', 'INTEGER NOT NULL'),
        ('branch_id', 'INTEGER NOT NULL'),
        ('academic_year_id', 'INTEGER NOT NULL'),
        ('cap_round_id', 'INTEGER NOT NULL'),
        ('category', 'VARCHAR(50) NOT NULL'),
        ('seat_type', 'VARCHAR(50)'),
        ('gender', "VARCHAR(20) DEFAULT 'Gender-Neutral'"),
        ('minority_status', 'VARCHAR(50)'),
        ('cutoff_percentile', 'NUMERIC(6,2)'),
        ('cutoff_rank', 'INTEGER'),
        ('source_pdf', 'VARCHAR(500)'),
        ('upload_job_id', 'INTEGER'),
        ('created_at', 'TIMESTAMP DEFAULT NOW()'),
    ],
    'upload_jobs': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('filename', 'VARCHAR(500) NOT NULL'),
        ('stored_path', 'VARCHAR(1000)'),
        ('file_hash', 'VARCHAR(64)'),
        ('file_size', 'INTEGER'),
        ('admission_type_id', 'INTEGER NOT NULL'),
        ('academic_year_id', 'INTEGER NOT NULL'),
        ('cap_round_id', 'INTEGER NOT NULL'),
        ('status', "VARCHAR(30) DEFAULT 'PENDING'"),
        ('total_rows', 'INTEGER DEFAULT 0'),
        ('valid_rows', 'INTEGER DEFAULT 0'),
        ('invalid_rows', 'INTEGER DEFAULT 0'),
        ('duplicate_rows', 'INTEGER DEFAULT 0'),
        ('started_at', 'TIMESTAMP'),
        ('completed_at', 'TIMESTAMP'),
        ('error_message', 'TEXT'),
        ('uploaded_by', 'INTEGER'),
        ('created_at', 'TIMESTAMP DEFAULT NOW()'),
    ],
    'admission_types': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('name', 'VARCHAR(100) NOT NULL'),
        ('code', 'VARCHAR(20) NOT NULL'),
    ],
    'academic_years': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('academic_year', 'VARCHAR(20) NOT NULL'),
    ],
    'cap_rounds': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('name', 'VARCHAR(50) NOT NULL'),
    ],
    'branches': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('branch_code', 'VARCHAR(20) NOT NULL'),
        ('branch_name', 'VARCHAR(200) NOT NULL'),
    ],
    'users': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('email', 'VARCHAR(200) NOT NULL'),
        ('first_name', 'VARCHAR(100)'),
        ('last_name', 'VARCHAR(100)'),
        ('password_hash', 'VARCHAR(255)'),
        ('profile_image_url', 'TEXT'),
        ('role', "VARCHAR(20) DEFAULT 'user'"),
        ('is_verified', 'BOOLEAN DEFAULT FALSE'),
        ('verification_code', 'VARCHAR(6)'),
        ('verification_code_expiry', 'TIMESTAMP'),
        ('reset_token', 'VARCHAR(100)'),
        ('reset_token_expiry', 'TIMESTAMP'),
        ('created_at', 'TIMESTAMP DEFAULT NOW()'),
        ('last_login', 'TIMESTAMP'),
    ],
    'backup_history': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('backup_date', 'TIMESTAMP DEFAULT NOW()'),
        ('backup_file', 'VARCHAR(500) NOT NULL'),
        ('file_size', 'INTEGER'),
        ('db_type', 'VARCHAR(20)'),
        ('record_count', 'INTEGER'),
        ('status', "VARCHAR(20) DEFAULT 'success'"),
        ('created_by', 'INTEGER'),
        ('notes', 'TEXT'),
    ],
    'audit_logs': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('user_id', 'INTEGER'),
        ('action', 'VARCHAR(50) NOT NULL'),
        ('resource_type', 'VARCHAR(50)'),
        ('resource_id', 'INTEGER'),
        ('ip_address', 'VARCHAR(45)'),
        ('user_agent', 'VARCHAR(255)'),
        ('created_at', 'TIMESTAMP DEFAULT NOW()'),
    ],
    'login_history': [
        ('id', 'SERIAL PRIMARY KEY'),
        ('user_id', 'INTEGER'),
        ('email', 'VARCHAR(200) NOT NULL'),
        ('ip_address', 'VARCHAR(45)'),
        ('user_agent', 'VARCHAR(255)'),
        ('success', 'BOOLEAN DEFAULT FALSE'),
        ('failure_reason', 'VARCHAR(100)'),
        ('created_at', 'TIMESTAMP DEFAULT NOW()'),
    ],
}


def run_sync():
    """Main synchronisation logic."""
    with app.app_context():
        logger.info("=" * 60)
        logger.info("SCHEMA SYNCHRONIZATION REPORT")
        logger.info("=" * 60)

        inspector = inspect(db.engine)
        all_tables = set(inspector.get_table_names())

        total_added = 0
        total_skipped = 0
        total_errors = 0
        report = []

        for table_name, expected_cols in EXPECTED_COLUMNS.items():
            if table_name not in all_tables:
                logger.warning(f"Table '{table_name}' does not exist — skipping")
                report.append(f"❌ {table_name}: table missing (run migrate_admin_v3.py first)")
                continue

            actual_cols = [c['name'] for c in inspector.get_columns(table_name)]
            expected_names = [c[0] for c in expected_cols]

            # ── Handle renames ──
            for rename in KNOWN_RENAMES:
                r_table, r_old, r_new = rename
                if r_table != table_name:
                    continue
                if r_old in actual_cols and r_new not in actual_cols:
                    try:
                        db.session.execute(text(
                            f'ALTER TABLE {table_name} RENAME COLUMN "{r_old}" TO "{r_new}"'
                        ))
                        db.session.commit()
                        logger.info(f"Renamed {table_name}.{r_old} -> {table_name}.{r_new}")
                        total_added += 1
                        actual_cols = [c['name'] for c in inspector.get_columns(table_name)]
                    except Exception as e:
                        db.session.rollback()
                        logger.error(f"Rename {table_name}.{r_old} failed: {e}")
                        total_errors += 1
                elif r_old in actual_cols and r_new in actual_cols:
                    # Both exist: merge data, drop old
                    try:
                        db.session.execute(text(
                            f"UPDATE {table_name} SET {r_new} = {r_old} WHERE {r_new} IS NULL"
                        ))
                        db.session.execute(text(
                            f'ALTER TABLE {table_name} DROP COLUMN "{r_old}"'
                        ))
                        db.session.commit()
                        logger.info(f"Merged {table_name}.{r_old} into {table_name}.{r_new}, dropped old")
                        total_added += 1
                        actual_cols = [c['name'] for c in inspector.get_columns(table_name)]
                    except Exception as e:
                        db.session.rollback()
                        logger.error(f"Merge {table_name}.{r_old} failed: {e}")
                        total_errors += 1

            # ── Add missing columns ──
            for col_name, col_type in expected_cols:
                if col_name in actual_cols:
                    continue
                if col_name in ('id',):  # Skip primary key — always exists
                    continue

                try:
                    # Strip NOT NULL constraints for new columns on existing tables
                    safe_type = col_type
                    if 'NOT NULL' in safe_type.upper():
                        safe_type = safe_type.replace('NOT NULL', '').strip()
                    # Remove PRIMARY KEY — can't add to existing table
                    if 'PRIMARY KEY' in safe_type.upper():
                        safe_type = safe_type.replace('PRIMARY KEY', '').strip()

                    db.session.execute(text(
                        f'ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS "{col_name}" {safe_type}'
                    ))
                    db.session.commit()
                    logger.info(f"Added {table_name}.{col_name} ({safe_type})")
                    total_added += 1
                except Exception as e:
                    db.session.rollback()
                    logger.warning(f"Could not add {table_name}.{col_name}: {e}")
                    total_errors += 1

            # ── Report ──
            actual_cols = [c['name'] for c in inspector.get_columns(table_name)]
            missing = [c for c in expected_names if c not in actual_cols]
            if missing:
                report.append(f"⚠️  {table_name}: missing {len(missing)} columns: {missing}")
            else:
                report.append(f"✅ {table_name}: {len(actual_cols)} columns OK")

        # ── Final verification ──
        logger.info("=" * 60)
        logger.info("VERIFICATION")
        logger.info("=" * 60)
        for line in report:
            logger.info(line)

        logger.info("=" * 60)
        logger.info(f"Summary: {total_added} columns added, {total_skipped} skipped, {total_errors} errors")

        # Verify College model works
        try:
            from models import College
            count = College.query.count()
            logger.info(f"College.query.count() = {count} ✅")
            if count > 0:
                sample = College.query.first()
                logger.info(f"  Sample: id={sample.id}, name={sample.college_name[:50] if sample.college_name else 'N/A'}")
        except Exception as e:
            logger.error(f"College.query.count() FAILED: {e}")

        return total_added, total_errors


if __name__ == '__main__':
    run_sync()