"""Audit logging helper for admin actions."""
import logging
from flask import request, g
from datetime import datetime
from database import db

logger = logging.getLogger(__name__)


def log_action(action, resource_type=None, resource_id=None, details=None):
    """Log an admin action to the AuditLog table.

    Args:
        action: String like 'login', 'upload', 'import_commit', 'delete', etc.
        resource_type: 'cutoff', 'college', 'user', 'backup', 'upload_job'
        resource_id: Primary key of the affected resource
        details: Dict of extra context to store as JSON
    """
    from models import AuditLog

    user = g.get('user') if hasattr(g, 'user') else None

    try:
        entry = AuditLog(
            user_id=user.id if user else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=request.remote_addr if request else None,
            user_agent=request.user_agent.string if request and request.user_agent else None,
            created_at=datetime.utcnow(),
        )
        db.session.add(entry)
        db.session.commit()
        logger.debug(f"AuditLog: {action} on {resource_type}#{resource_id}")
        return entry
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to write audit log: {e}")
        return None