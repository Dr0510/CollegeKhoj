"""
Bulk PDF Import Routes — REST API + admin pages for the Bulk Import Engine.

Blueprint registered at /admin/bulk-import

Endpoints:
  GET   /                          — List all import jobs (admin page)
  POST  /upload                    — Upload PDF, create ImportJob, return immediately
  GET   /<id>                      — Detail page for a single job
  GET   /<id>/progress             — AJAX progress JSON endpoint
  POST  /<id>/start                — Start/resume processing in background
  POST  /<id>/cancel               — Cancel running import
  POST  /<id>/retry-failed         — Retry only failed pages
  GET   /<id>/error-report         — Download CSV error report
  POST  /<id>/delete               — Delete job and associated file
"""
import os
import csv
import json
import io
import logging
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, jsonify,
    redirect, session, g, flash, current_app, Response
)
from sqlalchemy import text as sql_text

from database import db
from models import ImportJob, UploadedFile, ImportErrorRecord
from admin.audit import log_action
from auth_decorators import admin_required

logger = logging.getLogger(__name__)

# ── Blueprint ────────────────────────────────────────────────────────────────
bulk_import_bp = Blueprint(
    'bulk_import_bp', __name__,
    template_folder='../templates/admin',
    url_prefix='/admin/bulk-import'
)

