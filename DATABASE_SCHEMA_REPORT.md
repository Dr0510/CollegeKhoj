# Database Schema Report — CollegeKhoj

> **Target:** Brand-new (empty) Neon PostgreSQL database  
> **Source:** `models.py` — SQLAlchemy ORM models  
> **Tables detected:** 13

---

## All Tables

| # | Table Name | Model Class | Login Required? | Row Count Needed | Notes |
|---|------------|-------------|-----------------|------------------|-------|
| 1 | `users` | `User` | **YES** | ≥ 1 (admin) | Stores email, password_hash, role, is_verified. Central auth table. No FK dependencies. |
| 2 | `colleges` | `College` | No (but UI breaks without it) | ≥ 12 (sample) | No FK dependencies. Used by recommender engine and all public pages. |
| 3 | `college_cutoffs` | `CollegeCutoff` | No | ≥ 1 per college | Main cutoff table. FKs to `uploaded_files.id` (nullable) and `users.id` (nullable). |
| 4 | `cap_cutoffs` | `CAPCutoff` | No | 0 | DEPRECATED. FK `college_id` → `colleges.id` is **NOT NULL**. Empty at startup is fine. |
| 5 | `mhcet_students` | `MHCETStudent` | No | 0 | Standalone table. No FKs. |
| 6 | `uploaded_files` | `UploadedFile` | No | 0 | FK `uploaded_by` → `users.id` (nullable). |
| 7 | `import_jobs` | `ImportJob` | No | 0 | FK `file_id` → `uploaded_files.id` (**NOT NULL**). FK `approved_by`/`uploaded_by` → `users.id` (nullable). |
| 8 | `import_error_records` | `ImportErrorRecord` | No | 0 | FK `job_id` → `import_jobs.id` (**NOT NULL**). |
| 9 | `college_trends` | `CollegeTrend` | No | 0 | Standalone table. No FKs. |
| 10 | `backup_history` | `BackupHistory` | No | 0 | FK `created_by` → `users.id` (nullable). |
| 11 | `audit_logs` | `AuditLog` | No | 0 | FK `user_id` → `users.id` (nullable). |
| 12 | `approval_requests` | `ApprovalRequest` | No | 0 | FK `approved_by`/`rejected_by` → `users.id` (nullable). |
| 13 | `bulk_action_backups` | `BulkActionBackup` | No | 0 | FK `admin_id` → `users.id` (**NOT NULL**). |

---

## Indexes

### `college_cutoffs` — 12 indexes
| Index Name | Columns | Type |
|------------|---------|------|
| `uq_cutoff_unique` | `(year, round, college_code, course_code, category)` | UniqueConstraint |
| `idx_cc_year` | `year` | Single |
| `idx_cc_round` | `round` | Single |
| `idx_cc_college_name` | `college_name` | Single |
| `idx_cc_course_name` | `course_name` | Single |
| `idx_cc_category` | `category` | Single |
| `idx_cc_percentile` | `percentile` | Single |
| `idx_cc_college_code` | `college_code` | Single |
| `idx_cc_approval` | `approval_status` | Single |
| `idx_cc_course_code` | `course_code` | Single |
| `idx_cc_gender` | `gender` | Single |
| `idx_cc_exam_type` | `exam_type` | Single |
| `idx_cc_year_round_code` | `(year, round, college_code)` | Composite |
| `idx_cc_code_course` | `(college_code, course_code)` | Composite |
| `idx_cc_year_code` | `(year, college_code)` | Composite |

### `cap_cutoffs` — 3 indexes
| Index Name | Columns |
|------------|---------|
| `idx_cutoff_year` | `year` |
| `idx_cutoff_college_code` | `college_code` |
| `idx_cutoff_category` | `category` |

### Other tables — indexes on PK + FK/query columns only

---

## Foreign Key Dependency Graph

