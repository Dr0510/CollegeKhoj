"""
Diagnostic script for PDF Import Pipeline — CollegeKhoj Admin v2.

Run this script to:
1. Extract PDF data
2. Validate extracted rows with full logging
3. Verify all FK lookups (college, branch, admission_type, academic_year, cap_round, category)
4. Attempt batch insert with per-row savepoints
5. Produce a final failure report

Usage:
    python run_diagnostics.py <job_id>
    python run_diagnostics.py --file <path_to_pdf> --admission-type-id <id> --academic-year-id <id> --cap-round-id <id>
"""
import sys
import os
import json
import logging
import traceback
from datetime import datetime

# ── Logging Setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger('diagnostics')

# Suppress noisy loggers
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger('pdfminer').setLevel(logging.WARNING)

# ── Ensure app context ─────────────────────────────────────────────────────
os.environ['FLASK_ENV'] = 'development'

from database import db
from app import app


def check_fk_references(admission_type_id, academic_year_id, cap_round_id):
    """Check that the FK references actually exist in the database."""
    from models import AdmissionType, AcademicYear, CapRound

    issues = []

    at = AdmissionType.query.get(admission_type_id)
    if not at:
        issues.append(f"AdmissionType id={admission_type_id} NOT FOUND")
    else:
        logger.info(f"  ✓ AdmissionType: id={at.id} code={at.code} name={at.name}")

    ay = AcademicYear.query.get(academic_year_id)
    if not ay:
        issues.append(f"AcademicYear id={academic_year_id} NOT FOUND")
    else:
        logger.info(f"  ✓ AcademicYear: id={ay.id} year={ay.academic_year}")

    cr = CapRound.query.get(cap_round_id)
    if not cr:
        issues.append(f"CapRound id={cap_round_id} NOT FOUND")
    else:
        logger.info(f"  ✓ CapRound: id={cr.id} name={cr.name}")

    return issues


def check_college_lookup(college_code, college_name):
    """Check college lookup — verify exact vs fuzzy matching."""
    from models import College

    logger.info(f"  Looking up college: code={college_code!r} name={college_name!r}")

    # 1. Exact code match
    college = College.query.filter_by(college_code=college_code).first()
    if college:
        logger.info(f"    → Found by code: id={college.id} name={college.college_name!r}")
        return college.id

    # 2. Name match (case-insensitive exact)
    if college_name:
        college = College.query.filter(
            College.college_name.ilike(college_name)
        ).first()
        if college:
            logger.info(f"    → Found by name (exact): id={college.id} name={college.college_name!r}")
            return college.id

        # 3. Fuzzy name match
        all_colleges = College.query.all()
        for c in all_colleges:
            db_name = c.college_name.lower().replace(" ", "").replace(",", "").replace(".", "")
            pdf_name = college_name.lower().replace(" ", "").replace(",", "").replace(".", "")
            if db_name == pdf_name or db_name in pdf_name or pdf_name in db_name:
                logger.info(f"    → Found by name (fuzzy): id={c.id} db_name={c.college_name!r}")
                return c.id

        logger.warning(f"    ✗ NOT FOUND — college_code={college_code!r} college_name={college_name!r}")
        logger.warning(f"      DB has {College.query.count()} colleges total")

    return None


def check_branch_lookup(branch_name):
    """Check branch lookup with normalization."""
    from models import Branch
    from admin.branch_normalizer import normalize_branch

    canonical = normalize_branch(branch_name)
    logger.info(f"  Looking up branch: raw={branch_name!r} canonical={canonical!r}")

    # 1. Exact match
    branch = Branch.query.filter(
        Branch.branch_name.ilike(canonical)
    ).first()
    if branch:
        logger.info(f"    → Found by exact: id={branch.id} name={branch.branch_name!r}")
        return branch.id

    # 2. Substring match
    all_branches = Branch.query.all()
    for b in all_branches:
        db_lower = b.branch_name.lower()
        canonical_lower = canonical.lower()
        if db_lower in canonical_lower or canonical_lower in db_lower:
            logger.info(f"    → Found by substring: id={b.id} db_name={b.branch_name!r}")
            return b.id

    logger.warning(f"    ✗ NOT FOUND — branch={branch_name!r} canonical={canonical!r}")
    logger.warning(f"      DB has {Branch.query.count()} branches total")
    return None


