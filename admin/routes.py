"""
Admin Management Routes for CollegeKhoj.

All routes are prefixed with /admin (via the blueprint).
Protected by admin_required decorator.

All production data is stored in Neon PostgreSQL.
"""
import os
import csv
import json
import io
import logging
import threading
from datetime import datetime
from flask import render_template, request, jsonify, redirect, session, g, flash, current_app, Response

from database import db
from admin import admin_bp
from admin.audit import log_action
from admin.pdf_extractor import extract_pdf
from admin.validation_service import validate_all
from admin.backup_service import create_backup, restore_backup, BACKUP_DIR
from admin.trend_service import (
    compute_college_trends, compute_branch_popularity,
    get_safe_moderate_dream, recalculate_all_trends,
    store_trend_results
)
from auth_decorators import login_required, admin_required
from models import (
    User, College, CAPCutoff, CollegeCutoff, 
    UploadedFile, BackupHistory, AuditLog, ImportJob, CollegeTrend,
    ApprovalRequest, BulkActionBackup
)
from sqlalchemy import func as sa_func
from sqlalchemy import insert, text, func, and_

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
    total_cutoffs_new = CollegeCutoff.query.count()
    total_users = User.query.count()
    total_uploads = UploadedFile.query.count()

    # Summary stats for new cutoffs table
    distinct_colleges = 0
    distinct_courses = 0
    if total_cutoffs_new > 0:
        distinct_colleges = db.session.query(func.count(db.distinct(CollegeCutoff.college_code))).scalar() or 0
        distinct_courses = db.session.query(func.count(db.distinct(CollegeCutoff.course_code))).scalar() or 0

    last_upload = UploadedFile.query.order_by(UploadedFile.created_at.desc()).first()
    last_cutoff = CollegeCutoff.query.order_by(CollegeCutoff.imported_at.desc()).first()

    recent_uploads = UploadedFile.query.order_by(
        UploadedFile.created_at.desc()
    ).limit(5).all()

    # ── Approval Stats ──
    pending_approvals = ImportJob.query.filter(
        ImportJob.approval_status == 'pending_approval'
    ).count()
    approved_imports = ImportJob.query.filter(
        ImportJob.approval_status == 'approved'
    ).count()
    rejected_imports = ImportJob.query.filter(
        ImportJob.approval_status == 'rejected'
    ).count()

    return render_template('dashboard.html',
                           total_colleges=total_colleges,
                           total_cutoffs=total_cutoffs + total_cutoffs_new,
                           total_users=total_users,
                           total_uploads=total_uploads,
                           total_cutoffs_new=total_cutoffs_new,
                           distinct_colleges=distinct_colleges,
                           distinct_courses=distinct_courses,
                           last_upload=last_upload,
                           last_cutoff_date=last_cutoff.imported_at if last_cutoff else None,
                           recent_uploads=recent_uploads,
                           pending_approvals=pending_approvals,
                           approved_imports=approved_imports,
                           rejected_imports=rejected_imports)


# ── Upload PDF ──────────────────────────────────────────────────────────────


