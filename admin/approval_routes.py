"""
Bulk Import Approval Management Routes.

Dedicated blueprint at /admin/bulk-imports for the approval workflow.

Endpoints:
  GET    /                              — List all import jobs with approval info
  GET    /<id>                          — Detailed review page with imported records
  POST   /<id>/approve                  — Approve an import (records become active)
  POST   /<id>/reject                   — Reject an import with reason
  POST   /<id>/retry                    — Retry a failed import
  POST   /bulk-approve                  — Bulk approve multiple import jobs
"""
import os
import logging
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, redirect, g, flash, url_for
from sqlalchemy import text as sql_text

from database import db
from models import ImportJob, UploadedFile, CollegeCutoff, ImportErrorRecord, User
from admin.audit import log_action
from auth_decorators import admin_required

logger = logging.getLogger(__name__)

# ── Blueprint ────────────────────────────────────────────────────────────────
approval_bp = Blueprint(
    'approval_bp', __name__,
    template_folder='../templates/admin',
    url_prefix='/admin/bulk-imports'
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_admin():
    """Get the currently logged-in admin from g.user."""
    user = g.get('user')
    if user and user.is_admin():
        return user
    return None


def _sync_cutoff_approval_status(job_id: int, approval_status: str):
    """Sync the denormalized approval_status on all CollegeCutoff records."""
    try:
        job = db.session.get(ImportJob, job_id)
        if not job or not job.file_id:
            return
        db.session.execute(
            sql_text(
                "UPDATE college_cutoffs SET approval_status = :status "
                "WHERE source_file_id = :fid"
            ),
            {'status': approval_status, 'fid': job.file_id}
        )
        db.session.commit()
        logger.info(
            f"Synced approval_status='{approval_status}' for file#{job.file_id} "
            f"(job#{job_id})"
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to sync cutoff approval status: {e}")


# ── List View ────────────────────────────────────────────────────────────────

@approval_bp.route('/')
@admin_required
def approval_list():
    """List all import jobs with approval status, filtering, and stats."""
    page = request.args.get('page', 1, type=int)
    approval_filter = request.args.get('approval_status', '')
    status_filter = request.args.get('status', '')

    query = ImportJob.query

    if approval_filter:
        query = query.filter(ImportJob.approval_status == approval_filter)
    if status_filter:
        query = query.filter(ImportJob.status == status_filter)

    query = query.order_by(ImportJob.id.desc())
    pagination = query.paginate(page=page, per_page=20, error_out=False)

    # Get approval stats for the top summary cards
    total_pending = ImportJob.query.filter(
        ImportJob.approval_status == 'pending_approval'
    ).count()
    total_approved = ImportJob.query.filter(
        ImportJob.approval_status == 'approved'
    ).count()
    total_rejected = ImportJob.query.filter(
        ImportJob.approval_status == 'rejected'
    ).count()
    total_failed = ImportJob.query.filter(
        ImportJob.status == 'FAILED'
    ).count()

    # Check which jobs are currently running
    from admin.background_worker import get_active_jobs
    active_jobs = get_active_jobs()

    return render_template(
        'bulk_imports.html',
        jobs=pagination.items,
        pagination=pagination,
        approval_filter=approval_filter,
        status_filter=status_filter,
        active_jobs=active_jobs,
        stats={
            'pending': total_pending,
            'approved': total_approved,
            'rejected': total_rejected,
            'failed': total_failed,
        }
    )


# ── Detail / Review Page ─────────────────────────────────────────────────────

@approval_bp.route('/<int:job_id>')
@admin_required
def approval_detail(job_id):
    """Detailed review page with imported records table."""
    job = db.session.get(ImportJob, job_id)
    if not job:
        flash('Import job not found', 'error')
        return redirect('/admin/bulk-imports')

    from admin.background_worker import is_job_running
    is_running = is_job_running(job_id)

    error_records = ImportErrorRecord.query.filter_by(
        job_id=job_id
    ).order_by(ImportErrorRecord.id.desc()).limit(100).all()

    # Paginated imported cutoff records
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search = request.args.get('q', '')

    cutoff_query = CollegeCutoff.query
    if job.file_id:
        cutoff_query = cutoff_query.filter(
            CollegeCutoff.source_file_id == job.file_id
        )

    if search:
        cutoff_query = cutoff_query.filter(
            db.or_(
                CollegeCutoff.college_name.ilike(f'%{search}%'),
                CollegeCutoff.course_name.ilike(f'%{search}%'),
                CollegeCutoff.college_code.ilike(f'%{search}%'),
                CollegeCutoff.category.ilike(f'%{search}%'),
            )
        )

    cutoff_query = cutoff_query.order_by(
        CollegeCutoff.college_code, CollegeCutoff.course_code, CollegeCutoff.category
    )
    cutoff_pagination = cutoff_query.paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template(
        'bulk_import_detail.html',
        job=job,
        is_running=is_running,
        error_records=error_records,
        cutoff_records=cutoff_pagination.items,
        cutoff_pagination=cutoff_pagination,
        search=search,
    )


# ── Approve Import ──────────────────────────────────────────────────────────

@approval_bp.route('/<int:job_id>/approve', methods=['POST'])
@admin_required
def approve_import(job_id):
    """Approve an import. All associated cutoff records become active."""
    job = db.session.get(ImportJob, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Import job not found'}), 404

    if job.approval_status == 'approved':
        return jsonify({'ok': False, 'error': 'Import is already approved'}), 400

    if job.status not in ('COMPLETED',):
        return jsonify({
            'ok': False,
            'error': f'Cannot approve import with status "{job.status}". Only COMPLETED imports can be approved.'
        }), 400

    admin = _get_admin()
    try:
        job.approval_status = 'approved'
        job.approved_by = admin.id if admin else None
        job.approved_at = datetime.utcnow()
        db.session.commit()

        _sync_cutoff_approval_status(job_id, 'approved')

        if job.file:
            job.file.processed_status = 'approved'
        db.session.commit()

        log_action('approve_import', 'import_job', job_id, {
            'rows_imported': job.rows_imported,
            'filename': job.file.filename if job.file else None,
            'approver': admin.display_name() if admin else None,
        })

        return jsonify({
            'ok': True,
            'message': f'Import #{job_id} approved. {job.rows_imported} records active.',
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Approve #{job_id} failed: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Reject Import ───────────────────────────────────────────────────────────

@approval_bp.route('/<int:job_id>/reject', methods=['POST'])
@admin_required
def reject_import(job_id):
    """Reject an import with a reason. Records kept for audit."""
    job = db.session.get(ImportJob, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Import job not found'}), 404

    if job.approval_status == 'rejected':
        return jsonify({'ok': False, 'error': 'Import is already rejected'}), 400

    data = request.get_json() or request.form
    rejection_reason = data.get('reason', '').strip()
    admin = _get_admin()

    try:
        job.approval_status = 'rejected'
        job.approved_by = admin.id if admin else None
        job.approved_at = datetime.utcnow()
        job.rejection_reason = rejection_reason or 'No reason provided'
        db.session.commit()

        _sync_cutoff_approval_status(job_id, 'rejected')

        if job.file:
            job.file.processed_status = 'rejected'
        db.session.commit()

        log_action('reject_import', 'import_job', job_id, {
            'reason': rejection_reason,
            'filename': job.file.filename if job.file else None,
        })

        return jsonify({
            'ok': True,
            'message': f'Import #{job_id} rejected. Records kept for audit.',
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Reject #{job_id} failed: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Retry Import ────────────────────────────────────────────────────────────

@approval_bp.route('/<int:job_id>/retry', methods=['POST'])
@admin_required
def retry_import(job_id):
    """Retry a failed import. Resets to PENDING."""
    job = db.session.get(ImportJob, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Import job not found'}), 404

    if job.status not in ('FAILED',):
        return jsonify({
            'ok': False,
            'error': f'Only FAILED imports can be retried, got "{job.status}"'
        }), 400

    if not job.file or not os.path.exists(job.file.stored_path):
        return jsonify({'ok': False, 'error': 'PDF file not found on disk'}), 404

    try:
        if job.file_id:
            db.session.execute(
                sql_text("DELETE FROM college_cutoffs WHERE source_file_id = :fid"),
                {'fid': job.file_id}
            )

        job.status = 'PENDING'
        job.approval_status = None
        job.approved_by = None
        job.approved_at = None
        job.rejection_reason = None
        job.error_message = None
        job.processed_pages = 0
        job.checkpoint_page = 0
        job.rows_extracted = 0
        job.rows_imported = 0
        job.rows_failed = 0
        job.failed_pages = []
        job.error_log = []
        job.started_at = None
        job.completed_at = None
        job.memory_usage_mb = None

        if job.file:
            job.file.processed_status = 'pending'
        db.session.commit()

        log_action('retry_import', 'import_job', job_id, {
            'filename': job.file.filename if job.file else None,
        })
        return jsonify({
            'ok': True,
            'message': f'Import #{job_id} reset. You can start processing again.',
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Retry #{job_id} failed: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Bulk Approve ────────────────────────────────────────────────────────────

@approval_bp.route('/bulk-approve', methods=['POST'])
@admin_required
def bulk_approve():
    """Approve multiple import jobs at once."""
    data = request.get_json()
    if not data or 'job_ids' not in data:
        return jsonify({'ok': False, 'error': 'No job IDs provided'}), 400

    job_ids = data.get('job_ids', [])
    if not job_ids or not isinstance(job_ids, list):
        return jsonify({'ok': False, 'error': 'Invalid job IDs list'}), 400

    admin = _get_admin()
    approved_count = 0
    skipped_count = 0
    errors = []

    for job_id in job_ids:
        try:
            job = db.session.get(ImportJob, job_id)
            if not job:
                skipped_count += 1
                errors.append(f"Job #{job_id}: not found")
                continue
            if job.status != 'COMPLETED':
                skipped_count += 1
                continue
            if job.approval_status == 'approved':
                skipped_count += 1
                continue

            job.approval_status = 'approved'
            job.approved_by = admin.id if admin else None
            job.approved_at = datetime.utcnow()
            _sync_cutoff_approval_status(job_id, 'approved')
            if job.file:
                job.file.processed_status = 'approved'
            approved_count += 1
            log_action('bulk_approve', 'import_job', job_id, {
                'approver': admin.display_name() if admin else None,
            })
        except Exception as e:
            db.session.rollback()
            skipped_count += 1
            errors.append(f"Job #{job_id}: {str(e)}")

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': f'Bulk approve failed: {e}'}), 500

    return jsonify({
        'ok': True,
        'approved': approved_count,
        'skipped': skipped_count,
        'total': len(job_ids),
        'errors': errors if errors else None,
        'message': f'Approved {approved_count} import(s). {skipped_count} skipped.',
    })


# ── API Stats ───────────────────────────────────────────────────────────────

@approval_bp.route('/api/stats')
@admin_required
def approval_stats_api():
    """JSON endpoint for approval stats (used by dashboard)."""
    return jsonify({
        'ok': True,
        'pending': ImportJob.query.filter(
            ImportJob.approval_status == 'pending_approval'
        ).count(),
        'approved': ImportJob.query.filter(
            ImportJob.approval_status == 'approved'
        ).count(),
        'rejected': ImportJob.query.filter(
            ImportJob.approval_status == 'rejected'
        ).count(),
        'failed': ImportJob.query.filter(
            ImportJob.status == 'FAILED'
        ).count(),
        'total': ImportJob.query.count(),
    })

