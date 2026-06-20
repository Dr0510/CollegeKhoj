"""
Final schema sync script for CollegeKhoj admin v2.

Idempotent migration that synchronizes the database schema with SQLAlchemy models.
Safe to run multiple times - never drops existing data.
"""
import os
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ensure we're in the right directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask
from database import db, init_database, ensure_schema

def run_migration():
    """Run the complete schema synchronization."""
    app = Flask(__name__)
    app.secret_key = "migration-secret"
    
    # Load database URL
    raw = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if raw:
        app.config["SQLALCHEMY_DATABASE_URI"] = raw
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
            "DATABASE_URL", "sqlite:///collegekhoj.db"
        )
    
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }
    
    db.init_app(app)
    
    with app.app_context():
        # 1. Create/update all tables
        logger.info("Step 1: Synchronizing schema...")
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
        
        # Tables to ensure exist
        required_tables = [
            'admission_types', 'academic_years', 'cap_rounds',
            'colleges', 'branches', 'cutoffs',
            'upload_jobs', 'users', 'backup_history',
            'audit_logs', 'login_history',
            # Legacy tables kept for migration compat
            'college_cutoffs', 'cap_cutoffs', 'uploaded_files',
            'import_jobs', 'college_trends',
            'approval_requests', 'bulk_action_backups',
            'import_error_records', 'manual_cutoff_entries',
            'mhcet_students',
        ]
        
        from models import (
            AdmissionType, AcademicYear, CapRound, College, Branch,
            Cutoff, UploadJob, User, BackupHistory, AuditLog, LoginHistory,
        )
        
        # Create only the NEW tables if they don't exist
        new_models = [
            ('admission_types', AdmissionType),
            ('academic_years', AcademicYear),
            ('cap_rounds', CapRound),
            ('colleges', College),
            ('branches', Branch),
            ('cutoffs', Cutoff),
            ('upload_jobs', UploadJob),
            ('backup_history', BackupHistory),
            ('audit_logs', AuditLog),
            ('login_history', LoginHistory),
        ]
        
        for name, model in new_models:
            if name not in existing_tables:
                try:
                    model.__table__.create(db.engine)
                    logger.info(f"  + Created table: {name}")
                except Exception as e:
                    logger.warning(f"  ! Could not create {name}: {e}")
            else:
                logger.info(f"  ~ Table exists: {name}")
        
        # 2. Ensure users table exists (for auth)
        if 'users' not in existing_tables:
            try:
                User.__table__.create(db.engine)
                logger.info("  + Created table: users")
            except Exception as e:
                logger.warning(f"  ! Could not create users: {e}")
        
        # 3. Verify core tables have required columns
        logger.info("Step 2: Verifying columns...")
        column_checks = {
            'colleges': ['college_code', 'college_name', 'district', 'city',
                        'college_type', 'status', 'location', 'branch', 'fees',
                        'placement_rate', 'nirf_rank', 'rating'],
            'users': ['email', 'password_hash', 'role', 'is_verified',
                     'verification_code', 'verification_code_expiry',
                     'reset_token', 'reset_token_expiry', 'created_at', 'last_login'],
            'cutoffs': ['admission_type_id', 'college_id', 'branch_id',
                       'academic_year_id', 'cap_round_id', 'category',
                       'seat_type', 'gender', 'cutoff_percentile', 'cutoff_rank'],
            'upload_jobs': ['filename', 'status', 'admission_type_id',
                          'academic_year_id', 'cap_round_id'],
            'audit_logs': ['user_id', 'action', 'resource_type', 'resource_id',
                         'details', 'ip_address', 'user_agent', 'created_at'],
            'backup_history': ['backup_date', 'backup_file', 'file_size',
                            'db_type', 'record_count', 'status'],
        }
        
        for table, cols in column_checks.items():
            if table not in existing_tables:
                continue
            try:
                existing_cols = [c['name'] for c in inspector.get_columns(table)]
                for col in cols:
                    if col not in existing_cols:
                        logger.warning(f"  ! {table} missing column: {col}")
            except Exception as e:
                logger.warning(f"  ! Cannot inspect {table}: {e}")
        
        # 4. Ensure schema using existing ensure_schema
        logger.info("Step 3: Running ensure_schema() for remaining fixes...")
        result = ensure_schema()
        logger.info(f"  Schema check: {result}")
        
        # 5. Verify database is responsive
        logger.info("Step 4: Verifying database connection...")
        try:
            db.session.execute(text('SELECT 1'))
            logger.info("  Database connection: OK")
            
            # Log record counts
            for table in ['admission_types', 'academic_years', 'cap_rounds', 
                         'colleges', 'branches', 'cutoffs', 'users', 'upload_jobs']:
                try:
                    count = db.session.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar()
                    logger.info(f"  {table}: {count} records")
                except Exception:
                    pass
                    
        except Exception as e:
            logger.error(f"  Database connection failed: {e}")
            return False
        
        logger.info("Migration completed successfully!")
        return True

if __name__ == '__main__':
    success = run_migration()
    sys.exit(0 if success else 1)