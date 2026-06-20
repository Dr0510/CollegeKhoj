"""
Migration: Add missing College schema columns.
Adds the 9 extended columns to the `colleges` table that exist in the
College model but are missing from the actual database schema.

Safe to run multiple times — uses column existence checks.
Preserves all existing data. No DROP/DELETE/RECREATE operations.
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
from models import College


def column_exists(table, column):
    """Check if a column exists in the given table."""
    try:
        inspector = inspect(db.engine)
        cols = [c['name'] for c in inspector.get_columns(table)]
        return column in cols
    except Exception:
        return False


def run_migration():
    """Add missing columns to the colleges table."""
    with app.app_context():
        # ── 1. Verify current state ──
        inspector = inspect(db.engine)
        existing_cols = [c['name'] for c in inspector.get_columns('colleges')]
        logger.info(f"📋 Current `colleges` columns ({len(existing_cols)}): {existing_cols}")

        # ── 2. Define columns to add ──
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

        added = 0
        skipped = 0
        for col_name, col_type in college_columns:
            if column_exists('colleges', col_name):
                logger.info(f"⏭️  Column colleges.{col_name} already exists — skipping")
                skipped += 1
            else:
                try:
                    db.session.execute(text(
                        f"ALTER TABLE colleges ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info(f"✅ Added column colleges.{col_name} ({col_type})")
                    added += 1
                except Exception as e:
                    db.session.rollback()
                    logger.warning(f"⚠️  Could not add colleges.{col_name}: {e}")

        # ── 3. Add index on college_code (if the column was added or already exists) ──
        if column_exists('colleges', 'college_code'):
            try:
                db.session.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_colleges_code ON colleges(college_code)"
                ))
                logger.info("✅ Index idx_colleges_code already exists or was created")
            except Exception as e:
                logger.warning(f"⚠️  Could not create index: {e}")

        db.session.commit()

        # ── 4. Verify final state ──
        inspector = inspect(db.engine)
        final_cols = [c['name'] for c in inspector.get_columns('colleges')]
        logger.info(f"📋 Final `colleges` columns ({len(final_cols)}): {final_cols}")

        # Check all expected columns are present
        expected = ['id', 'college', 'location', 'branch', 'fees',
                    'placement_rate', 'nirf_rank', 'rating',
                    'college_code', 'college_type', 'district', 'university',
                    'address', 'website', 'naac_grade', 'is_autonomous', 'status']
        missing = [c for c in expected if c not in final_cols]
        if missing:
            logger.error(f"❌ Still missing columns: {missing}")
        else:
            logger.info(f"✅ All {len(expected)} columns present in colleges table")

        logger.info(f"✅ Migration complete: {added} added, {skipped} skipped, {len(final_cols)} total columns")
        return added, skipped, len(final_cols)


if __name__ == '__main__':
    run_migration()