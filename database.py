"""Database initialization and schema management for CollegeKhoj."""
import os
import re
import logging
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

logger = logging.getLogger(__name__)

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

def _clean_db_url(raw: str) -> str:
    """Strip psql wrapper and unsupported params so SQLAlchemy can parse the URL."""
    url = raw.strip().strip("'\"")
    if url.lower().startswith("psql "):
        url = url[5:].strip("'\"")
    url = re.sub(r'[&?]channel_binding=[^&]*', '', url)
    if 'neon.tech' in url and 'sslmode' not in url:
        url += ('&' if '?' in url else '?') + 'sslmode=require'
    return url

def init_database(app):
    """Initialize database with Flask app."""
    raw = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    db_url = _clean_db_url(raw) if raw else "postgresql://localhost/college_recommendation"

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    # NOTE: Neon pooled connections do NOT support search_path in URL options
    # or connect_args. The search_path is managed entirely by
    # _ensure_public_schema() which uses runtime SQL (SET search_path TO public,
    # ALTER DATABASE SET search_path TO public).
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "pool_size": 20,
        "max_overflow": 40,
    }

    db.init_app(app)
    return db


def get_db_columns(table_name: str) -> list:
    """Get list of actual column names from the database for a given table."""
    from sqlalchemy import inspect
    try:
        inspector = inspect(db.engine)
        return [c['name'] for c in inspector.get_columns(table_name)]
    except Exception:
        return []


def safe_query_first(model_class, *filters):
    """Query first matching row; if table is missing, recreate schema and retry.
    
    Catches ``UndefinedTable`` / ``ProgrammingError`` at the SQL layer and
    calls ``ensure_schema()`` to self-heal, then retries the query exactly
    once. If it fails again, returns None.
    
    Usage::
    
        user = safe_query_first(User, User.email == email, User.role == 'admin')
    """
    from sqlalchemy.exc import ProgrammingError, OperationalError
    try:
        return model_class.query.filter(*filters).first()
    except (ProgrammingError, OperationalError) as e:
        error_str = str(e).lower()
        if 'does not exist' in error_str or 'relation' in error_str:
            logger.warning(f"[DB] Table missing for {model_class.__tablename__} — self-healing…")
            ensure_schema()
            try:
                return model_class.query.filter(*filters).first()
            except Exception:
                return None
        return None


def _ensure_public_schema():
    """Ensure the public schema exists and is set in the search_path.
    
    This is critical for Neon/PostgreSQL connections where the search_path
    may not include 'public' by default, causing 'no schema has been selected'
    errors on CREATE TABLE and 'relation does not exist' on queries.
    """
    try:
        with db.engine.connect() as conn:
            # 1. Ensure public schema exists
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS public"))
            conn.commit()
            
            # 2. Set search_path explicitly to include public
            conn.execute(text("SET search_path TO public"))
            conn.commit()
            
            # 3. Verify the search_path took effect
            result = conn.execute(text("SHOW search_path"))
            path = result.scalar()
            logger.info(f"Database search_path set to: {path}")
            
            # 4. Also alter the database default so subsequent connections inherit it
            db_name = conn.execute(text("SELECT current_database()")).scalar()
            try:
                conn.execute(text(f"ALTER DATABASE \"{db_name}\" SET search_path TO public"))
                conn.commit()
                logger.info(f"Set default search_path for database '{db_name}' to public")
            except Exception as e:
                # May not have permissions to ALTER DATABASE, that's okay
                logger.debug(f"Cannot ALTER DATABASE search_path (non-fatal): {e}")
                conn.rollback()
    except Exception as e:
        logger.warning(f"Failed to ensure public schema: {e}")


