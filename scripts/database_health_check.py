#!/usr/bin/env python3
"""
Database Health Diagnostic for CollegeKhoj Admin System — v2 (Enhanced Diagnostics)

Performs read-only health checks with detailed step timing, startup logging,
full traceback capture, and hang-point identification.

Usage:
    python scripts/database_health_check.py

Requirements (16 total):
  1. Uses existing Flask app context.
  2. Imports app, db, and all models safely.
  3. Startup logging: App import started → completed → DB initialized → app context entered → first query.
  4. Time measurement for every step.
  5. Validates database connection.
  6. Lists all tables.
  7. Verifies existence of 9 required tables.
  8. For each table: Exists/Missing + row count.
  9. For cutoffs table: print columns, compare against model fields, report mismatches.
  10. Import Cutoff model and validate: Cutoff.query.count() + ORM mapping health.
  11. Validate: Flask app context, SQLAlchemy engine, session, connection pool.
  12. Inspect upload_jobs: id, status, created_at, error_message.
  13. Generate report with # DATABASE HEALTH REPORT, ✓ / ✗ markers.
  14. Catch and display full tracebacks.
  15. Identify exact failure point if startup hangs.
  16. Read-only — no data mutation, no migrations, no inserts.
"""

import os
import sys
import time
import traceback
import logging
from datetime import datetime, timezone

# Add project root to sys.path so we can import app, database, models
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ── Configuration ─────────────────────────────────────────────────────────────
REQUIRED_TABLES = [
    'cutoffs',
    'admission_types',
    'academic_years',
    'cap_rounds',
    'colleges',
    'branches',
    'upload_jobs',
    'audit_logs',
    'backup_history',
]

# Silence external logging noise during diagnostics
logging.basicConfig(level=logging.WARNING)
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger('flask').setLevel(logging.WARNING)

# ═══════════════════════════════════════════════════════════════════════════════
#  TIMER HELPER
# ═══════════════════════════════════════════════════════════════════════════════

class StepTimer:
    """Simple step timer that records elapsed time since the last call."""

    def __init__(self):
        self.start = time.perf_counter()
        self.last = self.start

    def lap(self, label: str) -> float:
        """Record a lap and return seconds since last lap."""
        now = time.perf_counter()
        elapsed = now - self.last
        total = now - self.start
        print(f"  [TIMER] {label}: +{elapsed:.3f}s  (total: {total:.3f}s)")
        sys.stdout.flush()
        self.last = now
        return elapsed

    def elapsed_total(self) -> float:
        return time.perf_counter() - self.start


def banner(text: str, char: str = '=') -> str:
    """Return a centred banner string."""
    width = 72
    side = (width - len(text) - 2) // 2
    return f"{char * side} {text} {char * (width - side - len(text) - 2)}"


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — BOOTSTRAP FLASK APP (with startup logging & hang detection)
# ═══════════════════════════════════════════════════════════════════════════════

