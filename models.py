"""Database models for CollegeKhoj."""
from database import db
from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, DateTime, Boolean, JSON, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
import secrets
import hashlib


# ═══════════════════════════════════════════════════════════════════════════════
# NEW ADMIN v2 TABLES
# ═══════════════════════════════════════════════════════════════════════════════


class AdmissionType(db.Model):
    """Types of admissions supported by CollegeKhoj.

    Examples: ENGG, DSE, POLY
    """
    __tablename__ = 'admission_types'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    code = Column(String(20), unique=True, nullable=False, index=True)

    cutoffs = relationship("Cutoff", back_populates="admission_type_rel")
    upload_jobs = relationship("UploadJob", back_populates="admission_type_rel")

    def __repr__(self):
        return f'<AdmissionType {self.code}>'

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'code': self.code}


class AcademicYear(db.Model):
    """Academic years for cutoff data. Example: 2026-27."""
    __tablename__ = 'academic_years'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    academic_year = Column(String(20), unique=True, nullable=False, index=True)

    cutoffs = relationship("Cutoff", back_populates="academic_year_rel")
    upload_jobs = relationship("UploadJob", back_populates="academic_year_rel")

    def __repr__(self):
        return f'<AcademicYear {self.academic_year}>'

    def to_dict(self):
        return {'id': self.id, 'academic_year': self.academic_year}


class CapRound(db.Model):
    """CAP rounds. Examples: Round I, Round II, Round III."""
    __tablename__ = 'cap_rounds'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)

    cutoffs = relationship("Cutoff", back_populates="cap_round_rel")
    upload_jobs = relationship("UploadJob", back_populates="cap_round_rel")

    def __repr__(self):
        return f'<CapRound {self.name}>'

    def to_dict(self):
        return {'id': self.id, 'name': self.name}


class College(db.Model):
    """College model — redesigned for admin v2."""
    __tablename__ = 'colleges'
    __table_args__ = (
        Index('idx_college_code', 'college_code'),
        Index('idx_college_name', 'college_name'),
        Index('idx_college_district', 'district'),
        Index('idx_college_status', 'status'),
        {'schema': 'public'},
    )

    id = Column(Integer, primary_key=True)
    college_code = Column(String(20), unique=True, nullable=True, index=True)
    college_name = Column(String(300), nullable=False)
    district = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    college_type = Column(String(50), nullable=True)  # Government, Private, Aided
    status = Column(String(20), default='active', nullable=True)

    # Legacy fields kept for backward compat with public site
    location = Column(String(100), nullable=True)
    branch = Column(String(200), nullable=True)
    fees = Column(Float, nullable=True)
    placement_rate = Column(Float, nullable=True)
    nirf_rank = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)
    university = Column(String(200), nullable=True)
    address = Column(Text, nullable=True)
    website = Column(String(500), nullable=True)
    naac_grade = Column(String(10), nullable=True)
    is_autonomous = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    cutoffs = relationship("Cutoff", back_populates="college_rel")

    def __repr__(self):
        return f'<College {self.college_code} - {self.college_name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'college_code': self.college_code,
            'college_name': self.college_name,
            'district': self.district,
            'city': self.city,
            'college_type': self.college_type,
            'status': self.status,
            'fees': self.fees,
            'placement_rate': self.placement_rate,
            'nirf_rank': self.nirf_rank,
            'rating': self.rating,
            'university': self.university,
            'website': self.website,
            'naac_grade': self.naac_grade,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Branch(db.Model):
    """Engineering/polytechnic branches."""
    __tablename__ = 'branches'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    branch_code = Column(String(20), unique=True, nullable=False, index=True)
    branch_name = Column(String(200), nullable=False)

    cutoffs = relationship("Cutoff", back_populates="branch_rel")

    def __repr__(self):
        return f'<Branch {self.branch_code} - {self.branch_name}>'

    def to_dict(self):
        return {'id': self.id, 'branch_code': self.branch_code, 'branch_name': self.branch_name}


