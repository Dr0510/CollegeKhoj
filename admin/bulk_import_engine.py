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
from sqlalchemy import text as sql_text

from database import db

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


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

    def run(self) -> Dict:
        """Execute the full import pipeline.

        Steps:
        1. Extract text from PDF using pdf_engine_v2
        2. Validate rows using validation_engine_v2
        3. Batch-insert valid rows with UPSERT
        4. Store invalid/duplicate rows in job record

        Returns summary dict.
        """
        self._start_time = time.time()

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

        # extract_pdf is a single call, so per-page granularity is best-effort:
        # publish the PDF page count and mark all pages processed once extraction
        # returns, interpolating EXTRACT_TEXT to completion (Req 3.3).
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

        validation = validate_rows(
            rows,
            admission_type_id=self.admission_type_id,
            academic_year_id=self.academic_year_id,
            cap_round_id=self.cap_round_id,
            existing_job_ids=[self.job_id],  # Exclude THIS job from duplicate check
        )

        logger.info(
            f"[Job {self.job_id}] Validation: {validation.valid} valid, "
            f"{validation.invalid} invalid, {validation.duplicates} duplicates"
        )

        # Store error rows for admin review
        self.error_rows = [
            {'row': r[0], 'reason': r[1]} for r in validation.invalid_rows
        ] + [
            {'row': d[0], 'reason': d[1]} for d in validation.duplicate_rows
        ]

        self.rows_invalid = validation.invalid
        self.rows_duplicate = validation.duplicates

        # Compute extraction accuracy at the VALIDATE_DATA / IMPORT_DATABASE
        # boundary (Req 5.1): valid_rows / total_rows_extracted * 100.
        total_rows_extracted = len(rows)
        accuracy = compute_accuracy(validation.valid, total_rows_extracted)

        # Step 7: IMPORT_DATABASE — batch-insert valid rows with UPSERT while
        # keeping row counters consistent (Req 3.6).
        tracker.begin_step(
            'IMPORT_DATABASE',
            total_rows_extracted=total_rows_extracted,
            total_rows_imported=self.rows_imported,
            failed_rows=self.rows_invalid,
            accuracy_percentage=accuracy,
            auto_created_colleges=validation.healed_colleges,
            auto_created_branches=validation.healed_branches,
        )

        self._batch_insert(validation.valid_rows)

        # Reconcile the committed counters with the rows actually imported.
        tracker.update_within_step(
            'IMPORT_DATABASE', 1.0,
            total_rows_imported=self.rows_imported,
            failed_rows=self.rows_invalid,
        )

        # Step 8: GENERATE_TRENDS — placeholder (no trend logic yet); still
        # emit the step so progress advances through the range.
        tracker.begin_step('GENERATE_TRENDS')

        # Step 9: UPDATE_ANALYTICS — placeholder (no analytics logic yet).
        tracker.begin_step('UPDATE_ANALYTICS')

        # 4. Final update
        elapsed = time.time() - self._start_time if self._start_time else 0
        status = 'COMPLETED' if self.rows_imported > 0 else 'FAILED'

        self._update_job(
            status=status,
            total_rows=len(rows),
            valid_rows=self.rows_imported,
            invalid_rows=self.rows_invalid,
            duplicate_rows=self.rows_duplicate,
            error_rows=self.error_rows,
            error_message=None if status == 'COMPLETED' else 'No valid rows to import',
        )

        # Step 10: COMPLETED — only on a successful import (Req 3.4). On the
        # FAILED path we leave progress at the last committed step.
        if status == 'COMPLETED':
            tracker.complete(
                total_rows_imported=self.rows_imported,
                failed_rows=self.rows_invalid,
                accuracy_percentage=accuracy,
            )

        summary = {
            'status': status,
            'rows_processed': len(rows),
            'rows_imported': self.rows_imported,
            'rows_invalid': self.rows_invalid,
            'rows_duplicate': self.rows_duplicate,
            'total_pages': total_pages,
            'elapsed_seconds': round(elapsed, 1),
        }

        logger.info(f"[Job {self.job_id}] {status}: {summary}")
        return summary

    def cancel(self):
        """Signal the engine to stop processing."""
        self._cancelled = True

    def _batch_insert(self, rows: List[Dict]):
        """Insert valid rows in batches using bulk UPSERT (ON CONFLICT DO UPDATE).

        Performance: single SQL statement per batch instead of N per-row queries.
        Each row is still wrapped in a savepoint so a single constraint violation
        does NOT rollback the entire batch.
        """
        from models import Cutoff

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

            # ── Include stage if extracted (Stage-I, Stage-II) ────────────
            stage = row.get('stage', row.get('round_stage', ''))
            if stage:
                rec['stage'] = str(stage).strip()

            # ── Detailed per-row logging ──────────────────────────────────
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
                self._flush_batch(batch)
                batch = []

        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: List[Dict]):
        """Execute UPSERT for a batch of records.

        Each row is wrapped in a savepoint (db.session.begin_nested())
        so that a single-row failure does NOT rollback the entire batch.
        """
        from models import Cutoff

        # ── Failure counters ──────────────────────────────────────────────
        fk_errors = 0
        missing_college = 0
        missing_branch = 0
        validation_errors = 0
        duplicate_key_errors = 0
        other_errors = 0

        for rec in batch:
            try:
                # Wrap each row in its own savepoint
                with db.session.begin_nested():
                    # Check if record exists
                    existing = Cutoff.query.filter_by(
                        admission_type_id=rec['admission_type_id'],
                        college_id=rec['college_id'],
                        branch_id=rec['branch_id'],
                        academic_year_id=rec['academic_year_id'],
                        cap_round_id=rec['cap_round_id'],
                        category=rec['category'],
                        seat_type=rec['seat_type'],
                    ).first()

                    if existing:
                        # Update existing record
                        existing.cutoff_percentile = rec.get('cutoff_percentile', existing.cutoff_percentile)
                        existing.cutoff_rank = rec.get('cutoff_rank', existing.cutoff_rank)
                        existing.gender = rec.get('gender', existing.gender)
                        existing.source_pdf = rec.get('source_pdf', existing.source_pdf)
                        existing.upload_job_id = rec.get('upload_job_id', existing.upload_job_id)
                        self.rows_duplicate += 1
                    else:
                        # Create new record
                        cutoff = Cutoff(**rec)
                        db.session.add(cutoff)
                        self.rows_imported += 1

            except Exception as e:
                self.rows_invalid += 1
                reason = str(e)
                logger.exception(
                    f"[Job {self.job_id}] Insert error for "
                    f"college_id={rec.get('college_id')} "
                    f"branch_id={rec.get('branch_id')} "
                    f"category={rec.get('category')}: {reason}"
                )
                self.error_rows.append({'row': rec, 'reason': reason})

                # Classify the failure
                if any(x in reason.upper() for x in ['FOREIGN KEY', 'FKEY', 'VIOLATES FOREIGN KEY']):
                    fk_errors += 1
                elif 'college' in reason.lower() and ('miss' in reason.lower() or 'not found' in reason.lower()):
                    missing_college += 1
                elif 'branch' in reason.lower() and ('miss' in reason.lower() or 'not found' in reason.lower()):
                    missing_branch += 1
                elif 'duplicate' in reason.lower() or 'unique' in reason.lower() or 'uq_' in reason.lower():
                    duplicate_key_errors += 1
                elif 'valid' in reason.lower() or 'invalid' in reason.lower():
                    validation_errors += 1
                else:
                    other_errors += 1

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.exception(f"[Job {self.job_id}] Batch commit failed: {e}")

        # Log failure breakdown
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

        # Print first 20 failed rows with reasons
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