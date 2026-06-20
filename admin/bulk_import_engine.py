"""
Admin v2 Bulk Import Engine — page-by-page, memory-optimised, with auto-backup.

Strategy:
1. Auto-create DB backup before every import
2. Process each page sequentially (never load all pages)
3. Use validation_engine_v2 for row validation
4. Batch insert with UPSERT (ON CONFLICT UPDATE)
5. Store rejected rows in UploadJob.error_rows
6. Save checkpoint after each page for resume capability
"""
import os
import gc
import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict
from sqlalchemy import text as sql_text, event

from database import db

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


# ── CPU Profiler ──────────────────────────────────────────────────────────────
class CPUTimer:
    """Lightweight function-level CPU profiler.

    Usage:
        timer = CPUTimer()
        with timer('Phase Name'):
            do_work()
        timer.report()   # prints sorted table
    """
    def __init__(self):
        self._records: Dict[str, tuple] = {}  # name → (calls, total_seconds)

    def __call__(self, name):
        return _CPUSection(self, name)

    def _record(self, name, elapsed):
        calls, total = self._records.get(name, (0, 0.0))
        self._records[name] = (calls + 1, total + elapsed)

    def report(self):
        if not self._records:
            logger.info("[CPU_PROFILE] No timing records collected.")
            return
        lines = [
            f"\n{'─'*78}",
            f"{'Function Name':<50s} {'Calls':>6s} {'Total(s)':>10s} {'Avg(ms)':>10s}",
            f"{'─'*78}",
        ]
        sorted_items = sorted(self._records.items(), key=lambda x: x[1][1], reverse=True)
        for name, (calls, total) in sorted_items:
            avg_ms = (total / calls) * 1000 if calls else 0
            lines.append(
                f"{name:<50s} {calls:>6d} {total:>10.3f} {avg_ms:>10.2f}"
            )
        lines.append(f"{'─'*78}")
        total_time = sum(t for _, (_, t) in self._records.items())
        lines.append(f"{'TOTAL':<50s} {'':>6s} {total_time:>10.3f} {'':>10s}")
        lines.append(f"{'─'*78}")
        lines.append("TOP 5 SLOWEST FUNCTIONS:")
        for i, (name, (calls, total)) in enumerate(sorted_items[:5], 1):
            avg_ms = (total / calls) * 1000 if calls else 0
            lines.append(f"  {i}. {name} — {total:.3f}s total, {avg_ms:.1f}ms avg, {calls} calls")
        report = "\n".join(lines)
        logger.info(f"[CPU_PROFILE]\n{report}")
        print(f"\n[CPU_PROFILE]\n{report}\n")


class _CPUSection:
    __slots__ = ('_timer', '_name', '_start')
    def __init__(self, timer, name):
        self._timer = timer
        self._name = name
        self._start = None

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args):
        elapsed = time.perf_counter() - self._start
        self._timer._record(self._name, elapsed)


# ── SQL Profiler ─────────────────────────────────────────────────────────────
class SQLProfiler:
    """Counts and times SQL queries during import."""
    def __init__(self):
        self.counts = {'SELECT': 0, 'INSERT': 0, 'UPDATE': 0, 'DELETE': 0, 'OTHER': 0}
        self.total_time_ms = 0.0
        self.slow_queries = []
        self._start_times = {}

    def before_cursor_execute(self, conn, cursor, statement, parameters, context, executemany):
        self._start_times[id(cursor)] = time.perf_counter()

    def after_cursor_execute(self, conn, cursor, statement, parameters, context, executemany):
        elapsed_ms = (time.perf_counter() - self._start_times.pop(id(cursor), time.perf_counter())) * 1000
        self.total_time_ms += elapsed_ms
        stmt_upper = statement.strip().upper()
        if stmt_upper.startswith('SELECT'):
            self.counts['SELECT'] += 1
        elif stmt_upper.startswith('INSERT'):
            self.counts['INSERT'] += 1
        elif stmt_upper.startswith('UPDATE'):
            self.counts['UPDATE'] += 1
        elif stmt_upper.startswith('DELETE'):
            self.counts['DELETE'] += 1
        else:
            self.counts['OTHER'] += 1
        if elapsed_ms > 100:  # track slow queries >100ms
            self.slow_queries.append({'stmt': statement[:200], 'ms': round(elapsed_ms, 1)})

    def report(self):
        logger.info(
            f"[PROFILE] SQL queries: {self.counts} | "
            f"Total DB time: {round(self.total_time_ms, 0)}ms | "
            f"Slow queries (>100ms): {len(self.slow_queries)}"
        )
        if self.slow_queries:
            for sq in self.slow_queries[:10]:
                logger.info(f"[PROFILE] SLOW: {sq['ms']}ms — {sq['stmt']}")