def ensure_schema():
    """Create tables safely — only creates missing tables and skips existing ones.
    
    Layer 1: Manual CREATE TABLE for each known model.
    Layer 2: db.create_all() as a catch-all fallback for any missed tables.
    
    Also synchronizes column-level changes (renames, additions) to keep the
    database schema in sync with SQLAlchemy models without dropping data.
    """
    from sqlalchemy import inspect
    from sqlalchemy.exc import ProgrammingError

    # ═══════════════════════════════════════════════════════════════════════
    # CRITICAL FIX: Ensure public schema exists and search_path is correct
    # ═══════════════════════════════════════════════════════════════════════
    _ensure_public_schema()

    inspector = inspect(db.engine)
    # Use explicit schema='public' to avoid search_path dependency
    try:
        existing_tables = set(inspector.get_table_names(schema='public'))
    except Exception:
        # If inspector fails entirely, fall back to db.create_all()
        logger.warning("[DB] Inspector failed — falling back to db.create_all()")
        db.create_all()
        from models import (
            AdmissionType, AcademicYear, CapRound, College, Branch,
            Cutoff, UploadJob, User, BackupHistory, AuditLog, LoginHistory,
            CollegeCutoff, CAPCutoff, UploadedFile, ImportJob,
            Category, CollegeAdmissionType,
        )
        _fix_column_mismatches()
        logger.info("[DB] db.create_all() complete")
        return 0, 0, 0

    from models import (
        AdmissionType, AcademicYear, CapRound, College, Branch,
        Cutoff, UploadJob, User, BackupHistory, AuditLog, LoginHistory,
        CollegeCutoff, CAPCutoff, UploadedFile, ImportJob,
        Category, CollegeAdmissionType,
    )

    created_count = 0
    skipped_count = 0
    failed_count = 0

    model_classes = [
        # 1. Dependency-free tables (no FK references)
        AdmissionType, AcademicYear, CapRound, Category, User,
        # 2. Tables that reference the above
        College, Branch,
        # 3. UploadJob before Cutoff (Cutoff has FK to upload_jobs)
        UploadJob,
        # 4. Cutoff — depends on admission_types, colleges, branches, academic_years, cap_rounds, upload_jobs
        Cutoff,
        # 5. Association tables
        CollegeAdmissionType,
        # 6. Audit/support tables
        BackupHistory, AuditLog, LoginHistory,
        # 7. Legacy tables (no FK dependencies)
        UploadedFile, CollegeCutoff, CAPCutoff, ImportJob,
    ]

    for model_class in model_classes:
        table_name = model_class.__tablename__
        if table_name in existing_tables:
            skipped_count += 1
            continue

        try:
            # Create with explicit schema
            model_class.__table__.create(db.engine, {'schema': 'public'})
            created_count += 1
            logger.info(f"[DB] Created table: {table_name}")
        except Exception as e:
            error_msg = str(e).lower()
            if 'already exists' in error_msg:
                skipped_count += 1
            else:
                logger.error(f"[DB] Failed to create table {table_name}: {e}")
                failed_count += 1

    # ── Layer 2: db.create_all() as a catch-all for any models we missed ──
    try:
        db.create_all()
        # Count what was actually created
        new_inspector = inspect(db.engine)
        after_tables = set(new_inspector.get_table_names(schema='public'))
        newly_created = after_tables - existing_tables
        for t in sorted(newly_created):
            if t not in [m.__tablename__ for m in model_classes]:
                logger.info(f"[DB] db.create_all() created: {t}")
                created_count += 1
    except Exception as e:
        logger.warning(f"[DB] db.create_all() fallback error: {e}")

    # Sync column-level schema for existing tables
    column_fixes = _fix_column_mismatches()
    created_count += column_fixes.get('applied', 0)

    logger.info(
        f"[DB] Schema check complete: {created_count} created, "
        f"{skipped_count} already exist, {failed_count} failed"
    )
    return created_count, skipped_count, failed_count


def create_default_admin():
    """Ensure at least one admin user exists; create from ADMIN_PASSWORD if none found.
    
    Safe to call on every startup — checks first, creates only if needed.
    The admin account uses email admin@collegekhoj.com (configurable via
    ADMIN_EMAIL env var) and the password from ADMIN_PASSWORD (default 'admin123').
    """
    from models import User
    from sqlalchemy.exc import ProgrammingError, OperationalError

    try:
        # Check if users table exists first
        existing = User.query.filter(User.role == 'admin').first()
        if existing:
            logger.info(f"[DB] Admin user already exists: {existing.email}")
            return existing
    except (ProgrammingError, OperationalError) as e:
        # Table doesn't exist yet — log and retry after ensure_schema runs
        logger.warning(f"[DB] Cannot query admin user — table may not exist yet: {e}")
        return None

    # No admin found — create one
    admin_email = os.environ.get('ADMIN_EMAIL', 'admin@collegekhoj.com')
    admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')

    try:
        from app import hash_password
        admin = User(
            email=admin_email,
            first_name='Admin',
            last_name='User',
            role='admin',
            is_verified=True,
            password_hash=hash_password(admin_password),
        )
        db.session.add(admin)
        db.session.commit()
        logger.info(f"[DB] Created default admin: {admin_email}")
        return admin
    except Exception as e:
        db.session.rollback()
        logger.warning(f"[DB] Could not create default admin: {e}")
        return None