```
users ────────────────────────── (no deps)
colleges ─────────────────────── (no deps)
mhcet_students ───────────────── (no deps)
college_trends ───────────────── (no deps)

uploaded_files ─── FK(uploaded_by) ─→ users.id (nullable)

backup_history ─── FK(created_by) ──→ users.id (nullable)
audit_logs ─────── FK(user_id) ─────→ users.id (nullable)
bulk_action_backups ─ FK(admin_id) ─→ users.id (NOT NULL)

college_cutoffs ── FK(source_file_id) ─→ uploaded_files.id (nullable)
                ── FK(approved_by) ────→ users.id (nullable)

cap_cutoffs ────── FK(college_id) ────→ colleges.id (NOT NULL)
                ── FK(source_file_id) ─→ uploaded_files.id (nullable)

import_jobs ────── FK(file_id) ───────→ uploaded_files.id (NOT NULL)
                ── FK(approved_by) ───→ users.id (nullable)
                ── FK(uploaded_by) ────→ users.id (nullable)

approval_requests ─ FK(approved_by) ──→ users.id (nullable)
                  ─ FK(rejected_by) ──→ users.id (nullable)

import_error_records ─ FK(job_id) ────→ import_jobs.id (NOT NULL)
```

---

## Constraint Summary

| Table | Constraint Type | Columns | Description |
|-------|----------------|---------|-------------|
| `users` | Unique | `email` | Prevents duplicate accounts |
| `college_cutoffs` | Unique | `(year, round, college_code, course_code, category)` | Prevents duplicate cutoff imports |
| `cap_cutoffs` | FK (NOT NULL) | `college_id` → `colleges.id` | Every cutoff must reference a college |
| `import_jobs` | FK (NOT NULL) | `file_id` → `uploaded_files.id` | Every job must reference a file |
| `import_error_records` | FK (NOT NULL) | `job_id` → `import_jobs.id` | Every error must reference a job |
| `bulk_action_backups` | FK (NOT NULL) | `admin_id` → `users.id` | Every backup must reference an admin |

---

## Table Schemas (DDL)

### `users`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK, auto-increment |
| `email` | `VARCHAR(200)` | NOT NULL, UNIQUE, indexed |
| `first_name` | `VARCHAR(100)` | nullable |
| `last_name` | `VARCHAR(100)` | nullable |
| `password_hash` | `VARCHAR(255)` | nullable |
| `profile_image_url` | `TEXT` | nullable |
| `role` | `VARCHAR(20)` | NOT NULL, default `'user'` |
| `is_verified` | `BOOLEAN` | NOT NULL, default `false` |
| `verification_code` | `VARCHAR(6)` | nullable |
| `verification_code_expiry` | `DATETIME` | nullable |
| `reset_token` | `VARCHAR(100)` | nullable |
| `reset_token_expiry` | `DATETIME` | nullable |
| `created_at` | `DATETIME` | NOT NULL, default `now()` |
| `last_login` | `DATETIME` | nullable |

### `colleges`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK, auto-increment |
| `college` | `VARCHAR(200)` | NOT NULL |
| `location` | `VARCHAR(100)` | NOT NULL |
| `branch` | `VARCHAR(100)` | NOT NULL |
| `fees` | `FLOAT` | NOT NULL |
| `placement_rate` | `FLOAT` | NOT NULL |
| `nirf_rank` | `INTEGER` | NOT NULL |
| `rating` | `FLOAT` | NOT NULL |

### `college_cutoffs`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK, auto-increment |
| `year` | `INTEGER` | NOT NULL |
| `round` | `INTEGER` | NOT NULL |
| `college_code` | `VARCHAR(20)` | NOT NULL |
| `college_name` | `TEXT` | NOT NULL |
| `course_code` | `VARCHAR(20)` | NOT NULL |
| `course_name` | `TEXT` | NOT NULL |
| `category` | `VARCHAR(20)` | NOT NULL |
| `rank` | `INTEGER` | nullable |
| `percentile` | `NUMERIC(5,2)` | nullable |
| `source_file_id` | `INTEGER` | nullable, FK → `uploaded_files.id` |
| `imported_at` | `DATETIME` | default `now()` |
| `gender` | `VARCHAR(10)` | NOT NULL, default `'Gender-Neutral'` |
| `opening_rank` | `INTEGER` | nullable |
| `closing_rank` | `INTEGER` | nullable |
| `seats_available` | `INTEGER` | nullable |
| `branch` | `VARCHAR(200)` | nullable |
| `exam_type` | `VARCHAR(20)` | NOT NULL, default `'MHT-CET'` |
| `approval_status` | `VARCHAR(20)` | NOT NULL, default `'pending_approval'`, indexed |
| `approved_at` | `DATETIME` | nullable |
| `approved_by` | `INTEGER` | nullable, FK → `users.id` |