def bootstrap_app(timer: StepTimer):
    """
    Import the Flask app, database, and all models with startup logging.
    If the script hangs, the last printed line identifies the exact failure point.
    """
    print()
    print(banner("STARTUP SEQUENCE"))
    print(f"  System time : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()

    # ── Milestone 1: App import started ────────────────────────────
    print("  [MILESTONE] App import started...")
    sys.stdout.flush()
    timer.lap("import app started")

    try:
        from app import app
    except Exception:
        print("  [FATAL] Failed to import 'app' from app module.")
        traceback.print_exc()
        sys.exit(1)

    timer.lap("import app completed")

    # ── Milestone 2: App import completed ──────────────────────────
    print("  [MILESTONE] App import completed successfully.")
    sys.stdout.flush()

    # ── Milestone 3: Database initialized ──────────────────────────
    from database import db
    timer.lap("database imported")
    print("  [MILESTONE] Database (db) imported successfully.")
    sys.stdout.flush()

    # ── Milestone 4: App context entered ───────────────────────────
    print("  [MILESTONE] Entering app context...")
    sys.stdout.flush()

    try:
        ctx = app.app_context()
        ctx.push()
    except Exception:
        print("  [FATAL] Failed to enter Flask app context.")
        traceback.print_exc()
        sys.exit(1)

    timer.lap("app context entered")
    print("  [MILESTONE] App context entered successfully.")
    sys.stdout.flush()

    # ── Import all models safely ───────────────────────────────────
    print("  [MILESTONE] Importing models...")
    sys.stdout.flush()

    model_import_errors = []
    model_names = [
        'AdmissionType', 'AcademicYear', 'CapRound', 'College', 'Branch',
        'Cutoff', 'UploadJob', 'User', 'BackupHistory', 'AuditLog',
        'LoginHistory', 'CollegeCutoff', 'CAPCutoff', 'UploadedFile',
        'ImportJob', 'Category', 'CollegeAdmissionType',
    ]

    loaded_models = {}
    for name in model_names:
        try:
            module = __import__('models', fromlist=[name])
            loaded_models[name] = getattr(module, name)
        except Exception as exc:
            model_import_errors.append((name, str(exc)))
            traceback.print_exc()

    if model_import_errors:
        print(f"  [WARN] {len(model_import_errors)} model(s) failed to import:")
        for mname, merr in model_import_errors:
            print(f"         ✗ {mname}: {merr}")
    else:
        print("  [MILESTONE] All models imported successfully.")

    sys.stdout.flush()

    # ── Milestone 5: First query started ───────────────────────────
    print("  [MILESTONE] Running first query (SELECT 1)...")
    sys.stdout.flush()

    try:
        from sqlalchemy import text
        result = db.session.execute(text("SELECT 1"))
        _ = result.scalar()
        timer.lap("first query completed")
        print("  [MILESTONE] First query completed — database is responsive.")
    except Exception:
        print("  [FATAL] First query failed — database may be unreachable.")
        traceback.print_exc()
        sys.exit(1)

    sys.stdout.flush()

    return app, db, loaded_models


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — TABLE AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def check_tables(insp, db) -> dict:
    """Verify existence and row counts of required tables."""
    from sqlalchemy import text

    print()
    print(banner("TABLE STATUS", '-'))

    existing_tables = set(insp.get_table_names())
    print(f"  Total tables in database: {len(existing_tables)}")
    if existing_tables:
        print(f"  All tables: {', '.join(sorted(existing_tables))}")
    print()

    results = {}
    for table in REQUIRED_TABLES:
        exists = table in existing_tables
        count = 0
        if exists:
            try:
                result = db.session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
            except Exception as e:
                print(f"  [ERROR] Could not count {table}: {e}")
                traceback.print_exc()
                count = -1
        results[table] = {'exists': exists, 'count': count}

        icon = "✓" if exists else "✗"
        if exists:
            print(f"  {icon} {table} — {count} row(s)")
        else:
            print(f"  {icon} {table} — TABLE NOT FOUND")

    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 3 — CUTOFF COLUMN VALIDATION (dynamic vs model fields)
# ═══════════════════════════════════════════════════════════════════════════════

def validate_cutoff_columns(insp, loaded_models) -> dict:
    """Compare actual cutoffs table columns against the Cutoff model fields."""
    print()
    print(banner("CUTOFF TABLE COLUMN VALIDATION", '-'))

    Cutoff = loaded_models.get('Cutoff')
    existing_tables = set(insp.get_table_names())

    if 'cutoffs' not in existing_tables:
        print("  ✗ cutoffs table does not exist — cannot validate columns.")
        return {'exists': False, 'db_columns': [], 'model_columns': [], 'missing': [], 'extra': []}

    # Get actual DB columns
    try:
        db_columns = [c['name'] for c in insp.get_columns('cutoffs')]
    except Exception as e:
        print(f"  ✗ Cannot inspect cutoffs columns: {e}")
        traceback.print_exc()
        return {'exists': True, 'db_columns': [], 'model_columns': [], 'missing': [], 'extra': [], 'error': str(e)}

    # Get model columns
    if Cutoff is None:
        print("  ✗ Cutoff model not available — cannot compare.")
        return {'exists': True, 'db_columns': db_columns, 'model_columns': [], 'missing': [], 'extra': []}

    try:
        model_columns = [c.name for c in Cutoff.__table__.columns]
    except Exception as e:
        print(f"  ✗ Cannot inspect Cutoff model columns: {e}")
        traceback.print_exc()
        return {'exists': True, 'db_columns': db_columns, 'model_columns': [], 'missing': [], 'extra': [], 'error': str(e)}

    missing_from_db = [c for c in model_columns if c not in db_columns]
    extra_in_db = [c for c in db_columns if c not in model_columns]

    print(f"  DB columns ({len(db_columns)}): {', '.join(db_columns)}")
    print(f"  Model columns ({len(model_columns)}): {', '.join(model_columns)}")
    print()

    if missing_from_db:
        print(f"  ✗ Missing from database ({len(missing_from_db)}):")
        for c in missing_from_db:
            print(f"      - {c}")
    else:
        print("  ✓ All model columns present in database.")

    if extra_in_db:
        print(f"  ⚠ Extra columns in database (not in model) ({len(extra_in_db)}):")
        for c in extra_in_db:
            print(f"      - {c}")
    else:
        print("  ✓ No extra columns in database.")

    return {
        'exists': True,
        'db_columns': db_columns,
        'model_columns': model_columns,
        'missing': missing_from_db,
        'extra': extra_in_db,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 4 — ORM MAPPING VALIDATION (Cutoff model)
# ═══════════════════════════════════════════════════════════════════════════════

def validate_orm_mapping(db, loaded_models) -> dict:
    """Verify ORM model maps correctly to the cutoffs table."""
    from sqlalchemy import text

    print()
    print(banner("ORM MAPPING VALIDATION", '-'))

    Cutoff = loaded_models.get('Cutoff')
    if Cutoff is None:
        print("  ✗ Cutoff model is not loaded — cannot perform ORM validation.")
        return {'orm_ok': False, 'error': 'Cutoff model not loaded'}

    results = {}

    # 1. ORM query via Cutoff
    try:
        orm_count = Cutoff.query.count()
        results['orm_count'] = orm_count
        results['orm_ok'] = True
        print(f"  ✓ Cutoff.query.count() = {orm_count}")
    except Exception as e:
        results['orm_count'] = -1
        results['orm_ok'] = False
        results['orm_error'] = str(e)
        print(f"  ✗ Cutoff.query.count() FAILED: {e}")
        traceback.print_exc()

    # 2. Raw SQL count
    try:
        raw = db.session.execute(text("SELECT COUNT(*) FROM cutoffs"))
        results['raw_count'] = raw.scalar()
        results['sql_ok'] = True
        print(f"  ✓ Raw SQL COUNT(*)    = {results['raw_count']}")
    except Exception as e:
        results['raw_count'] = -1
        results['sql_ok'] = False
        results['sql_error'] = str(e)
        print(f"  ✗ Raw SQL COUNT(*) FAILED: {e}")
        traceback.print_exc()

    # 3. Compare
    if results.get('orm_ok') and results.get('sql_ok'):
        results['match'] = results['orm_count'] == results['raw_count']
        if results['match']:
            print("  ✓ ORM and raw SQL counts MATCH — mapping is correct.")
        else:
            print(f"  ✗ MISMATCH: ORM={results['orm_count']} vs SQL={results['raw_count']}")
    else:
        results['match'] = False
        print("  ✗ Cannot verify mapping due to query errors.")

    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 5 — UPLOAD JOBS INSPECTION
# ═══════════════════════════════════════════════════════════════════════════════

def inspect_upload_jobs(db, loaded_models) -> list:
    """Fetch upload_jobs and display id, status, created_at, error_message."""
    from sqlalchemy import text

    print()
    print(banner("UPLOAD JOB INSPECTION", '-'))

    UploadJob = loaded_models.get('UploadJob')

    # Try ORM first, fall back to raw SQL
    jobs = []
    if UploadJob is not None:
        try:
            orm_jobs = UploadJob.query.order_by(UploadJob.id).all()
            for job in orm_jobs:
                err = job.error_message
                if err and len(str(err)) > 200:
                    err = str(err)[:200] + '...'
                jobs.append({
                    'id': job.id,
                    'status': job.status,
                    'created_at': job.created_at.isoformat() if job.created_at else None,
                    'error_message': err,
                })
        except Exception as e:
            print(f"  [WARN] ORM query for UploadJob failed, falling back to raw SQL: {e}")
            traceback.print_exc()

    if not jobs:
        # Raw SQL fallback
        try:
            sql_jobs = db.session.execute(
                text("SELECT id, status, created_at, error_message FROM upload_jobs ORDER BY id")
            ).fetchall()
            for row in sql_jobs:
                err = row.error_message
                if err and len(str(err)) > 200:
                    err = str(err)[:200] + '...'
                jobs.append({
                    'id': row.id,
                    'status': row.status,
                    'created_at': row.created_at.isoformat() if row.created_at else None,
                    'error_message': err,
                })
        except Exception as e:
            print(f"  ✗ Cannot query upload_jobs table: {e}")
            traceback.print_exc()

    if not jobs:
        print("  (No upload jobs found or table is empty)")
    else:
        print(f"  Total jobs: {len(jobs)}")
        print()
        # Table header
        print(f"  {'ID':>4} | {'Status':<22} | {'Created At':<22} | {'Error Message':<50}")
        print(f"  {'-'*4}-+-{'-'*22}-+-{'-'*22}-+-{'-'*50}")

        for j in jobs:
            err_display = (j['error_message'] or '')[:50].replace('\n', ' ')
            created = (j['created_at'] or '')[:22]
            print(f"  {j['id']:>4} | {j['status']:<22} | {created:<22} | {err_display:<50}")

        # Status summary
        statuses = {}
        for j in jobs:
            s = j['status']
            statuses[s] = statuses.get(s, 0) + 1
        print(f"\n  Status summary: {', '.join(f'{v} {k}' for k, v in statuses.items())}")

        # Failed jobs detail
        failed = [j for j in jobs if j['status'] == 'FAILED']
        if failed:
            print(f"\n  ⚠ Failed jobs ({len(failed)}):")
            for fj in failed:
                print(f"    - Job #{fj['id']}")
                print(f"      Status: {fj['status']}")
                print(f"      Error: {fj['error_message'] or '(no details)'}")

    return jobs


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 6 — SESSION & CONNECTION HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

def validate_session_health(app, db) -> dict:
    """Test Flask app context, SQLAlchemy engine, session, and connection pool."""
    from sqlalchemy import text

    print()
    print(banner("SESSION & CONNECTION HEALTH", '-'))

    results = {}

    # 1. Flask app context
    try:
        _ = app.name
        results['app_context'] = True
        print("  ✓ Flask app context is active.")
    except Exception as e:
        results['app_context'] = False
        results['app_context_error'] = str(e)
        print(f"  ✗ Flask app context is INACTIVE: {e}")
        traceback.print_exc()

    # 2. Database dialect
    try:
        dialect = db.engine.dialect.name
        results['dialect'] = dialect
        print(f"  ✓ Database dialect: {dialect}")
    except Exception as e:
        results['dialect'] = 'unknown'
        print(f"  ✗ Cannot determine dialect: {e}")

    # 3. Database connectivity (session)
    try:
        db.session.execute(text("SELECT 1"))
        results['db_connected'] = True
        print("  ✓ Database session is connected.")
    except Exception as e:
        results['db_connected'] = False
        results['db_connect_error'] = str(e)
        print(f"  ✗ Database session is DISCONNECTED: {e}")
        traceback.print_exc()

    # 4. SQLAlchemy engine health
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        results['engine_healthy'] = True
        print("  ✓ SQLAlchemy engine is healthy.")
    except Exception as e:
        results['engine_healthy'] = False
        results['engine_error'] = str(e)
        print(f"  ✗ SQLAlchemy engine is UNHEALTHY: {e}")
        traceback.print_exc()

    # 5. Connection pool status
    try:
        pool = db.engine.pool
        pool_status = pool.status()
        results['pool_status'] = pool_status
        results['pool_size'] = pool.size()
        results['pool_checkedin'] = pool.checkedin()
        results['pool_overflow'] = pool.overflow()
        print(f"  ✓ Connection pool: {pool_status}")
        print(f"    Size: {pool.size()}, Checked-in: {pool.checkedin()}, Overflow: {pool.overflow()}")
    except Exception as e:
        results['pool_status'] = str(e)
        print(f"  ⚠ Cannot inspect connection pool: {e}")

    # 6. Database URL (sanitized)
    try:
        raw_url = str(db.engine.url)
        # Hide password
        if '@' in raw_url:
            parts = raw_url.split('@')
            creds = parts[0].split('://')[1] if '://' in parts[0] else ''
            if ':' in creds:
                user = creds.split(':')[0]
                sanitized = raw_url.replace(creds, f"{user}:****")
            else:
                sanitized = raw_url
        else:
            sanitized = raw_url
        results['db_url_sanitized'] = sanitized
        print(f"  ✓ Database URL: {sanitized}")
    except Exception as e:
        print(f"  ⚠ Cannot read database URL: {e}")

    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Main entry point — orchestrates all diagnostic checks."""
    timer = StepTimer()

    # ── Phase 1: Bootstrap ─────────────────────────────────────────────
    try:
        app, db, loaded_models = bootstrap_app(timer)
    except SystemExit:
        print("\n  ✗ Fatal error during startup — cannot continue diagnostics.")
        print(f"\n{'=' * 72}")
        print("  ROOT CAUSE: Application failed during startup sequence.")
        print("  FAILURE POINT: See the [MILESTONE] and [FATAL] messages above.")
        print("  The last printed [MILESTONE] is where the hang occurred.")
        print(f"{'=' * 72}")
        sys.exit(1)

    start_total = timer.elapsed_total()

    try:
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy import text

        insp = sa_inspect(db.engine)

        # ── Phase 2: Table audit ────────────────────────────────────
        t1 = time.perf_counter()
        table_info = check_tables(insp, db)
        print(f"  [TIMER] Table audit: +{time.perf_counter() - t1:.3f}s")

        # ── Phase 3: Cutoff column validation ───────────────────────
        t2 = time.perf_counter()
        cutoff_cols = validate_cutoff_columns(insp, loaded_models)
        print(f"  [TIMER] Cutoff column validation: +{time.perf_counter() - t2:.3f}s")

        # ── Phase 4: ORM mapping validation ─────────────────────────
        t3 = time.perf_counter()
        orm_result = validate_orm_mapping(db, loaded_models)
        print(f"  [TIMER] ORM validation: +{time.perf_counter() - t3:.3f}s")

        # ── Phase 5: Upload job inspection ──────────────────────────
        t4 = time.perf_counter()
        jobs = inspect_upload_jobs(db, loaded_models)
        print(f"  [TIMER] Upload job inspection: +{time.perf_counter() - t4:.3f}s")

        # ── Phase 6: Session health ─────────────────────────────────
        t5 = time.perf_counter()
        health = validate_session_health(app, db)
        print(f"  [TIMER] Session health: +{time.perf_counter() - t5:.3f}s")

        # ── Phase 7: Generate final report ──────────────────────────
        print()
        print(banner("DATABASE HEALTH REPORT", '='))
        print(f"  Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"  Dialect  : {health.get('dialect', 'unknown')}")
        print()

        # Table summary
        missing_tables = [t for t, i in table_info.items() if not i['exists']]
        present_tables = [t for t, i in table_info.items() if i['exists']]

        if present_tables:
            print(f"  ✓ Existing Tables ({len(present_tables)}):")
            for t in present_tables:
                print(f"      {t} — {table_info[t]['count']} row(s)")
        else:
            print("  ✗ No required tables exist.")

        if missing_tables:
            print(f"  ✗ Missing Tables ({len(missing_tables)}): {', '.join(missing_tables)}")
        else:
            print(f"  ✓ No missing tables.")

        print()

        # Row counts
        print(f"  ✓ Row Counts:")
        for t in REQUIRED_TABLES:
            info = table_info.get(t, {'exists': False, 'count': 0})
            if info['exists']:
                print(f"      {t}: {info['count']}")
            else:
                print(f"      {t}: 0 (table missing)")

        print()

        # Cutoff validation
        if cutoff_cols.get('exists'):
            missing_c = cutoff_cols.get('missing', [])
            if missing_c:
                print(f"  ✗ Cutoff Validation: {len(missing_c)} column(s) missing from DB")
                for m in missing_c:
                    print(f"      - {m}")
            else:
                print(f"  ✓ Cutoff Validation: All {len(cutoff_cols.get('model_columns', []))} model columns present.")
        else:
            print("  ✗ Cutoff Validation: Table does not exist.")

        print()

        # Upload jobs
        if jobs:
            failed_count = len([j for j in jobs if j['status'] == 'FAILED'])
            print(f"  ✓ Upload Jobs: {len(jobs)} total, {failed_count} failed")
        else:
            print(f"  ✓ Upload Jobs: 0 total")

        print()

        # ORM validation
        if orm_result.get('match'):
            print("  ✓ ORM Validation: Mapping verified — counts match.")
        elif orm_result.get('orm_ok') is False:
            print(f"  ✗ ORM Validation: Query failed — {orm_result.get('orm_error', 'unknown')}")
        else:
            print(f"  ✗ ORM Validation: Count mismatch — ORM={orm_result.get('orm_count')} SQL={orm_result.get('raw_count')}")

        print()

        # Session validation
        checks = [
            ('Flask App Context', health.get('app_context', False)),
            ('DB Connection', health.get('db_connected', False)),
            ('SQLAlchemy Engine', health.get('engine_healthy', False)),
            ('Connection Pool', health.get('pool_status') is not None and 'error' not in str(health.get('pool_status')).lower()),
        ]
        all_healthy = all(v for _, v in checks)
        for label, ok in checks:
            icon = "✓" if ok else "✗"
            print(f"  {icon} {label}")

        if all_healthy:
            print("  ✓ Session Validation: All systems operational.")
        else:
            print("  ✗ Session Validation: Issues detected (see above).")

        print()

        # ── Root cause & recommendations ─────────────────────────────
        print(banner("ANALYSIS", '='))
        print()

        issues = []

        if missing_tables:
            issues.append(f"Missing tables: {', '.join(missing_tables)} — schema may be incomplete.")

        cutoff_issues = cutoff_cols.get('missing', [])
        if cutoff_issues:
            issues.append(f"Cutoff table missing columns: {', '.join(cutoff_issues)} — model/DB mismatch.")

        if not health.get('db_connected'):
            issues.append("Database connection failed — check DATABASE_URL/NEON_DATABASE_URL.")

        if not health.get('engine_healthy'):
            issues.append("SQLAlchemy engine is unhealthy — check database server status.")

        failed_jobs = [j for j in jobs if j['status'] == 'FAILED'] if jobs else []
        if failed_jobs:
            issues.append(f"{len(failed_jobs)} failed upload job(s) detected — may need retry or re-upload.")

        if issues:
            print("  Issues detected:")
            for issue in issues:
                print(f"    • {issue}")
        else:
            print("  ✓ No issues detected. Database is healthy.")

        print()

        # ── Recommended fix ──────────────────────────────────────────
        print(banner("RECOMMENDED FIX", '='))
        print()

        if missing_tables:
            print("  1. Run schema migration or ensure_schema() to create missing tables.")
        if 'cutoffs' not in missing_tables and cutoff_issues:
            print("  2. Run a migration to add missing columns to the cutoffs table.")
        if failed_jobs:
            print("  3. Re-upload failed PDFs or implement a retry mechanism in background_worker.py.")
        if not health.get('db_connected') or not health.get('engine_healthy'):
            print("  4. Verify database server is running and NEON_DATABASE_URL is correct in .env.")

        if not issues:
            print("  Database is healthy. No fixes required.")
        else:
            print()

        print(f"  Total diagnostic time: {time.perf_counter() - timer.start:.3f}s")
        print(f"  Startup time: {start_total:.3f}s")
        print()

    except Exception as e:
        print(f"\n  [FATAL] Unexpected error during diagnostics: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Cleanup: pop app context if it was pushed
        try:
            from flask import _app_ctx_stack
            if _app_ctx_stack.top is not None:
                _app_ctx_stack.pop()
        except Exception:
            pass


if __name__ == '__main__':
    main()