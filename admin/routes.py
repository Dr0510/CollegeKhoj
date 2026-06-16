"""
Admin Management Routes for CollegeKhoj.

All routes are prefixed with /admin (via the blueprint).
Protected by admin_required decorator.
"""
import os
import json
import logging
from datetime import datetime
from flask import render_template, request, jsonify, redirect, session, g, flash, current_app

from database import db
from admin import admin_bp
from admin.audit import log_action
from admin.pdf_extractor import extract_pdf
from admin.validation_service import validate_all
from admin.backup_service import create_backup, restore_backup, BACKUP_DIR
from admin.trend_service import (
    compute_college_trends, compute_branch_popularity,
    get_safe_moderate_dream, recalculate_all_trends
)
from auth_decorators import login_required, admin_required
from models import User, College, CAPCutoff, UploadedFile, BackupHistory, AuditLog

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.environ.get(
    'ADMIN_UPLOAD_DIR',
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads', 'cutoffs')
)


# ── Helper ──────────────────────────────────────────────────────────────────

def _get_admin():
    """Get the currently logged-in admin from g.user, or None."""
    user = g.get('user')
    if user and user.is_admin():
        return user
    return None


def _ensure_upload_dir(year=None):
    """Create upload subdirectory for the given year."""
    base = UPLOAD_DIR
    if year:
        base = os.path.join(UPLOAD_DIR, str(year))
    os.makedirs(base, exist_ok=True)
    return base


def _save_uploaded_file(file_storage, year=None):
    """Save an uploaded file permanently and return the stored path."""
    _ensure_upload_dir(year)
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    safe_name = f"{timestamp}_{file_storage.filename}"
    if year:
        dest = os.path.join(UPLOAD_DIR, str(year), safe_name)
    else:
        dest = os.path.join(UPLOAD_DIR, safe_name)
    file_storage.save(dest)
    return dest


# ── Admin Login (no auth required) ──────────────────────────────────────────