### `cap_cutoffs` (DEPRECATED)
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK, auto-increment |
| `college_id` | `INTEGER` | **NOT NULL**, FK → `colleges.id` |
| `college_code` | `VARCHAR(20)` | nullable, indexed |
| `college_name` | `VARCHAR(200)` | nullable |
| `year` | `INTEGER` | NOT NULL, indexed |
| `round_number` | `INTEGER` | NOT NULL |
| `branch` | `VARCHAR(100)` | nullable |
| `category` | `VARCHAR(20)` | NOT NULL |
| `gender` | `VARCHAR(10)` | NOT NULL |
| `cutoff_percentile` | `FLOAT` | NOT NULL |
| `opening_rank` | `INTEGER` | nullable |
| `closing_rank` | `INTEGER` | nullable |
| `seats_available` | `INTEGER` | nullable |
| `source_file_id` | `INTEGER` | nullable, indexed, FK → `uploaded_files.id` |
| `is_auto_generated` | `BOOLEAN` | default `false` |
| `validation_status` | `VARCHAR(20)` | default `'validated'` |
| `raw_pdf_text` | `TEXT` | nullable |
| `imported_at` | `DATETIME` | default `now()` |

### `mhcet_students`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK |
| `name` | `VARCHAR(100)` | NOT NULL |
| `mhcet_score` | `FLOAT` | NOT NULL |
| `percentile` | `FLOAT` | NOT NULL |
| `category` | `VARCHAR(20)` | NOT NULL |
| `gender` | `VARCHAR(10)` | NOT NULL |
| `domicile` | `VARCHAR(50)` | NOT NULL |
| `budget_max` | `FLOAT` | nullable |
| `preferred_locations` | `TEXT` | nullable |
| `preferred_branches` | `TEXT` | nullable |

### `uploaded_files`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK |
| `filename` | `VARCHAR(255)` | NOT NULL |
| `stored_path` | `VARCHAR(500)` | NOT NULL |
| `file_size` | `INTEGER` | nullable |
| `mime_type` | `VARCHAR(50)` | nullable |
| `year` | `INTEGER` | nullable, indexed |
| `round_number` | `INTEGER` | nullable |
| `processed_status` | `VARCHAR(20)` | default `'pending'` |
| `total_rows` | `INTEGER` | default `0` |
| `valid_rows` | `INTEGER` | default `0` |
| `rejected_rows` | `INTEGER` | default `0` |
| `duplicate_rows` | `INTEGER` | default `0` |
| `preview_data` | `JSON` | nullable |
| `validation_report` | `JSON` | nullable |
| `extraction_method` | `VARCHAR(20)` | default `'pdfplumber'` |
| `extraction_confidence` | `FLOAT` | nullable |
| `uploaded_by` | `INTEGER` | nullable, FK → `users.id` |
| `created_at` | `DATETIME` | default `now()` |
| `committed_at` | `DATETIME` | nullable |

### `import_jobs`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK |
| `file_id` | `INTEGER` | **NOT NULL**, FK → `uploaded_files.id`, indexed |
| `status` | `VARCHAR(20)` | NOT NULL, default `'PENDING'`, indexed |
| `approval_status` | `VARCHAR(20)` | nullable, indexed |
| `approved_by` | `INTEGER` | nullable, FK → `users.id` |
| `approved_at` | `DATETIME` | nullable |
| `rejection_reason` | `TEXT` | nullable |
| `uploaded_by` | `INTEGER` | nullable, FK → `users.id` |
| `total_pages` | `INTEGER` | default `0` |
| `processed_pages` | `INTEGER` | default `0` |
| `checkpoint_page` | `INTEGER` | default `0` |
| `rows_extracted` | `INTEGER` | default `0` |
| `rows_imported` | `INTEGER` | default `0` |
| `rows_failed` | `INTEGER` | default `0` |
| `failed_pages` | `JSON` | default `[]` |
| `error_log` | `JSON` | default `[]` |
| `page_range_start` | `INTEGER` | default `1` |
| `page_range_end` | `INTEGER` | nullable |
| `memory_usage_mb` | `FLOAT` | nullable |
| `extraction_method` | `VARCHAR(20)` | default `'pdfplumber'` |
| `confidence_score` | `FLOAT` | nullable |
| `started_at` | `DATETIME` | nullable |
| `completed_at` | `DATETIME` | nullable |
| `error_message` | `TEXT` | nullable |

### `college_trends`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK |
| `college_code` | `VARCHAR(20)` | NOT NULL, indexed |
| `college_name` | `VARCHAR(200)` | nullable |
| `branch` | `VARCHAR(100)` | nullable |
| `category` | `VARCHAR(20)` | nullable |
| `trend_data` | `JSON` | nullable |
| `direction` | `VARCHAR(20)` | nullable |
| `difference` | `FLOAT` | nullable |
| `computed_at` | `DATETIME` | default `now()` |

### `backup_history`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK |
| `backup_date` | `DATETIME` | default `now()` |
| `backup_file` | `VARCHAR(500)` | NOT NULL |
| `file_size` | `INTEGER` | nullable |
| `db_type` | `VARCHAR(20)` | nullable |
| `record_count` | `INTEGER` | nullable |
| `status` | `VARCHAR(20)` | default `'success'` |
| `created_by` | `INTEGER` | nullable, FK → `users.id` |
| `notes` | `TEXT` | nullable |

### `audit_logs`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK |
| `user_id` | `INTEGER` | nullable, FK → `users.id`, indexed |
| `action` | `VARCHAR(50)` | NOT NULL |
| `resource_type` | `VARCHAR(50)` | nullable |
| `resource_id` | `INTEGER` | nullable |
| `details` | `JSON` | nullable |
| `ip_address` | `VARCHAR(45)` | nullable |
| `user_agent` | `VARCHAR(255)` | nullable |
| `created_at` | `DATETIME` | NOT NULL, indexed, default `now()` |

### `approval_requests`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK |
| `name` | `VARCHAR(200)` | NOT NULL, indexed |
| `email` | `VARCHAR(200)` | NOT NULL, indexed |
| `request_type` | `VARCHAR(100)` | NOT NULL, indexed |
| `submitted_date` | `DATETIME` | default `now()`, indexed |
| `status` | `VARCHAR(20)` | NOT NULL, default `'PENDING'`, indexed |
| `approved_at` | `DATETIME` | nullable |
| `approved_by` | `INTEGER` | nullable, FK → `users.id` |
| `rejected_at` | `DATETIME` | nullable |
| `rejected_by` | `INTEGER` | nullable, FK → `users.id` |
| `notes` | `TEXT` | nullable |
| `data_snapshot` | `JSON` | nullable |
| `created_at` | `DATETIME` | default `now()` |
| `updated_at` | `DATETIME` | default `now()`, auto-updates |

### `bulk_action_backups`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK |
| `action_type` | `VARCHAR(50)` | NOT NULL, indexed |
| `admin_id` | `INTEGER` | **NOT NULL**, FK → `users.id`, indexed |
| `affected_count` | `INTEGER` | default `0` |
| `snapshot_data` | `JSON` | NOT NULL |
| `status_filter` | `VARCHAR(20)` | nullable |
| `created_at` | `DATETIME` | default `now()`, indexed |

### `import_error_records`
| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `INTEGER` | PK |
| `job_id` | `INTEGER` | **NOT NULL**, FK → `import_jobs.id`, indexed |
| `page_number` | `INTEGER` | nullable |
| `college_code` | `VARCHAR(20)` | nullable |
| `college_name` | `TEXT` | nullable |
| `course_code` | `VARCHAR(20)` | nullable |
| `course_name` | `TEXT` | nullable |
| `category` | `VARCHAR(20)` | nullable |
| `rank` | `INTEGER` | nullable |
| `percentile` | `FLOAT` | nullable |
| `error_reason` | `TEXT` | NOT NULL |
| `raw_text_snippet` | `TEXT` | nullable |
| `created_at` | `DATETIME` | default `now()` |