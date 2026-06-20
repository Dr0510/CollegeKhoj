"""Authentication decorators for protecting Flask routes with custom session auth.

Part 2 fix: ``admin_required`` now checks ``session["is_admin"]`` first,
avoiding a database lookup on every request. The database is only queried
when the view function actually needs the user object.
"""
import logging
from functools import wraps
from flask import g, redirect, url_for, request, jsonify, session

logger = logging.getLogger(__name__)


def login_required(f):
    """Require an authenticated user for this route.
    
    Redirects to login page for HTML requests.
    Returns 401 JSON for API requests.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        user = g.get('user')
        if not user:
            # If it's an API/JSON request, return 401
            if (request.is_json or 
                request.accept_mimetypes.best == 'application/json' or
                request.path.startswith('/api/')):
                return jsonify({'error': 'Authentication required', 'code': 'unauthenticated'}), 401
            
            # HTML request — redirect to login
            logger.debug(f"Redirecting unauthenticated user to login from {request.path}")
            return redirect(url_for('login_page', next=request.url))
        return f(*args, **kwargs)
    return decorated


def optional_auth(f):
    """Load user if available, but don't require authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # User is already loaded by before_request hook
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    """Require authentication for API endpoints. Returns 401 JSON on failure."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = g.get('user')
        if not user:
            return jsonify({
                'error': 'Authentication required',
                'code': 'unauthenticated',
                'message': 'Please sign in to access this resource.'
            }), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require an admin user using session-based auth (no database dependency).

    Checks ``session["is_admin"]`` first — this is a pure cookie check that
    never hits the database, so it cannot fail due to missing tables or DB
    latency. If the session flag is missing, redirects to the admin login.
    
    Once the session-based check passes, ``g.user`` is populated on demand
    for views that need the User object.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # ── Session-based admin check (Part 2: no database dependency) ─────
        if not session.get("is_admin"):
            # Fallback: check via g.user (may trigger a DB query)
            user = g.get('user')
            if not user or not user.is_admin():
                if (request.is_json or
                    request.accept_mimetypes.best == 'application/json' or
                    request.path.startswith('/api/')):
                    return jsonify({
                        'error': 'Admin access required',
                        'code': 'admin_required',
                        'message': 'You need admin privileges to access this resource.'
                    }), 403
                logger.debug(f"Redirecting non-admin to login from {request.path}")
                return redirect(url_for('admin_bp.admin_login', next=request.url))
            
            # Populate session for subsequent requests
            session['admin_id'] = user.id
            session['admin_email'] = user.email
            session['is_admin'] = True
        
        return f(*args, **kwargs)
    return decorated