UPLOAD_DIR = os.environ.get(
    'ADMIN_UPLOAD_DIR',
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads', 'cutoffs')
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ensure_upload_dir():
    """Create upload directory if it doesn't exist."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def _get_admin_user():
    """Get the currently logged-in admin from g.user."""
    user = g.get('user')
    if user and user.is_admin():
        return user
    return None


# ── List View (Admin Page) ──────────────────────────────────────────────────

@bulk_import_bp.route('/')
@admin_required
def list_imports():
    """Admin page showing all bulk import jobs."""
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')

    query = ImportJob.query

    if status_filter:
        query = query.filter(ImportJob.status == status_filter)

    # Order by most recent first
    query = query.order_by(ImportJob.id.desc())

    pagination = query.paginate(page=page, per_page=20, error_out=False)

    # Check which jobs are currently running
    from admin.background_worker import get_active_jobs
    active_jobs = get_active_jobs()

    return render_template(
        'bulk_imports.html',
        jobs=pagination.items,
        pagination=pagination,
        status_filter=status_filter,
        active_jobs=active_jobs,
    )


# ── Upload PDF ──────────────────────────────────────────────────────────────

@bulk_import_bp.route('/upload', methods=['POST'])
@admin_required
def upload_pdf():
    """
    Upload a PDF for bulk import.

    Creates an UploadedFile record and an ImportJob, then returns immediately.
    The actual processing is started via POST /<id>/start.

    Request:
        file: PDF file
        year: (optional) override auto-detected year
        round: (optional) override auto-detected round
        page_start: (optional) start page for smart range import
        page_end: (optional) end page for smart range import

    Response:
        { ok: true, job_id: ..., file_id: ..., total_pages: ..., message: ... }
    """
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'ok': False, 'error': 'No file selected'}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'ok': False, 'error': 'Only PDF files are supported'}), 400

    try:
        _ensure_upload_dir()

        # 1. Save file permanently
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        safe_name = f"{timestamp}_{file.filename}"
        dest = os.path.join(UPLOAD_DIR, safe_name)
        file.save(dest)
        file_size = os.path.getsize(dest)

        # 2. Auto-detect year/round from filename
        from admin.pdf_extractor import detect_year_round_from_filename
        year, round_num = detect_year_round_from_filename(file.filename)

        # Allow admin override via form
        year = request.form.get('year', type=int) or year or 2025
        round_num = request.form.get('round', type=int) or round_num or 1

        # 3. Count pages using pdfplumber
        from admin.bulk_import_engine import get_pdf_page_count
        total_pages = get_pdf_page_count(dest)

        if total_pages == 0:
            # Try fallback page count
            try:
                import pdfplumber
                with pdfplumber.open(dest) as pdf:
                    total_pages = len(pdf.pages)
            except Exception:
                pass

        # 4. Create UploadedFile record
        admin_user = _get_admin_user()
        upload_record = UploadedFile(
            filename=file.filename,
            stored_path=dest,
            file_size=file_size,
            mime_type='application/pdf',
            processed_status='pending',
            uploaded_by=admin_user.id if admin_user else None,
            year=year,
            round_number=round_num,
        )
        db.session.add(upload_record)
        db.session.flush()

        # 5. Parse page range
        page_start = request.form.get('page_start', 1, type=int)
        page_end = request.form.get('page_end', type=int)

        # 6. Create ImportJob
        job = ImportJob(
            file_id=upload_record.id,
            status='PENDING',
            total_pages=total_pages,
            processed_pages=0,
            checkpoint_page=0,
            rows_extracted=0,
            rows_imported=0,
            rows_failed=0,
            failed_pages=[],
            error_log=[],
            page_range_start=page_start,
            page_range_end=page_end,
            extraction_method='pdfplumber',
        )
        db.session.add(job)
        db.session.commit()

        log_action('bulk_upload', 'uploaded_file', upload_record.id, {
            'filename': file.filename,
            'year': year,
            'round': round_num,
            'total_pages': total_pages,
            'job_id': job.id,
            'page_range': f'{page_start}-{page_end}' if page_end else f'{page_start}-end',
        })

        logger.info(
            f"Bulk PDF uploaded: job#{job.id}, file#{upload_record.id}, "
            f"{total_pages} pages, year={year}, round={round_num}"
        )

        # 7. Return immediately — don't block
        return jsonify({
            'ok': True,
            'job_id': job.id,
            'file_id': upload_record.id,
            'total_pages': total_pages,
            'year': year,
            'round': round_num,
            'filename': file.filename,
            'message': (
                f'PDF uploaded: {total_pages} pages. '
                f'Click "Start Import" to begin processing.'
            ),
        })

    except Exception as e:
        logger.error(f"Bulk upload error: {e}")
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Detail View (Admin Page) ────────────────────────────────────────────────

@bulk_import_bp.route('/<int:job_id>')
@admin_required
def job_detail(job_id):
    """Detail page for a single import job."""
    job = db.session.get(ImportJob, job_id)
    if not job:
        flash('Import job not found', 'error')
        return redirect('/admin/bulk-import')

    # Check if running
    from admin.background_worker import is_job_running
    is_running = is_job_running(job_id)

    # Get error records
    error_records = ImportErrorRecord.query.filter_by(
        job_id=job_id
    ).order_by(ImportErrorRecord.id.desc()).limit(100).all()

    return render_template(
        'bulk_import_detail.html',
        job=job,
        is_running=is_running,
        error_records=error_records,
    )


# ── Progress (JSON) ─────────────────────────────────────────────────────────

@bulk_import_bp.route('/<int:job_id>/progress')
@admin_required
def job_progress(job_id):
    """
    AJAX endpoint for live progress updates.

    Returns JSON with current job state.
    """
    job = db.session.get(ImportJob, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404

    from admin.background_worker import is_job_running
    is_running = is_job_running(job_id)

    data = job.to_dict()
    data['is_running'] = is_running

    # Calculate progress percentage
    if job.total_pages > 0:
        data['progress_pct'] = round(
            (job.processed_pages / job.total_pages) * 100, 1
        )
    else:
        data['progress_pct'] = 0.0

    # Rate calculation (if running)
    if is_running and job.started_at:
        elapsed = (datetime.now(timezone.utc) - job.started_at).total_seconds()
        if elapsed > 0:
            data['rate_pages_per_sec'] = round(job.processed_pages / elapsed, 2)
            remaining = job.total_pages - job.processed_pages
            data['eta_seconds'] = round(remaining / (job.processed_pages / elapsed), 1) if job.processed_pages > 0 else 0
        else:
            data['rate_pages_per_sec'] = 0
            data['eta_seconds'] = 0
    else:
        data['rate_pages_per_sec'] = 0
        data['eta_seconds'] = 0

    return jsonify({'ok': True, 'job': data})


# ── Start / Resume Import ──────────────────────────────────────────────────

@bulk_import_bp.route('/<int:job_id>/start', methods=['POST'])
@admin_required
def start_import(job_id):
    """
    Start or resume a bulk import job in the background.

    The job must be in PENDING or FAILED status.
    If the job was previously checkpointed, it resumes from the checkpoint.
    """
    job = db.session.get(ImportJob, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404

    if job.status == 'PROCESSING':
        return jsonify({'ok': False, 'error': 'Job is already running'}), 400

    if job.status == 'COMPLETED':
        return jsonify({'ok': False, 'error': 'Job is already completed'}), 400

    if job.status == 'CANCELLED':
        # Allow restarting cancelled jobs
        pass

    # Ensure file exists
    file_record = job.file
    if not file_record or not os.path.exists(file_record.stored_path):
        return jsonify({'ok': False, 'error': 'PDF file not found on disk'}), 404

    # Launch background worker
    from admin.background_worker import start_import as worker_start
    started = worker_start(job_id)

    if started:
        log_action('bulk_start', 'import_job', job_id, {
            'status': job.status,
            'checkpoint': job.checkpoint_page,
            'total_pages': job.total_pages,
        })

        return jsonify({
            'ok': True,
            'message': f'Import started from page {max(job.checkpoint_page or 0, job.page_range_start or 1) + 1}',
        })
    else:
        return jsonify({
            'ok': False,
            'error': 'Job could not be started. Check if another import is running.',
        }), 409


# ── Cancel Import ──────────────────────────────────────────────────────────

@bulk_import_bp.route('/<int:job_id>/cancel', methods=['POST'])
@admin_required
def cancel_import(job_id):
    """
    Cancel a running import job.

    The engine will stop at the next page boundary and save a checkpoint.
    """
    from admin.background_worker import cancel_import as worker_cancel
    cancelled = worker_cancel(job_id)

    if cancelled:
        log_action('bulk_cancel', 'import_job', job_id)
        return jsonify({'ok': True, 'message': 'Import cancellation requested'})
    else:
        return jsonify({'ok': False, 'error': 'Job is not currently running'}), 400


# ── Retry Failed Pages ─────────────────────────────────────────────────────

@bulk_import_bp.route('/<int:job_id>/retry-failed', methods=['POST'])
@admin_required
def retry_failed_pages(job_id):
    """
    Retry pages that previously failed during import.

    This creates a new processing run that only processes the failed pages.
    If the job completed with some failures, this allows selective retry.
    """
    job = db.session.get(ImportJob, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404

    if not job.failed_pages:
        return jsonify({'ok': False, 'error': 'No failed pages to retry'}), 400

    # For retry, we set the page range to only the failed pages
    # The engine already handles per-page failures, so we just start a new
    # run with page_range covering the failed pages.
    # Since the engine processes a contiguous range, we'll set it to the
    # min/max of failed pages, but the engine's per-page error handling
    # will skip already-successful pages.

    # Get the failed page numbers
    failed = sorted(job.failed_pages)

    # Reset failed pages tracking for this retry
    old_failed = list(job.failed_pages)
    job.failed_pages = []
    job.error_log = []
    job.status = 'PENDING'
    job.page_range_start = failed[0]
    job.page_range_end = failed[-1]
    db.session.commit()

    # Start the import
    from admin.background_worker import start_import as worker_start
    started = worker_start(job_id)

    if started:
        log_action('bulk_retry_failed', 'import_job', job_id, {
            'pages': old_failed,
        })

        return jsonify({
            'ok': True,
            'message': f'Retrying {len(old_failed)} failed pages: {old_failed[:20]}...',
            'failed_pages': old_failed,
        })
    else:
        return jsonify({
            'ok': False,
            'error': 'Could not start retry. Check if another import is running.',
        }), 409


# ── Error Report (CSV Download) ────────────────────────────────────────────

@bulk_import_bp.route('/<int:job_id>/error-report')
@admin_required
def error_report(job_id):
    """Download a CSV report of all errors for a job."""
    job = db.session.get(ImportJob, job_id)
    if not job:
        flash('Job not found', 'error')
        return redirect('/admin/bulk-import')

    # Get error records
    errors = ImportErrorRecord.query.filter_by(
        job_id=job_id
    ).order_by(ImportErrorRecord.id).all()

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'ID', 'Page', 'College Code', 'College Name',
        'Course Code', 'Course Name', 'Category',
        'Rank', 'Percentile', 'Error Reason', 'Timestamp',
    ])

    for err in errors:
        writer.writerow([
            err.id,
            err.page_number or '',
            err.college_code or '',
            err.college_name or '',
            err.course_code or '',
            err.course_name or '',
            err.category or '',
            err.rank or '',
            err.percentile or '',
            err.error_reason or '',
            err.created_at.strftime('%Y-%m-%d %H:%M:%S') if err.created_at else '',
        ])

    # Also add failed pages from error_log
    if job.error_log:
        for entry in job.error_log:
            writer.writerow([
                '',
                entry.get('page', ''),
                '', '', '', '', '',
                '', '',
                entry.get('error', ''),
                '',
            ])

    csv_content = output.getvalue()
    output.close()

    filename = f'import_{job_id}_errors.csv'

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': 'text/csv; charset=utf-8',
        }
    )


# ── Delete Job ─────────────────────────────────────────────────────────────

@bulk_import_bp.route('/<int:job_id>/delete', methods=['POST'])
@admin_required
def delete_job(job_id):
    """Delete an import job and its associated file."""
    job = db.session.get(ImportJob, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404

    # Check if running
    from admin.background_worker import is_job_running
    if is_job_running(job_id):
        return jsonify({'ok': False, 'error': 'Cannot delete a running job. Cancel it first.'}), 400

    try:
        file_record = job.file

        # Delete error records
        ImportErrorRecord.query.filter_by(job_id=job_id).delete()

        # Delete the job
        db.session.delete(job)

        # Delete file record and physical file if no other jobs reference it
        if file_record:
            # Check if other jobs reference this file
            other_jobs = ImportJob.query.filter(
                ImportJob.file_id == file_record.id,
                ImportJob.id != job_id
            ).count()

            if other_jobs == 0:
                # Delete physical file
                if os.path.exists(file_record.stored_path):
                    os.remove(file_record.stored_path)
                db.session.delete(file_record)

        db.session.commit()

        log_action('bulk_delete', 'import_job', job_id)

        return jsonify({'ok': True, 'message': 'Job deleted successfully'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Delete job {job_id} error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── API: List Jobs (JSON) ──────────────────────────────────────────────────

@bulk_import_bp.route('/api/jobs')
@admin_required
def api_list_jobs():
    """JSON API to list all import jobs."""
    limit = request.args.get('limit', 50, type=int)
    status = request.args.get('status', '')

    query = ImportJob.query
    if status:
        query = query.filter(ImportJob.status == status)
    query = query.order_by(ImportJob.id.desc()).limit(limit)

    jobs = []
    for job in query.all():
        data = job.to_dict()
        data['progress_pct'] = round(
            (job.processed_pages / job.total_pages) * 100, 1
        ) if job.total_pages > 0 else 0
        jobs.append(data)

    return jsonify({'ok': True, 'jobs': jobs})


# ── Migration Helper (adds new columns to existing import_jobs table) ──────

def run_migration():
    """Add new columns to import_jobs table if they don't exist."""
    try:
        inspector = db.inspect(db.engine)
        if 'import_jobs' not in inspector.get_table_names():
            logger.info("import_jobs table does not exist yet — skipping migration")
            return

        columns = [c['name'] for c in inspector.get_columns('import_jobs')]
        migrates = [
            ('checkpoint_page', 'INTEGER DEFAULT 0'),
            ('rows_extracted', 'INTEGER DEFAULT 0'),
            ('rows_imported', 'INTEGER DEFAULT 0'),
            ('rows_failed', 'INTEGER DEFAULT 0'),
            ('failed_pages', 'JSON DEFAULT \'[]\''),
            ('error_log', 'JSON DEFAULT \'[]\''),
            ('page_range_start', 'INTEGER DEFAULT 1'),
            ('page_range_end', 'INTEGER'),
            ('memory_usage_mb', 'FLOAT'),
            ('extraction_method', 'VARCHAR(20) DEFAULT \'pdfplumber\''),
            ('confidence_score', 'FLOAT'),
        ]

        for col_name, col_type in migrates:
            if col_name not in columns:
                db.session.execute(sql_text(
                    f'ALTER TABLE import_jobs ADD COLUMN {col_name} {col_type}'
                ))
                logger.info(f"✅ Added column {col_name} to import_jobs")

        # Check for import_error_records table
        if 'import_error_records' not in inspector.get_table_names():
            # Create via SQLAlchemy
            from models import ImportErrorRecord
            # db.create_all() won't create it if already called, but we can
            # use raw SQL as fallback
            db.session.execute(sql_text("""
                CREATE TABLE IF NOT EXISTS import_error_records (
                    id SERIAL PRIMARY KEY,
                    job_id INTEGER REFERENCES import_jobs(id),
                    page_number INTEGER,
                    college_code VARCHAR(20),
                    college_name TEXT,
                    course_code VARCHAR(20),
                    course_name TEXT,
                    category VARCHAR(20),
                    rank INTEGER,
                    percentile FLOAT,
                    error_reason TEXT NOT NULL,
                    raw_text_snippet TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            logger.info("✅ Created import_error_records table")

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Import job migration note: {e}")