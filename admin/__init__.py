"""Admin v2 blueprint for CollegeKhoj management system."""
from flask import Blueprint

admin_bp = Blueprint('admin_bp', __name__,
                     template_folder='../templates/admin',
                     static_folder='../static',
                     url_prefix='/admin')

from admin import routes  # noqa: E402, F401