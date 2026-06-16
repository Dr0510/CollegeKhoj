import os
import re
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

def _clean_db_url(raw: str) -> str:
    """Strip psql wrapper and unsupported params so SQLAlchemy can parse the URL."""
    url = raw.strip().strip("'\"")
    if url.lower().startswith("psql "):
        url = url[5:].strip("'\"")
    # Remove channel_binding — not understood by psycopg2
    url = re.sub(r'[&?]channel_binding=[^&]*', '', url)
    # Ensure sslmode is present for Neon
    if 'neon.tech' in url and 'sslmode' not in url:
        url += ('&' if '?' in url else '?') + 'sslmode=require'
    return url

def init_database(app):
    """Initialize database with Flask app.
    Prefers NEON_DATABASE_URL when set, falls back to DATABASE_URL."""
    raw = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    db_url = _clean_db_url(raw) if raw else "postgresql://localhost/college_recommendation"

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "pool_size": 20,
        "max_overflow": 40,
    }

    db.init_app(app)
    return db