"""
Background worker for the Bulk PDF Import Engine.

Runs imports in a single background thread (ThreadPoolExecutor max_workers=1)
to comply with Render Free Tier constraints (1 CPU, no multiprocessing).

Provides:
- start_import() — launches a background thread for a given job
- cancel_import() — signals the engine to stop
- get_active_jobs() — returns list of currently running job IDs
- recover_stale_jobs() — resets PROCESSING jobs on startup (Render recovery)
"""
import gc
import os
import logging
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from database import db
from models import ImportJob

logger = logging.getLogger(__name__)

# ── Global state ──────────────────────────────────────────────────────────────
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bulk_import')
_active_engines: dict[int, object] = {}  # job_id -> BulkImportEngine instance
_active_lock = threading.Lock()


def start_import(job_id: int) -> bool:
    """
    Launch a background import for the given job.

    Returns True if the job was successfully started, False if already running.
    """
    with _active_lock:
        if job_id in _active_engines:
            logger.warning(f"[Worker] Job {job_id} is already running")
            return False

    # Submit to thread pool
    future = _executor.submit(_run_import_worker, job_id)

    # Register callback for cleanup
    future.add_done_callback(lambda f: _cleanup_job(job_id, f))

    logger.info(f"[Worker] Job {job_id} submitted to background thread")
    return True


def cancel_import(job_id: int) -> bool:
    """
    Cancel a running import job.

    Returns True if the job was found and signalled to cancel.
    """
    with _active_lock:
        engine = _active_engines.get(job_id)
        if engine:
            engine.cancel()
            logger.info(f"[Worker] Job {job_id} cancel signal sent")
            return True
        logger.warning(f"[Worker] Job {job_id} not found or not running")
        return False


def get_active_jobs() -> list[int]:
    """Get list of job IDs currently being processed."""
    with _active_lock:
        return list(_active_engines.keys())


def is_job_running(job_id: int) -> bool:
    """Check if a specific job is currently running."""
    with _active_lock:
        return job_id in _active_engines


def recover_stale_jobs():
    """
    On application startup, reset any jobs stuck in PROCESSING status.

    This handles the case where Render restarts mid-import.
    Stale jobs are reset to PENDING so the admin can manually resume them.
    """
    try:
        stale = ImportJob.query.filter(
            ImportJob.status.in_(['PROCESSING', 'VALIDATING', 'IMPORTING'])
        ).all()

        for job in stale:
            checkpoint = job.checkpoint_page or 0
            logger.info(
                f"[Worker] Recovering stale job {job.id} "
                f"(file={job.file.filename if job.file else '?'}, "
                f"checkpoint=page {checkpoint})"
            )
            old_status = job.status
            job.status = 'PENDING'
            job.error_message = (
                f"Recovered from {old_status} on app restart. "
                f"Last checkpoint: page {checkpoint}. "
                f"Click Resume to continue."
            )
            db.session.commit()

        if stale:
            logger.info(
                f"[Worker] Recovered {len(stale)} stale import job(s) "
                f"from previous run"
            )
    except Exception as e:
        db.session.rollback()
        logger.error(f"[Worker] Failed to recover stale jobs: {e}")


# ── Internal Worker ──────────────────────────────────────────────────────────

def _run_import_worker(job_id: int):
    """
    Worker function that runs in the background thread.

    Creates a new application context for the thread (Flask-SQLAlchemy sessions
    are not thread-safe by default, but using app.app_context() works).
    """
    gc.collect()
    from app import app

    with app.app_context():
        try:
            # Fetch the job
            job = db.session.get(ImportJob, job_id)
            if not job:
                logger.error(f"[Worker] Job {job_id} not found in database")
                return

            if job.status == 'PROCESSING':
                logger.warning(
                    f"[Worker] Job {job_id} is already PROCESSING — skipping"
                )
                return

            # Determine start page from checkpoint
            start_page = (job.checkpoint_page or 0) + 1

            # If job has a page range, respect it
            page_start = job.page_range_start or 1
            page_end = job.page_range_end

            # For resume: use max of checkpoint and original range start
            start_page = max(start_page, page_start)

            # Extract year/round from the job's file metadata
            file_record = job.file
            year = file_record.year if file_record else 2025
            round_num = file_record.round_number if file_record else 1

            if not file_record or not os.path.exists(file_record.stored_path):
                error_msg = f"File not found at {file_record.stored_path if file_record else '?'}"
                logger.error(f"[Worker] Job {job_id}: {error_msg}")
                job.status = 'FAILED'
                job.error_message = error_msg
                job.completed_at = datetime.now(timezone.utc)
                db.session.commit()
                return

            # Create the engine
            from admin.bulk_import_engine import BulkImportEngine

            engine = BulkImportEngine(
                db_session=db.session,
                job_id=job_id,
                filepath=file_record.stored_path,
                source_file_id=file_record.id,
                year=year,
                round_number=round_num,
                start_page=start_page,
                end_page=page_end,
            )

            # Register as active
            with _active_lock:
                _active_engines[job_id] = engine

            # Run the import
            summary = engine.run()

            logger.info(
                f"[Worker] Job {job_id} completed: "
                f"{summary.get('status')}, "
                f"{summary.get('rows_imported', 0)} rows imported"
            )

        except Exception as e:
            logger.error(f"[Worker] Job {job_id} worker error: {e}")
            try:
                job = db.session.get(ImportJob, job_id)
                if job:
                    job.status = 'FAILED'
                    job.error_message = str(e)[:1000]
                    job.completed_at = datetime.now(timezone.utc)
                    db.session.commit()
            except Exception:
                db.session.rollback()
        finally:
            # Cleanup
            with _active_lock:
                _active_engines.pop(job_id, None)
            gc.collect()


def _cleanup_job(job_id: int, future):
    """Cleanup callback after a job future completes."""
    with _active_lock:
        _active_engines.pop(job_id, None)
    gc.collect()
    logger.debug(f"[Worker] Job {job_id} cleaned up")


# ── Initialization ───────────────────────────────────────────────────────────

def init_worker(app):
    """Initialize the background worker on app startup."""
    with app.app_context():
        recover_stale_jobs()
    logger.info("[Worker] Background worker initialized (max_workers=1)")