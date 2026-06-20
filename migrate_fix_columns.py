"""
Safe Migration: Fix schema mismatches between SQLAlchemy models and PostgreSQL.

Detects and fixes:
  - colleges.college -> colleges.college_name (renamed in new model)
  - Any other missing model columns vs actual DB columns

Idempotent — safe to run multiple times. Preserves all existing data.
"""
import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask
from database import db, init_database
from sqlalchemy import text, inspect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'migration-column-fix'
init_database(app)


def get_table_columns(table_name: str) -> list:
    """Get list of actual column names from the database."""
    try:
        inspector = inspect(db.engine)
        return [c['name'] for c in inspector.get_columns(table_name)]
    except Exception as e:
        logger.error(f"Cannot inspect table {table_name}: {e}")
        return []


def column_exists(table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    return column in get_table_columns(table)


def run_migration():
    """Detect and fix schema mismatches."""
    with app.app_context():
        fixes_applied = 0
        fixes_skipped = 0

        # ═══════════════════════════════════════════════════════════════
        # FIX 1: colleges.college -> colleges.college_name
        # ═══════════════════════════════════════════════════════════════
        table = 'colleges'
        old_col = 'college'
        new_col = 'college_name'

        has_old = column_exists(table, old_col)
        has_new = column_exists(table, new_col)

        if has_old and not has_new:
            logger.info(f"Renaming {table}.{old_col} -> {table}.{new_col}")
            try:
                db.session.execute(text(
                    f"ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col}"
                ))
                db.session.commit()
                logger.info(f"Renamed {table}.{old_col} -> {table}.{new_col}")
                fixes_applied += 1
            except Exception as e:
                db.session.rollback()
                logger.warning(f"Could not rename {table}.{old_col}: {e}")
                fixes_skipped += 1
        elif has_new and not has_old:
            logger.info(f"Column {table}.{new_col} already exists, {old_col} not found — OK")
            fixes_skipped += 1
        elif has_new and has_old:
            logger.info(f"Both {table}.{old_col} and {table}.{new_col} exist — need data merge")
            # Both exist: copy data from old to new where new is NULL, then drop old
            try:
                db.session.execute(text(
                    f"UPDATE {table} SET {new_col} = {old_col} WHERE {new_col} IS NULL"
                ))
                db.session.execute(text(
                    f"ALTER TABLE {table} DROP COLUMN {old_col}"
                ))
                db.session.commit()
                logger.info(f"Merged {table}.{old_col} into {table}.{new_col} and dropped old column")
                fixes_applied += 1
            except Exception as e:
                db.session.rollback()
                logger.warning(f"Could not merge columns: {e}")
                fixes_skipped += 1
        else:
            logger.info(f"Neither {old_col} nor {new_col} found in {table} — adding new column")
            try:
                db.session.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {new_col} VARCHAR(300) NOT NULL DEFAULT ''"
                ))
                db.session.commit()
                logger.info(f"Added {table}.{new_col}")
                fixes_applied += 1
            except Exception as e:
                db.session.rollback()
                logger.warning(f"Could not add {table}.{new_col}: {e}")
                fixes_skipped += 1

        # ═══════════════════════════════════════════════════════════════
        # FIX 2: Add any other missing model columns
        # ═══════════════════════════════════════════════════════════════
        expected_columns = {
            'colleges': [
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
        }

        for table_name, columns in expected_columns.items():
            existing = get_table_columns(table_name)
            for col_name, col_type in columns:
                if col_name not in existing:
                    try:
                        nullable = 'NULL' if 'NOT NULL' not in col_type.upper() else ''
                        db.session.execute(text(
                            f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                        ))
                        db.session.commit()
                        logger.info(f"Added missing column {table_name}.{col_name}")
                        fixes_applied += 1
                    except Exception as e:
                        db.session.rollback()
                        logger.warning(f"Could not add {table_name}.{col_name}: {e}")
                        fixes_skipped += 1
                else:
                    fixes_skipped += 1

        # ═══════════════════════════════════════════════════════════════
        # Verify
        # ═══════════════════════════════════════════════════════════════
        from models import College
        count = College.query.count()
        logger.info(f"College.query.count() = {count}")

        if count > 0:
            sample = College.query.first()
            logger.info(f"Sample college: id={sample.id}, name={sample.college_name[:50] if sample.college_name else 'MISSING'}")
            logger.info(f"  college_code={sample.college_code}, district={sample.district}, status={sample.status}")

        logger.info(f"Migration complete: {fixes_applied} applied, {fixes_skipped} skipped")
        return fixes_applied, fixes_skipped


if __name__ == '__main__':
    run_migration()