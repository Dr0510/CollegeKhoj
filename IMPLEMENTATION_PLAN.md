# Implementation Plan: Core Platform Fixes

## Summary

This implementation unifies the cutoff data architecture (eliminating the dual `cap_cutoffs`/`college_cutoffs` split), fixes the recommendation engine to use imported data, optimizes bulk imports, adds indexes, implements college master upload, and adds polytechnic support — using only the existing `college_cutoffs` table as the single source of truth.

---

## Files Modified

| # | File | Task | Change Description |
|---|------|------|--------------------|
| 1 | `models.py` | T1, T2, T4, T6 | Added `gender`, `opening_rank`, `closing_rank`, `seats_available`, `branch`, `exam_type` columns to `CollegeCutoff`. Added composite indexes `idx_cc_year_round_code`, `idx_cc_code_course`, `idx_cc_year_code` and single-column indexes `idx_cc_course_code`, `idx_cc_gender`, `idx_cc_exam_type`. Marked `CAPCutoff` model as DEPRECATED. |
| 2 | `mhcet_recommender.py` | T1, T2, T6 | Changed `get_mhcet_recommendations()` to query `CollegeCutoff` instead of `CAPCutoff`. Queries by `college_code`, `category`, `gender`, filters by `exam_type='MHT-CET'`. Added fallback name-based matching for colleges without codes. Added branch-wise breakdown in admission data. |
| 3 | `admin/trend_service.py` | T2 | Changed all queries from `CAPCutoff` to `CollegeCutoff`. Adapted field names: `cutoff_percentile` → `percentile`, `branch` → `course_name`, `round_number` → `round`, `college_id` → `college_code` join. |
| 4 | `admin/validation_service.py` | T2 | Changed DB queries from `CAPCutoff` to `CollegeCutoff`. Added `exam_type` validation. Updated duplicate key detection to include `gender`. |
| 5 | `admin/routes.py` | T2, T5 | Added college master upload routes: `GET/POST /admin/college-upload` (parse + preview) and `POST /admin/college-upload/commit` (upsert). Dashboard still shows legacy `CAPCutoff` count but primary stats come from `CollegeCutoff`. |
| 6 | `admin/bulk_import_engine.py` | T3 | Optimized `_flush_buffer()` to use `bulk_insert_mappings` pattern. Added `CollegeCutoff` model import for batch operations. Reduced per-row overhead with chunked insert strategy. |
| 7 | `app.py` | T2 | Updated `init_sample_cutoff_data()` to write to `CollegeCutoff` (not `CAPCutoff`). Updated `college_profile()` route to query `CollegeCutoff`. |
| 8 | `admin/college_upload_service.py` | T5 | **NEW FILE.** Parses CSV/Excel, validates each row against `College` model columns, detects create vs update, and commits via upsert logic. Supports flexible column naming (e.g., "College Name" → "Name"). |
| 9 | `templates/admin/college_upload.html` | T5 | **NEW FILE.** Admin page with drag-drop upload zone, preview table with color-coded status (Create/Update/Error), and commit workflow. |
| 10 | `migrate_unified_cutoffs.py` | T2 | **NEW FILE.** Migration script that adds columns/indexes, migrates data from `cap_cutoffs` → `college_cutoffs`, and verifies results. |

---

## Migrations Required

All migrations are PostgreSQL (Neon) compatible and idempotent (safe to run multiple times).

### Migration 1: Schema Changes (6 columns + 6 indexes)

```sql
-- Columns
ALTER TABLE college_cutoffs ADD COLUMN IF NOT EXISTS gender VARCHAR(10) DEFAULT 'Gender-Neutral' NOT NULL;
ALTER TABLE college_cutoffs ADD COLUMN IF NOT EXISTS opening_rank INTEGER;
ALTER TABLE college_cutoffs ADD COLUMN IF NOT EXISTS closing_rank INTEGER;
ALTER TABLE college_cutoffs ADD COLUMN IF NOT EXISTS seats_available INTEGER;
ALTER TABLE college_cutoffs ADD COLUMN IF NOT EXISTS branch VARCHAR(200);
ALTER TABLE college_cutoffs ADD COLUMN IF NOT EXISTS exam_type VARCHAR(20) DEFAULT 'MHT-CET' NOT NULL;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_cc_course_code ON college_cutoffs(course_code);
CREATE INDEX IF NOT EXISTS idx_cc_gender ON college_cutoffs(gender);
CREATE INDEX IF NOT EXISTS idx_cc_exam_type ON college_cutoffs(exam_type);
CREATE INDEX IF NOT EXISTS idx_cc_year_round_code ON college_cutoffs(year, round, college_code);
CREATE INDEX IF NOT EXISTS idx_cc_code_course ON college_cutoffs(college_code, course_code);
CREATE INDEX IF NOT EXISTS idx_cc_year_code ON college_cutoffs(year, college_code);
```

### Migration 2: Data Migration (cap_cutoffs → college_cutoffs)

```python
# Executed by migrate_unified_cutoffs.py
# Batch inserts 500 records at a time via bulk_save_objects
# Maps: cutoff_percentile → percentile, round_number → round,
#       closing_rank → rank, branch → course_name & branch,
#       gender, opening_rank, closing_rank, seats_available preserved
```

---

## Estimated Impact

| Aspect | Impact | Details |
|--------|--------|---------|
| **Schema** | Medium | 6 new columns on existing table. No table drops. Backward compatible — `CAPCutoff` model retained as deprecated. |
| **Data** | Medium | Existing `cap_cutoffs` data (~thousands of rows) migrated to `college_cutoffs`. Zero data loss. |
| **Query Performance** | High | Composite indexes on common query patterns (`year+round+college_code`, `college_code+course_code`, `year+college_code`) will accelerate recommendations by 10-50x for large datasets. |
| **Import Performance** | High | Bulk insert optimization reduces time for 50k rows from ~minutes to ~seconds. |
| **API Compatibility** | Low | All user-facing routes use `College` model (unchanged). Only internal admin/trend/recommender code changed. |
| **Rollback Risk** | Low | `CAPCutoff` table and model preserved. Can revert to old code without data loss. |

---

## Implementation Order

```
1. Run migration script        → migrate_unified_cutoffs.py
2. Deploy models.py            → new columns + indexes active
3. Deploy mhcet_recommender.py → recommender reads from college_cutoffs
4. Deploy trend_service.py     → trends read from college_cutoffs
5. Deploy validation_service.py→ validator works with unified schema
6. Deploy bulk_import_engine.py → optimized bulk inserts
7. Deploy app.py                → seed data + profile use college_cutoffs
8. Deploy college upload        → admin_college_upload_service.py + template + routes
```

Each step is independently deployable and backward-compatible.

---

## Verification Checklist

- [ ] `db.create_all()` completes without errors
- [ ] Migration script reports success for schema + data
- [ ] CollegeCutoff has all 6 new columns with correct types
- [ ] All 9 indexes exist on college_cutoffs table
- [ ] cap_cutoffs data is visible in college_cutoffs after migration
- [ ] `/mhcet/recommend` returns recommendations from imported data
- [ ] Admin trend page shows data from college_cutoffs
- [ ] Bulk PDF import inserts >1000 rows in under 10 seconds
- [ ] College upload: CSV preview shows create/update/error badges
- [ ] College upload commit creates new College records and updates existing
- [ ] `exam_type='MHT-CET'` used for engineering, `'POLYTECHNIC'` for diploma