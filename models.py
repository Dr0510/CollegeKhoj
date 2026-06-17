"""Database models for CollegeKhoj."""
from database import db
from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, DateTime, Boolean, JSON, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
import secrets


class CollegeCutoff(db.Model):
    """Main storage table for MHT-CET/DSE CAP cutoff data.

    Created per user requirements: stores year, round, college_code,
    college_name, course_code, course_name, category, rank, percentile.

    Approval workflow: records are hidden from user-facing queries until the
    associated ImportJob's approval_status is set to 'approved'.
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
        Index('idx_cc_approval', 'approval_status'),
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

    # Approval workflow — denormalized from ImportJob for fast filtering
    approval_status = Column(String(20), default='pending_approval', nullable=False, index=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(Integer, ForeignKey('users.id'), nullable=True)

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
            'approval_status': self.approval_status,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'approved_by': self.approved_by,
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
    """
    Tracks the status of a PDF import process (background processing).

    Enhanced with fields for the Bulk PDF Import Engine:
    - checkpoint_page: last successfully processed page (for resume)
    - rows_extracted: total rows parsed from PDF
    - rows_imported: rows actually inserted (after dedup)
    - rows_failed: rows that failed validation
    - failed_pages: JSON list of page numbers that failed
    - error_log: JSON list of error details
    - page_range_start / page_range_end: smart page range import
    - memory_usage_mb: peak memory usage during processing
    - log_every_n_pages: logging frequency (default 10)

    Approval Workflow:
    Status life cycle: UPLOADED → PROCESSING → COMPLETED → PENDING_APPROVAL → APPROVED | REJECTED
    Failed/Cancelled are terminal error states.
    """

    __tablename__ = 'import_jobs'

    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey('uploaded_files.id'), nullable=False, index=True)

    # Status life cycle: UPLOADED → PROCESSING → COMPLETED → PENDING_APPROVAL → APPROVED | REJECTED
    status = Column(String(20), default='PENDING', nullable=False, index=True)

    # ── Approval Workflow Fields ────────────────────────────────────────────
    approval_status = Column(String(20), nullable=True, index=True)  # pending_approval | approved | rejected | None
    approved_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)

    # Denormalized uploader reference (the admin who uploaded the file)
    uploaded_by = Column(Integer, ForeignKey('users.id'), nullable=True)

    # Progress tracking
    total_pages = Column(Integer, default=0)
    processed_pages = Column(Integer, default=0)
    checkpoint_page = Column(Integer, default=0)
    rows_extracted = Column(Integer, default=0)
    rows_imported = Column(Integer, default=0)
    rows_failed = Column(Integer, default=0)

    # Failed/error tracking
    failed_pages = Column(JSON, default=list)   # [1, 5, 23, ...]
    error_log = Column(JSON, default=list)       # [{"page": 5, "error": "..."}, ...]

    # Page range (smart import)
    page_range_start = Column(Integer, default=1)
    page_range_end = Column(Integer, nullable=True)  # None = all pages

    # Performance metrics
    memory_usage_mb = Column(Float, nullable=True)
    extraction_method = Column(String(20), default='pdfplumber')
    confidence_score = Column(Float, nullable=True)

    # Timing
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    # Relationships
    file = relationship("UploadedFile", back_populates="import_jobs")
    approver = relationship("User", foreign_keys=[approved_by])
    uploaded_by_user = relationship("User", foreign_keys=[uploaded_by])

    def __repr__(self):
        return f'<ImportJob {self.id} for file#{self.file_id} [{self.status}]>'

    def to_dict(self):
        return {
            'id': self.id,
            'file_id': self.file_id,
            'filename': self.file.filename if self.file else None,
            'status': self.status,
            'approval_status': self.approval_status,
            'approved_by': self.approved_by,
            'approver_name': self.approver.display_name() if self.approver else None,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'rejection_reason': self.rejection_reason,
            'uploaded_by': self.uploaded_by,
            'uploader_name': self.uploaded_by_user.display_name() if self.uploaded_by_user else None,
            'total_pages': self.total_pages,
            'processed_pages': self.processed_pages,
            'checkpoint_page': self.checkpoint_page,
            'rows_extracted': self.rows_extracted,
            'rows_imported': self.rows_imported,
            'rows_failed': self.rows_failed,
            'failed_pages': self.failed_pages or [],
            'error_log': self.error_log or [],
            'page_range_start': self.page_range_start,
            'page_range_end': self.page_range_end,
            'memory_usage_mb': self.memory_usage_mb,
            'extraction_method': self.extraction_method,
            'confidence_score': self.confidence_score,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if hasattr(self, 'created_at') and self.created_at else None,
        }


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


class ApprovalRequest(db.Model):
    """Stores approval requests for the Bulk Approval Management System."""

    __tablename__ = 'approval_requests'

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, index=True)
    email = Column(String(200), nullable=False, index=True)
    request_type = Column(String(100), nullable=False, index=True)
    submitted_date = Column(DateTime, default=datetime.utcnow, index=True)
    status = Column(String(20), default='PENDING', nullable=False, index=True)  # PENDING | APPROVED | REJECTED

    # Approval tracking
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejected_by = Column(Integer, ForeignKey('users.id'), nullable=True)

    # Metadata
    notes = Column(Text, nullable=True)
    data_snapshot = Column(JSON, nullable=True)  # extra request data
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    approver = relationship("User", foreign_keys=[approved_by])
    rejecter = relationship("User", foreign_keys=[rejected_by])

    def __repr__(self):
        return f'<ApprovalRequest {self.id} [{self.status}] {self.name} — {self.request_type}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'request_type': self.request_type,
            'submitted_date': self.submitted_date.isoformat() if self.submitted_date else None,
            'status': self.status,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'approved_by': self.approved_by,
            'rejected_at': self.rejected_at.isoformat() if self.rejected_at else None,
            'rejected_by': self.rejected_by,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class BulkActionBackup(db.Model):
    """Stores backup snapshots before bulk actions for restore capability."""

    __tablename__ = 'bulk_action_backups'

    id = Column(Integer, primary_key=True)
    action_type = Column(String(50), nullable=False, index=True)  # approve_selected | reject_selected | delete_selected | approve_all | reject_all | delete_all
    admin_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    affected_count = Column(Integer, default=0)
    snapshot_data = Column(JSON, nullable=False)  # Array of request dicts before modification
    status_filter = Column(String(20), nullable=True)  # PENDING | APPROVED | REJECTED | None
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    admin = relationship("User", foreign_keys=[admin_id])

    def __repr__(self):
        return f'<BulkActionBackup {self.id} [{self.action_type}] {self.affected_count} records>'


class ImportErrorRecord(db.Model):
    """
    Stores rows that failed validation during bulk import.
    Admin can review, edit, and retry these rows.
    """

    __tablename__ = 'import_error_records'

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('import_jobs.id'), nullable=False, index=True)
    page_number = Column(Integer, nullable=True)
    college_code = Column(String(20), nullable=True)
    college_name = Column(Text, nullable=True)
    course_code = Column(String(20), nullable=True)
    course_name = Column(Text, nullable=True)
    category = Column(String(20), nullable=True)
    rank = Column(Integer, nullable=True)
    percentile = Column(Float, nullable=True)
    error_reason = Column(Text, nullable=False)
    raw_text_snippet = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("ImportJob", foreign_keys=[job_id])

    def __repr__(self):
        return f'<ImportErrorRecord {self.id} job#{self.job_id}: {self.error_reason[:50]}>'

    def to_dict(self):
        return {
            'id': self.id,
            'job_id': self.job_id,
            'page_number': self.page_number,
            'college_code': self.college_code,
            'college_name': self.college_name,
            'course_code': self.course_code,
            'course_name': self.course_name,
            'category': self.category,
            'rank': self.rank,
            'percentile': self.percentile,
            'error_reason': self.error_reason,
            'raw_text_snippet': self.raw_text_snippet,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }