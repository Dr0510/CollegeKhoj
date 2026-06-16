from database import db
from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, DateTime, Boolean, JSON, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
import secrets


class CollegeCutoff(db.Model):
    """Main storage table for MHT-CET/DSE CAP cutoff data.

    Created per user requirements: stores year, round, college_code,
    college_name, course_code, course_name, category, rank, percentile.
    """

    __tablename__ = 'college_cutoffs'

    __table_args__ = (
        UniqueConstraint('year', 'round', 'college_code', 'course_code', 'category',
                         name='uq_cutoff_unique'),
        Index('idx_cc_year', 'year'),
        Index('idx_cc_round', 'round'),
        Index('idx_cc_college_name', 'college_name'),
        Index('idx_cc_course_name', 'course_name'),
        Index('idx_cc_category', 'category'),
        Index('idx_cc_percentile', 'percentile'),
        Index('idx_cc_college_code', 'college_code'),
    )

    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    round = Column(Integer, nullable=False)
    college_code = Column(String(20), nullable=False)
    college_name = Column(Text, nullable=False)
    course_code = Column(String(20), nullable=False)
    course_name = Column(Text, nullable=False)
    category = Column(String(20), nullable=False)
    rank = Column(Integer, nullable=True)
    percentile = Column(Numeric(5, 2), nullable=True)
    source_file_id = Column(Integer, ForeignKey('uploaded_files.id'), nullable=True, index=True)
    imported_at = Column(DateTime, default=datetime.utcnow)

    # Relationship back to UploadedFile
    source_file = relationship("UploadedFile", back_populates="cutoff_records_new")

    def __repr__(self):
        return (
            f'<CollegeCutoff {self.college_code} {self.course_code} '
            f'{self.category} Y{self.year}R{self.round}>'
        )

    def to_dict(self):
        return {
            'id': self.id,
            'year': self.year,
            'round': self.round,
            'college_code': self.college_code,
            'college_name': self.college_name,
            'course_code': self.course_code,
            'course_name': self.course_name,
            'category': self.category,
            'rank': self.rank,
            'percentile': float(self.percentile) if self.percentile else None,
            'imported_at': self.imported_at.isoformat() if self.imported_at else None,
        }


class College(db.Model):
    """College model for storing college information"""

    __tablename__ = 'colleges'

    id = Column(Integer, primary_key=True)
    college = Column(String(200), nullable=False)
    location = Column(String(100), nullable=False)
    branch = Column(String(100), nullable=False)
    fees = Column(Float, nullable=False)
    placement_rate = Column(Float, nullable=False)  # Percentage
    nirf_rank = Column(Integer, nullable=False)
    rating = Column(Float, nullable=False)  # Out of 5

    # Relationship to cutoff data
    cutoff_data = relationship("CAPCutoff", back_populates="college")

    def __repr__(self):
        return f'<College {self.college} - {self.branch} ({self.location})>'

    def to_dict(self):
        return {
            'id': self.id,
            'college': self.college,
            'location': self.location,
            'branch': self.branch,
            'fees': self.fees,
            'placement_rate': self.placement_rate,
            'nirf_rank': self.nirf_rank,
            'rating': self.rating,
        }

    def get_cutoff_data(self, year=None, category=None, gender=None):
        query = CAPCutoff.query.filter_by(college_id=self.id)
        if year:
            query = query.filter_by(year=year)
        if category:
            query = query.filter_by(category=category)
        if gender:
            query = query.filter_by(gender=gender)
        return query.all()


