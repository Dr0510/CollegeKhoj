"""Admin blueprint for CollegeKhoj management system."""
from flask import Blueprint

admin_bp = Blueprint('admin_bp', __name__,
                     template_folder='../templates/admin',
                     static_folder='../static',
                     url_prefix='/admin')

from admin import routes  # noqa: E402, F401

# Bulk Import Engine — registered as a separate blueprint
# to keep the codebase modular and avoid merge conflicts.
from admin.bulk_import_routes import bulk_import_bp
from admin.bulk_import_routes import run_migration as run_bulk_import_migration
from admin.background_worker import init_worker

# Approval Management Blueprint
from admin.approval_routes import approval_bp


def register_bulk_import_engine(app):
    """
    Register the bulk import blueprint and approval blueprint, run migrations.

    Order matters:
      1. Register blueprints (routes available)
      2. Run migration (add new columns to existing tables)
      3. Init worker (recover stale jobs — needs columns to exist)

    Does NOT call db.create_all() — that's handled by app.py.
    """
    app.register_blueprint(bulk_import_bp)
    app.register_blueprint(approval_bp)
    with app.app_context():
        # Migration MUST run before init_worker (recover_stale_jobs needs new columns)
        run_bulk_import_migration()
        init_worker(app)
    app.logger.info("Bulk PDF Import Engine registered")
    app.logger.info("Bulk Import Approval Management registered at /admin/bulk-imports")
