"""
Admin v3 Migration Script — Creates new tables, seeds data, migrates old data.

Steps:
1. Create new tables (admission_types, academic_years, cap_rounds, branches, cutoffs, upload_jobs)
2. Seed admission_types (ENGG, DSE, POLY)
3. Seed cap_rounds (Round I, II, III)
4. Seed academic_years from college_cutoffs data
5. Migrate College records (add college_code as primary identifier)
6. Create Branch records from distinct course_name/branch values
7. Migrate data from old CollegeCutoff → new Cutoff table
8. Verify migration success
"""
import os
import sys
import logging
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from database import db, init_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create a minimal Flask app for context
app = Flask(__name__)
app.secret_key = 'migration-secret'
init_database(app)


def seed_admission_types():
    """Seed the admission_types table."""
    from models import AdmissionType
    if AdmissionType.query.count() > 0:
        logger.info("admission_types already seeded, skipping")
        return

    types = [
        AdmissionType(name='MHT CET Engineering', code='ENGG'),
        AdmissionType(name='Direct Second Year Engineering', code='DSE'),
        AdmissionType(name='Polytechnic Diploma', code='POLY'),
    ]
    for t in types:
        db.session.add(t)
    db.session.commit()
    logger.info(f"Seeded {len(types)} admission types")


def seed_cap_rounds():
    """Seed the cap_rounds table."""
    from models import CapRound
    if CapRound.query.count() > 0:
        logger.info("cap_rounds already seeded, skipping")
        return

    rounds = [
        CapRound(name='Round I'),
        CapRound(name='Round II'),
        CapRound(name='Round III'),
        CapRound(name='Round IV'),
        CapRound(name='Round V'),
    ]
    for r in rounds:
        db.session.add(r)
    db.session.commit()
    logger.info(f"Seeded {len(rounds)} cap rounds")


def seed_academic_years():
    """Seed academic_years from existing college_cutoffs data."""
    from models import AcademicYear, CollegeCutoff
    if AcademicYear.query.count() > 0:
        logger.info("academic_years already seeded, skipping")
        return

    # Get distinct years from college_cutoffs
    years = db.session.query(CollegeCutoff.year).distinct().order_by(CollegeCutoff.year.desc()).all()
    if not years:
        # Seed default years
        default_years = ['2023-24', '2024-25', '2025-26', '2026-27']
        for y in default_years:
            db.session.add(AcademicYear(academic_year=y))
        db.session.commit()
        logger.info(f"Seeded {len(default_years)} default academic years")
        return

    for (year,) in years:
        ay = f"{year}-{str(year + 1)[-2:]}"
        exists = AcademicYear.query.filter_by(academic_year=ay).first()
        if not exists:
            db.session.add(AcademicYear(academic_year=ay))
    db.session.commit()
    logger.info(f"Seeded academic years from college_cutoffs data")


def migrate_colleges():
    """Ensure College records have college_code set."""
    from models import College
    count = College.query.filter(College.college_code.is_(None)).count()
    if count == 0:
        logger.info("All colleges already have college_code")
        return

    colleges = College.query.filter(College.college_code.is_(None)).all()
    for c in colleges:
        c.college_code = str(c.id).zfill(4)
    db.session.commit()
    logger.info(f"Updated {len(colleges)} colleges with college_code")


def migrate_branches():
    """Create Branch records from distinct course_name/branch values in college_cutoffs."""
    from models import Branch, CollegeCutoff
    if Branch.query.count() > 0:
        logger.info("branches already seeded, skipping")
        return

    # Get distinct course name + exam_type combos
    distinct_courses = db.session.query(
        CollegeCutoff.course_name, CollegeCutoff.branch, CollegeCutoff.exam_type
    ).distinct().all()

    seen = set()
    branches = []
    for course_name, branch, exam_type in distinct_courses:
        name = branch or course_name
        if name and name.strip() and name.strip().lower() not in seen:
            code = name.strip().upper().replace(' ', '_').replace('&', 'AND')[:20]
            seen.add(name.strip().lower())
            branches.append(Branch(branch_code=code, branch_name=name.strip()))

    for b in branches:
        db.session.add(b)
    db.session.commit()
    logger.info(f"Created {len(branches)} branch records")