@admin_bp.route('/upload-cutoff', methods=['GET', 'POST'])
@admin_required
def upload_cutoff():
    """Upload a CAP Round cutoff PDF, parse it, show preview before saving."""
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

        # 2. Extract PDF data synchronously for preview
        extraction = extract_pdf(stored_path, file.filename)

        if extraction.get('error'):
            logger.error(f"PDF extraction failed: {extraction['error']}")
            return jsonify({'ok': False, 'error': extraction['error'][:500]}), 400

        rows = extraction.get('rows', [])
        year = extraction['year'] or 2025
        round_num = extraction['round'] or 1

        if not rows:
            return jsonify({'ok': False, 'error': 'No data could be extracted from the PDF'}), 400

        # 3. Count duplicates against existing records
        duplicate_count = 0
        if rows:
            # Check for existing records matching (year, round, college_code, course_code, category)
            keys_to_check = set()
            for row in rows:
                keys_to_check.add((row['year'], row['round'], row['college_code'],
                                   row['course_code'], row['category']))

            existing = set()
            if keys_to_check:
                # Batch check existing records
                for yr, rd, cc, ccode, cat in keys_to_check:
                    exists = db.session.execute(
                        text("SELECT 1 FROM college_cutoffs WHERE year=:y AND round=:r "
                             "AND college_code=:cc AND course_code=:ccc AND category=:cat LIMIT 1"),
                        {'y': yr, 'r': rd, 'cc': cc, 'ccc': ccode, 'cat': cat}
                    ).scalar()
                    if exists:
                        existing.add((yr, rd, cc, ccode, cat))

            duplicate_count = sum(
                1 for row in rows
                if (row['year'], row['round'], row['college_code'],
                    row['course_code'], row['category']) in existing
            )

        # 4. Compute unique colleges and courses from parsed data
        unique_colleges = set(row['college_code'] for row in rows)
        unique_courses = set(row['course_code'] for row in rows)

        # 5. Preview data (first 50 rows for display)
        preview_rows = []
        for row in rows[:50]:
            preview_rows.append({
                'college_code': row['college_code'],
                'college_name': row['college_name'][:60],
                'course_code': row['course_code'],
                'course_name': row['course_name'],
                'category': row['category'],
                'rank': row['rank'],
                'percentile': row['percentile'],
            })

        # 6. Create UploadedFile record
        admin_user = _get_admin()
        upload_record = UploadedFile(
            filename=file.filename,
            stored_path=stored_path,
            file_size=file_size,
            mime_type='application/pdf',
            processed_status='preview',
            uploaded_by=admin_user.id,
            year=year,
            round_number=round_num,
            total_rows=len(rows),
            valid_rows=len(rows) - duplicate_count,
            duplicate_rows=duplicate_count,
            extraction_method=extraction['method'],
            extraction_confidence=extraction['confidence'],
            preview_data={
                'rows': preview_rows,
                'total_rows': len(rows),
                'year': year,
                'round': round_num,
                'method': extraction['method'],
                'confidence': extraction['confidence'],
                'duplicate_count': duplicate_count,
                'unique_colleges': len(unique_colleges),
                'unique_courses': len(unique_courses),
            }
        )
        db.session.add(upload_record)
        db.session.flush()
        upload_id = upload_record.id
        db.session.commit()

        log_action('upload_preview', 'uploaded_file', upload_id, {
            'filename': file.filename,
            'year': year,
            'round': round_num,
            'rows': len(rows),
            'duplicates': duplicate_count,
        })

        logger.info(
            f"Upload preview ready: file#{upload_id}, {len(rows)} rows "
            f"({duplicate_count} duplicates), {len(unique_colleges)} colleges, "
            f"{len(unique_courses)} courses"
        )

        return jsonify({
            'ok': True,
            'upload_id': upload_id,
            'year': year,
            'round': round_num,
            'total_rows': len(rows),
            'duplicate_count': duplicate_count,
            'unique_colleges': len(unique_colleges),
            'unique_courses': len(unique_courses),
            'message': f'Parsed {len(rows)} records from PDF. Review and confirm import.',
        })

    except Exception as e:
        logger.error(f"Upload error: {e}")
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500


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
    """Commit parsed rows into the CollegeCutoff table with duplicate detection."""
    upload = db.session.get(UploadedFile, upload_id)
    if not upload:
        return jsonify({'ok': False, 'error': 'Upload record not found'}), 404
    if upload.processed_status not in ('preview', 'pending'):
        return jsonify({'ok': False, 'error': f"Invalid status: {upload.processed_status}"}), 400

    try:
        # Re-parse the PDF to get fresh data
        extraction = extract_pdf(upload.stored_path, upload.filename)
        if extraction.get('error'):
            return jsonify({'ok': False, 'error': extraction['error']}), 400

        rows = extraction.get('rows', [])
        if not rows:
            return jsonify({'ok': False, 'error': 'No rows extracted'}), 400

        year = extraction['year'] or upload.year or 2025
        round_num = extraction['round'] or upload.round_number or 1

        # Insert with duplicate detection using ON CONFLICT
        imported_count = 0
        duplicate_count = 0
        records_to_insert = []

        for row in rows:
            records_to_insert.append({
                'year': row.get('year', year),
                'round': row.get('round', round_num),
                'college_code': row['college_code'],
                'college_name': row['college_name'],
                'course_code': row['course_code'],
                'course_name': row['course_name'],
                'category': row['category'],
                'rank': row.get('rank'),
                'percentile': row.get('percentile'),
                'source_file_id': upload.id,
                'imported_at': datetime.utcnow(),
            })

        # Bulk insert with ON CONFLICT DO NOTHING
        if records_to_insert:
            # Use batch insert with individual conflict handling
            for rec in records_to_insert:
                try:
                    db.session.execute(
                        text("""
                            INSERT INTO college_cutoffs 
                            (year, round, college_code, college_name, course_code, course_name,
                             category, rank, percentile, source_file_id, imported_at)
                            VALUES (:year, :round, :college_code, :college_name, :course_code,
                                    :course_name, :category, :rank, :percentile,
                                    :source_file_id, :imported_at)
                            ON CONFLICT (year, round, college_code, course_code, category)
                            DO NOTHING
                        """),
                        rec
                    )
                    if db.session.execute(text("GET DIAGNOSTICS v = ROW_COUNT")).scalar() if hasattr(db, 'dialect') else True:
                        # Check if row was inserted
                        pass
                except Exception as e:
                    db.session.rollback()
                    logger.warning(f"Insert failed for {rec}: {e}")
                    continue

            db.session.commit()

            # Count what was actually inserted vs duplicates
            result = db.session.execute(
                text("SELECT count(*) FROM college_cutoffs WHERE source_file_id = :fid"),
                {'fid': upload.id}
            )
            imported_count = result.scalar() or 0
            duplicate_count = len(records_to_insert) - imported_count

        # Compute summary stats
        distinct_colleges = db.session.execute(
            text("SELECT count(DISTINCT college_code) FROM college_cutoffs WHERE source_file_id = :fid"),
            {'fid': upload.id}
        ).scalar() or 0

        distinct_courses = db.session.execute(
            text("SELECT count(DISTINCT course_code) FROM college_cutoffs WHERE source_file_id = :fid"),
            {'fid': upload.id}
        ).scalar() or 0

        # Update upload record
        upload.processed_status = 'committed'
        upload.committed_at = datetime.utcnow()
        upload.total_rows = len(rows)
        upload.valid_rows = imported_count
        upload.duplicate_rows = duplicate_count
        db.session.commit()

        log_action('import_commit', 'uploaded_file', upload.id, {
            'imported': imported_count,
            'duplicates': duplicate_count,
            'year': year,
            'round': round_num,
            'colleges': distinct_colleges,
            'courses': distinct_courses,
        })

        logger.info(
            f"Import committed: {imported_count} rows (+{duplicate_count} duplicates skipped) "
            f"from {upload.filename}"
        )

        return jsonify({
            'ok': True,
            'imported': imported_count,
            'duplicates_skipped': duplicate_count,
            'colleges_found': distinct_colleges,
            'courses_found': distinct_courses,
            'message': (
                f'Successfully imported {imported_count} cutoff records. '
                f'Duplicates skipped: {duplicate_count}. '
                f'Colleges: {distinct_colleges}. Courses: {distinct_courses}.'
            ),
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

    log_action('cancel_import', 'uploaded_file', upload_id)
    return jsonify({'ok': True})


# ── Upload History ──────────────────────────────────────────────────────────


@admin_bp.route('/uploads')
@admin_required
def uploads_history():
    """View all uploaded PDFs with their import history."""
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')

    query = UploadedFile.query

    if status_filter:
        query = query.filter(UploadedFile.processed_status == status_filter)

    query = query.order_by(UploadedFile.created_at.desc())
    pagination = query.paginate(page=page, per_page=20, error_out=False)

    # Get stats for each upload
    upload_stats = {}
    for u in pagination.items:
        if u.processed_status == 'committed':
            colleges = db.session.execute(
                text("SELECT count(DISTINCT college_code) FROM college_cutoffs WHERE source_file_id = :fid"),
                {'fid': u.id}
            ).scalar() or 0
            courses = db.session.execute(
                text("SELECT count(DISTINCT course_code) FROM college_cutoffs WHERE source_file_id = :fid"),
                {'fid': u.id}
            ).scalar() or 0
            upload_stats[u.id] = {'colleges': colleges, 'courses': courses}
        else:
            upload_stats[u.id] = {'colleges': 0, 'courses': 0}

    return render_template('uploads.html',
                           uploads=pagination.items,
                           pagination=pagination,
                           status_filter=status_filter,
                           upload_stats=upload_stats)


@admin_bp.route('/uploads/<int:upload_id>/delete', methods=['POST'])
@admin_required
def delete_upload(upload_id):
    """Delete an uploaded PDF and its imported records."""
    upload = db.session.get(UploadedFile, upload_id)
    if not upload:
        return jsonify({'ok': False, 'error': 'Upload record not found'}), 404

    try:
        # Delete associated cutoff records
        if upload.processed_status == 'committed':
            db.session.execute(
                text("DELETE FROM college_cutoffs WHERE source_file_id = :fid"),
                {'fid': upload.id}
            )

        # Delete the physical file
        if os.path.exists(upload.stored_path):
            os.remove(upload.stored_path)

        # Delete the upload record
        db.session.delete(upload)
        db.session.commit()

        log_action('delete_upload', 'uploaded_file', upload_id)
        return jsonify({'ok': True, 'message': 'Upload deleted successfully'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Delete upload failed: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@admin_bp.route('/uploads/<int:upload_id>/reimport', methods=['POST'])
@admin_required
def reimport_upload(upload_id):
    """Re-import a PDF (delete existing records, re-parse, re-import)."""
    upload = db.session.get(UploadedFile, upload_id)
    if not upload:
        return jsonify({'ok': False, 'error': 'Upload record not found'}), 404

    try:
        # Delete existing records from this source
        if upload.processed_status == 'committed':
            deleted = db.session.execute(
                text("DELETE FROM college_cutoffs WHERE source_file_id = :fid"),
                {'fid': upload.id}
            ).rowcount
            logger.info(f"Deleted {deleted} existing records for reimport of file#{upload_id}")

        # Reset status
        upload.processed_status = 'pending'
        upload.committed_at = None
        upload.total_rows = 0
        upload.valid_rows = 0
        upload.duplicate_rows = 0
        db.session.commit()

        # Re-parse and re-import via the commit endpoint
        return jsonify({
            'ok': True,
            'message': 'Existing records deleted. Ready for re-import.',
            'redirect': f'/admin/upload-cutoff/{upload_id}/preview'
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Reimport failed: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── CSV Export ──────────────────────────────────────────────────────────────


@admin_bp.route('/cutoffs/export')
@admin_required
def export_cutoffs_csv():
    """Export cutoff records as CSV."""
    year = request.args.get('year', type=int)
    round_num = request.args.get('round', type=int)
    category = request.args.get('category', '')
    college_code = request.args.get('college_code', '')

    query = CollegeCutoff.query

    if year:
        query = query.filter(CollegeCutoff.year == year)
    if round_num:
        query = query.filter(CollegeCutoff.round == round_num)
    if category:
        query = query.filter(CollegeCutoff.category == category)
    if college_code:
        query = query.filter(CollegeCutoff.college_code == college_code)

    query = query.order_by(CollegeCutoff.year.desc(), CollegeCutoff.round,
                           CollegeCutoff.college_code, CollegeCutoff.category)
    records = query.all()

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Year', 'Round', 'College Code', 'College Name',
        'Course Code', 'Course Name', 'Category', 'Rank', 'Percentile'
    ])

    for rec in records:
        writer.writerow([
            rec.year,
            rec.round,
            rec.college_code,
            rec.college_name,
            rec.course_code,
            rec.course_name,
            rec.category,
            rec.rank,
            float(rec.percentile) if rec.percentile else '',
        ])

    csv_content = output.getvalue()
    output.close()

    # Build filename
    parts = ['cutoffs']
    if year:
        parts.append(str(year))
    if round_num:
        parts.append(f'R{round_num}')
    filename = '_'.join(parts) + '.csv'

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': 'text/csv; charset=utf-8',
        }
    )


# ── Cutoff Records (New table) ──────────────────────────────────────────────


@admin_bp.route('/cutoffs')
@admin_required
def list_cutoffs():
    """View and search cutoff records from the new college_cutoffs table."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    year = request.args.get('year', type=int)
    round_num = request.args.get('round', type=int)
    category = request.args.get('category', '')
    college_code = request.args.get('college_code', '')
    search = request.args.get('q', '')

    query = CollegeCutoff.query

    if year:
        query = query.filter(CollegeCutoff.year == year)
    if round_num:
        query = query.filter(CollegeCutoff.round == round_num)
    if category:
        query = query.filter(CollegeCutoff.category == category)
    if college_code:
        query = query.filter(CollegeCutoff.college_code.ilike(f'%{college_code}%'))
    if search:
        query = query.filter(
            db.or_(
                CollegeCutoff.college_name.ilike(f'%{search}%'),
                CollegeCutoff.course_name.ilike(f'%{search}%'),
                CollegeCutoff.college_code.ilike(f'%{search}%'),
            )
        )

    query = query.order_by(CollegeCutoff.year.desc(), CollegeCutoff.round,
                           CollegeCutoff.college_code, CollegeCutoff.category)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    # Get distinct filter values
    years = [r[0] for r in db.session.query(CollegeCutoff.year).distinct().order_by(
        CollegeCutoff.year.desc()).all()]
    categories = [r[0] for r in db.session.query(CollegeCutoff.category).distinct().all()]
    rounds = [r[0] for r in db.session.query(CollegeCutoff.round).distinct().order_by(
        CollegeCutoff.round).all()]

    return render_template('cutoffs.html',
                           cutoffs=pagination.items,
                           pagination=pagination,
                           years=years,
                           rounds=rounds,
                           categories=categories,
                           filters={
                               'year': year,
                               'round': round_num,
                               'category': category,
                               'college_code': college_code,
                               'q': search,
                           })


@admin_bp.route('/cutoffs/<int:cutoff_id>/delete', methods=['POST'])
@admin_required
def delete_cutoff(cutoff_id):
    """Delete a single cutoff record."""
    cutoff = db.session.get(CollegeCutoff, cutoff_id)
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
    result = store_trend_results()
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


# ── Database Health Page ────────────────────────────────────────────────────


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

        status_info['total_cutoff_records'] = CAPCutoff.query.count()
        status_info['total_new_cutoff_records'] = CollegeCutoff.query.count()
        status_info['total_users'] = User.query.count()
        status_info['total_uploads'] = UploadedFile.query.count()

        last_cutoff = CollegeCutoff.query.order_by(CollegeCutoff.imported_at.desc()).first()
        if last_cutoff and last_cutoff.imported_at:
            status_info['last_import_date'] = last_cutoff.imported_at.isoformat()

        engine_options = current_app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {})
        status_info['connection_pool_size'] = engine_options.get('pool_size', 0)

    except Exception as e:
        logger.error(f"Database status check failed: {e}")
        status_info['error'] = str(e)

    return render_template('database_status.html', status=status_info)


# ── Dashboard API ───────────────────────────────────────────────────────────


@admin_bp.route('/api/dashboard-stats')
@admin_required
def dashboard_stats_api():
    """JSON stats for dashboard."""
    total_new = CollegeCutoff.query.count()
    colleges = db.session.query(func.count(db.distinct(CollegeCutoff.college_code))).scalar() or 0
    courses = db.session.query(func.count(db.distinct(CollegeCutoff.course_code))).scalar() or 0
    years = [r[0] for r in db.session.query(CollegeCutoff.year).distinct().order_by(
        CollegeCutoff.year.desc()).all()]

    return jsonify({
        'ok': True,
        'total_records': total_new,
        'total_colleges': colleges,
        'total_courses': courses,
        'years': years,
    })


# ── Full Dashboard API (v2 — used by new dashboard UI) ──────────────────────


@admin_bp.route('/api/dashboard-full')
@admin_required
def dashboard_full_api():
    """
    Full dashboard JSON — exam-type-aware analytics.
    Query param ?exam_type= filters by CollegeCutoff.exam_type (MHT-CET, DSE, POLYTECHNIC).
    Returns all data needed for the v2 dashboard, monitors, charts, and timeline.
    """
    exam_type = request.args.get('exam_type', '').upper().strip()
    exam_type = exam_type if exam_type in ('MHT-CET', 'DSE', 'POLYTECHNIC') else None

    # ── Filter builder ──
    def _filter_cutoff(q):
        return q.filter(CollegeCutoff.exam_type == exam_type) if exam_type else q

    # ── 1. Analytics Cards ──
    total_colleges = College.query.count()

    cutoff_query = CollegeCutoff.query
    if exam_type:
        cutoff_query = cutoff_query.filter(CollegeCutoff.exam_type == exam_type)
    total_cutoff_records = cutoff_query.count()

    # Distinct branches from CollegeCutoff (course_name)
    branch_q = db.session.query(func.count(db.distinct(CollegeCutoff.course_name)))
    branch_q = _filter_cutoff(branch_q) if exam_type else branch_q.filter(
        CollegeCutoff.course_name.isnot(None), CollegeCutoff.course_name != ''
    )
    total_branches = branch_q.scalar() or 0

    total_users = User.query.count()

    pending_approvals = ImportJob.query.filter(
        ImportJob.approval_status == 'pending_approval'
    ).count()

    failed_imports = ImportJob.query.filter(
        ImportJob.status == 'FAILED'
    ).count()

    stats = {
        'total_colleges': total_colleges,
        'total_branches': total_branches,
        'total_cutoff_records': total_cutoff_records,
        'total_users': total_users,
        'pending_approvals': pending_approvals,
        'failed_imports': failed_imports,
    }

    # ── 2. College Management Overview ──
    # Top locations
    location_rows = db.session.query(
        College.location, func.count(College.id).label('cnt')
    ).group_by(College.location).order_by(func.count(College.id).desc()).limit(10).all()
    top_locations = [{'location': r.location, 'count': r.cnt} for r in location_rows]

    # Recently added colleges
    recent_colleges = College.query.order_by(College.id.desc()).limit(5).all()
    recently_added = [{
        'id': c.id,
        'college': c.college,
        'branch': c.branch,
        'location': c.location,
        'fees': c.fees,
        'nirf_rank': c.nirf_rank,
        'rating': c.rating,
    } for c in recent_colleges]

    college_overview = {
        'total_colleges': total_colleges,
        'total_branches': total_branches,
        'top_locations': top_locations,
        'recently_added': recently_added,
    }

    # ── 3. Charts Data ──
    # Records by year
    year_q = db.session.query(
        CollegeCutoff.year, func.count(CollegeCutoff.id).label('cnt')
    )
    if exam_type:
        year_q = year_q.filter(CollegeCutoff.exam_type == exam_type)
    year_q = year_q.group_by(CollegeCutoff.year).order_by(CollegeCutoff.year)
    records_by_year = [{'year': r.year, 'count': r.cnt} for r in year_q.all()]

    # Branch popularity — reuse existing trend_service
    branch_pop = compute_branch_popularity(top_n=15)

    # Import success rate (always unfiltered)
    import_status_counts = db.session.query(
        ImportJob.status, func.count(ImportJob.id).label('cnt')
    ).group_by(ImportJob.status).all()
    import_success_rate = {}
    for r in import_status_counts:
        import_success_rate[r.status] = r.cnt

    # College distribution by location
    dist_q = db.session.query(
        College.location, func.count(College.id).label('cnt')
    ).group_by(College.location).order_by(func.count(College.id).desc()).all()
    college_distribution = [{'location': r.location, 'count': r.cnt} for r in dist_q]

    charts = {
        'records_by_year': records_by_year,
        'branch_popularity': branch_pop,
        'import_success_rate': import_success_rate,
        'college_distribution': college_distribution,
    }

    # ── 4. Import Monitoring ──
    from admin.background_worker import get_active_jobs
    active_job_ids = get_active_jobs()

    recent_jobs_raw = ImportJob.query.order_by(ImportJob.id.desc()).limit(10).all()
    recent_jobs = []
    for j in recent_jobs_raw:
        d = j.to_dict()
        d['progress_pct'] = round(
            (j.processed_pages / j.total_pages) * 100, 1
        ) if j.total_pages > 0 else 0
        recent_jobs.append(d)

    import_monitoring = {
        'recent_jobs': recent_jobs,
        'active_job_ids': active_job_ids,
    }

    # ── 5. Activity Timeline ──
    timeline_entries = AuditLog.query.order_by(
        AuditLog.created_at.desc()
    ).limit(20).all()

    timeline = []
    for entry in timeline_entries:
        user_name = entry.user.display_name() if entry.user else 'System'
        timeline.append({
            'id': entry.id,
            'action': entry.action,
            'resource_type': entry.resource_type,
            'resource_id': entry.resource_id,
            'user': user_name,
            'details': entry.details,
            'timestamp': entry.created_at.isoformat() if entry.created_at else None,
        })

    return jsonify({
        'ok': True,
        'exam_type': exam_type or 'ALL',
        'stats': stats,
        'college_overview': college_overview,
        'charts': charts,
        'import_monitoring': import_monitoring,
        'timeline': timeline,
    })


# ── Recommendation Test API ─────────────────────────────────────────────────


@admin_bp.route('/api/dashboard/recommendation-test', methods=['POST'])
@admin_required
def dashboard_recommendation_test():
    """
    Test Safe / Moderate / Dream classification using the actual recommendation engine.
    Uses existing trend_service.get_safe_moderate_dream().
    """
    data = request.get_json() or {}
    try:
        percentile = float(data.get('percentile', 0))
        category = data.get('category', 'Open')
        gender = data.get('gender', 'Gender-Neutral')
        branch_filter = data.get('branch', '').strip()
        district = data.get('district', '').strip()
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Invalid numeric value for percentile'}), 400

    if percentile <= 0 or percentile > 100:
        return jsonify({'ok': False, 'error': 'Percentile must be between 0 and 100'}), 400

    valid_categories = ['Open', 'OBC', 'SC', 'ST', 'NT', 'EWS']
    if category not in valid_categories:
        return jsonify({'ok': False, 'error': f'Invalid category. Must be one of: {", ".join(valid_categories)}'}), 400

    try:
        # Use the actual recommendation engine
        result = get_safe_moderate_dream(
            student_percentile=percentile,
            category=category,
            gender=gender,
            top_n=20
        )

        # Post-filter by branch if specified
        if branch_filter and branch_filter != 'all':
            def _filter_branch(lst):
                return [c for c in lst if branch_filter.lower() in c.get('branch', '').lower()]
            result['safe'] = _filter_branch(result.get('safe', []))
            result['moderate'] = _filter_branch(result.get('moderate', []))
            result['dream'] = _filter_branch(result.get('dream', []))

        # Count summary
        summary = {
            'safe_count': len(result.get('safe', [])),
            'moderate_count': len(result.get('moderate', [])),
            'dream_count': len(result.get('dream', [])),
            'latest_year': result.get('latest_year'),
        }

        return jsonify({
            'ok': True,
            'summary': summary,
            'safe': result.get('safe', []),
            'moderate': result.get('moderate', []),
            'dream': result.get('dream', []),
        })

    except Exception as e:
        logger.error(f"Recommendation test error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── College Master Upload ────────────────────────────────────────────────────


@admin_bp.route('/college-upload', methods=['GET', 'POST'])
@admin_required
def college_upload():
    """Upload CSV/Excel for college master data with preview."""
    if request.method == 'GET':
        return render_template('college_upload.html')

    # Handle POST: parse + preview
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'ok': False, 'error': 'No file selected'}), 400

    try:
        from admin.college_upload_service import parse_upload, commit_upload

        # Parse and preview
        preview = parse_upload(file)

        if preview.total_rows == 0:
            return jsonify({'ok': False, 'error': preview.errors[0] if preview.errors else 'No data found'}), 400

        return jsonify({
            'ok': True,
            'preview': preview.to_dict(),
            'message': (
                f'Parsed {preview.total_rows} rows: '
                f'{preview.valid_rows} valid ({preview.to_create} new, {preview.to_update} updates), '
                f'{preview.error_rows} errors.'
            ),
        })

    except Exception as e:
        logger.error(f"College upload error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@admin_bp.route('/college-upload/commit', methods=['POST'])
@admin_required
def college_upload_commit():
    """Commit the previewed college upload to the database."""
    try:
        from admin.college_upload_service import commit_upload

        data = request.get_json()
        if not data or 'preview' not in data:
            return jsonify({'ok': False, 'error': 'No preview data provided'}), 400

        # Reconstruct preview result from stored data
        preview_dict = data['preview']

        class PreviewWrapper:
            pass

        preview = PreviewWrapper()
        preview.total_rows = preview_dict.get('total_rows', 0)
        preview.valid_rows = preview_dict.get('valid_rows', 0)
        preview.error_rows = preview_dict.get('error_rows', 0)
        preview.preview_rows = preview_dict.get('preview_rows', [])
        preview.errors = preview_dict.get('errors', [])

        result = commit_upload(preview)

        log_action('college_upload_commit', 'college', None, {
            'created': result.created,
            'updated': result.updated,
            'errors': len(result.errors),
        })

        return jsonify({
            'ok': True,
            'result': result.to_dict(),
            'message': (
                f'Committed: {result.created} created, {result.updated} updated, '
                f'{len(result.errors)} errors.'
            ),
        })

    except Exception as e:
        logger.error(f"College upload commit error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500