@admin_bp.route('/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page (separate from user login)."""
    if _get_admin():
        return redirect('/admin/dashboard')

    # Capture next URL for redirect-after-login
    next_url = request.args.get('next') or request.form.get('next') or '/admin/dashboard'

    if request.method == 'POST':
        data = request.get_json() or request.form
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        user = User.query.filter_by(email=email, role='admin').first()
        if not user:
            if request.is_json:
                return jsonify({'ok': False, 'error': 'Invalid admin credentials'}), 401
            flash('Invalid admin credentials', 'error')
            return render_template('login.html', error='Invalid credentials', next_url=next_url)

        # Verify password (using bcrypt from app)
        from app import check_password
        if not user.password_hash or not check_password(password, user.password_hash):
            if request.is_json:
                return jsonify({'ok': False, 'error': 'Invalid admin credentials'}), 401
            flash('Invalid admin credentials', 'error')
            return render_template('login.html', error='Invalid credentials', next_url=next_url)

        if not user.is_verified:
            if request.is_json:
                return jsonify({'ok': False, 'error': 'Admin account not verified'}), 403
            flash('Admin account not verified', 'error')
            return render_template('login.html', error='Account not verified', next_url=next_url)

        # Login
        session['user_id'] = user.id
        session['role'] = user.role
        log_action('login', 'user', user.id)

        flash('Welcome Admin', 'success')

        if request.is_json:
            return jsonify({'ok': True, 'redirect': next_url})

        return redirect(next_url)

    return render_template('login.html', next_url=next_url)


# ── Dashboard ───────────────────────────────────────────────────────────────


@admin_bp.route('/dashboard')
@admin_required
def admin_dashboard():
    """Dashboard with stats cards."""
    total_colleges = College.query.count()
    total_cutoffs = CAPCutoff.query.count()
    total_users = User.query.count()
    total_uploads = UploadedFile.query.count()

    last_upload = UploadedFile.query.order_by(UploadedFile.created_at.desc()).first()
    last_cutoff = CAPCutoff.query.order_by(CAPCutoff.imported_at.desc()).first()

    recent_uploads = UploadedFile.query.order_by(
        UploadedFile.created_at.desc()
    ).limit(5).all()

    backup_count = BackupHistory.query.count()
    latest_backup = BackupHistory.query.order_by(BackupHistory.backup_date.desc()).first()

    return render_template('dashboard.html',
                           total_colleges=total_colleges,
                           total_cutoffs=total_cutoffs,
                           total_users=total_users,
                           total_uploads=total_uploads,
                           last_upload=last_upload,
                           last_cutoff_date=last_cutoff.imported_at if last_cutoff else None,
                           recent_uploads=recent_uploads,
                           backup_count=backup_count,
                           latest_backup=latest_backup)


# ── Upload PDF ──────────────────────────────────────────────────────────────


@admin_bp.route('/upload-cutoff', methods=['GET', 'POST'])
@admin_required
def upload_cutoff():
    """Upload a CAP Round cutoff PDF, extract data, show preview."""
    if request.method == 'GET':
        return render_template('upload_cutoff.html')

    # Handle POST
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'ok': False, 'error': 'No file selected'}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'ok': False, 'error': 'Only PDF files are supported'}), 400

    try:
        # 1. Save file permanently
        stored_path = _save_uploaded_file(file)
        file_size = os.path.getsize(stored_path)

        # 2. Create UploadedFile record (pending)
        upload_record = UploadedFile(
            filename=file.filename,
            stored_path=stored_path,
            file_size=file_size,
            mime_type='application/pdf',
            processed_status='pending',
            uploaded_by=_get_admin().id,
        )
        db.session.add(upload_record)
        db.session.flush()
        upload_id = upload_record.id

        # 3. Extract PDF data
        extraction = extract_pdf(stored_path, file.filename)

        # Update with detected metadata
        upload_record.year = extraction['year']
        upload_record.round_number = extraction['round_number']
        upload_record.extraction_method = extraction['method']
        upload_record.extraction_confidence = extraction['confidence']
        db.session.flush()

        # 4. Validate extracted rows
        if extraction['rows']:
            year = extraction['year']
            round_num = extraction['round_number']
            validation_result = validate_all(extraction['rows'], year, round_num)
            upload_record.total_rows = validation_result.summary['total']
            upload_record.valid_rows = validation_result.summary['valid']
            upload_record.rejected_rows = validation_result.summary['rejected']
            upload_record.duplicate_rows = validation_result.summary['duplicates']

            # Store preview data as JSON
            preview = {
                'rows': validation_result.valid_rows[:500],  # limit preview
                'rejected': [
                    {'row': r[0], 'reason': r[1]}
                    for r in validation_result.rejected_rows
                ],
                'duplicates': [
                    {'row': d[0]} for d in validation_result.duplicate_rows
                ],
                'summary': validation_result.summary,
                'year': year,
                'round_number': round_num,
                'method': extraction['method'],
                'confidence': extraction['confidence'],
            }
            upload_record.preview_data = preview
            upload_record.validation_report = validation_result.to_dict()
            upload_record.processed_status = 'preview'
        else:
            upload_record.processed_status = 'failed'
            upload_record.validation_report = {'error': 'No rows could be extracted from the PDF'}

        db.session.commit()

        # 5. Log audit
        log_action('upload', 'uploaded_file', upload_id, {
            'filename': file.filename,
            'year': extraction['year'],
            'round': extraction['round_number'],
            'rows': len(extraction['rows']),
            'confidence': extraction['confidence'],
        })

        if request.is_json:
            return jsonify({
                'ok': True,
                'upload_id': upload_id,
                'preview': upload_record.preview_data,
                'redirect': f'/admin/upload-cutoff/{upload_id}/preview',
            })

        return redirect(f'/admin/upload-cutoff/{upload_id}/preview')

    except Exception as e:
        logger.error(f"Upload error: {e}")
        db.session.rollback()
        if request.is_json:
            return jsonify({'ok': False, 'error': str(e)}), 500
        flash(f'Upload failed: {str(e)}', 'error')
        return render_template('upload_cutoff.html')


@admin_bp.route('/upload-cutoff/<int:upload_id>/preview')
@admin_required
def upload_preview(upload_id):
    """Show preview of parsed data before committing."""
    upload = db.session.get(UploadedFile, upload_id)
    if not upload:
        flash('Upload record not found', 'error')
        return redirect('/admin/upload-cutoff')

    return render_template('import_preview.html', upload=upload)