def diagnostic_import_pipeline(
    filepath,
    admission_type_id,
    academic_year_id,
    cap_round_id,
    job_id=None,
):
    """Run full diagnostic on the import pipeline."""
    print("\n" + "=" * 80)
    print(f"DIAGNOSTIC REPORT — Import Pipeline")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 80)

    step = 1

    # ── Step 1: Check file ────────────────────────────────────────────────
    print(f"\n{'─'*40}")
    print(f"[Step {step}] File Check")
    print(f"{'─'*40}")
    step += 1

    if not os.path.exists(filepath):
        print(f"  ✗ FILE NOT FOUND: {filepath}")
        return
    print(f"  ✓ File exists: {filepath}")
    print(f"  ✓ File size: {os.path.getsize(filepath)} bytes")

    # ── Step 2: Check FK references ───────────────────────────────────────
    print(f"\n{'─'*40}")
    print(f"[Step {step}] FK Reference Check")
    print(f"{'─'*40}")
    step += 1

    fk_issues = check_fk_references(admission_type_id, academic_year_id, cap_round_id)
    if fk_issues:
        for issue in fk_issues:
            print(f"  ✗ {issue}")
        print(f"\n  ❌ FK REFERENCES MISSING — cannot proceed with import")
        return
    else:
        print(f"  ✓ All FK references valid")

    # ── Step 3: Extract PDF ───────────────────────────────────────────────
    print(f"\n{'─'*40}")
    print(f"[Step {step}] PDF Extraction")
    print(f"{'─'*40}")
    step += 1

    from admin.pdf_engine_v2 import extract_pdf

    extraction = extract_pdf(filepath, os.path.basename(filepath))
    if extraction.get('error'):
        print(f"  ✗ Extraction failed: {extraction['error']}")
        return

    rows = extraction.get('rows', [])
    total_pages = extraction.get('total_pages', 0)

    print(f"  ✓ Pages: {total_pages}")
    print(f"  ✓ Rows extracted: {len(rows)}")
    print(f"  ✓ Method: {extraction.get('method', 'N/A')}")
    print(f"  ✓ Confidence: {extraction.get('confidence', 'N/A')}")

    if not rows:
        print(f"  ✗ No rows extracted — nothing to import")
        return

    # Print first 5 rows for inspection
    print(f"\n  First 5 rows:")
    for i, row in enumerate(rows[:5]):
        print(f"    [{i+1}] college_code={row.get('college_code','')!r} "
              f"college_name={row.get('college_name','')!r} "
              f"course_name={row.get('course_name','')!r} "
              f"category={row.get('category','')!r} "
              f"percentile={row.get('percentile')} "
              f"rank={row.get('rank')}")

    # ── Step 4: Validate rows (with self-healing) ─────────────────────────
    print(f"\n{'─'*40}")
    print(f"[Step {step}] Row Validation & Master Data Lookup")
    print(f"{'─'*40}")
    step += 1

    from admin.validation_engine_v2 import validate_rows

    # First, do manual lookups for the first 5 rows to verify
    print(f"\n  Manual lookup verification (first 5 rows):")
    manual_failures = 0
    for i, row in enumerate(rows[:5]):
        print(f"\n  Row {i+1}:")
        college_id = check_college_lookup(
            row.get('college_code', ''),
            row.get('college_name', '')
        )
        branch_id = check_branch_lookup(
            row.get('course_name', row.get('branch', ''))
        )
        if college_id is None:
            manual_failures += 1
        if branch_id is None:
            manual_failures += 1

    # Now run full validation
    print(f"\n  Running full validation via validate_rows()...")
    validation = validate_rows(
        rows,
        admission_type_id=admission_type_id,
        academic_year_id=academic_year_id,
        cap_round_id=cap_round_id,
    )

    print(f"\n  Validation Results:")
    print(f"    Total rows: {validation.total}")
    print(f"    Valid rows: {validation.valid}")
    print(f"    Invalid rows: {validation.invalid}")
    print(f"    Duplicate rows: {validation.duplicates}")
    print(f"    Auto-created colleges: {validation.healed_colleges}")
    print(f"    Auto-created branches: {validation.healed_branches}")

    # ── Step 5: Print invalid row details ──────────────────────────────────
    if validation.invalid_rows:
        print(f"\n{'─'*40}")
        print(f"[Step {step}.a] Invalid Row Details")
        print(f"{'─'*40}")
        step_tmp = step
        step += 1

        for i, (row, reason) in enumerate(validation.invalid_rows[:20]):
            print(f"\n  Invalid #{i+1}:")
            print(f"    college_code={row.get('college_code','')!r}")
            print(f"    college_name={row.get('college_name','')!r}")
            print(f"    course_name={row.get('course_name','')!r}")
            print(f"    category={row.get('category','')!r}")
            print(f"    percentile={row.get('percentile')}")
            print(f"    rank={row.get('rank')}")
            print(f"    reason={reason}")

    if validation.duplicate_rows:
        print(f"\n  Duplicate rows: {len(validation.duplicate_rows)}")

    # If no valid rows, we can stop here
    if validation.valid == 0:
        print(f"\n  ❌ No valid rows to insert — import would fail")
        print(f"  Final Report:")
        print(f"    Rows Extracted: {len(rows)}")
        print(f"    Rows Attempted: {validation.total}")
        print(f"    Rows Validated: {validation.valid}")
        print(f"    Rows Invalid: {validation.invalid}")
        print(f"    Rows Duplicate: {validation.duplicates}")
        print(f"    Rows to Import: 0")
        return

    # ── Step 6: Attempt batch insert ──────────────────────────────────────
    print(f"\n{'─'*40}")
    print(f"[Step {step}] Batch Insert Attempt")
    print(f"{'─'*40}")
    step += 1

    from admin.bulk_import_engine import BulkImportEngine

    # Create a temporary engine to test batch insert
    # If no real job_id is provided, set upload_job_id to None to avoid FK issues
    engine_job_id = job_id if job_id and job_id > 0 else None
    engine = BulkImportEngine(
        db_session=db.session,
        job_id=engine_job_id,
        filepath=filepath,
        admission_type_id=admission_type_id,
        academic_year_id=academic_year_id,
        cap_round_id=cap_round_id,
    )

    print(f"\n  Attempting batch insert of {len(validation.valid_rows)} valid rows...")
    print(f"  Using per-row savepoints (begin_nested) for isolation.")

    try:
        engine._batch_insert(validation.valid_rows)
        db.session.commit()
        print(f"  ✓ Batch insert committed successfully")
    except Exception as e:
        db.session.rollback()
        print(f"  ✗ Batch insert failed: {e}")
        traceback.print_exc()

    # ── Step 7: Final Report ──────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"FINAL REPORT")
    print(f"{'='*80}")
    print(f"\n  Pipeline Summary:")
    print(f"    Pages Processed: {total_pages}")
    print(f"    Rows Extracted:  {len(rows)}")
    print(f"    Rows Attempted:  {engine.rows_processed}")
    print(f"    Rows Imported:   {engine.rows_imported}")
    print(f"    Rows Duplicate:  {engine.rows_duplicate}")
    print(f"    Rows Failed:     {engine.rows_invalid}")

    # Count failures by reason
    fk_errors = sum(1 for err in engine.error_rows if err.get('row', {}).get('college_id') is None)
    fk_errors += sum(1 for err in engine.error_rows if 'foreign key' in str(err.get('reason', '')).lower())
    missing_college = sum(1 for err in engine.error_rows if 'college' in str(err.get('reason', '')).lower() and ('miss' in str(err.get('reason', '')).lower() or 'not found' in str(err.get('reason', '')).lower()))
    missing_branch = sum(1 for err in engine.error_rows if 'branch' in str(err.get('reason', '')).lower() and ('miss' in str(err.get('reason', '')).lower() or 'not found' in str(err.get('reason', '')).lower()))
    dup_errors = sum(1 for err in engine.error_rows if 'duplicate' in str(err.get('reason', '')).lower() or 'uq_' in str(err.get('reason', '')).lower() or 'unique' in str(err.get('reason', '')).lower())
    validation_errs = sum(1 for err in engine.error_rows if 'valid' in str(err.get('reason', '')).lower() or 'invalid' in str(err.get('reason', '')).lower())

    print(f"\n  Failure Breakdown:")
    print(f"    Foreign Key Errors:    {fk_errors}")
    print(f"    Missing College:       {missing_college}")
    print(f"    Missing Branch:        {missing_branch}")
    print(f"    Duplicate Key Errors:  {dup_errors}")
    print(f"    Validation Errors:     {validation_errs}")
    print(f"    Other Errors:          {len(engine.error_rows) - fk_errors - missing_college - missing_branch - dup_errors - validation_errs}")

    # First 20 failed rows
    if engine.error_rows:
        print(f"\n  First {min(20, len(engine.error_rows))} Failed Rows:")
        for i, err in enumerate(engine.error_rows[:20]):
            row = err.get('row', {})
            print(f"\n    Failed #{i+1}:")
            print(f"      college_id={row.get('college_id')} branch_id={row.get('branch_id')}")
            print(f"      category={row.get('category')} seat_type={row.get('seat_type')}")
            print(f"      percentile={row.get('cutoff_percentile')} rank={row.get('cutoff_rank')}")
            print(f"      reason={err.get('reason')}")

    print(f"\n{'='*80}")
    print(f"DIAGNOSTIC COMPLETE")
    print(f"{'='*80}\n")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    with app.app_context():
        if sys.argv[1] == '--file':
            # Direct file mode
            filepath = sys.argv[2]
            try:
                at_id = int(sys.argv[3])
                ay_id = int(sys.argv[4])
                cr_id = int(sys.argv[5])
            except (IndexError, ValueError):
                print("Usage: python run_diagnostics.py --file <path> <admission_type_id> <academic_year_id> <cap_round_id>")
                return

            diagnostic_import_pipeline(
                filepath=filepath,
                admission_type_id=at_id,
                academic_year_id=ay_id,
                cap_round_id=cr_id,
            )
        else:
            # Job mode — look up the job
            job_id = int(sys.argv[1])
            from models import UploadJob

            job = db.session.get(UploadJob, job_id)
            if not job:
                print(f"Job {job_id} not found")
                return

            print(f"Job: {job.filename}")
            print(f"  Status: {job.status}")
            print(f"  Admission Type ID: {job.admission_type_id}")
            print(f"  Academic Year ID: {job.academic_year_id}")
            print(f"  CAP Round ID: {job.cap_round_id}")
            print(f"  File: {job.stored_path}")

            if not job.stored_path or not os.path.exists(job.stored_path):
                print(f"File not found: {job.stored_path}")
                return

            diagnostic_import_pipeline(
                filepath=job.stored_path,
                admission_type_id=job.admission_type_id,
                academic_year_id=job.academic_year_id,
                cap_round_id=job.cap_round_id,
                job_id=job.id,
            )


if __name__ == '__main__':
    main()