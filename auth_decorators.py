"""Authentication decorators for protecting Flask routes with custom session auth."""
import logging
from functools import wraps
from flask import g, redirect, url_for, request, jsonify

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