"""
Migration Script: Unify Cutoff Architecture

Migrates from dual-table (cap_cutoffs + college_cutoffs) to single table
(college_cutoffs) as the single source of truth.

Steps:
  1. Add new columns to college_cutoffs (gender, opening_rank, closing_rank,
     seats_available, branch, exam_type) if missing.
  2. Add PostgreSQL indexes.
  3. Migrate data from cap_cutoffs → college_cutoffs.
  4. Verify migration.

Usage:
    python migrate_unified_cutoffs.py

Run with app context:
    from app import app
    with app.app_context():
        from migrate_unified_cutoffs import run_migration
        run_migration()
"""
import logging
from datetime import datetime
from sqlalchemy import text, inspect

from database import db

logger = logging.getLogger(__name__)

# ── Migration Steps ────────────────────────────────────────────────────────────

MIGRATION_SQL = [
    # Step 1: Add gender column (if not exists)
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='college_cutoffs' AND column_name='gender'
        ) THEN
            ALTER TABLE college_cutoffs ADD COLUMN gender VARCHAR(10) DEFAULT 'Gender-Neutral' NOT NULL;
        END IF;
    END $$;
    """,

    # Step 2: Add opening_rank
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='college_cutoffs' AND column_name='opening_rank'
        ) THEN
            ALTER TABLE college_cutoffs ADD COLUMN opening_rank INTEGER;
        END IF;
    END $$;
    """,

    # Step 3: Add closing_rank
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='college_cutoffs' AND column_name='closing_rank'
        ) THEN
            ALTER TABLE college_cutoffs ADD COLUMN closing_rank INTEGER;
        END IF;
    END $$;
    """,

    # Step 4: Add seats_available
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='college_cutoffs' AND column_name='seats_available'
        ) THEN
            ALTER TABLE college_cutoffs ADD COLUMN seats_available INTEGER;
        END IF;
    END $$;
    """,

    # Step 5: Add branch (denormalized alias for course_name)
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='college_cutoffs' AND column_name='branch'
        ) THEN
            ALTER TABLE college_cutoffs ADD COLUMN branch VARCHAR(200);
        END IF;
    END $$;
    """,

    # Step 6: Add exam_type discriminator
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='college_cutoffs' AND column_name='exam_type'
        ) THEN
            ALTER TABLE college_cutoffs ADD COLUMN exam_type VARCHAR(20) DEFAULT 'MHT-CET' NOT NULL;
        END IF;
    END $$;
    """,
]

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_cc_course_code ON college_cutoffs(course_code)",
    "CREATE INDEX IF NOT EXISTS idx_cc_gender ON college_cutoffs(gender)",
    "CREATE INDEX IF NOT EXISTS idx_cc_exam_type ON college_cutoffs(exam_type)",
    "CREATE INDEX IF NOT EXISTS idx_cc_year_round_code ON college_cutoffs(year, round, college_code)",
    "CREATE INDEX IF NOT EXISTS idx_cc_code_course ON college_cutoffs(college_code, course_code)",
    "CREATE INDEX IF NOT EXISTS idx_cc_year_code ON college_cutoffs(year, college_code)",
]


def is_postgresql() -> bool:
    """Check if we're connected to PostgreSQL (Neon)."""
    return 'postgresql' in str(db.engine.url)


def run_schema_migration():
    """Add new columns and indexes to college_cutoffs table."""
    logger.info("=== Step 1: Schema Migration ===")
    logger.info(f"Database: {db.engine.url}")

    if not is_postgresql():
        logger.warning("SQLite detected — skipping PostgreSQL-specific SQL constructs")
        # For SQLite, just ensure columns exist via SQLAlchemy create_all
        from models import CollegeCutoff
        db.create_all()
        logger.info("SQLite: tables created/verified via SQLAlchemy")
        return True

    try:
        # Run each ALTER TABLE statement
        for sql in MIGRATION_SQL:
            try:
                db.session.execute(text(sql))
                db.session.commit()
                logger.info(f"  ✓ Column migration applied")
            except Exception as e:
                db.session.rollback()
                logger.warning(f"  - Skipped (may already exist): {e}")

        # Run each CREATE INDEX statement
        for sql in INDEX_SQL:
            try:
                db.session.execute(text(sql))
                db.session.commit()
                logger.info(f"  ✓ Index created/verified")
            except Exception as e:
                db.session.rollback()
                logger.warning(f"  - Index skipped: {e}")

        logger.info("Schema migration complete!")
        return True

    except Exception as e:
        db.session.rollback()
        logger.error(f"Schema migration failed: {e}")
        return False


