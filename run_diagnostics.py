"""
Diagnostic runner: profile CPU time for every function in the PDF import pipeline.

Usage:
    python run_diagnostics.py [path/to/pdf]

If no path given, uses the first PDF found in uploads/cutoffs/.
"""
import os
import sys
import logging
import time

# ── Bootstrap ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from app import app as flask_app
from database import db
from models import UploadJob, AdmissionType, AcademicYear, CapRound
from admin.bulk_import_engine import BulkImportEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("diagnostics")


def pick_test_pdf() -> str:
    cutoff_dir = os.path.join(os.path.dirname(__file__), "uploads", "cutoffs")
    if os.path.isdir(cutoff_dir):
        for f in sorted(os.listdir(cutoff_dir)):
            if f.lower().endswith(".pdf"):
                return os.path.join(cutoff_dir, f)
    raise FileNotFoundError("No PDF found in uploads/cutoffs/")


def resolve_lookup_ids(session) -> tuple:
    at = session.query(AdmissionType).first()
    ay = session.query(AcademicYear).first()
    cr = session.query(CapRound).first()
    if not (at and ay and cr):
        raise RuntimeError("Run `flask seed-data` first to populate lookup tables.")
    return at.id, ay.id, cr.id


def main():
    # 1. Select PDF
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else pick_test_pdf()
    if not os.path.isfile(pdf_path):
        logger.error(f"File not found: {pdf_path}")
        sys.exit(1)
    logger.info(f"PDF: {pdf_path}")

    # 2. Resolve FK IDs (needed by engine but not for diagnostic output)
    with flask_app.app_context():
        with db.session.begin():
            at_id, ay_id, cr_id = resolve_lookup_ids(db.session)
        logger.info(f"Lookup IDs: admission_type={at_id}, year={ay_id}, round={cr_id}")

        # 3. Create a throwaway job row (the engine writes status updates to it)
        job = UploadJob(
            filename=os.path.basename(pdf_path),
            status="PROCESSING",
            admission_type_id=at_id,
            academic_year_id=ay_id,
            cap_round_id=cr_id,
        )
        db.session.add(job)
        db.session.commit()
        logger.info(f"Diagnostic job id={job.id}")

        try:
            # 4. Run the engine — profiling is built into BulkImportEngine + pdf_extractor
            t_start = time.perf_counter()
            engine = BulkImportEngine(
                db_session=db.session,
                job_id=job.id,
                filepath=pdf_path,
                admission_type_id=at_id,
                academic_year_id=ay_id,
                cap_round_id=cr_id,
            )
            summary = engine.run()
            t_total = time.perf_counter() - t_start

            # 5. Print final summary
            print("\n" + "=" * 78)
            print("DIAGNOSTIC SUMMARY")
            print("=" * 78)
            print(f"  File          : {os.path.basename(pdf_path)}")
            print(f"  Job ID        : {job.id}")
            print(f"  Status        : {summary.get('status')}")
            print(f"  Total time    : {t_total:.2f}s")
            print(f"  Rows parsed   : {summary.get('rows_processed', 0)}")
            print(f"  Rows imported : {summary.get('rows_imported', 0)}")
            print(f"  Rows invalid  : {summary.get('rows_invalid', 0)}")
            print(f"  Rows dupe     : {summary.get('rows_duplicate', 0)}")
            print(f"  Pages         : {summary.get('total_pages', 0)}")
            print(f"  SQL SELECTs   : {summary.get('sql_queries', {}).get('SELECT', '?')}")
            print(f"  SQL INSERTs   : {summary.get('sql_queries', {}).get('INSERT', '?')}")
            print(f"  SQL UPDATEs   : {summary.get('sql_queries', {}).get('UPDATE', '?')}")
            print(f"  SQL DB time   : {summary.get('sql_total_time_ms', 0):.0f}ms")
            print("=" * 78)
            print("\nScroll up in this output to see:")
            print("  [CPU_PROFILE]  → phase-level timing from BulkImportEngine")
            print("  [PROFILE]      → pdfplumber per-page timing")
            print("  Drill-Down     → per-function timing from pdf_extractor.py")
            print("  [Validation][PROFILE] → preload + college/branch timings")
            print()

        finally:
            # Cleanup throwaway job
            UploadJob.query.filter(UploadJob.id == job.id).delete()
            db.session.commit()


if __name__ == "__main__":
    main()