class CAPCutoff(db.Model):
    """Single source-of-truth for CAP cutoff data (populated via admin PDF import)."""

    __tablename__ = 'cap_cutoffs'

    __table_args__ = (
        Index('idx_cutoff_year', 'year'),
        Index('idx_cutoff_college_code', 'college_code'),
        Index('idx_cutoff_category', 'category'),
    )

    id = Column(Integer, primary_key=True)
    college_id = Column(Integer, ForeignKey('colleges.id'), nullable=False)
    college_code = Column(String(20), nullable=True, index=True)  # MH-CET institute code
    college_name = Column(String(200), nullable=True)  # Denormalized for fast lookup
    year = Column(Integer, nullable=False, index=True)
    round_number = Column(Integer, nullable=False)
    branch = Column(String(100), nullable=True)  # Denormalized branch name
    category = Column(String(20), nullable=False)  # Open, OBC, SC, ST, NT, EWS
    gender = Column(String(10), nullable=False)  # Male, Female, Other
    cutoff_percentile = Column(Float, nullable=False)
    opening_rank = Column(Integer, nullable=True)
    closing_rank = Column(Integer, nullable=True)
    seats_available = Column(Integer, nullable=True)

    # New columns for admin import tracking
    source_file_id = Column(Integer, ForeignKey('uploaded_files.id'), nullable=True, index=True)
    is_auto_generated = Column(Boolean, default=False)
    validation_status = Column(String(20), default='validated')  # validated | flagged | rejected
    raw_pdf_text = Column(Text, nullable=True)
    imported_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    college = relationship("College", back_populates="cutoff_data")
    source_file = relationship("UploadedFile", back_populates="cutoff_records")

    def __repr__(self):
        return f'<CAPCutoff {self.college_code} - {self.year} R{self.round_number} {self.category}>'

    def to_dict(self):
        return {
            'id': self.id,
            'college_id': self.college_id,
            'college_code': self.college_code,
            'year': self.year,
            'round_number': self.round_number,
            'category': self.category,
            'gender': self.gender,
            'cutoff_percentile': self.cutoff_percentile,
            'opening_rank': self.opening_rank,
            'closing_rank': self.closing_rank,
            'seats_available': self.seats_available,
            'validation_status': self.validation_status,
            'imported_at': self.imported_at.isoformat() if self.imported_at else None,
        }


class MHCETStudent(db.Model):
    """Model for storing MH-CET student information and preferences"""

    __tablename__ = 'mhcet_students'

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    mhcet_score = Column(Float, nullable=False)
    percentile = Column(Float, nullable=False)
    category = Column(String(20), nullable=False)
    gender = Column(String(10), nullable=False)
    domicile = Column(String(50), nullable=False)
    budget_max = Column(Float, nullable=True)
    preferred_locations = Column(Text, nullable=True)
    preferred_branches = Column(Text, nullable=True)

    def __repr__(self):
        return f'<MHCETStudent {self.name} - {self.percentile}%ile>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'mhcet_score': self.mhcet_score,
            'percentile': self.percentile,
            'category': self.category,
            'gender': self.gender,
            'domicile': self.domicile,
            'budget_max': self.budget_max,
            'preferred_locations': self.preferred_locations,
            'preferred_branches': self.preferred_branches,
        }


class User(db.Model):
    """User model — stores user accounts with custom auth."""

    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    email = Column(String(200), unique=True, nullable=False, index=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    password_hash = Column(String(255), nullable=True)
    profile_image_url = Column(Text, nullable=True)
    role = Column(String(20), default='user', nullable=False)  # user | admin
    is_verified = Column(Boolean, default=False, nullable=False)
    verification_code = Column(String(6), nullable=True)
    verification_code_expiry = Column(DateTime, nullable=True)
    reset_token = Column(String(100), nullable=True)
    reset_token_expiry = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login = Column(DateTime, default=datetime.utcnow, nullable=True)

    def is_admin(self):
        return self.role == 'admin' and self.is_verified

    def display_name(self):
        parts = [self.first_name or '', self.last_name or '']
        name = ' '.join(p for p in parts if p).strip()
        return name if name else (self.email or 'User')

    def initials(self):
        fn = (self.first_name or '')[:1].upper()
        ln = (self.last_name or '')[:1].upper()
        return (fn + ln) or (self.email or 'U')[:1].upper()

    def generate_verification_code(self):
        self.verification_code = ''.join(secrets.choice('0123456789') for _ in range(6))
        self.verification_code_expiry = datetime.utcnow() + timedelta(minutes=10)
        return self.verification_code

    def verify_code(self, code: str) -> bool:
        if not self.verification_code or not self.verification_code_expiry:
            return False
        if self.verification_code != code:
            return False
        if datetime.utcnow() > self.verification_code_expiry:
            return False
        return True

    def generate_reset_token(self):
        self.reset_token = secrets.token_urlsafe(32)
        self.reset_token_expiry = datetime.utcnow() + timedelta(hours=1)
        return self.reset_token

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'profile_image_url': self.profile_image_url,
            'display_name': self.display_name(),
            'initials': self.initials(),
            'role': self.role,
            'is_verified': self.is_verified,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None,
        }


# ── Admin / Import Models ────────────────────────────────────────────────────


