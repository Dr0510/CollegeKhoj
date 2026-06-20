"""
Background worker for Admin v2 Bulk Import Engine.

Runs imports in a single background thread (ThreadPoolExecutor max_workers=1)
to comply with Render Free Tier constraints (1 CPU, no multiprocessing).

Provides:
- start_import() — creates backup then launches background thread for a given job
- cancel_import() — signals the engine to stop
- get_active_jobs() — returns list of currently running job IDs
- recover_stale_jobs() — resets PROCESSING jobs on startup
"""
import gc
import os
import traceback
import logging
import threading
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List

from database import db
from models import UploadJob

logger = logging.getLogger(__name__)

STALE_JOB_TIMEOUT_MINUTES = 30

# ── Global state ──────────────────────────────────────────────────────────────
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bulk_import')
_active_engines: dict[int, object] = {}
_active_lock = threading.Lock()


def start_import(job_id: int) -> bool:
    """
    Launch a background import for the given job.

    Creates an automatic database backup before starting the import.

    Returns True if the job was successfully started, False if already running.
    """
    with _active_lock:
        if job_id in _active_engines:
            logger.warning(f"[Worker] Job {job_id} is already running")
            return False

    # Create automatic backup before import
    try:
        from admin.backup_service import create_backup
        backup_result = create_backup(notes=f'Automatic backup before import job #{job_id}')
        if backup_result['success']:
            logger.info(f"[Worker] Auto-backup created for job {job_id}: backup#{backup_result['backup_id']}")
        else:
            logger.warning(f"[Worker] Auto-backup failed for job {job_id}: {backup_result.get('error')}")
    except Exception as e:
        logger.warning(f"[Worker] Auto-backup error for job {job_id}: {e}")

    # Submit to thread pool
    future = _executor.submit(_run_import_worker, job_id)
    future.add_done_callback(lambda f: _cleanup_job(job_id, f))

    logger.info(f"[Worker] Job {job_id} submitted to background thread")
    return True


def cancel_import(job_id: int) -> bool:
    """Cancel a running import job."""
    with _active_lock:
        engine = _active_engines.get(job_id)
        if engine:
            engine.cancel()
            logger.info(f"[Worker] Job {job_id} cancel signal sent")
            return True
        logger.warning(f"[Worker] Job {job_id} not found or not running")
        return False


def get_active_jobs() -> List[int]:
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

    Handles two cases:
    1. Jobs in PROCESSING > 30 minutes → marked FAILED (timeout).
    2. Newer PROCESSING jobs → reset to PENDING for manual resume.

    Also checks for any job with started_at > 30 min ago still in PROCESSING
    (in case the worker crashed mid-import without the PROCESSING status).
    """
    try:
        # Defensive check: skip if upload_jobs table doesn't exist yet
        from sqlalchemy import inspect
        try:
            inspector = inspect(db.engine)
            all_tables = set(inspector.get_table_names(schema='public'))
            if 'upload_jobs' not in all_tables:
                logger.info("[Worker] upload_jobs table does not exist yet — skipping stale job recovery")
                return
        except Exception as e:
            logger.warning(f"[Worker] Cannot inspect tables for stale recovery: {e}")
            return

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_JOB_TIMEOUT_MINUTES)
        stale = UploadJob.query.filter(
            UploadJob.status.in_(['PROCESSING'])
        ).all()

        for job in stale:
            started = job.started_at
            if started and started.replace(tzinfo=timezone.utc) < cutoff:
                # Job has been PROCESSING for > 30 minutes — timeout
                logger.warning(
                    f"[Worker] Job {job.id} timed out after {STALE_JOB_TIMEOUT_MINUTES} min "
                    f"(started={started})"
                )
                job.status = 'FAILED'
                job.error_message = (
                    f"Job timed out after {STALE_JOB_TIMEOUT_MINUTES} minutes in PROCESSING status. "
                    f"Click Resume to retry."
                )
                job.completed_at = datetime.now(timezone.utc)
            else:
                # Recent PROCESSING — likely from a restart
                logger.info(
                    f"[Worker] Recovering stale job {job.id} (file={job.filename})"
                )
                job.status = 'PENDING'
                job.error_message = (
                    f"Recovered from {job.status} on app restart. "
                    f"Click Resume to continue."
                )
            db.session.commit()

        if stale:
            logger.info(f"[Worker] Recovered {len(stale)} stale import job(s)")
    except Exception as e:
        db.session.rollback()
        logger.error(f"[Worker] Failed to recover stale jobs: {e}")


# ── Internal Worker ──────────────────────────────────────────────────────────

def _run_import_worker(job_id: int):
    """Worker function that runs in the background thread."""
    gc.collect()
    from app import app

    with app.app_context():
        try:
            # Fetch the job
            job = db.session.get(UploadJob, job_id)
            if not job:
                logger.error(f"[Worker] Job {job_id} not found in database")
                return

            if job.status == 'PROCESSING':
                logger.warning(f"[Worker] Job {job_id} is already PROCESSING — skipping")
                return

            if not job.stored_path or not os.path.exists(job.stored_path):
                error_msg = f"File not found at {job.stored_path}"
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
                filepath=job.stored_path,
                admission_type_id=job.admission_type_id,
                academic_year_id=job.academic_year_id,
                cap_round_id=job.cap_round_id,
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
            logger.exception(f"[Worker] Job {job_id} worker error: {e}")
            try:
                job = db.session.get(UploadJob, job_id)
                if job:
                    job.status = 'FAILED'
                    job.error_message = str(e)[:1000]
                    job.completed_at = datetime.now(timezone.utc)
                    db.session.commit()
            except Exception:
                db.session.rollback()
        finally:
            # Reconcile any job still in PROCESSING after run() returned without
            # reaching a terminal state, guaranteeing a terminal status + completed_at.
            try:
                job = db.session.get(UploadJob, job_id)
                if job and job.status == 'PROCESSING':
                    job.status = 'FAILED'
                    job.error_message = (
                        job.error_message or 'Import ended without reaching a terminal state.'
                    )
                    job.completed_at = datetime.now(timezone.utc)
                    db.session.commit()
            except Exception:
                db.session.rollback()

            with _active_lock:
                _active_engines.pop(job_id, None)
            gc.collect()


def _cleanup_job(job_id: int, future):
    """Cleanup callback after a job future completes."""
    with _active_lock:
        _active_engines.pop(job_id, None)
    gc.collect()
    logger.debug(f"[Worker] Job {job_id} cleaned up")


def init_worker(app):
    """Initialize the background worker on app startup."""
    with app.app_context():
        recover_stale_jobs()
    logger.info("[Worker] Background worker initialized (max_workers=1)")