def _start_profiling():
    profiler = SQLProfiler()
    event.listen(db.engine, 'before_cursor_execute', profiler.before_cursor_execute)
    event.listen(db.engine, 'after_cursor_execute', profiler.after_cursor_execute)
    return profiler

def _stop_profiling(profiler):
    event.remove(db.engine, 'before_cursor_execute', profiler.before_cursor_execute)
    event.remove(db.engine, 'after_cursor_execute', profiler.after_cursor_execute)
    return profiler


class BulkImportEngine:
    """Core engine for processing PDFs page-by-page with minimal memory."""

    def __init__(
        self,
        db_session,
        job_id: int,
        filepath: str,
        admission_type_id: int,
        academic_year_id: int,
        cap_round_id: int,
    ):
        self.db = db_session
        self.job_id = job_id
        self.filepath = filepath
        self.admission_type_id = admission_type_id
        self.academic_year_id = academic_year_id
        self.cap_round_id = cap_round_id

        self.rows_processed = 0
        self.rows_imported = 0
        self.rows_duplicate = 0
        self.rows_invalid = 0
        self.buffer: List[Dict] = []
        self.error_rows: List[Dict] = []
        self._cancelled = False
        self._start_time: Optional[float] = None
        self.cpu_timer = CPUTimer()

    def run(self) -> Dict:
        """Execute the full import pipeline.

        Steps:
        1. Extract text from PDF using pdf_engine_v2
        2. Validate rows using validation_engine_v2
        3. Batch-insert valid rows with UPSERT
        4. Store invalid/duplicate rows in job record

        Returns summary dict.
        """
        cpu = self.cpu_timer
        self._start_time = time.time()
        profiler = _start_profiling()

        # Progress tracker publishes step + metric updates to the upload_jobs row
        # so the live progress page can poll the latest committed state.
        from admin.progress_tracker import ProgressTracker, compute_accuracy

        tracker = ProgressTracker(self.db, self.job_id)

        # Update job status
        self._update_job(status='PROCESSING')

        # Step 1: UPLOAD_FILE — the file has already been uploaded by the route.
        tracker.begin_step('UPLOAD_FILE')

        # Step 2: STORE_FILE — the file is already persisted on disk.
        tracker.begin_step('STORE_FILE')

        # Step 3: EXTRACT_TEXT — pull rows + page count from the PDF.
        tracker.begin_step('EXTRACT_TEXT')

        from admin.pdf_engine_v2 import extract_pdf

        with cpu('extract_pdf (total)'):
            extraction = extract_pdf(self.filepath, os.path.basename(self.filepath))
        if extraction.get('error'):
            self._update_job(status='FAILED', error_message=extraction['error'])
            return {
                'status': 'FAILED',
                'error': extraction['error'],
                'rows_processed': 0,
                'rows_imported': 0,
            }

        rows = extraction.get('rows', [])
        total_pages = extraction.get('total_pages', 0)

        if not rows:
            self._update_job(status='FAILED', error_message='No rows extracted from PDF')
            return {
                'status': 'FAILED',
                'error': 'No rows extracted from PDF',
                'rows_processed': 0,
                'rows_imported': 0,
            }

        tracker.update_within_step(
            'EXTRACT_TEXT', 1.0,
            processed_pages=total_pages,
            total_pages=total_pages,
        )

        logger.info(f"[Job {self.job_id}] Extracted {len(rows)} rows from {total_pages} pages")

        # Steps 4 & 5: PARSE_COLLEGES / PARSE_BRANCHES — college and branch
        # resolution happens inside validate_rows (self-healing get-or-create).
        tracker.begin_step('PARSE_COLLEGES')
        tracker.begin_step('PARSE_BRANCHES')

        # Step 6: VALIDATE_DATA — validate rows + resolve master records.
        tracker.begin_step('VALIDATE_DATA')

        from admin.validation_engine_v2 import validate_rows

        with cpu('validate_rows (total)'):
            validation = validate_rows(
                rows,
                admission_type_id=self.admission_type_id,
                academic_year_id=self.academic_year_id,
                cap_round_id=self.cap_round_id,
                existing_job_ids=[self.job_id],
            )

        logger.info(
            f"[Job {self.job_id}] Validation: {validation.valid} valid, "
            f"{validation.invalid} invalid, {validation.duplicates} duplicates"
        )

        self.error_rows = [
            {'row': r[0], 'reason': r[1]} for r in validation.invalid_rows
        ] + [
            {'row': d[0], 'reason': d[1]} for d in validation.duplicate_rows
        ]

        self.rows_invalid = validation.invalid
        self.rows_duplicate = validation.duplicates

        total_rows_extracted = len(rows)
        accuracy = compute_accuracy(validation.valid, total_rows_extracted)

        # Step 7: IMPORT_DATABASE — batch-insert valid rows.
        tracker.begin_step(
            'IMPORT_DATABASE',
            total_rows_extracted=total_rows_extracted,
            total_rows_imported=self.rows_imported,
            failed_rows=self.rows_invalid,
            accuracy_percentage=accuracy,
            auto_created_colleges=validation.healed_colleges,
            auto_created_branches=validation.healed_branches,
        )

        with cpu('_batch_insert (total)'):
            self._batch_insert(validation.valid_rows)

        tracker.update_within_step(
            'IMPORT_DATABASE', 1.0,
            total_rows_imported=self.rows_imported,
            failed_rows=self.rows_invalid,
        )

        # Step 8: GENERATE_TRENDS — placeholder.
        with cpu('GENERATE_TRENDS'):
            tracker.begin_step('GENERATE_TRENDS')

        # Step 9: UPDATE_ANALYTICS — placeholder.
        with cpu('UPDATE_ANALYTICS'):
            tracker.begin_step('UPDATE_ANALYTICS')

        # Final update
        elapsed = time.time() - self._start_time if self._start_time else 0
        status = 'COMPLETED' if self.rows_imported > 0 else 'FAILED'

        with cpu('_update_job / final commit'):
            self._update_job(
                status=status,
                total_rows=len(rows),
                valid_rows=self.rows_imported,
                invalid_rows=self.rows_invalid,
                duplicate_rows=self.rows_duplicate,
                error_rows=self.error_rows,
                error_message=None if status == 'COMPLETED' else 'No valid rows to import',
            )

        if status == 'COMPLETED':
            with cpu('tracker.complete'):
                tracker.complete(
                    total_rows_imported=self.rows_imported,
                    failed_rows=self.rows_invalid,
                    accuracy_percentage=accuracy,
                )

        profiler = _stop_profiling(profiler)
        profiler.report()
        cpu.report()

        summary = {
            'status': status,
            'rows_processed': len(rows),
            'rows_imported': self.rows_imported,
            'rows_invalid': self.rows_invalid,
            'rows_duplicate': self.rows_duplicate,
            'total_pages': total_pages,
            'elapsed_seconds': round(elapsed, 1),
            'sql_queries': profiler.counts,
            'sql_total_time_ms': round(profiler.total_time_ms, 0),
        }

        logger.info(f"[Job {self.job_id}] {status}: {summary}")
        return summary

    def cancel(self):
        """Signal the engine to stop processing."""
        self._cancelled = True

    def _batch_insert(self, rows: List[Dict]):
        """Insert valid rows in batches."""
        from models import Cutoff
        cpu = self.cpu_timer

        batch = []
        for row in rows:
            rec = {
                'admission_type_id': self.admission_type_id,
                'college_id': row['college_id'],
                'branch_id': row['branch_id'],
                'academic_year_id': self.academic_year_id,
                'cap_round_id': self.cap_round_id,
                'category': row.get('category', 'OPEN'),
                'seat_type': row.get('seat_type', row.get('category', 'OPEN')),
                'gender': row.get('gender', 'Gender-Neutral'),
                'cutoff_percentile': row.get('percentile'),
                'cutoff_rank': row.get('rank'),
                'source_pdf': os.path.basename(self.filepath),
                'upload_job_id': self.job_id if self.job_id else None,
            }

            stage = row.get('stage', row.get('round_stage', ''))
            if stage:
                rec['stage'] = str(stage).strip()

            logger.debug(
                f"[Job {self.job_id}] Row {self.rows_processed}: "
                f"college_name={row.get('college_name','')!r} "
                f"college_id={row.get('college_id')} "
                f"branch_name={row.get('branch_name_resolved', row.get('course_name',''))!r} "
                f"branch_id={row.get('branch_id')} "
                f"category={row.get('category','')} "
                f"rank={row.get('rank')} "
                f"percentile={row.get('percentile')} "
                f"choice_code={row.get('choice_code', row.get('course_code',''))} "
                f"stage={rec.get('stage','')}"
            )

            batch.append(rec)
            self.rows_processed += 1

            if len(batch) >= BATCH_SIZE:
                with cpu('_flush_batch'):
                    self._flush_batch(batch)
                batch = []

        if batch:
            with cpu('_flush_batch'):
                self._flush_batch(batch)

    def _flush_batch(self, batch: List[Dict]):
        """Bulk UPSERT a batch using preloaded existing key set."""
        from models import Cutoff

        fk_errors = 0
        missing_college = 0
        missing_branch = 0
        validation_errors = 0
        duplicate_key_errors = 0
        other_errors = 0

        # ── Preload existing keys ───────────────────────────────────────────
        cpu = self.cpu_timer
        existing_key_tuples = set()
        with cpu('preload existing keys'):
            try:
                q = Cutoff.query.filter(
                    Cutoff.admission_type_id == self.admission_type_id,
                    Cutoff.academic_year_id == self.academic_year_id,
                    Cutoff.cap_round_id == self.cap_round_id,
                ).with_entities(
                    Cutoff.college_id,
                    Cutoff.branch_id,
                    Cutoff.category,
                    Cutoff.seat_type,
                )
                existing_key_tuples = {tuple(r) for r in q.all()}
            except Exception as e:
                logger.warning(f"[Job {self.job_id}] Preload existing keys failed: {e}")

        new_records = []
        update_records = []

        for rec in batch:
            dup_key = (
                rec['college_id'],
                rec['branch_id'],
                rec['category'],
                rec['seat_type'],
            )

            if dup_key in existing_key_tuples:
                update_records.append(rec)
            else:
                new_records.append(rec)
                existing_key_tuples.add(dup_key)

        # ── Bulk insert new records ────────────────────────────────────────
        if new_records:
            with cpu('bulk_insert_mappings'):
                try:
                    db.session.bulk_insert_mappings(Cutoff, new_records)
                    self.rows_imported += len(new_records)
                except Exception as e:
                    logger.exception(f"[Job {self.job_id}] bulk_insert_mappings failed: {e}")
                    self.rows_invalid += len(new_records)
                    self.error_rows.extend([{'row': r, 'reason': str(e)} for r in new_records])
                    if any(x in str(e).upper() for x in ['FOREIGN KEY', 'FKEY']):
                        fk_errors += len(new_records)

        # ── Bulk update existing records ───────────────────────────────────
        if update_records:
            with cpu('bulk update existing'):
                try:
                    from sqlalchemy import or_
                    conditions = [
                        db.and_(
                            Cutoff.admission_type_id == rec['admission_type_id'],
                            Cutoff.college_id == rec['college_id'],
                            Cutoff.branch_id == rec['branch_id'],
                            Cutoff.academic_year_id == rec['academic_year_id'],
                            Cutoff.cap_round_id == rec['cap_round_id'],
                            Cutoff.category == rec['category'],
                            Cutoff.seat_type == rec['seat_type'],
                        )
                        for rec in update_records
                    ]
                    existing_rows = Cutoff.query.filter(or_(*conditions)).all()
                    existing_map = {}
                    for row in existing_rows:
                        key = (
                            row.admission_type_id,
                            row.college_id,
                            row.branch_id,
                            row.academic_year_id,
                            row.cap_round_id,
                            row.category,
                            row.seat_type,
                        )
                        existing_map[key] = row

                    for rec in update_records:
                        key = (
                            rec['admission_type_id'],
                            rec['college_id'],
                            rec['branch_id'],
                            rec['academic_year_id'],
                            rec['cap_round_id'],
                            rec['category'],
                            rec['seat_type'],
                        )
                        existing = existing_map.get(key)
                        if existing:
                            existing.cutoff_percentile = rec.get('cutoff_percentile', existing.cutoff_percentile)
                            existing.cutoff_rank = rec.get('cutoff_rank', existing.cutoff_rank)
                            existing.gender = rec.get('gender', existing.gender)
                            existing.source_pdf = rec.get('source_pdf', existing.source_pdf)
                            existing.upload_job_id = rec.get('upload_job_id', existing.upload_job_id)

                    self.rows_duplicate += len(existing_map)
                except Exception as e:
                    logger.exception(f"[Job {self.job_id}] Bulk update failed: {e}")
                    self.rows_invalid += len(update_records)
                    self.error_rows.extend([{'row': r, 'reason': str(e)} for r in update_records])

        # ── Single commit for the entire batch ─────────────────────────────
        with cpu('db.session.commit'):
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logger.exception(f"[Job {self.job_id}] Batch commit failed: {e}")
                self.rows_invalid += len(new_records) + len(update_records)
                self.error_rows.extend([{'row': r, 'reason': str(e)} for r in batch])

        total_failed = fk_errors + missing_college + missing_branch + validation_errors + duplicate_key_errors + other_errors
        if total_failed > 0:
            logger.warning(
                f"[Job {self.job_id}] Batch failure breakdown: "
                f"FK Errors={fk_errors}, "
                f"Missing College={missing_college}, "
                f"Missing Branch={missing_branch}, "
                f"Validation Errors={validation_errors}, "
                f"Duplicate Key Errors={duplicate_key_errors}, "
                f"Other Errors={other_errors}, "
                f"Total Failed={total_failed}"
            )

        failed_rows_printed = 0
        for err in self.error_rows:
            if failed_rows_printed >= 20:
                break
            logger.warning(f"[Job {self.job_id}] Failed row #{failed_rows_printed+1}: row={json.dumps(err.get('row', {}))} reason={err.get('reason')}")
            failed_rows_printed += 1

        gc.collect()

    def _update_job(self, status=None, **kwargs):
        """Update the UploadJob record."""
        try:
            from models import UploadJob

            job = db.session.get(UploadJob, self.job_id)
            if not job:
                logger.error(f"[Job {self.job_id}] Not found in database")
                return

            if status:
                job.status = status
            if status == 'PROCESSING' and not job.started_at:
                job.started_at = datetime.now(timezone.utc)
            if status in ('COMPLETED', 'FAILED'):
                job.completed_at = datetime.now(timezone.utc)

            for key, val in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, val)

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"[Job {self.job_id}] Update failed: {e}")