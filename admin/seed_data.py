"""Seed reference data for CollegeKhoj admin.

Seeds admission_types, academic_years, cap_rounds, and categories tables.
Idempotent — safe to run on every startup.
"""
import logging
from database import db

logger = logging.getLogger(__name__)


def seed_reference_data():
    """Seed all reference tables if they are empty.

    Call this function once during app startup.
    Only seeds tables that have zero rows — never overwrites existing data.
    """
    from models import AdmissionType, AcademicYear, CapRound, Category

    seeded = 0

    # ── Admission Types ──
    try:
        if AdmissionType.query.count() == 0:
            types = [
                ('ENGG', 'Engineering'),
                ('DSE', 'Direct Second Year Engineering'),
                ('POLY', 'Polytechnic Diploma'),
            ]
            for code, name in types:
                db.session.add(AdmissionType(code=code, name=name))
                seeded += 1
            db.session.commit()
            logger.info(f"Seeded {len(types)} admission types")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Seeding admission types skipped: {e}")

    # ── Academic Years ──
    try:
        if AcademicYear.query.count() == 0:
            years = ['2024-25', '2025-26', '2026-27', '2027-28']
            for y in years:
                db.session.add(AcademicYear(academic_year=y))
                seeded += 1
            db.session.commit()
            logger.info(f"Seeded {len(years)} academic years")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Seeding academic years skipped: {e}")

    # ── CAP Rounds ──
    try:
        if CapRound.query.count() == 0:
            rounds = ['Round I', 'Round II', 'Round III']
            for r in rounds:
                db.session.add(CapRound(name=r))
                seeded += 1
            db.session.commit()
            logger.info(f"Seeded {len(rounds)} CAP rounds")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Seeding CAP rounds skipped: {e}")

    # ── Categories ──
    try:
        if Category.query.count() == 0:
            cats = [
                ('OPEN', 'Open (General) Category', 'active'),
                ('OBC', 'Other Backward Class', 'active'),
                ('SC', 'Scheduled Caste', 'active'),
                ('ST', 'Scheduled Tribe', 'active'),
                ('EWS', 'Economically Weaker Section', 'active'),
                ('TFWS', 'Tuition Fee Waiver Scheme', 'active'),
                ('NT', 'Nomadic Tribes', 'active'),
                ('SBC', 'Special Backward Class', 'active'),
                ('SEBC', 'Socially and Educationally Backward Class', 'active'),
                ('PWD', 'Persons with Disabilities', 'active'),
                ('DEF', 'Defence', 'active'),
            ]
            for name, desc, status in cats:
                db.session.add(Category(name=name, description=desc, status=status))
                seeded += 1
            db.session.commit()
            logger.info(f"Seeded {len(cats)} categories")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Seeding categories skipped: {e}")

    if seeded > 0:
        logger.info(f"Reference data seeding complete: {seeded} rows added")
    return seeded