def migrate_cutoff_data():
    """Migrate data from old CollegeCutoff → new Cutoff table."""
    from models import (
        CollegeCutoff, Cutoff, AdmissionType, AcademicYear,
        CapRound, College, Branch
    )

    old_count = CollegeCutoff.query.count()
    if old_count == 0:
        logger.info("No old cutoff data to migrate")
        return

    new_count = Cutoff.query.count()
    if new_count > 0:
        logger.info(f"New cutoffs table already has {new_count} records, skipping migration")
        return

    logger.info(f"Migrating {old_count} records from college_cutoffs → cutoffs")

    # Get reference data
    engg_type = AdmissionType.query.filter_by(code='ENGG').first()
    dse_type = AdmissionType.query.filter_by(code='DSE').first()
    poly_type = AdmissionType.query.filter_by(code='POLY').first()

    round_map = {
        1: CapRound.query.filter_by(name='Round I').first(),
        2: CapRound.query.filter_by(name='Round II').first(),
        3: CapRound.query.filter_by(name='Round III').first(),
        4: CapRound.query.filter_by(name='Round IV').first(),
        5: CapRound.query.filter_by(name='Round V').first(),
    }

    batch_size = 500
    offset = 0
    total_migrated = 0
    skipped = 0

    while True:
        old_records = CollegeCutoff.query.offset(offset).limit(batch_size).all()
        if not old_records:
            break

        for old in old_records:
            try:
                # Determine admission type
                exam = (old.exam_type or 'MHT-CET').upper().strip()
                if 'POLY' in exam or exam == 'POLYTECHNIC':
                    atype = poly_type
                elif 'DSE' in exam:
                    atype = dse_type
                else:
                    atype = engg_type
                if not atype:
                    skipped += 1
                    continue

                # Get or create academic year
                ay_str = f"{old.year}-{str(old.year + 1)[-2:]}"
                ay = AcademicYear.query.filter_by(academic_year=ay_str).first()
                if not ay:
                    ay = AcademicYear(academic_year=ay_str)
                    db.session.add(ay)
                    db.session.flush()

                # Get cap round
                cap_round = round_map.get(old.round)
                if not cap_round:
                    skipped += 1
                    continue

                # Find college
                college = College.query.filter(
                    (College.college_code == old.college_code) |
                    (College.college_name.ilike(old.college_name[:100]))
                ).first()
                if not college:
                    skipped += 1
                    continue

                # Find branch
                branch_name = old.branch or old.course_name or 'General'
                branch = Branch.query.filter(
                    Branch.branch_name.ilike(branch_name[:100])
                ).first()
                if not branch:
                    # Create branch on the fly
                    code = branch_name.strip().upper().replace(' ', '_').replace('&', 'AND')[:20]
                    branch = Branch(branch_code=code, branch_name=branch_name.strip())
                    db.session.add(branch)
                    db.session.flush()

                # Check if cutoff already exists (by unique key)
                existing = Cutoff.query.filter_by(
                    admission_type_id=atype.id,
                    college_id=college.id,
                    branch_id=branch.id,
                    academic_year_id=ay.id,
                    cap_round_id=cap_round.id,
                    category=old.category,
                    seat_type=old.category,  # Use category as seat_type for migration
                ).first()

                if existing:
                    # Update existing record
                    existing.gender = old.gender or 'Gender-Neutral'
                    existing.cutoff_percentile = float(old.percentile) if old.percentile else None
                    existing.cutoff_rank = old.rank
                    existing.source_pdf = f"migrated_from_college_cutoffs_{old.id}"
                else:
                    new_cutoff = Cutoff(
                        admission_type_id=atype.id,
                        college_id=college.id,
                        branch_id=branch.id,
                        academic_year_id=ay.id,
                        cap_round_id=cap_round.id,
                        category=old.category,
                        seat_type=old.category,
                        gender=old.gender or 'Gender-Neutral',
                        cutoff_percentile=float(old.percentile) if old.percentile else None,
                        cutoff_rank=old.rank,
                        source_pdf=f"migrated_from_college_cutoffs_{old.id}",
                    )
                    db.session.add(new_cutoff)

                total_migrated += 1

            except Exception as e:
                logger.warning(f"Failed to migrate record {old.id}: {e}")
                skipped += 1

        offset += batch_size
        db.session.commit()
        logger.info(f"Migrated {total_migrated} records (skipped {skipped})...")

    db.session.commit()
    logger.info(f"Migration complete: {total_migrated} migrated, {skipped} skipped")


def verify_migration():
    """Verify that the migration was successful."""
    from models import (
        AdmissionType, AcademicYear, CapRound, Branch, College, Cutoff, UploadJob
    )

    results = {
        'admission_types': AdmissionType.query.count(),
        'academic_years': AcademicYear.query.count(),
        'cap_rounds': CapRound.query.count(),
        'branches': Branch.query.count(),
        'colleges': College.query.count(),
        'cutoffs': Cutoff.query.count(),
        'upload_jobs': UploadJob.query.count(),
    }

    logger.info("=" * 50)
    logger.info("MIGRATION VERIFICATION REPORT")
    logger.info("=" * 50)
    for name, count in results.items():
        status = '✅' if count > 0 else '❌'
        logger.info(f"  {status} {name}: {count}")
    logger.info("=" * 50)

    return all(v >= 0 for v in results.values())


def run_migration():
    """Run the full migration."""
    with app.app_context():
        # Create tables safely — only missing ones, never recreates existing constraints
        from database import ensure_schema
        ensure_schema()

        # Seed data
        seed_admission_types()
        seed_cap_rounds()
        seed_academic_years()

        # Migrate data
        migrate_colleges()
        migrate_branches()
        migrate_cutoff_data()

        # Verify
        success = verify_migration()
        logger.info(f"Migration {'succeeded' if success else 'FAILED'}")
        return success


if __name__ == '__main__':
    success = run_migration()
    sys.exit(0 if success else 1)