def run_data_migration():
    """Migrate data from cap_cutoffs → college_cutoffs."""
    from models import CAPCutoff, CollegeCutoff, College

    logger.info("=== Step 2: Data Migration ===")
    
    cap_count = CAPCutoff.query.count()
    logger.info(f"Records in cap_cutoffs: {cap_count}")

    if cap_count == 0:
        logger.info("No data to migrate from cap_cutoffs.")
        return True

    college_code_map = {}
    colleges = College.query.all()
    for c in colleges:
        college_code_map[c.id] = str(c.id).zfill(4)

    migrated = 0
    skipped = 0
    errors = 0

    batch = []
    batch_size = 500

    for cutoff in CAPCutoff.query.yield_per(batch_size):
        try:
            # Check if duplicate already exists in college_cutoffs
            existing = CollegeCutoff.query.filter_by(
                year=cutoff.year,
                round=cutoff.round_number or 1,
                college_code=cutoff.college_code or college_code_map.get(cutoff.college_id, '0000'),
                course_code=f"{cutoff.college_code or '0000'}000",
                category=cutoff.category,
            ).first()

            if existing:
                skipped += 1
                continue

            # Create new CollegeCutoff record
            new_record = CollegeCutoff(
                year=cutoff.year,
                round=cutoff.round_number or 1,
                college_code=cutoff.college_code or college_code_map.get(cutoff.college_id, '0000'),
                college_name=cutoff.college_name or '',
                course_code=f"{cutoff.college_code or '0000'}000",
                course_name=cutoff.branch or '',
                branch=cutoff.branch or '',
                category=cutoff.category,
                gender=cutoff.gender or 'Gender-Neutral',
                percentile=cutoff.cutoff_percentile,
                rank=cutoff.closing_rank,
                opening_rank=cutoff.opening_rank,
                closing_rank=cutoff.closing_rank,
                seats_available=cutoff.seats_available,
                exam_type='MHT-CET',
                approval_status='approved',  # Migrated data is trusted
                imported_at=cutoff.imported_at or datetime.utcnow(),
            )
            batch.append(new_record)

            if len(batch) >= batch_size:
                db.session.bulk_save_objects(batch)
                db.session.commit()
                migrated += len(batch)
                logger.info(f"  Migrated {migrated} records...")
                batch = []

        except Exception as e:
            db.session.rollback()
            logger.warning(f"  Error migrating record: {e}")
            errors += 1

    # Flush remaining batch
    if batch:
        try:
            db.session.bulk_save_objects(batch)
            db.session.commit()
            migrated += len(batch)
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error flushing batch: {e}")

    logger.info(f"Data migration complete: {migrated} migrated, {skipped} skipped, {errors} errors")
    return migrated > 0 or skipped > 0


def verify_migration():
    """Verify migration results."""
    from models import CollegeCutoff

    logger.info("=== Step 3: Verification ===")

    total = CollegeCutoff.query.count()
    with_gender = CollegeCutoff.query.filter(CollegeCutoff.gender.isnot(None)).count()
    with_exam = CollegeCutoff.query.filter(CollegeCutoff.exam_type.isnot(None)).count()
    distinct_years = [r[0] for r in db.session.query(CollegeCutoff.year).distinct().all()]
    distinct_exam_types = [r[0] for r in db.session.query(CollegeCutoff.exam_type).distinct().all()]

    logger.info(f"Total records in college_cutoffs: {total}")
    logger.info(f"Records with gender: {with_gender}")
    logger.info(f"Records with exam_type: {with_exam}")
    logger.info(f"Years present: {sorted(distinct_years)}")
    logger.info(f"Exam types: {distinct_exam_types}")

    # Verify indexes exist (PostgreSQL only)
    if is_postgresql():
        try:
            result = db.session.execute(text("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'college_cutoffs'
                ORDER BY indexname
            """)).fetchall()
            indexes = [r[0] for r in result]
            logger.info(f"Indexes on college_cutoffs: {indexes}")
        except Exception as e:
            logger.warning(f"Could not verify indexes: {e}")

    return {
        'total_records': total,
        'with_gender': with_gender,
        'with_exam_type': with_exam,
        'years': sorted(distinct_years),
        'exam_types': distinct_exam_types,
    }


def run_migration():
    """Run the full migration pipeline."""
    logger.info("=" * 60)
    logger.info("Cutoff Architecture Unification Migration")
    logger.info("=" * 60)

    # Step 1: Schema changes
    if not run_schema_migration():
        logger.error("Schema migration failed — aborting")
        return False

    # Step 2: Data migration
    data_result = run_data_migration()
    
    # Step 3: Verify
    stats = verify_migration()
    logger.info("=" * 60)
    logger.info(f"Migration complete! Stats: {stats}")
    logger.info("=" * 60)

    return True


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    from app import app
    with app.app_context():
        run_migration()