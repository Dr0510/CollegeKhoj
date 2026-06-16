"""Database backup and restore service.

Supports both PostgreSQL (pg_dump) and SQLite (.dump) databases.
Stores backups in the configured BACKUP_DIR.
"""
import os
import subprocess
import logging
from datetime import datetime
from database import db

logger = logging.getLogger(__name__)

BACKUP_DIR = os.environ.get('BACKUP_DIR', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backups'))


def _ensure_backup_dir():
    """Create backup directory if it doesn't exist."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    return BACKUP_DIR


def get_db_url() -> str:
    """Get the database URL from Flask app config."""
    from flask import current_app
    return current_app.config.get('SQLALCHEMY_DATABASE_URI', '')


def detect_db_type() -> str:
    """Detect whether we're using postgresql or sqlite."""
    url = get_db_url()
    if url.startswith('postgresql'):
        return 'postgresql'
    return 'sqlite'


def create_backup(notes: str = '') -> dict:
    """Create a full database backup.

    Returns:
        dict with keys: success, filepath, file_size, record_count, error
    """
    db_type = detect_db_type()
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    ext = 'sql' if db_type == 'sqlite' else 'dump'
    backup_filename = f"backup_{timestamp}.{ext}"
    backup_path = os.path.join(_ensure_backup_dir(), backup_filename)

    try:
        if db_type == 'postgresql':
            url = get_db_url()
            # Parse the URL for pg_dump
            result = subprocess.run(
                ['pg_dump', '--no-owner', '--no-acl', '--clean', url],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError(f"pg_dump failed: {result.stderr}")
            with open(backup_path, 'w') as f:
                f.write(result.stdout)
            record_count = _count_records()
        else:
            # SQLite
            from database import db
            db_path = get_db_url().replace('sqlite:///', '')
            if not os.path.isabs(db_path):
                # Resolve relative path
                base = os.path.dirname(os.path.dirname(__file__))
                db_path = os.path.join(base, db_path)
            result = subprocess.run(
                ['sqlite3', db_path, '.dump'],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError(f"sqlite3 dump failed: {result.stderr}")
            with open(backup_path, 'w') as f:
                f.write(result.stdout)
            record_count = _count_records()

        file_size = os.path.getsize(backup_path)

        # Save metadata to DB
        from models import BackupHistory
        from flask import g
        user = g.get('user') if hasattr(g, 'user') else None
        entry = BackupHistory(
            backup_file=backup_path,
            file_size=file_size,
            db_type=db_type,
            record_count=record_count,
            status='success',
            created_by=user.id if user else None,
            notes=notes,
        )
        db.session.add(entry)
        db.session.commit()

        logger.info(f"Backup created: {backup_path} ({file_size} bytes, {record_count} records)")
        return {
            'success': True,
            'filepath': backup_path,
            'filename': backup_filename,
            'file_size': file_size,
            'record_count': record_count,
            'backup_id': entry.id,
        }

    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return {'success': False, 'error': str(e)}


def restore_backup(backup_id: int) -> dict:
    """Restore database from a backup.

    Args:
        backup_id: ID of the BackupHistory record

    Returns:
        dict with success/error info
    """
    from models import BackupHistory

    try:
        entry = db.session.get(BackupHistory, backup_id)
        if not entry:
            return {'success': False, 'error': 'Backup record not found'}
        if entry.status != 'success':
            return {'success': False, 'error': f"Backup status is '{entry.status}', cannot restore"}

        backup_path = entry.backup_file
        if not os.path.exists(backup_path):
            return {'success': False, 'error': f"Backup file not found: {backup_path}"}

        db_type = detect_db_type()

        if db_type == 'postgresql':
            url = get_db_url()
            result = subprocess.run(
                ['psql', url],
                input=open(backup_path).read(),
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                raise RuntimeError(f"psql restore failed: {result.stderr}")
        else:
            from database import db
            db_path = get_db_url().replace('sqlite:///', '')
            if not os.path.isabs(db_path):
                base = os.path.dirname(os.path.dirname(__file__))
                db_path = os.path.join(base, db_path)
            result = subprocess.run(
                ['sqlite3', db_path],
                input=open(backup_path).read(),
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                raise RuntimeError(f"sqlite3 restore failed: {result.stderr}")

        logger.info(f"Database restored from backup #{backup_id}: {backup_path}")
        return {'success': True, 'message': 'Database restored successfully'}

    except Exception as e:
        logger.error(f"Restore failed: {e}")
        return {'success': False, 'error': str(e)}


def _count_records() -> int:
    """Count total records across all major tables."""
    from models import CAPCutoff, College, User, UploadedFile
    try:
        total = 0
        for model in [CAPCutoff, College, User, UploadedFile]:
            total += model.query.count()
        return total
    except Exception:
        return 0