class UploadedFile(db.Model):
    """Tracks every PDF uploaded through the admin panel (permanent storage)."""

    __tablename__ = 'uploaded_files'

    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)
    mime_type = Column(String(50), nullable=True)

    # Auto-detected metadata
    year = Column(Integer, nullable=True, index=True)
    round_number = Column(Integer, nullable=True)

    # Processing state
    processed_status = Column(String(20), default='pending')  # pending | preview | committed | failed
    total_rows = Column(Integer, default=0)
    valid_rows = Column(Integer, default=0)
    rejected_rows = Column(Integer, default=0)
    duplicate_rows = Column(Integer, default=0)
    preview_data = Column(JSON, nullable=True)  # cached parsed preview
    validation_report = Column(JSON, nullable=True)

    # Extraction metadata
    extraction_method = Column(String(20), default='pdfplumber')  # pdfplumber | ai_fallback
    extraction_confidence = Column(Float, nullable=True)

    # Audit
    uploaded_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    committed_at = Column(DateTime, nullable=True)

    # Relationships
    uploader = relationship("User", foreign_keys=[uploaded_by])
    cutoff_records = relationship("CAPCutoff", back_populates="source_file",
                                  foreign_keys='CAPCutoff.source_file_id')
    cutoff_records_new = relationship("CollegeCutoff", back_populates="source_file",
                                      foreign_keys='CollegeCutoff.source_file_id')
    import_jobs = relationship("ImportJob", back_populates="file", lazy="dynamic")

    def __repr__(self):
        return f'<UploadedFile {self.filename} ({self.processed_status})>'


class ImportJob(db.Model):
    """Tracks the status of a PDF import process (background processing)."""

    __tablename__ = 'import_jobs'

    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey('uploaded_files.id'), nullable=False, index=True)
    status = Column(String(20), default='PENDING', nullable=False, index=True)  # PENDING | PROCESSING | VALIDATING | IMPORTING | COMPLETED | FAILED
    total_pages = Column(Integer, default=0)
    processed_pages = Column(Integer, default=0)
    valid_records = Column(Integer, default=0)
    rejected_records = Column(Integer, default=0)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    confidence_score = Column(Float, nullable=True)

    # Relationships
    file = relationship("UploadedFile", back_populates="import_jobs")

    def __repr__(self):
        return f'<ImportJob {self.id} for file#{self.file_id} [{self.status}]>'


class CollegeTrend(db.Model):
    """Stores pre-computed trend data for colleges."""

    __tablename__ = 'college_trends'

    id = Column(Integer, primary_key=True)
    college_code = Column(String(20), nullable=False, index=True)
    college_name = Column(String(200), nullable=True)
    branch = Column(String(100), nullable=True)
    category = Column(String(20), nullable=True)
    trend_data = Column(JSON, nullable=True)  # {"2022": 95.5, "2023": 97.2, "2024": 98.1}
    direction = Column(String(20), nullable=True)  # increasing | decreasing | stable
    difference = Column(Float, nullable=True)
    computed_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<CollegeTrend {self.college_code} - {self.branch} ({self.direction})>'

    def to_dict(self):
        return {
            'id': self.id,
            'college_code': self.college_code,
            'college_name': self.college_name,
            'branch': self.branch,
            'category': self.category,
            'trend_data': self.trend_data,
            'direction': self.direction,
            'difference': self.difference,
            'computed_at': self.computed_at.isoformat() if self.computed_at else None,
        }


class BackupHistory(db.Model):
    """Database backup metadata."""

    __tablename__ = 'backup_history'

    id = Column(Integer, primary_key=True)
    backup_date = Column(DateTime, default=datetime.utcnow)
    backup_file = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)
    db_type = Column(String(20), nullable=True)  # postgresql | sqlite
    record_count = Column(Integer, nullable=True)
    status = Column(String(20), default='success')
    created_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    notes = Column(Text, nullable=True)

    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f'<BackupHistory {self.backup_date} ({self.status})>'


class AuditLog(db.Model):
    """Audit trail for all admin actions."""

    __tablename__ = 'audit_logs'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    action = Column(String(50), nullable=False)  # login, logout, upload, import_commit, delete, restore, backup
    resource_type = Column(String(50), nullable=True)  # cutoff, college, user, backup, uploaded_file
    resource_id = Column(Integer, nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f'<AuditLog {self.action} on {self.resource_type} at {self.created_at}>'