def _fix_column_mismatches():
    """Synchronize column definitions between SQLAlchemy models and PostgreSQL.
    
    For the colleges table, this handles:
      - RENAME college -> college_name (preserves all data)
      - ADD missing columns like city, created_at, updated_at
    
    For all tables, this checks every model column and adds any that are missing.
    Safe to call on every startup — idempotent, preserves existing data.
    """
    from sqlalchemy import text, inspect
    import logging
    logger = logging.getLogger(__name__)

    fixes = {'applied': 0, 'skipped': 0}

    try:
        inspector = inspect(db.engine)
    except Exception:
        return fixes

    all_tables = set(inspector.get_table_names(schema='public'))

    # ═══════════════════════════════════════════════════════════════════
    # Per-table column mappings: (model_column_name, sql_type, is_nullable)
    # These mirror what the SQLAlchemy models define
    # ═══════════════════════════════════════════════════════════════════
    table_columns = {
        'colleges': [
            ('college_code', 'VARCHAR(20)', True),
            ('college_name', 'VARCHAR(300)', False),
            ('district', 'VARCHAR(100)', True),
            ('city', 'VARCHAR(100)', True),
            ('college_type', 'VARCHAR(50)', True),
            ('status', "VARCHAR(20) DEFAULT 'active'", True),
            ('location', 'VARCHAR(100)', True),
            ('branch', 'VARCHAR(200)', True),
            ('fees', 'FLOAT', True),
            ('placement_rate', 'FLOAT', True),
            ('nirf_rank', 'INTEGER', True),
            ('rating', 'FLOAT', True),
            ('university', 'VARCHAR(200)', True),
            ('address', 'TEXT', True),
            ('website', 'VARCHAR(500)', True),
            ('naac_grade', 'VARCHAR(10)', True),
            ('is_autonomous', 'BOOLEAN DEFAULT FALSE', True),
            ('created_at', 'TIMESTAMP DEFAULT NOW()', True),
            ('updated_at', 'TIMESTAMP DEFAULT NOW()', True),
        ],
        'users': [
            ('created_at', 'TIMESTAMP DEFAULT NOW()', True),
            ('last_login', 'TIMESTAMP', True),
        ],
        # Progress monitoring columns on upload_jobs — added idempotently on
        # startup. Each ALTER TABLE below is applied independently in the loop,
        # so an "already exists" condition is a no-op (IF NOT EXISTS) and any
        # other error is logged while the remaining columns still get applied.
        'upload_jobs': [
            ('progress_percentage', 'INTEGER DEFAULT 0', True),
            ('current_step', "VARCHAR(40) DEFAULT 'UPLOAD_FILE'", True),
            ('total_pages', 'INTEGER DEFAULT 0', True),
            ('processed_pages', 'INTEGER DEFAULT 0', True),
            ('total_rows_extracted', 'INTEGER DEFAULT 0', True),
            ('total_rows_imported', 'INTEGER DEFAULT 0', True),
            ('failed_rows', 'INTEGER DEFAULT 0', True),
            ('auto_created_colleges', 'INTEGER DEFAULT 0', True),
            ('auto_created_branches', 'INTEGER DEFAULT 0', True),
            ('accuracy_percentage', 'INTEGER DEFAULT 0', True),
        ],
    }

    for table_name, columns in table_columns.items():
        if table_name not in all_tables:
            continue

        try:
            existing_cols = [c['name'] for c in inspector.get_columns(table_name, schema='public')]
        except Exception as e:
            logger.warning(f"Cannot inspect {table_name}: {e}")
            continue

        # ── Handle rename: college -> college_name ──
        if table_name == 'colleges':
            has_old = 'college' in existing_cols
            has_new = 'college_name' in existing_cols
            if has_old and not has_new:
                try:
                    db.session.execute(text(
                        'ALTER TABLE colleges RENAME COLUMN college TO college_name'
                    ))
                    db.session.commit()
                    logger.info("Column fix: colleges.college -> colleges.college_name")
                    fixes['applied'] += 1
                    existing_cols = [c['name'] for c in inspector.get_columns(table_name, schema='public')]
                except Exception as e:
                    db.session.rollback()
                    logger.warning(f"Rename colleges.college failed: {e}")
            elif has_new and has_old:
                try:
                    db.session.execute(text(
                        "UPDATE colleges SET college_name = college WHERE college_name IS NULL"
                    ))
                    db.session.execute(text(
                        "ALTER TABLE colleges DROP COLUMN college"
                    ))
                    db.session.commit()
                    logger.info("Column fix: Merged colleges.college -> colleges.college_name")
                    fixes['applied'] += 1
                    existing_cols = [c['name'] for c in inspector.get_columns(table_name, schema='public')]
                except Exception as e:
                    db.session.rollback()
                    logger.warning(f"Merge colleges.college failed: {e}")

        # ── Add missing columns ──
        for col_name, col_type, nullable in columns:
            if col_name in existing_cols:
                continue

            try:
                # PostgreSQL supports IF NOT EXISTS; SQLite doesn't
                try:
                    db.session.execute(text(
                        f'ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS "{col_name}" {col_type}'
                    ))
                    db.session.commit()
                    logger.info(f"Added column {table_name}.{col_name} ({col_type})")
                    fixes['applied'] += 1
                except Exception as e:
                    db.session.rollback()
                    # Fallback for SQLite: try without IF NOT EXISTS or skip
                    if 'syntax error' in str(e).lower() or 'SQLite' in str(type(e).__name__):
                        try:
                            db.session.execute(text(
                                f'ALTER TABLE {table_name} ADD COLUMN "{col_name}" {col_type}'
                            ))
                            db.session.commit()
                            logger.info(f"Added column {table_name}.{col_name} ({col_type})")
                            fixes['applied'] += 1
                        except Exception:
                            db.session.rollback()
                            logger.warning(f"Could not add {table_name}.{col_name}: column may already exist")
                    else:
                        logger.warning(f"Could not add {table_name}.{col_name}: {e}")
            except Exception as e:
                db.session.rollback()
                logger.warning(f"Could not add {table_name}.{col_name}: {e}")

    return fixes