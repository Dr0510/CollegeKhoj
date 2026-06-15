"""Clerk authentication utilities for Flask."""
import os
import json
import logging
import time
import urllib.request
import requests as http_requests

import jwt
from jwt.algorithms import RSAAlgorithm

CLERK_PUBLISHABLE_KEY = os.environ.get('CLERK_PUBLISHABLE_KEY', '')
CLERK_SECRET_KEY = os.environ.get('CLERK_SECRET_KEY', '')

_jwks_cache = {'keys': None, 'fetched_at': 0}


def _get_jwks():
    """Fetch and cache Clerk's JSON Web Key Set (refreshed hourly)."""
    now = time.time()
    if _jwks_cache['keys'] and now - _jwks_cache['fetched_at'] < 3600:
        return _jwks_cache['keys']

    if not CLERK_SECRET_KEY:
        logging.warning("CLERK_SECRET_KEY not set — skipping JWKS fetch")
        return []

    try:
        resp = http_requests.get(
            'https://api.clerk.com/v1/jwks',
            headers={'Authorization': f'Bearer {CLERK_SECRET_KEY}'},
            timeout=5
        )
        keys = resp.json().get('keys', [])
        _jwks_cache['keys'] = keys
        _jwks_cache['fetched_at'] = now
        return keys
    except Exception as e:
        logging.error(f"JWKS fetch failed: {e}")
        return []


def verify_token(token: str) -> dict | None:
    """Verify a Clerk session JWT and return its claims, or None on failure."""
    if not token or not CLERK_SECRET_KEY:
        return None
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get('kid')
        for key in _get_jwks():
            if key.get('kid') == kid:
                pub_key = RSAAlgorithm.from_jwk(json.dumps(key))
                claims = jwt.decode(
                    token, pub_key, algorithms=['RS256'],
                    options={"verify_aud": False}
                )
                return claims
    except Exception as e:
        logging.debug(f"Token verification failed: {e}")
    return None


def get_clerk_user_data(clerk_id: str) -> dict | None:
    """Fetch user profile from Clerk API (email, name, avatar)."""
    if not CLERK_SECRET_KEY or not clerk_id:
        return None
    try:
        resp = http_requests.get(
            f'https://api.clerk.com/v1/users/{clerk_id}',
            headers={'Authorization': f'Bearer {CLERK_SECRET_KEY}'},
            timeout=5
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        logging.error(f"Clerk user fetch failed: {e}")
    return None


def extract_primary_email(clerk_user: dict) -> str:
    """Pull the primary email address from a Clerk user object."""
    emails = clerk_user.get('email_addresses', [])
    primary_id = clerk_user.get('primary_email_address_id')
    for e in emails:
        if e.get('id') == primary_id:
            return e.get('email_address', '')
    if emails:
        return emails[0].get('email_address', '')
    return ''
