"""
Diagnostic runner: profile CPU time for every function in the PDF import pipeline.

Usage:
    python run_diagnostics.py [path/to/pdf] [workers]

If no path given, uses the first PDF found in uploads/cutoffs/.
If no workers given, defaults to 1 (sequential).
"""
import os
import sys
import logging
import time

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


def run_import(pdf_path: str, max_workers: int = 1) -> dict:
    with flask_app.app_context():
        with db.session.begin():
            at_id, ay_id, cr_id = resolve_lookup_ids(db.session)

        job = UploadJob(
            filename=os.path.basename(pdf_path),
            status="PROCESSING",
            admission_type_id=at_id,
            academic_year_id=ay_id,
            cap_round_id=cr_id,
        )
        db.session.add(job)
        db.session.commit()
        logger.info(f"Diagnostic job id={job.id} workers={max_workers}")

        try:
            t_start = time.perf_counter()
            engine = BulkImportEngine(
                db_session=db.session,
                job_id=job.id,
                filepath=pdf_path,
                admission_type_id=at_id,
                academic_year_id=ay_id,
                cap_round_id=cr_id,
            )
            # Set max_workers if engine supports it
            if hasattr(engine, 'max_workers'):
                engine.max_workers = max_workers
            summary = engine.run()
            t_total = time.perf_counter() - t_start

            return {
                'job_id': job.id,
                'workers': max_workers,
                'total_time': t_total,
                'summary': summary,
            }
        finally:
            UploadJob.query.filter(UploadJob.id == job.id).delete()
            db.session.commit()


def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else pick_test_pdf()
    if not os.path.isfile(pdf_path):
        logger.error(f"File not found: {pdf_path}")
        sys.exit(1)

    worker_counts = [1, 2, 4, 8]
    results = []

    for workers in worker_counts:
        logger.info(f"=== Benchmarking with {workers} worker(s) ===")
        result = run_import(pdf_path, max_workers=workers)
        results.append(result)
        logger.info(
            f"Workers={workers}: total={result['total_time']:.2f}s "
            f"extract_pdf={result['summary'].get('extract_pdf_time', 'N/A')} "
            f"imported={result['summary'].get('rows_imported', 0)}"
        )

    print("\n" + "=" * 80)
    print("PARALLEL EXTRACTION BENCHMARK")
    print("=" * 80)
    print(f"{'Workers':<10} {'Total(s)':<12} {'extract_pdf(s)':<15} {'validate(s)':<12} {'insert(s)':<12} {'imported':<10} {'duplicate':<10}")
    print("-" * 80)
    for r in results:
        s = r['summary']
        print(
            f"{r['workers']:<10} "
            f"{r['total_time']:<12.2f} "
            f"{s.get('extract_pdf_time', 0):<15.2f} "
            f"{s.get('validate_rows_time', 0):<12.2f} "
            f"{s.get('batch_insert_time', 0):<12.2f} "
            f"{s.get('rows_imported', 0):<10} "
            f"{s.get('rows_duplicate', 0):<10}"
        )
    print("=" * 80)


if __name__ == "__main__":
    main()