@admin_bp.route('/upload-cutoff/<int:upload_id>/commit', methods=['POST'])
@admin_required
def commit_import(upload_id):
    """Commit validated rows into the CAPCutoff table."""
    upload = db.session.get(UploadedFile, upload_id)
    if not upload:
        return jsonify({'ok': False, 'error': 'Upload record not found'}), 404
    if upload.processed_status != 'preview':
        return jsonify({'ok': False, 'error': f"Invalid status: {upload.processed_status}"}), 400

    preview = upload.preview_data
    if not preview or not preview.get('rows'):
        return jsonify({'ok': False, 'error': 'No valid rows to import'}), 400

    try:
        # 1. Create backup before import
        backup = create_backup(notes=f"Auto-backup before importing {upload.filename}")
        if not backup['success']:
            logger.warning(f"Pre-import backup failed: {backup.get('error')}")

        # 2. Batch insert valid rows
        year = preview.get('year') or upload.year
        round_num = preview.get('round_number') or upload.round_number
        rows = preview['rows']
        imported_count = 0

        for row in rows:
            # Find or skip college by code
            college_code = row.get('college_code', '')
            branch = row.get('branch', '')

            # Try to find matching college in DB
            college = None
            if college_code:
                # Match by college_code -> try to find college name similarity
                college = College.query.filter(
                    College.college.ilike(f'%{row.get("college_name", "")[:20]}%')
                ).first()

            if not college and row.get('college_name'):
                college = College.query.filter(
                    College.college.ilike(f'%{row["college_name"][:20]}%')
                ).first()

            if not college:
                # Create a temporary college entry if not found
                college = College(
                    college=row.get('college_name', f'Institute {college_code}'),
                    location='Unknown',
                    branch=branch or 'General',
                    fees=0,
                    placement_rate=0,
                    nirf_rank=999,
                    rating=0,
                )
                db.session.add(college)
                db.session.flush()

            cutoff = CAPCutoff(
                college_id=college.id,
                college_code=college_code,
                year=row.get('year', year),
                round_number=row.get('round_number', round_num),
                category=row.get('category', 'Open'),
                gender=row.get('gender', 'Gender-Neutral'),
                cutoff_percentile=row['cutoff_percentile'],
                source_file_id=upload.id,
                validation_status='validated',
                imported_at=datetime.utcnow(),
            )
            # Store branch info in college_code if branch differs
            if branch and branch != college.branch:
                cutoff.college_code = f"{college_code}|{branch}"
            db.session.add(cutoff)
            imported_count += 1

        # 3. Update upload record
        upload.processed_status = 'committed'
        upload.committed_at = datetime.utcnow()
        db.session.commit()

        # 4. Recalculate trends
        trends = recalculate_all_trends()

        # 5. Log audit
        log_action('import_commit', 'uploaded_file', upload.id, {
            'imported': imported_count,
            'year': year,
            'round': round_num,
        })

        logger.info(f"Import committed: {imported_count} rows from {upload.filename}")

        return jsonify({
            'ok': True,
            'imported': imported_count,
            'trends': trends,
            'message': f'Successfully imported {imported_count} cutoff records',
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Import commit failed: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@admin_bp.route('/upload-cutoff/<int:upload_id>/cancel', methods=['POST'])
@admin_required
def cancel_import(upload_id):
    """Cancel an import (mark as failed/discarded)."""
    upload = db.session.get(UploadedFile, upload_id)
    if not upload:
        return jsonify({'ok': False, 'error': 'Upload record not found'}), 404

    upload.processed_status = 'failed'
    db.session.commit()
    log_action('cancel_import', 'uploaded_file', upload.id)
    return jsonify({'ok': True})


# ── Cutoff Records ──────────────────────────────────────────────────────────


@admin_bp.route('/cutoffs')
@admin_required
def list_cutoffs():
    """View and search cutoff records."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    year = request.args.get('year', type=int)
    category = request.args.get('category', '')
    college_code = request.args.get('college_code', '')
    search = request.args.get('q', '')

    query = CAPCutoff.query

    if year:
        query = query.filter(CAPCutoff.year == year)
    if category:
        query = query.filter(CAPCutoff.category == category)
    if college_code:
        query = query.filter(CAPCutoff.college_code.ilike(f'%{college_code}%'))
    if search:
        query = query.join(College).filter(
            db.or_(
                College.college.ilike(f'%{search}%'),
                CAPCutoff.college_code.ilike(f'%{search}%'),
            )
        )

    query = query.order_by(CAPCutoff.year.desc(), CAPCutoff.college_code)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    # Get distinct filter values
    years = [r[0] for r in db.session.query(CAPCutoff.year).distinct().order_by(CAPCutoff.year.desc()).all()]
    categories = [r[0] for r in db.session.query(CAPCutoff.category).distinct().all()]

    return render_template('cutoffs.html',
                           cutoffs=pagination.items,
                           pagination=pagination,
                           years=years,
                           categories=categories,
                           filters={
                               'year': year,
                               'category': category,
                               'college_code': college_code,
                               'q': search,
                           })


@admin_bp.route('/cutoffs/<int:cutoff_id>/delete', methods=['POST'])
@admin_required
def delete_cutoff(cutoff_id):
    """Delete a single cutoff record."""
    cutoff = db.session.get(CAPCutoff, cutoff_id)
    if not cutoff:
        return jsonify({'ok': False, 'error': 'Cutoff record not found'}), 404

    db.session.delete(cutoff)
    db.session.commit()
    log_action('delete', 'cutoff', cutoff_id)
    return jsonify({'ok': True})


# ── Colleges ────────────────────────────────────────────────────────────────


@admin_bp.route('/colleges')
@admin_required
def list_colleges():
    """View all colleges."""
    page = request.args.get('page', 1, type=int)
    search = request.args.get('q', '')

    query = College.query
    if search:
        query = query.filter(
            db.or_(
                College.college.ilike(f'%{search}%'),
                College.location.ilike(f'%{search}%'),
                College.branch.ilike(f'%{search}%'),
            )
        )

    query = query.order_by(College.nirf_rank)
    pagination = query.paginate(page=page, per_page=50, error_out=False)

    return render_template('colleges.html', colleges=pagination.items, pagination=pagination, q=search)


@admin_bp.route('/colleges/<int:college_id>/edit', methods=['POST'])
@admin_required
def edit_college(college_id):
    """Edit a college entry."""
    college = db.session.get(College, college_id)
    if not college:
        return jsonify({'ok': False, 'error': 'College not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'ok': False, 'error': 'No data provided'}), 400

    college.college = data.get('college', college.college)
    college.location = data.get('location', college.location)
    college.branch = data.get('branch', college.branch)
    college.fees = data.get('fees', college.fees)
    college.placement_rate = data.get('placement_rate', college.placement_rate)
    college.nirf_rank = data.get('nirf_rank', college.nirf_rank)
    college.rating = data.get('rating', college.rating)

    db.session.commit()
    log_action('edit', 'college', college_id)
    return jsonify({'ok': True, 'college': college.to_dict()})


# ── Users ───────────────────────────────────────────────────────────────────


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

    return render_template('users.html', users=pagination.items, pagination=pagination, q=search)


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


# ── Trends ──────────────────────────────────────────────────────────────────


@admin_bp.route('/trends')
@admin_required
def trends_page():
    """View cutoff trends and analytics."""
    college_code = request.args.get('college_code', '')
    year_filter = request.args.get('year', type=int)

    trends = compute_college_trends(college_code=college_code if college_code else None, limit=200)
    branch_pop = compute_branch_popularity(year=year_filter, top_n=15)

    # Get top colleges with most trend data
    top_trends = [t for t in trends if len(t.get('years', [])) >= 2][:20]

    return render_template('trends.html',
                           trends=top_trends,
                           branch_popularity=branch_pop,
                           college_code=college_code,
                           year_filter=year_filter)


@admin_bp.route('/api/trends/recalculate', methods=['POST'])
@admin_required
def recalculate_trends_api():
    """API to recalculate all trends."""
    result = recalculate_all_trends()
    return jsonify({'ok': True, **result})


# ── Backups ─────────────────────────────────────────────────────────────────


@admin_bp.route('/backups')
@admin_required
def list_backups():
    """View backup history."""
    page = request.args.get('page', 1, type=int)
    pagination = BackupHistory.query.order_by(
        BackupHistory.backup_date.desc()
    ).paginate(page=page, per_page=20, error_out=False)

    return render_template('backups.html', backups=pagination.items, pagination=pagination)


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


# ── Audit Logs ──────────────────────────────────────────────────────────────


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

    # Get distinct action types for filter
    actions = [r[0] for r in db.session.query(AuditLog.action).distinct().all()]

    return render_template('audit_log.html',
                           logs=pagination.items,
                           pagination=pagination,
                           actions=actions,
                           current_action=action_filter)


# ── Settings ────────────────────────────────────────────────────────────────


@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    """Admin settings page."""
    if request.method == 'POST':
        data = request.get_json() or request.form
        admin = _get_admin()

        # Change password
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

    return render_template('settings.html', admin=_get_admin())


# ── Logout ──────────────────────────────────────────────────────────────────


@admin_bp.route('/logout')
@admin_required
def admin_logout():
    """Admin logout."""
    log_action('logout', 'user', _get_admin().id)
    session.clear()
    return redirect('/admin/login')


# ── Dashboard API ───────────────────────────────────────────────────────────


@admin_bp.route('/api/dashboard-stats')
@admin_required
def dashboard_stats_api():
    """JSON stats for dashboard."""
    return jsonify({
        'total_colleges': College.query.count(),
        'total_cutoffs': CAPCutoff.query.count(),
        'total_users': User.query.count(),
        'total_uploads': UploadedFile.query.count(),
        'total_backups': BackupHistory.query.filter_by(status='success').count(),
    })