class Cutoff(db.Model):
    """Unified cutoff records — single source of truth.

    Duplicate key: (admission_type_id, college_id, branch_id, academic_year_id,
                    cap_round_id, category, seat_type)
    On conflict: UPDATE existing record.
    """
    __tablename__ = 'cutoffs'
    __table_args__ = (
        UniqueConstraint(
            'admission_type_id', 'college_id', 'branch_id',
            'academic_year_id', 'cap_round_id', 'category', 'seat_type',
            name='uq_cutoff_unique'
        ),
        Index('idx_cutoff_admission_type', 'admission_type_id'),
        Index('idx_cutoff_college', 'college_id'),
        Index('idx_cutoff_branch', 'branch_id'),
        Index('idx_cutoff_year', 'academic_year_id'),
        Index('idx_cutoff_round', 'cap_round_id'),
        Index('idx_cutoff_category', 'category'),
        Index('idx_cutoff_percentile', 'cutoff_percentile'),
        Index('idx_cutoff_rank', 'cutoff_rank'),
        {'schema': 'public'},
    )

    id = Column(Integer, primary_key=True)

    # Foreign keys — schema-qualified to match __table_args__ schema='public'
    admission_type_id = Column(Integer, ForeignKey('public.admission_types.id'), nullable=False, index=True)
    college_id = Column(Integer, ForeignKey('public.colleges.id'), nullable=False, index=True)
    branch_id = Column(Integer, ForeignKey('public.branches.id'), nullable=False, index=True)
    academic_year_id = Column(Integer, ForeignKey('public.academic_years.id'), nullable=False, index=True)
    cap_round_id = Column(Integer, ForeignKey('public.cap_rounds.id'), nullable=False, index=True)

    # Cutoff data
    category = Column(String(50), nullable=False, index=True)
    seat_type = Column(String(50), nullable=True)  # GOPEN, LOPEN, GSC, etc.
    gender = Column(String(20), default='Gender-Neutral', nullable=False)
    minority_status = Column(String(50), nullable=True)
    cutoff_percentile = Column(Numeric(6, 2), nullable=True)
    cutoff_rank = Column(Integer, nullable=True)

    # Source tracking
    source_pdf = Column(String(500), nullable=True)
    upload_job_id = Column(Integer, ForeignKey('public.upload_jobs.id'), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    admission_type_rel = relationship("AdmissionType", back_populates="cutoffs")
    college_rel = relationship("College", back_populates="cutoffs")
    branch_rel = relationship("Branch", back_populates="cutoffs")
    academic_year_rel = relationship("AcademicYear", back_populates="cutoffs")
    cap_round_rel = relationship("CapRound", back_populates="cutoffs")
    upload_job_rel = relationship("UploadJob", back_populates="cutoffs")

    def __repr__(self):
        return (
            f'<Cutoff AT{self.admission_type_id} C{self.college_id} '
            f'B{self.branch_id} Y{self.academic_year_id} R{self.cap_round_id} '
            f'{self.category}>'
        )

    def to_dict(self):
        return {
            'id': self.id,
            'admission_type_id': self.admission_type_id,
            'admission_type': self.admission_type_rel.code if self.admission_type_rel else None,
            'college_id': self.college_id,
            'college_code': self.college_rel.college_code if self.college_rel else None,
            'college_name': self.college_rel.college_name if self.college_rel else None,
            'branch_id': self.branch_id,
            'branch_code': self.branch_rel.branch_code if self.branch_rel else None,
            'branch_name': self.branch_rel.branch_name if self.branch_rel else None,
            'academic_year_id': self.academic_year_id,
            'academic_year': self.academic_year_rel.academic_year if self.academic_year_rel else None,
            'cap_round_id': self.cap_round_id,
            'cap_round': self.cap_round_rel.name if self.cap_round_rel else None,
            'category': self.category,
            'seat_type': self.seat_type,
            'gender': self.gender,
            'minority_status': self.minority_status,
            'cutoff_percentile': float(self.cutoff_percentile) if self.cutoff_percentile else None,
            'cutoff_rank': self.cutoff_rank,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class UploadJob(db.Model):
    """Tracks PDF upload and import jobs.

    Status lifecycle: PENDING → PROCESSING → COMPLETED | PARTIALLY_COMPLETED | FAILED
    """
    __tablename__ = 'upload_jobs'
    __table_args__ = (
        Index('idx_upload_status', 'status'),
        Index('idx_upload_created', 'created_at'),
        Index('idx_upload_hash', 'file_hash'),
        {'schema': 'public'},
    )

    id = Column(Integer, primary_key=True)
    filename = Column(String(500), nullable=False)
    stored_path = Column(String(1000), nullable=True)
    file_hash = Column(String(64), nullable=True, index=True)  # SHA-256 for duplicate detection
    file_size = Column(Integer, nullable=True)

    admission_type_id = Column(Integer, ForeignKey('public.admission_types.id'), nullable=False, index=True)
    academic_year_id = Column(Integer, ForeignKey('public.academic_years.id'), nullable=False, index=True)
    cap_round_id = Column(Integer, ForeignKey('public.cap_rounds.id'), nullable=False, index=True)

    status = Column(String(30), default='PENDING', nullable=False, index=True)

    total_rows = Column(Integer, default=0)
    valid_rows = Column(Integer, default=0)
    invalid_rows = Column(Integer, default=0)
    duplicate_rows = Column(Integer, default=0)
    error_rows = Column(JSON, nullable=True)  # Stores rejected rows with reasons

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    # Progress monitoring columns (added idempotently via ensure_schema on startup)
    progress_percentage = Column(Integer, default=0)
    current_step = Column(String(40), default='UPLOAD_FILE')
    total_pages = Column(Integer, default=0)
    processed_pages = Column(Integer, default=0)
    total_rows_extracted = Column(Integer, default=0)
    total_rows_imported = Column(Integer, default=0)
    failed_rows = Column(Integer, default=0)
    auto_created_colleges = Column(Integer, default=0)
    auto_created_branches = Column(Integer, default=0)
    accuracy_percentage = Column(Integer, default=0)

    uploaded_by = Column(Integer, ForeignKey('public.users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    admission_type_rel = relationship("AdmissionType", back_populates="upload_jobs")
    academic_year_rel = relationship("AcademicYear", back_populates="upload_jobs")
    cap_round_rel = relationship("CapRound", back_populates="upload_jobs")
    uploader = relationship("User", foreign_keys=[uploaded_by])
    cutoffs = relationship("Cutoff", back_populates="upload_job_rel")

    def __repr__(self):
        return f'<UploadJob {self.id} {self.filename} [{self.status}]>'

    def to_dict(self):
        return {
            'id': self.id,
            'filename': self.filename,
            'file_size': self.file_size,
            'admission_type': self.admission_type_rel.code if self.admission_type_rel else None,
            'admission_type_id': self.admission_type_id,
            'academic_year': self.academic_year_rel.academic_year if self.academic_year_rel else None,
            'academic_year_id': self.academic_year_id,
            'cap_round': self.cap_round_rel.name if self.cap_round_rel else None,
            'cap_round_id': self.cap_round_id,
            'status': self.status,
            'total_rows': self.total_rows,
            'valid_rows': self.valid_rows,
            'invalid_rows': self.invalid_rows,
            'duplicate_rows': self.duplicate_rows,
            'error_rows': self.error_rows,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error_message': self.error_message,
            'progress_percentage': self.progress_percentage,
            'current_step': self.current_step,
            'total_pages': self.total_pages,
            'processed_pages': self.processed_pages,
            'total_rows_extracted': self.total_rows_extracted,
            'total_rows_imported': self.total_rows_imported,
            'failed_rows': self.failed_rows,
            'auto_created_colleges': self.auto_created_colleges,
            'auto_created_branches': self.auto_created_branches,
            'accuracy_percentage': self.accuracy_percentage,
            'uploaded_by': self.uploaded_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# COLLEGE ADMISSION TYPE ASSOCIATION (NEW)
# ═══════════════════════════════════════════════════════════════════════════════


class CollegeAdmissionType(db.Model):
    """Maps colleges to admission types (ENGG, DSE, POLY)."""
    __tablename__ = 'college_admission_types'
    __table_args__ = (
        UniqueConstraint('college_id', 'admission_type_id', name='uq_college_adm_type'),
        Index('idx_cat_college', 'college_id'),
        Index('idx_cat_admission_type', 'admission_type_id'),
        {'schema': 'public'},
    )

    id = Column(Integer, primary_key=True)
    college_id = Column(Integer, ForeignKey('public.colleges.id', ondelete='CASCADE'), nullable=False, index=True)
    admission_type_id = Column(Integer, ForeignKey('public.admission_types.id', ondelete='CASCADE'), nullable=False, index=True)

    college = relationship("College", backref="admission_type_links")
    admission_type = relationship("AdmissionType")

    def __repr__(self):
        return f'<CollegeAdmissionType college#{self.college_id} type#{self.admission_type_id}>'


class Category(db.Model):
    """Cutoff categories like OPEN, OBC, SC, ST, EWS, TFWS, etc."""
    __tablename__ = 'categories'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(String(300), nullable=True)
    status = Column(String(20), default='active', nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Category {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# RETAINED MODELS (minimal changes)
# ═══════════════════════════════════════════════════════════════════════════════


class User(db.Model):
    """User model — stores user accounts with custom auth."""
    __tablename__ = 'users'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    email = Column(String(200), unique=True, nullable=False, index=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    password_hash = Column(String(255), nullable=True)
    profile_image_url = Column(Text, nullable=True)
    role = Column(String(20), default='user', nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    verification_code = Column(String(6), nullable=True)
    verification_code_expiry = Column(DateTime, nullable=True)
    reset_token = Column(String(100), nullable=True)
    reset_token_expiry = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login = Column(DateTime, default=datetime.utcnow, nullable=True)

    def is_admin(self):
        return self.role == 'admin' and self.is_verified

    def has_role(self, *roles):
        """Check if user has one of the specified roles."""
        return self.role in roles and self.is_verified

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


class BackupHistory(db.Model):
    """Database backup metadata."""
    __tablename__ = 'backup_history'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    backup_date = Column(DateTime, default=datetime.utcnow)
    backup_file = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)
    db_type = Column(String(20), nullable=True)
    record_count = Column(Integer, nullable=True)
    status = Column(String(20), default='success')
    created_by = Column(Integer, ForeignKey('public.users.id'), nullable=True)
    notes = Column(Text, nullable=True)

    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f'<BackupHistory {self.backup_date} ({self.status})>'


class AuditLog(db.Model):
    """Audit trail for all admin actions."""
    __tablename__ = 'audit_logs'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('public.users.id'), nullable=True, index=True)
    action = Column(String(50), nullable=False)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(Integer, nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f'<AuditLog {self.action} on {self.resource_type} at {self.created_at}>'


class LoginHistory(db.Model):
    """Tracks login attempts for security auditing."""
    __tablename__ = 'login_history'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('public.users.id'), nullable=True, index=True)
    email = Column(String(200), nullable=False, index=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    success = Column(Boolean, default=False, nullable=False)
    failure_reason = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f'<LoginHistory {self.email} {"✅" if self.success else "❌"} at {self.created_at}>'


# ═══════════════════════════════════════════════════════════════════════════════
# DEPRECATED – KEPT ONLY FOR MIGRATION COMPATIBILITY
# Will be removed after migrate_admin_v3.py runs successfully.
# ═══════════════════════════════════════════════════════════════════════════════


class CollegeCutoff(db.Model):
    """DEPRECATED — kept for migration. Use Cutoff (cutoffs table) instead."""
    __tablename__ = 'college_cutoffs'
    __table_args__ = (
        UniqueConstraint('year', 'round', 'college_code', 'course_code', 'category',
                         name='uq_college_cutoff_unique'),
        Index('idx_cc_year', 'year'),
        Index('idx_cc_round', 'round'),
        Index('idx_cc_college_name', 'college_name'),
        Index('idx_cc_course_name', 'course_name'),
        Index('idx_cc_category', 'category'),
        Index('idx_cc_percentile', 'percentile'),
        Index('idx_cc_college_code', 'college_code'),
        Index('idx_cc_approval', 'approval_status'),
        Index('idx_cc_course_code', 'course_code'),
        Index('idx_cc_gender', 'gender'),
        Index('idx_cc_exam_type', 'exam_type'),
        Index('idx_cc_year_round_code', 'year', 'round', 'college_code'),
        Index('idx_cc_code_course', 'college_code', 'course_code'),
        Index('idx_cc_year_code', 'year', 'college_code'),
        {'schema': 'public'},
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
    source_file_id = Column(Integer, ForeignKey('public.uploaded_files.id'), nullable=True, index=True)
    imported_at = Column(DateTime, default=datetime.utcnow)

    gender = Column(String(10), default='Gender-Neutral', nullable=False)
    opening_rank = Column(Integer, nullable=True)
    closing_rank = Column(Integer, nullable=True)
    seats_available = Column(Integer, nullable=True)
    branch = Column(String(200), nullable=True)
    exam_type = Column(String(20), default='MHT-CET', nullable=False)

    approval_status = Column(String(20), default='pending_approval', nullable=False, index=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(Integer, ForeignKey('public.users.id'), nullable=True)

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
            'gender': self.gender,
            'opening_rank': self.opening_rank,
            'closing_rank': self.closing_rank,
            'seats_available': self.seats_available,
            'branch': self.branch,
            'exam_type': self.exam_type,
            'approval_status': self.approval_status,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'approved_by': self.approved_by,
            'imported_at': self.imported_at.isoformat() if self.imported_at else None,
        }


class CAPCutoff(db.Model):
    """DEPRECATED — kept for migration compatibility."""
    __tablename__ = 'cap_cutoffs'
    __table_args__ = (
        Index('idx_cutoff_year', 'year'),
        Index('idx_cutoff_college_code', 'college_code'),
        Index('idx_cutoff_category', 'category'),
        {'schema': 'public'},
    )

    id = Column(Integer, primary_key=True)
    college_id = Column(Integer, ForeignKey('public.colleges.id'), nullable=False)
    college_code = Column(String(20), nullable=True, index=True)
    college_name = Column(String(200), nullable=True)
    year = Column(Integer, nullable=False, index=True)
    round_number = Column(Integer, nullable=False)
    branch = Column(String(100), nullable=True)
    category = Column(String(20), nullable=False)
    gender = Column(String(10), nullable=False)
    cutoff_percentile = Column(Float, nullable=False)
    opening_rank = Column(Integer, nullable=True)
    closing_rank = Column(Integer, nullable=True)
    seats_available = Column(Integer, nullable=True)
    source_file_id = Column(Integer, ForeignKey('public.uploaded_files.id'), nullable=True, index=True)
    is_auto_generated = Column(Boolean, default=False)
    validation_status = Column(String(20), default='validated')
    raw_pdf_text = Column(Text, nullable=True)
    imported_at = Column(DateTime, default=datetime.utcnow)

    college = relationship("College", foreign_keys=[college_id], backref="cap_cutoffs")
    source_file = relationship("UploadedFile", back_populates="cutoff_records")

    def __repr__(self):
        return f'<CAPCutoff {self.college_code} - {self.year} R{self.round_number} {self.category}>'


class UploadedFile(db.Model):
    """DEPRECATED — kept for migration compatibility. Use UploadJob instead."""
    __tablename__ = 'uploaded_files'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)
    mime_type = Column(String(50), nullable=True)
    year = Column(Integer, nullable=True, index=True)
    round_number = Column(Integer, nullable=True)
    processed_status = Column(String(20), default='pending')
    total_rows = Column(Integer, default=0)
    valid_rows = Column(Integer, default=0)
    rejected_rows = Column(Integer, default=0)
    duplicate_rows = Column(Integer, default=0)
    preview_data = Column(JSON, nullable=True)
    validation_report = Column(JSON, nullable=True)
    extraction_method = Column(String(20), default='pdfplumber')
    extraction_confidence = Column(Float, nullable=True)
    uploaded_by = Column(Integer, ForeignKey('public.users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    committed_at = Column(DateTime, nullable=True)

    uploader = relationship("User", foreign_keys=[uploaded_by])
    cutoff_records = relationship("CAPCutoff", back_populates="source_file",
                                  foreign_keys='CAPCutoff.source_file_id')
    cutoff_records_new = relationship("CollegeCutoff", back_populates="source_file",
                                      foreign_keys='CollegeCutoff.source_file_id')
    import_jobs = relationship("ImportJob", back_populates="file", lazy="dynamic")

    def __repr__(self):
        return f'<UploadedFile {self.filename} ({self.processed_status})>'


class ImportJob(db.Model):
    """DEPRECATED — kept for migration compatibility. Use UploadJob instead."""
    __tablename__ = 'import_jobs'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey('public.uploaded_files.id'), nullable=False, index=True)
    status = Column(String(20), default='PENDING', nullable=False, index=True)
    approval_status = Column(String(20), nullable=True, index=True)
    approved_by = Column(Integer, ForeignKey('public.users.id'), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    uploaded_by = Column(Integer, ForeignKey('public.users.id'), nullable=True)
    total_pages = Column(Integer, default=0)
    processed_pages = Column(Integer, default=0)
    checkpoint_page = Column(Integer, default=0)
    rows_extracted = Column(Integer, default=0)
    rows_imported = Column(Integer, default=0)
    rows_failed = Column(Integer, default=0)
    failed_pages = Column(JSON, default=list)
    error_log = Column(JSON, default=list)
    page_range_start = Column(Integer, default=1)
    page_range_end = Column(Integer, nullable=True)
    memory_usage_mb = Column(Float, nullable=True)
    extraction_method = Column(String(20), default='pdfplumber')
    confidence_score = Column(Float, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    file = relationship("UploadedFile", back_populates="import_jobs")
    approver = relationship("User", foreign_keys=[approved_by])
    uploaded_by_user = relationship("User", foreign_keys=[uploaded_by])

    def __repr__(self):
        return f'<ImportJob {self.id} for file#{self.file_id} [{self.status}]>'


class CollegeTrend(db.Model):
    """DEPRECATED — kept for migration."""
    __tablename__ = 'college_trends'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    college_code = Column(String(20), nullable=False, index=True)
    college_name = Column(String(200), nullable=True)
    branch = Column(String(100), nullable=True)
    category = Column(String(20), nullable=True)
    trend_data = Column(JSON, nullable=True)
    direction = Column(String(20), nullable=True)
    difference = Column(Float, nullable=True)
    computed_at = Column(DateTime, default=datetime.utcnow)


class ApprovalRequest(db.Model):
    """DEPRECATED — kept for migration."""
    __tablename__ = 'approval_requests'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, index=True)
    email = Column(String(200), nullable=False, index=True)
    request_type = Column(String(100), nullable=False, index=True)
    submitted_date = Column(DateTime, default=datetime.utcnow, index=True)
    status = Column(String(20), default='PENDING', nullable=False, index=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(Integer, ForeignKey('public.users.id'), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejected_by = Column(Integer, ForeignKey('public.users.id'), nullable=True)
    notes = Column(Text, nullable=True)
    data_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    approver = relationship("User", foreign_keys=[approved_by])
    rejecter = relationship("User", foreign_keys=[rejected_by])


class BulkActionBackup(db.Model):
    """DEPRECATED — kept for migration."""
    __tablename__ = 'bulk_action_backups'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    action_type = Column(String(50), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey('public.users.id'), nullable=False, index=True)
    affected_count = Column(Integer, default=0)
    snapshot_data = Column(JSON, nullable=False)
    status_filter = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    admin = relationship("User", foreign_keys=[admin_id])


class ImportErrorRecord(db.Model):
    """DEPRECATED — kept for migration."""
    __tablename__ = 'import_error_records'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('public.import_jobs.id'), nullable=False, index=True)
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


class ManualCutoffEntry(db.Model):
    """DEPRECATED — kept for migration."""
    __tablename__ = 'manual_cutoff_entries'
    __table_args__ = {'schema': 'public'}

    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False, index=True)
    round = Column(Integer, nullable=False)
    exam_type = Column(String(20), nullable=False, default='MHT-CET')
    college_code = Column(String(20), nullable=False)
    college_name = Column(String(300), nullable=False)
    course_code = Column(String(20), nullable=False)
    course_name = Column(String(200), nullable=False)
    category = Column(String(20), nullable=False)
    rank = Column(Integer, nullable=True)
    percentile = Column(Float, nullable=True)
    gender = Column(String(10), default='Gender-Neutral')
    opening_rank = Column(Integer, nullable=True)
    closing_rank = Column(Integer, nullable=True)
    seats_available = Column(Integer, nullable=True)
    branch = Column(String(200), nullable=True)
    entered_by = Column(Integer, ForeignKey('public.users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    enterer = relationship("User", foreign_keys=[entered_by])


class MHCETStudent(db.Model):
    """DEPRECATED — kept for migration."""
    __tablename__ = 'mhcet_students'
    __table_args__ = {'schema': 'public'}

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