# Table Creation Order — CollegeKhoj

> **Target:** Brand-new (empty) Neon PostgreSQL database  
> **Source:** `models.py` — SQLAlchemy ORM models with FK dependencies

---

## Required Creation Order

Tables must be created in dependency order to respect foreign key constraints.  
SQLAlchemy's `db.create_all()` handles this automatically using its model metadata,  
but the logical order is documented here:

### PHASE 1 — No Dependencies
These tables have no foreign keys and can be created first:

```
 1. users
 2. colleges
 3. mhcet_students
 4. college_trends
```

### PHASE 2 — Depends on Phase 1 (users)
These tables reference `users.id` (all nullable except `bulk_action_backups`):

```
 5. uploaded_files            — FK(uploaded_by → users.id, nullable)
 6. backup_history            — FK(created_by → users.id, nullable)
 7. audit_logs                — FK(user_id → users.id, nullable)
 8. bulk_action_backups       — FK(admin_id → users.id, NOT NULL)
```

### PHASE 3 — Depends on Phase 1 + 2
These tables reference both `users.id` and `uploaded_files.id`:

```
 9.  college_cutoffs          — FK(source_file_id → uploaded_files.id, nullable)
                             — FK(approved_by → users.id, nullable)

10. cap_cutoffs               — FK(college_id → colleges.id, NOT NULL)
                             — FK(source_file_id → uploaded_files.id, nullable)

11. import_jobs               — FK(file_id → uploaded_files.id, NOT NULL)
                             — FK(approved_by → users.id, nullable)
                             — FK(uploaded_by → users.id, nullable)

12. approval_requests         — FK(approved_by → users.id, nullable)
                             — FK(rejected_by → users.id, nullable)
```

### PHASE 4 — Depends on Phase 3
This table references `import_jobs.id`:

```
13. import_error_records      — FK(job_id → import_jobs.id, NOT NULL)
```

---

## How to Create All Tables

### Method 1 — Run the application (recommended)

```bash
# Set environment variable for your Neon database
export NEON_DATABASE_URL="postgresql://neondb_owner:npg_0HRFQb5ucknA@ep-mute-fire-ao4cupq5-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"

# Run the app — startup code calls db.create_all()
python main.py
```

This triggers `app.py` lines 980-983:
```python
with app.app_context():
    db.create_all()
    logging.info("✅ Database tables created/verified on Neon PostgreSQL")
```

### Method 2 — Manual creation via Python shell

```bash
python -c "
from app import app
from database import db

with app.app_context():
    db.create_all()
    print('✅ All 13 tables created')
"
```

### Method 3 — SQLAlchemy inspect (verify creation)

```python
from app import app
from database import db
from sqlalchemy import inspect

with app.app_context():
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    for i, t in enumerate(tables, 1):
        print(f'{i:2d}. {t}')
    print(f'\nTotal: {len(tables)} tables')
```

---

## Verification Commands

### Verify via psql (all tables)

```bash
psql 'postgresql://neondb_owner:npg_0HRFQb5ucknA@ep-mute-fire-ao4cupq5-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require' \
  -c "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;"
```

**Expected output (13 tables):**
```
      table_name       
------------------------
 approval_requests
 audit_logs
 backup_history
 bulk_action_backups
 cap_cutoffs
 college_cutoffs
 college_trends
 colleges
 import_error_records
 import_jobs
 mhcet_students
 uploaded_files
 users
```

### Verify via psql (column details)

```bash
psql 'NEON_URL' -c "
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema='public'
ORDER BY table_name, ordinal_position;
"
```

---

## Notes

- `db.create_all()` is **idempotent** — running it multiple times will not recreate existing tables. It only creates tables that do not yet exist.
- If you need to recreate tables from scratch (reset), use:
  ```python
  db.drop_all()
  db.create_all()
  ```
  **WARNING:** This destroys all data. Only do this on an empty database.
- The app's `init_sample_data()` and `init_sample_cutoff_data()` functions (called after `db.create_all()` in `app.py`) insert seed data into `colleges` and `college_cutoffs`. These are optional but recommended for a functional app.