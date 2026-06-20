"""Database backup and restore service for CollegeKhoj.

Production: Uses pg_dump/psql for Neon PostgreSQL backups.
Development: SQLite is supported for local testing only.

Backup metadata is stored in the backup_history table.
Automatic backup is triggered before every PDF import.
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

    PostgreSQL (Neon): Uses pg_dump for reliable, consistent backups.
    SQLite: Uses .dump command (local development only).

    Returns:
        dict with keys: success, filepath, file_size, record_count, error, backup_id
    """
    from models import BackupHistory, Cutoff, AdmissionType

    db_type = detect_db_type()
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    ext = 'dump' if db_type == 'postgresql' else 'sqlite'
    backup_filename = f"collegekhoj_backup_{timestamp}.{ext}"
    backup_path = os.path.join(_ensure_backup_dir(), backup_filename)

    try:
        if db_type == 'postgresql':
            url = get_db_url()
            # Parse the URL for pg_dump
            result = subprocess.run(
                ['pg_dump', '--no-owner', '--no-acl', '-Fc', url],
                capture_output=True, timeout=300
            )
            if result.returncode != 0:
                error_msg = result.stderr.decode()[:500]
                logger.error(f"pg_dump failed: {error_msg}")
                return {'success': False, 'error': error_msg}

            with open(backup_path, 'wb') as f:
                f.write(result.stdout)
        else:
            # SQLite: copy the file directly
            import shutil
            db_path = get_db_url().replace('sqlite:///', '')
            if not os.path.exists(db_path):
                return {'success': False, 'error': f'Database file not found: {db_path}'}
            shutil.copy2(db_path, backup_path)

        file_size = os.path.getsize(backup_path)
        record_count = Cutoff.query.count()

        # Create backup history record
        # g may not be available in background threads, so use safe get
        try:
            from flask import g
            admin_user = g.get('user') if hasattr(g, 'user') else None
            created_by = admin_user.id if admin_user else None
        except Exception:
            created_by = None

        backup_record = BackupHistory(
            backup_date=datetime.utcnow(),
            backup_file=backup_filename,
            file_size=file_size,
            db_type=db_type,
            record_count=record_count,
            status='success',
            created_by=created_by,
            notes=notes,
        )
        db.session.add(backup_record)
        db.session.commit()

        logger.info(f"Backup created: {backup_filename} ({file_size} bytes, {record_count} records)")
        return {
            'success': True,
            'filepath': backup_path,
            'file_size': file_size,
            'record_count': record_count,
            'backup_id': backup_record.id,
        }

    except subprocess.TimeoutExpired:
        logger.error("Backup timed out after 300s")
        return {'success': False, 'error': 'Backup timed out'}
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return {'success': False, 'error': str(e)}


def restore_backup(backup_id: int) -> dict:
    """Restore database from a backup.

    Args:
        backup_id: ID of the backup record to restore from.

    Returns:
        dict with keys: success, error
    """
    from models import BackupHistory
    db_type = detect_db_type()

    try:
        backup = db.session.get(BackupHistory, backup_id)
        if not backup:
            return {'success': False, 'error': 'Backup record not found'}

        backup_path = os.path.join(BACKUP_DIR, backup.backup_file)
        if not os.path.exists(backup_path):
            return {'success': False, 'error': f'Backup file not found: {backup_path}'}

        if db_type == 'postgresql':
            url = get_db_url()
            result = subprocess.run(
                ['pg_restore', '--clean', '--no-owner', '--no-acl', '-d', url, backup_path],
                capture_output=True, timeout=600
            )
            if result.returncode != 0:
                error_msg = result.stderr.decode()[:500]
                logger.error(f"pg_restore failed: {error_msg}")
                return {'success': False, 'error': error_msg}
        else:
            # SQLite: copy the file back
            import shutil
            db_path = get_db_url().replace('sqlite:///', '')
            shutil.copy2(backup_path, db_path)

        logger.info(f"Database restored from backup #{backup_id}")
        return {'success': True}

    except subprocess.TimeoutExpired:
        logger.error("Restore timed out after 600s")
        return {'success': False, 'error': 'Restore timed out'}
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        return {'success': False, 'error': str(e)}


def delete_backup_file(backup_id: int) -> dict:
    """Delete a backup file and its metadata record.

    Args:
        backup_id: ID of the backup to delete.

    Returns:
        dict with keys: success, error
    """
    from models import BackupHistory

    try:
        backup = db.session.get(BackupHistory, backup_id)
        if not backup:
            return {'success': False, 'error': 'Backup record not found'}

        backup_path = os.path.join(BACKUP_DIR, backup.backup_file)
        if os.path.exists(backup_path):
            os.remove(backup_path)

        db.session.delete(backup)
        db.session.commit()

        logger.info(f"Backup #{backup_id} deleted")
        return {'success': True}

    except Exception as e:
        db.session.rollback()
        logger.error(f"Delete backup failed: {e}")
        return {'success': False, 'error': str(e)}


def get_backup_file_path(backup_id: int) -> str:
    """Get the full file path for a backup.

    Args:
        backup_id: ID of the backup record.

    Returns:
        Full file path string, or None if not found.
    """
    from models import BackupHistory

    backup = db.session.get(BackupHistory, backup_id)
    if not backup:
        return None

    path = os.path.join(BACKUP_DIR, backup.backup_file)
    return path if os.path.exists(path) else None