"""
Migration to fix schema constraints in the colleges table.

The legacy DB schema has NOT NULL on several columns (location, branch, fees,
placement_rate, nirf_rank, rating) but the SQLAlchemy model defines them as
nullable=True. This causes NotNullViolation when the self-healing service
tries to auto-create a new College with only code + name + status.

Run: python migrate_fix_schema_constraints.py
"""
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('migrate_schema')

os.environ['FLASK_ENV'] = 'development'

from app import app
from database import db


def fix_colleges_not_null():
    """Make legacy NOT NULL columns nullable to match the SQLAlchemy model."""
    from sqlalchemy import text

    with app.app_context():
        logger.info("Checking colleges table constraints...")

        # Check which columns are currently NOT NULL
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        cols = inspector.get_columns('colleges', schema='public')

        not_null_cols = [c['name'] for c in cols if c['nullable'] is False and c['name'] not in ('id', 'college_name')]

        if not not_null_cols:
            logger.info("No columns to fix — all are already nullable (except PK and college_name).")
            return

        logger.info(f"Columns with NOT NULL constraint to fix: {not_null_cols}")

        for col_name in not_null_cols:
            try:
                logger.info(f"  Altering column '{col_name}' to allow NULL...")
                db.session.execute(
                    text(f'ALTER TABLE public.colleges ALTER COLUMN "{col_name}" DROP NOT NULL')
                )
                db.session.commit()
                logger.info(f"  ✓ Column '{col_name}' is now nullable.")
            except Exception as e:
                db.session.rollback()
                logger.error(f"  ✗ Failed to alter '{col_name}': {e}")

        # Verify
        inspector = inspect(db.engine)
        cols = inspector.get_columns('colleges', schema='public')
        remaining_not_null = [c['name'] for c in cols if c['nullable'] is False and c['name'] not in ('id', 'college_name')]
        if remaining_not_null:
            logger.warning(f"Still NOT NULL: {remaining_not_null}")
        else:
            logger.info("✓ All columns now match model definition.")


def seed_common_branches():
    """Seed the branches table with common engineering branches if it's empty."""
    from models import Branch

    with app.app_context():
        count = Branch.query.count()
        if count > 0:
            logger.info(f"Branches table already has {count} rows — skipping seed.")
            return

        logger.info("Branches table is empty. Seeding common branches...")

        common_branches = [
            ('COMP', 'Computer Engineering'),
            ('IT', 'Information Technology'),
            ('MECH', 'Mechanical Engineering'),
            ('CIVIL', 'Civil Engineering'),
            ('ELECTRICAL', 'Electrical Engineering'),
            ('EXTC', 'Electronics & Telecommunication Engineering'),
            ('ELEC', 'Electronics Engineering'),
            ('CHEM', 'Chemical Engineering'),
            ('AIDS', 'Artificial Intelligence & Data Science'),
            ('INSTRU', 'Instrumentation Engineering'),
            ('FOOD', 'Food Technology'),
            ('TEXT', 'Textile Engineering'),
            ('PROD', 'Production Engineering'),
            ('AI', 'Artificial Intelligence and Machine Learning'),
        ]

        for code, name in common_branches:
            try:
                branch = Branch(branch_code=code, branch_name=name)
                db.session.add(branch)
                db.session.flush()
                logger.info(f"  Created branch: {code} — {name}")
            except Exception as e:
                db.session.rollback()
                logger.warning(f"  Skipped branch {code}: {e}")

        db.session.commit()
        logger.info(f"✓ Seeded {len(common_branches)} common branches.")


def fix_cutoffs_defaults():
    """Ensure cutoffs table has proper defaults for all columns."""
    from sqlalchemy import text

    with app.app_context():
        logger.info("Checking cutoffs table constraints...")

        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        cols = inspector.get_columns('cutoffs', schema='public')

        # Check if gender has a proper default
        for c in cols:
            if c['name'] == 'gender' and c['default'] is None:
                try:
                    logger.info("  Adding default 'Gender-Neutral' to gender column...")
                    db.session.execute(
                        text("ALTER TABLE public.cutoffs ALTER COLUMN gender SET DEFAULT 'Gender-Neutral'")
                    )
                    db.session.commit()
                    logger.info("  ✓ gender default set.")
                except Exception as e:
                    db.session.rollback()
                    logger.warning(f"  Could not set gender default: {e}")

            # Make sure seat_type is nullable
            if c['name'] == 'seat_type' and c['nullable'] is False:
                try:
                    logger.info("  Making seat_type nullable...")
                    db.session.execute(
                        text("ALTER TABLE public.cutoffs ALTER COLUMN seat_type DROP NOT NULL")
                    )
                    db.session.commit()
                    logger.info("  ✓ seat_type is now nullable.")
                except Exception as e:
                    db.session.rollback()
                    logger.warning(f"  Could not alter seat_type: {e}")

        logger.info("✓ Cutoffs table constraints verified.")


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("SCHEMA CONSTRAINT FIX MIGRATION")
    logger.info("=" * 60)

    fix_colleges_not_null()
    seed_common_branches()
    fix_cutoffs_defaults()

    logger.info("\n" + "=" * 60)
    logger.info("MIGRATION COMPLETE")
    logger.info("=" * 60)