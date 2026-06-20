"""
Admin v2 Routes — Complete redesign with new sidebar structure.

All routes are prefixed with /admin (via the blueprint).
Protected by admin_required decorator.
"""
import os
import csv
import json
import io
import logging
from datetime import datetime, timezone
from flask import render_template, request, jsonify, redirect, session, g, flash, current_app, Response, send_file

from database import db, safe_query_first
from admin import admin_bp
from admin.audit import log_action
from admin.backup_service import (
    create_backup, restore_backup, delete_backup_file, get_backup_file_path, BACKUP_DIR
)
from admin.trend_service import (
    get_dashboard_stats, compute_college_trends, get_safe_moderate_dream,
    compute_branch_popularity
)
from admin.pdf_engine_v2 import extract_pdf, compute_file_hash
from admin.validation_engine_v2 import validate_rows
from admin.background_worker import start_import, cancel_import, get_active_jobs, init_worker
from auth_decorators import login_required, admin_required
from models import (
    User, College, Branch, AdmissionType, AcademicYear, CapRound,
    Cutoff, UploadJob, BackupHistory, AuditLog, Category, CollegeAdmissionType
)
from sqlalchemy import func as sa_func

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.environ.get(
    'ADMIN_UPLOAD_DIR',
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads', 'cutoffs')
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_admin():
    """Safe helper to retrieve the current admin user.

    Returns None if the user is not logged in, not admin, or if the database
    query fails (e.g. users table missing on first startup).
    """
    try:
        user = g.get('user')
        if user and user.is_admin():
            return user
    except Exception:
        logger.warning("[AUTH] _get_admin() failed — returning None", exc_info=True)
    return None


def _save_upload(file_storage):
    """Save an uploaded file and return (stored_path, file_size)."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    safe_name = f"{timestamp}_{file_storage.filename}"
    dest = os.path.join(UPLOAD_DIR, safe_name)
    file_storage.save(dest)
    return dest, os.path.getsize(dest)


# ── Admin Login ──────────────────────────────────────────────────────────────

@admin_bp.route('/login', methods=['GET', 'POST'])
def admin_login():
    # Debug: log session state on every admin login page load
    logger.info(f"[AUTH] admin_login — session keys: {list(session.keys())}, "
                f"is_admin={session.get('is_admin')}, admin_id={session.get('admin_id')}")

    if _get_admin():
        return redirect('/admin/dashboard')

    next_url = request.args.get('next') or request.form.get('next') or '/admin/dashboard'

    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        # ── Debug logging ──────────────────────────────────────────
        logger.info(f"[AUTH] Login attempt — email={email}, is_json={request.is_json}")
        logger.info(f"[AUTH] Session keys before query: {list(session.keys())}")

        # Use direct query (safe_query_first self-heals if table is missing)
        user = safe_query_first(User, User.email == email, User.role == 'admin')
        logger.info(f"[AUTH] User query result: {'found' if user else 'NOT FOUND'}")

        if not user:
            # Self-heal: ensure schema exists, then retry once
            logger.warning(f"[AUTH] Admin user {email} not found — running schema self-heal")
            from database import ensure_schema
            ensure_schema()
            # Also try to create admin if missing
            from database import create_default_admin
            create_default_admin()
            user = User.query.filter_by(email=email, role='admin').first()
            logger.info(f"[AUTH] Retry query result: {'found' if user else 'still missing'}")

            if not user:
                if request.is_json:
                    return jsonify({'ok': False, 'error': 'Invalid admin credentials'}), 401
                flash('Invalid admin credentials', 'error')
                return render_template('admin/login.html', error='Invalid credentials', next_url=next_url)

        from app import check_password
        if not user.password_hash or not check_password(password, user.password_hash):
            logger.warning(f"[AUTH] Password check FAILED for {email}")
            if request.is_json:
                return jsonify({'ok': False, 'error': 'Invalid admin credentials'}), 401
            flash('Invalid admin credentials', 'error')
            return render_template('admin/login.html', error='Invalid credentials', next_url=next_url)

        logger.info(f"[AUTH] Password check OK for {email}, is_verified={user.is_verified}")

        if not user.is_verified:
            if request.is_json:
                return jsonify({'ok': False, 'error': 'Admin account not verified'}), 403
            flash('Admin account not verified', 'error')
            return render_template('admin/login.html', error='Account not verified', next_url=next_url)

        # ── Store admin session (no DB dependency for auth check) ──
        session.clear()  # clean slate before setting admin keys
        session['user_id'] = user.id
        session['admin_id'] = user.id
        session['admin_email'] = user.email
        session['is_admin'] = True
        session['role'] = user.role
        logger.info(f"[AUTH] Login SUCCESS — set admin session: id={user.id}, email={user.email}")

        log_action('login', 'user', user.id)

        flash('Welcome Admin', 'success')
        if request.is_json:
            return jsonify({'ok': True, 'redirect': next_url})
        return redirect(next_url)

    return render_template('admin/login.html', next_url=next_url)


@admin_bp.route('/logout')
@admin_required
def admin_logout():
    admin_user = _get_admin() or g.get('user')
    admin_id = getattr(admin_user, 'id', None) or session.get('admin_id')
    if admin_id:
        log_action('logout', 'user', admin_id)
    session.clear()
    return redirect('/admin/login')


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/dashboard')
@admin_required
def admin_dashboard():
    stats = get_dashboard_stats()
    return render_template('admin/dashboard.html', stats=stats)


@admin_bp.route('/api/dashboard-stats')
@admin_required
def dashboard_stats_api():
    """JSON endpoint for dashboard charts.

    Gracefully handles missing columns by returning zeros.
    """
    stats = get_dashboard_stats()

    # Records by year
    records_by_year = []
    try:
        years_data = db.session.query(
            AcademicYear.academic_year,
            db.func.count(Cutoff.id).label('count')
        ).join(
            Cutoff, Cutoff.academic_year_id == AcademicYear.id
        ).group_by(
            AcademicYear.academic_year, AcademicYear.id
        ).order_by(AcademicYear.id).all()
        records_by_year = [{'year': r.academic_year, 'count': r.count} for r in years_data]
    except Exception as e:
        logger.warning(f"Dashboard API: records_by_year failed: {e}")

    # Activity timeline
    activity = []
    try:
        timeline = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(20).all()
        activity = [{
            'id': e.id,
            'action': e.action,
            'resource_type': e.resource_type,
            'user': e.user.display_name() if e.user else 'System',
            'details': e.details,
            'timestamp': e.created_at.isoformat() if e.created_at else None,
        } for e in timeline]
    except Exception as e:
        logger.warning(f"Dashboard API: activity timeline failed: {e}")

    return jsonify({
        'ok': True,
        'stats': stats,
        'records_by_year': records_by_year,
        'activity': activity,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# ADMISSIONS — Engineering / DSE / Polytechnic
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/admissions/<string:admission_code>')
@admin_required
def admissions_list(admission_code):
    """View cutoff records for a specific admission type."""
    try:
        atype = AdmissionType.query.filter_by(code=admission_code.upper()).first()
        if not atype:
            flash(f'Invalid admission type: {admission_code}', 'error')
            return redirect('/admin/dashboard')

        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        college_id = request.args.get('college_id', type=int)
        branch_id = request.args.get('branch_id', type=int)
        year_id = request.args.get('year_id', type=int)
        round_id = request.args.get('round_id', type=int)
        category = request.args.get('category', '')
        search = request.args.get('q', '')

        query = Cutoff.query.filter(Cutoff.admission_type_id == atype.id)

        if college_id:
            query = query.filter(Cutoff.college_id == college_id)
        if branch_id:
            query = query.filter(Cutoff.branch_id == branch_id)
        if year_id:
            query = query.filter(Cutoff.academic_year_id == year_id)
        if round_id:
            query = query.filter(Cutoff.cap_round_id == round_id)
        if category:
            query = query.filter(Cutoff.category == category.upper())

        query = query.order_by(Cutoff.academic_year_id.desc(), Cutoff.college_id, Cutoff.branch_id)
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        # Filter options
        colleges = College.query.filter(College.status == 'active').order_by(College.college_name).all()
        branches = Branch.query.order_by(Branch.branch_name).all()
        years = AcademicYear.query.order_by(AcademicYear.id.desc()).all()
        rounds = CapRound.query.all()

        return render_template('admin/admissions_list.html',
                               admission_type=atype,
                               cutoffs=pagination.items,
                               pagination=pagination,
                               colleges=colleges,
                               branches=branches,
                               years=years,
                               rounds=rounds,
                               filters={
                                   'college_id': college_id,
                                   'branch_id': branch_id,
                                   'year_id': year_id,
                                   'round_id': round_id,
                                   'category': category,
                                   'q': search,
                               })
    except Exception as e:
        logger.error(f"Admissions list error for {admission_code}: {e}", exc_info=True)
        flash('Unable to load cutoff records.', 'error')
        return render_template('admin/admissions_list.html',
                               admission_type=AdmissionType(code=admission_code.upper(), name=admission_code.upper()),
                               cutoffs=[],
                               pagination=None,
                               colleges=[],
                               branches=[],
                               years=[],
                               rounds=[],
                               filters={})


@admin_bp.route('/api/admissions/<string:admission_code>/export')
@admin_required
def export_admissions_csv(admission_code):
    """Export cutoff records as CSV."""
    atype = AdmissionType.query.filter_by(code=admission_code.upper()).first()
    if not atype:
        return jsonify({'ok': False, 'error': 'Invalid admission type'}), 400

    query = Cutoff.query.filter(Cutoff.admission_type_id == atype.id)
    year_id = request.args.get('year_id', type=int)
    if year_id:
        query = query.filter(Cutoff.academic_year_id == year_id)

    records = query.order_by(Cutoff.academic_year_id.desc(), Cutoff.college_id).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'College Code', 'College Name', 'Branch', 'Academic Year',
        'CAP Round', 'Category', 'Seat Type', 'Gender',
        'Percentile', 'Rank'
    ])

    for r in records:
        writer.writerow([
            r.college_rel.college_code if r.college_rel else '',
            r.college_rel.college_name if r.college_rel else '',
            r.branch_rel.branch_name if r.branch_rel else '',
            r.academic_year_rel.academic_year if r.academic_year_rel else '',
            r.cap_round_rel.name if r.cap_round_rel else '',
            r.category,
            r.seat_type,
            r.gender,
            float(r.cutoff_percentile) if r.cutoff_percentile else '',
            r.cutoff_rank or '',
        ])

    csv_content = output.getvalue()
    output.close()

    filename = f"cutoffs_{admission_code}.csv"
    return Response(
        csv_content,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': 'text/csv; charset=utf-8',
        }
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BULK IMPORT — Upload PDF
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/import/upload', methods=['GET', 'POST'])
@admin_required
def import_upload():
    if request.method == 'GET':
        admission_types = AdmissionType.query.all()
        academic_years = AcademicYear.query.order_by(AcademicYear.id.desc()).all()
        cap_rounds = CapRound.query.all()
        return render_template('admin/import_upload.html',
                               admission_types=admission_types,
                               academic_years=academic_years,
                               cap_rounds=cap_rounds)

    # Handle POST — upload PDF(s)
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400

    files = request.files.getlist('file')
    if not files or files[0].filename == '':
        return jsonify({'ok': False, 'error': 'No files selected'}), 400

    admission_type_id = request.form.get('admission_type_id', type=int)
    academic_year_id = request.form.get('academic_year_id', type=int)
    cap_round_id = request.form.get('cap_round_id', type=int)

    if not all([admission_type_id, academic_year_id, cap_round_id]):
        return jsonify({'ok': False, 'error': 'Missing admission type, year, or round'}), 400

    results = []
    for file in files:
        if not file.filename.lower().endswith('.pdf'):
            continue

        try:
            # Save the file
            stored_path, file_size = _save_upload(file)
            file_hash = compute_file_hash(stored_path)

            # Check for duplicate upload (same file hash)
            existing = UploadJob.query.filter_by(file_hash=file_hash).first()
            if existing:
                os.remove(stored_path)
                results.append({
                    'filename': file.filename,
                    'status': 'duplicate',
                    'message': f'Already uploaded as job #{existing.id}'
                })
                continue

            # Create upload job
            admin_user = _get_admin() or g.get('user')
            uploader_id = getattr(admin_user, 'id', None) or session.get('admin_id')
            if not uploader_id:
                logger.warning(f"[Upload] No admin user found for upload — session admin_id={session.get('admin_id')}")
                uploader_id = 1  # fallback to default admin
            job = UploadJob(
                filename=file.filename,
                stored_path=stored_path,
                file_hash=file_hash,
                file_size=file_size,
                admission_type_id=admission_type_id,
                academic_year_id=academic_year_id,
                cap_round_id=cap_round_id,
                status='PENDING',
                uploaded_by=uploader_id,
            )
            db.session.add(job)
            db.session.flush()
            job_id = job.id
            db.session.commit()

            log_action('upload', 'upload_job', job_id, {
                'filename': file.filename,
                'admission_type_id': admission_type_id,
                'academic_year_id': academic_year_id,
                'cap_round_id': cap_round_id,
            })

            results.append({
                'filename': file.filename,
                'job_id': job_id,
                'status': 'uploaded',
                'message': f'Queued for processing (job #{job_id})'
            })

        except Exception as e:
            logger.error(f"Upload error for {file.filename}: {e}")
            results.append({
                'filename': file.filename,
                'status': 'error',
                'message': str(e)
            })

    return jsonify({'ok': True, 'results': results})


@admin_bp.route('/import/upload/preview', methods=['POST'])
@admin_required
def import_preview():
    """Preview a PDF before committing — extract and validate."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'ok': False, 'error': 'No file selected'}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'ok': False, 'error': 'Only PDF files supported'}), 400

    admission_type_id = request.form.get('admission_type_id', type=int)
    academic_year_id = request.form.get('academic_year_id', type=int)
    cap_round_id = request.form.get('cap_round_id', type=int)

    try:
        # Save temp file
        stored_path, _ = _save_upload(file)

        # Extract
        extraction = extract_pdf(stored_path, file.filename)
        if extraction.get('error'):
            os.remove(stored_path)
            return jsonify({'ok': False, 'error': extraction['error']}), 400

        rows = extraction.get('rows', [])
        if not rows:
            os.remove(stored_path)
            return jsonify({'ok': False, 'error': 'No data could be extracted from the PDF'}), 400

        # Validate
        validation = validate_rows(
            rows,
            admission_type_id=admission_type_id,
            academic_year_id=academic_year_id,
            cap_round_id=cap_round_id,
        )

        # Preview first 50 rows
        preview_rows = []
        for row in rows[:50]:
            preview_rows.append({
                'college_code': row.get('college_code', ''),
                'college_name': row.get('college_name', '')[:60],
                'course_name': row.get('course_name', ''),
                'category': row.get('category', ''),
                'percentile': row.get('percentile'),
                'rank': row.get('rank'),
            })

        # Cleanup temp file
        os.remove(stored_path)

        return jsonify({
            'ok': True,
            'total_rows': len(rows),
            'valid_rows': validation.valid,
            'invalid_rows': validation.invalid,
            'duplicate_rows': validation.duplicates,
            'preview_rows': preview_rows,
            'admission_type': extraction.get('admission_type'),
            'academic_year': extraction.get('academic_year'),
            'cap_round': extraction.get('cap_round'),
            'method': extraction.get('method'),
            'confidence': extraction.get('confidence'),
        })

    except Exception as e:
        logger.error(f"Preview error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# BULK IMPORT — Pending & History
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/import/pending')
@admin_required
def import_pending():
    """View pending import jobs."""
    page = request.args.get('page', 1, type=int)
    jobs = UploadJob.query.filter(
        UploadJob.status.in_(['PENDING', 'PROCESSING'])
    ).order_by(UploadJob.created_at.desc()).paginate(page=page, per_page=20, error_out=False)

    active_jobs = get_active_jobs()
    return render_template('admin/import_pending.html',
                           jobs=jobs.items,
                           pagination=jobs,
                           active_jobs=active_jobs)


@admin_bp.route('/import/history')
@admin_required
def import_history():
    """View all import jobs history."""
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')

    query = UploadJob.query
    if status_filter:
        query = query.filter(UploadJob.status == status_filter)

    query = query.order_by(UploadJob.created_at.desc())
    pagination = query.paginate(page=page, per_page=20, error_out=False)

    return render_template('admin/import_history.html',
                           jobs=pagination.items,
                           pagination=pagination,
                           status_filter=status_filter)


@admin_bp.route('/import-jobs')
@admin_required
def import_jobs_history():
    """Import History page with status-filter tabs (Pending, Running, Completed, Failed).

    Filters displayed jobs server-side by the selected status so every displayed
    job matches the selected tab. Reuses the import_history.html template.
    """
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')

    query = UploadJob.query
    if status_filter:
        query = query.filter(UploadJob.status == status_filter)

    query = query.order_by(UploadJob.created_at.desc())
    pagination = query.paginate(page=page, per_page=20, error_out=False)

    return render_template('admin/import_history.html',
                           jobs=pagination.items,
                           pagination=pagination,
                           status_filter=status_filter)


@admin_bp.route('/import/<int:job_id>')
@admin_required
def import_detail(job_id):
    """View details of a specific import job."""
    job = db.session.get(UploadJob, job_id)
    if not job:
        flash('Job not found', 'error')
        return redirect('/admin/import/history')

    return render_template('admin/import_detail.html', job=job)


@admin_bp.route('/import-jobs/<int:job_id>')
@admin_required
def import_job_progress(job_id):
    """Live progress page for a single import job (polls the status endpoint)."""
    job = db.session.get(UploadJob, job_id)
    if not job:
        flash('Job not found', 'error')
        return redirect('/admin/import/history')

    return render_template('admin/import_progress.html', job=job)


@admin_bp.route('/import/<int:job_id>/start', methods=['POST'])
@admin_required
def import_start(job_id):
    """Start background processing for a job."""
    job = db.session.get(UploadJob, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404

    if job.status not in ('PENDING', 'FAILED'):
        return jsonify({'ok': False, 'error': f'Cannot start job in status: {job.status}'}), 400

    if not os.path.exists(job.stored_path or ''):
        return jsonify({'ok': False, 'error': 'PDF file not found on disk'}), 400

    success = start_import(job_id)
    if success:
        log_action('start_import', 'upload_job', job_id)
        return jsonify({'ok': True, 'message': 'Import started in background'})
    else:
        return jsonify({'ok': False, 'error': 'Job is already running'})


@admin_bp.route('/import/<int:job_id>/cancel', methods=['POST'])
@admin_required
def import_cancel(job_id):
    """Cancel a running import."""
    success = cancel_import(job_id)
    return jsonify({'ok': success, 'message': 'Cancel signal sent' if success else 'Job not running'})


@admin_bp.route('/import/<int:job_id>/delete', methods=['POST'])
@admin_required
def import_delete(job_id):
    """Delete a job and its associated file."""
    job = db.session.get(UploadJob, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404

    try:
        # Delete file
        if job.stored_path and os.path.exists(job.stored_path):
            os.remove(job.stored_path)

        # Delete associated cutoffs
        Cutoff.query.filter(Cutoff.upload_job_id == job.id).delete()

        db.session.delete(job)
        db.session.commit()

        log_action('delete_import', 'upload_job', job_id)
        return jsonify({'ok': True, 'message': 'Job deleted'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500


@admin_bp.route('/api/import/<int:job_id>/progress')
@admin_required
def import_progress(job_id):
    """AJAX endpoint for import progress."""
    job = db.session.get(UploadJob, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Not found'}), 404

    is_running = job_id in get_active_jobs()

    return jsonify({
        'ok': True,
        'status': job.status,
        'total_rows': job.total_rows or 0,
        'valid_rows': job.valid_rows or 0,
        'invalid_rows': job.invalid_rows or 0,
        'duplicate_rows': job.duplicate_rows or 0,
        'error_rows': len(job.error_rows) if job.error_rows else 0,
        'is_running': is_running,
        'started_at': job.started_at.isoformat() if job.started_at else None,
        'completed_at': job.completed_at.isoformat() if job.completed_at else None,
        'error_message': job.error_message,
    })


def _estimate_remaining(job):
    """Estimate remaining seconds for an import job from started_at and progress.

    Returns 0 when the job is not actively running, has no measurable progress,
    or lacks a start timestamp. ETA is linearly extrapolated from elapsed time:
    eta = elapsed * (100 - progress) / progress.
    """
    progress = job.progress_percentage or 0
    if job.status not in ('PENDING', 'PROCESSING') or progress <= 0:
        return 0
    if job.started_at is None:
        return 0

    started_at = job.started_at
    # Treat naive timestamps as UTC so the subtraction stays timezone-aware.
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    if elapsed <= 0:
        return 0

    return int(elapsed * (100 - progress) / progress)


@admin_bp.route('/import-jobs/<int:job_id>/status')
@admin_required
def import_job_status(job_id):
    """JSON progress payload for the live progress page (polled every 2s).

    Returns the most recently committed values for the job, which naturally
    yields final values for terminal COMPLETED/FAILED jobs.
    """
    job = db.session.get(UploadJob, job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    return jsonify({
        'status': job.status,
        'progress': job.progress_percentage or 0,
        'current_step': job.current_step or 'UPLOAD_FILE',
        'processed_pages': job.processed_pages or 0,
        'total_pages': job.total_pages or 0,
        'rows_extracted': job.total_rows_extracted or 0,
        'rows_imported': job.total_rows_imported or 0,
        'failed_rows': job.failed_rows or 0,
        'accuracy': job.accuracy_percentage or 0,
        'auto_created_colleges': job.auto_created_colleges or 0,
        'auto_created_branches': job.auto_created_branches or 0,
        'error_message': job.error_message,
        'eta_seconds': _estimate_remaining(job),
    }), 200


@admin_bp.route('/import-jobs/<int:job_id>/error-report')
@admin_required
def upload_job_error_report(job_id):
    """Download the error report CSV for an import job.

    Always emits a header row first. Each entry in ``job.error_rows`` becomes a
    row of [json-encoded source row, reason]. When there are no failed rows the
    CSV contains only the header row.
    """
    job = db.session.get(UploadJob, job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Row', 'Reason'])

    for err in (job.error_rows or []):
        writer.writerow([json.dumps(err.get('row')), err.get('reason', '')])

    csv_content = output.getvalue()
    output.close()

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename="job_{job_id}_errors.csv"',
            'Content-Type': 'text/csv; charset=utf-8',
        }
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER DATA — Colleges
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/master/colleges')
@admin_required
def master_colleges():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('q', '')
    type_filter = request.args.get('type', '')
    status_filter = request.args.get('status', '')
    admission_type_id = request.args.get('admission_type_id', type=int)

    query = College.query.outerjoin(CollegeAdmissionType)

    if search:
        query = query.filter(
            db.or_(
                College.college_name.ilike(f'%{search}%'),
                College.college_code.ilike(f'%{search}%'),
                College.district.ilike(f'%{search}%'),
                College.city.ilike(f'%{search}%'),
            )
        )
    if type_filter:
        query = query.filter(College.college_type == type_filter)
    if status_filter:
        query = query.filter(College.status == status_filter)
    if admission_type_id:
        query = query.filter(CollegeAdmissionType.admission_type_id == admission_type_id)

    query = query.order_by(College.college_name)
    pagination = query.paginate(page=page, per_page=50, error_out=False)

    college_types = [r[0] for r in db.session.query(College.college_type).distinct().all() if r[0]]
    admission_types = AdmissionType.query.all()

    return render_template('admin/colleges.html',
                           colleges=pagination.items,
                           pagination=pagination,
                           q=search,
                           college_types=college_types,
                           admission_types=admission_types,
                           filters={
                               'type': type_filter,
                               'status': status_filter,
                               'admission_type_id': admission_type_id,
                           })


@admin_bp.route('/master/colleges/add', methods=['POST'])
@admin_required
def master_college_add():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'ok': False, 'error': 'No data provided'}), 400

        college_code = data.get('college_code', '').strip()
        if college_code:
            existing = College.query.filter_by(college_code=college_code).first()
            if existing:
                return jsonify({'ok': False, 'error': f'College code {college_code} already exists'}), 409

        college = College(
            college_code=college_code or None,
            college_name=data.get('college_name', '').strip(),
            district=data.get('district', '').strip(),
            city=data.get('city', '').strip(),
            college_type=data.get('college_type', '').strip(),
            status=data.get('status', 'active'),
        )
        db.session.add(college)
        db.session.flush()

        # Assign admission types
        admission_type_ids = data.get('admission_type_ids', [])
        if admission_type_ids:
            for at_id in admission_type_ids:
                cat = CollegeAdmissionType(college_id=college.id, admission_type_id=at_id)
                db.session.add(cat)

        db.session.commit()

        log_action('add', 'college', college.id, {'college_code': college_code})
        return jsonify({'ok': True, 'college': college.to_dict()}), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f"College add error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@admin_bp.route('/master/colleges/<int:college_id>/edit', methods=['POST'])
@admin_required
def master_college_edit(college_id):
    try:
        college = db.session.get(College, college_id)
        if not college:
            return jsonify({'ok': False, 'error': 'College not found'}), 404

        data = request.get_json()
        if not data:
            return jsonify({'ok': False, 'error': 'No data provided'}), 400

        new_code = data.get('college_code', '').strip()
        if new_code and new_code != college.college_code:
            existing = College.query.filter(College.college_code == new_code, College.id != college_id).first()
            if existing:
                return jsonify({'ok': False, 'error': f'College code {new_code} already exists'}), 409

        college.college_code = new_code or college.college_code
        college.college_name = data.get('college_name', college.college_name)
        college.district = data.get('district', college.district)
        college.city = data.get('city', college.city)
        college.college_type = data.get('college_type', college.college_type)
        college.status = data.get('status', college.status)

        # Update admission types
        admission_type_ids = data.get('admission_type_ids', None)
        if admission_type_ids is not None:
            # Remove existing links
            CollegeAdmissionType.query.filter(CollegeAdmissionType.college_id == college.id).delete()
            # Add new links
            for at_id in admission_type_ids:
                cat = CollegeAdmissionType(college_id=college.id, admission_type_id=at_id)
                db.session.add(cat)

        db.session.commit()

        log_action('edit', 'college', college_id)
        return jsonify({'ok': True, 'college': college.to_dict()})

    except Exception as e:
        db.session.rollback()
        logger.error(f"College edit error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@admin_bp.route('/master/colleges/<int:college_id>/delete', methods=['POST'])
@admin_required
def master_college_delete(college_id):
    college = db.session.get(College, college_id)
    if not college:
        return jsonify({'ok': False, 'error': 'College not found'}), 404

    db.session.delete(college)
    db.session.commit()

    log_action('delete', 'college', college_id)
    return jsonify({'ok': True, 'message': 'College deleted'})


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER DATA — Branches
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/master/branches')
@admin_required
def master_branches():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('q', '')

    query = Branch.query
    if search:
        query = query.filter(
            db.or_(
                Branch.branch_name.ilike(f'%{search}%'),
                Branch.branch_code.ilike(f'%{search}%'),
            )
        )

    query = query.order_by(Branch.branch_name)
    pagination = query.paginate(page=page, per_page=50, error_out=False)

    return render_template('admin/branches.html', branches=pagination.items, pagination=pagination, q=search)


@admin_bp.route('/master/branches/add', methods=['POST'])
@admin_required
def master_branch_add():
    data = request.get_json()
    if not data:
        return jsonify({'ok': False, 'error': 'No data'}), 400

    code = data.get('branch_code', '').strip().upper().replace(' ', '_')
    name = data.get('branch_name', '').strip()

    if not code or not name:
        return jsonify({'ok': False, 'error': 'Branch code and name are required'}), 400

    existing = Branch.query.filter_by(branch_code=code).first()
    if existing:
        return jsonify({'ok': False, 'error': f'Branch code {code} already exists'}), 409

    branch = Branch(branch_code=code, branch_name=name)
    db.session.add(branch)
    db.session.commit()

    return jsonify({'ok': True, 'branch': branch.to_dict()}), 201


@admin_bp.route('/master/branches/<int:branch_id>/edit', methods=['POST'])
@admin_required
def master_branch_edit(branch_id):
    branch = db.session.get(Branch, branch_id)
    if not branch:
        return jsonify({'ok': False, 'error': 'Branch not found'}), 404

    data = request.get_json()
    if data.get('branch_name'):
        branch.branch_name = data['branch_name'].strip()
    if data.get('branch_code'):
        branch.branch_code = data['branch_code'].strip().upper().replace(' ', '_')

    db.session.commit()
    return jsonify({'ok': True, 'branch': branch.to_dict()})


@admin_bp.route('/master/branches/<int:branch_id>/delete', methods=['POST'])
@admin_required
def master_branch_delete(branch_id):
    branch = db.session.get(Branch, branch_id)
    if not branch:
        return jsonify({'ok': False, 'error': 'Branch not found'}), 404

    db.session.delete(branch)
    db.session.commit()
    return jsonify({'ok': True, 'message': 'Branch deleted'})


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER DATA — Categories
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/master/categories')
@admin_required
def master_categories():
    """View all categories with search and pagination."""
    try:
        page = request.args.get('page', 1, type=int)
        search = request.args.get('q', '')

        query = Category.query
        if search:
            query = query.filter(Category.name.ilike(f'%{search}%'))

        query = query.order_by(Category.name)
        pagination = query.paginate(page=page, per_page=50, error_out=False)

        return render_template('admin/categories.html',
                               categories=pagination.items,
                               pagination=pagination,
                               q=search)

    except Exception as e:
        logger.error(f"Categories list error: {e}")
        flash('Unable to load categories.', 'error')
        return render_template('admin/categories.html', categories=[], pagination=None, q='')


@admin_bp.route('/master/categories/add', methods=['POST'])
@admin_required
def master_category_add():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'ok': False, 'error': 'No data'}), 400

        name = data.get('name', '').strip().upper()
        description = data.get('description', '').strip()

        if not name:
            return jsonify({'ok': False, 'error': 'Category name is required'}), 400

        existing = Category.query.filter_by(name=name).first()
        if existing:
            return jsonify({'ok': False, 'error': f'Category {name} already exists'}), 409

        category = Category(name=name, description=description)
        db.session.add(category)
        db.session.commit()

        return jsonify({'ok': True, 'category': category.to_dict()}), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f"Category add error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN — Settings / Profile
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    """Admin settings page."""
    if request.method == 'POST':
        data = request.get_json() or request.form
        admin = _get_admin()

        if data.get('current_password') and data.get('new_password'):
            from app import check_password, hash_password
            if not check_password(data['current_password'], admin.password_hash):
                if request.is_json:
                    return jsonify({'ok': False, 'error': 'Current password is incorrect'}), 400
                flash('Current password is incorrect', 'error')
                return redirect('/admin/settings')

            if len(data['new_password']) < 8:
                if request.is_json:
                    return jsonify({'ok': False, 'error': 'Password must be at least 8 characters'}), 400
                flash('Password must be at least 8 characters', 'error')
                return redirect('/admin/settings')

            admin.password_hash = hash_password(data['new_password'])
            db.session.commit()
            log_action('change_password', 'user', admin.id)

            if request.is_json:
                return jsonify({'ok': True, 'message': 'Password updated'})
            flash('Password updated successfully', 'success')

        return redirect('/admin/settings')

    return render_template('admin/settings.html', admin=_get_admin())


# ═══════════════════════════════════════════════════════════════════════════════
# DB HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/database-status')
@admin_required
def database_status():
    """Health check page for the database connection."""
    status_info = {
        'database_connected': False,
        'database_type': 'Unknown',
        'neon_region': 'N/A',
        'total_cutoff_records': 0,
        'total_new_cutoff_records': 0,
        'total_users': 0,
        'total_uploads': 0,
        'last_import_date': None,
        'connection_pool_size': 0,
    }

    try:
        db.session.execute(text('SELECT 1'))
        status_info['database_connected'] = True

        db_url = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if 'postgresql' in db_url:
            status_info['database_type'] = 'PostgreSQL (Neon)'
        elif 'sqlite' in db_url:
            status_info['database_type'] = 'SQLite (Development Only)'
        else:
            status_info['database_type'] = 'Other'

        import re as re_module
        region_match = re_module.search(r'ep-([a-z-]+)\.c-(\d+)\.(aws|gcp|azure)\.neon\.tech', db_url)
        if region_match:
            status_info['neon_region'] = f"{region_match.group(1)} ({region_match.group(3)})"

        status_info['total_cutoff_records'] = Cutoff.query.count()
        status_info['total_users'] = User.query.count()
        status_info['total_uploads'] = UploadJob.query.count()

        last_cutoff = Cutoff.query.order_by(Cutoff.created_at.desc()).first()
        if last_cutoff and last_cutoff.created_at:
            status_info['last_import_date'] = last_cutoff.created_at.isoformat()

        engine_options = current_app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {})
        status_info['connection_pool_size'] = engine_options.get('pool_size', 0)

    except Exception as e:
        logger.error(f"Database status check failed: {e}")
        status_info['error'] = str(e)

    return render_template('admin/database_status.html', status=status_info)


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT LOGS
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/audit-logs')
@admin_required
def audit_logs_page():
    """View audit log entries."""
    page = request.args.get('page', 1, type=int)
    action_filter = request.args.get('action', '')

    query = AuditLog.query
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)

    query = query.order_by(AuditLog.created_at.desc())
    pagination = query.paginate(page=page, per_page=50, error_out=False)

    actions = [r[0] for r in db.session.query(AuditLog.action).distinct().all()]

    return render_template('admin/audit_log.html',
                           logs=pagination.items,
                           pagination=pagination,
                           actions=actions,
                           current_action=action_filter)


# ═══════════════════════════════════════════════════════════════════════════════
# BACKUPS
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/backups')
@admin_required
def list_backups():
    """View backup history."""
    page = request.args.get('page', 1, type=int)
    pagination = BackupHistory.query.order_by(
        BackupHistory.backup_date.desc()
    ).paginate(page=page, per_page=20, error_out=False)

    return render_template('admin/backups.html', backups=pagination.items, pagination=pagination)


@admin_bp.route('/backups/create', methods=['POST'])
@admin_required
def create_backup_route():
    """Create a new database backup."""
    notes = request.get_json().get('notes', '') if request.is_json else ''
    result = create_backup(notes=notes)

    if result['success']:
        log_action('backup', 'backup', result.get('backup_id'))
        return jsonify({'ok': True, 'backup_id': result['backup_id']})

    return jsonify({'ok': False, 'error': result.get('error', 'Backup failed')}), 500


@admin_bp.route('/backups/<int:backup_id>/restore', methods=['POST'])
@admin_required
def restore_backup_route(backup_id):
    """Restore database from a backup."""
    result = restore_backup(backup_id)
    if result['success']:
        log_action('restore', 'backup', backup_id)
        return jsonify({'ok': True, 'message': 'Database restored. Please restart the app.'})
    return jsonify({'ok': False, 'error': result.get('error', 'Restore failed')}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/users')
@admin_required
def list_users():
    """View all registered users."""
    page = request.args.get('page', 1, type=int)
    search = request.args.get('q', '')

    query = User.query
    if search:
        query = query.filter(
            db.or_(
                User.email.ilike(f'%{search}%'),
                User.first_name.ilike(f'%{search}%'),
                User.last_name.ilike(f'%{search}%'),
            )
        )

    query = query.order_by(User.created_at.desc())
    pagination = query.paginate(page=page, per_page=50, error_out=False)

    return render_template('admin/users.html', users=pagination.items, pagination=pagination, q=search)


@admin_bp.route('/users/<int:user_id>/toggle-admin', methods=['POST'])
@admin_required
def toggle_admin(user_id):
    """Toggle a user's admin role."""
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'ok': False, 'error': 'User not found'}), 404
    if user.id == _get_admin().id:
        return jsonify({'ok': False, 'error': 'Cannot change your own role'}), 400

    user.role = 'user' if user.role == 'admin' else 'admin'
    db.session.commit()
    log_action('toggle_admin', 'user', user_id, {'new_role': user.role})
    return jsonify({'ok': True, 'role': user.role})


# ═══════════════════════════════════════════════════════════════════════════════
# TRENDS
# ═══════════════════════════════════════════════════════════════════════════════

@admin_bp.route('/trends')
@admin_required
def trends_page():
    """View cutoff trends and analytics."""
    college_code = request.args.get('college_code', '')
    year_filter = request.args.get('year', type=int)

    trends = compute_college_trends(college_code=college_code if college_code else None, limit=200)
    branch_pop = compute_branch_popularity(year=year_filter, top_n=15)

    top_trends = [t for t in trends if len(t.get('years', [])) >= 2][:20]

    return render_template('admin/trends.html',
                           trends=top_trends,
                           branch_popularity=branch_pop,
                           college_code=college_code,
                           year_filter=year_filter)


@admin_bp.route('/api/trends/recalculate', methods=['POST'])
@admin_required
def recalculate_trends_api():
    """API to recalculate all trends."""
    from admin.trend_service import store_trend_results
    result = store_trend_results()
    return jsonify({'ok': True, **result})