"""
Bulk PDF Import Engine — page-by-page, memory-optimised, checkpointing.

Designed for Render Free Tier constraints:
  - RAM: < 150 MB
  - CPU: 1 core
  - No multiprocessing
  - No OCR by default
  - No image conversion

Strategy:
  1. Open PDF with pdfplumber (never load all pages).
  2. Process each page sequentially via a generator.
  3. Extract text via page.extract_text() — no tables, no OCR.
  4. If text length < 100 chars → mark page as OCR_REQUIRED, skip.
  5. Parse using admin.pdf_extractor.parse_page_text().
  6. Buffer rows in memory, batch-insert every 500 rows.
  7. Delete page object + gc.collect() after each page.
  8. Save checkpoint every 10 pages.
  9. Log progress every 10 pages.
"""
import gc
import os
import re
import json
import time
import logging
import tracemalloc
from datetime import datetime, timezone
from typing import Optional, Generator, Any

from sqlalchemy import text as sql_text, inspect

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
BATCH_SIZE = 500
CHECKPOINT_INTERVAL = 10
LOG_INTERVAL = 10
MIN_TEXT_LENGTH = 100  # minimum chars to attempt parsing
MAX_FAILED_PAGES = 50  # auto-fail import if too many pages fail

# ── Insert SQL template ──────────────────────────────────────────────────────
INSERT_SQL = sql_text("""
    INSERT INTO college_cutoffs
        (year, round, college_code, college_name, course_code, course_name,
         category, rank, percentile, source_file_id, imported_at)
    VALUES
        (:year, :round, :college_code, :college_name, :course_code,
         :course_name, :category, :rank, :percentile, :source_file_id, :imported_at)
    ON CONFLICT (year, round, college_code, course_code, category)
    DO NOTHING
""")


class BulkImportEngine:
    """
    Core engine for processing large PDFs page-by-page with minimal memory.

    Typical usage::

        engine = BulkImportEngine(db, job_id, filepath, year, round_number)
        engine.run()

    For resume::

        engine = BulkImportEngine(db, job_id, filepath, year, round_number,
                                  start_page=job.checkpoint_page + 1)
        engine.run()
    """

    def __init__(
        self,
        db_session,
        job_id: int,
        filepath: str,
        source_file_id: int,
        year: int,
        round_number: int,
        start_page: int = 1,
        end_page: Optional[int] = None,
    ):
        self.db = db_session
        self.job_id = job_id
        self.filepath = filepath
        self.source_file_id = source_file_id
        self.year = year
        self.round_number = round_number
        self.start_page = start_page
        self.end_page = end_page
        self.imported_at = datetime.now(timezone.utc)

        # Runtime state
        self.processed_pages = 0
        self.rows_extracted = 0
        self.rows_imported = 0
        self.rows_failed = 0
        self.failed_pages: list[int] = []
        self.error_log: list[dict] = []
        self.buffer: list[dict] = []
        self.peak_memory_mb = 0.0
        self.current_memory_mb = 0.0
        self._start_time: Optional[float] = None
        self._cancelled = False

        # Enable tracemalloc for memory tracking
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start()
        except Exception:
            pass

    # ── Public API ──────────────────────────────────────────────────────────

    def run(self) -> dict:
        """
        Execute the full import pipeline.

        Returns a summary dict with stats.
        """
        self._start_time = time.time()
        logger.info(
            f"[Job {self.job_id}] Starting import: "
            f"pages {self.start_page}-{self.end_page or 'end'}, "
            f"file={os.path.basename(self.filepath)}"
        )

        try:
            import pdfplumber
        except ImportError:
            return self._fail("pdfplumber is not installed")

        # Update job status
        self._update_job(status='PROCESSING')

        try:
            with pdfplumber.open(self.filepath) as pdf:
                total_pages = len(pdf.pages)
                actual_end = self.end_page or total_pages

                # Validate page range
                if self.start_page > total_pages:
                    return self._fail(
                        f"start_page {self.start_page} exceeds total pages {total_pages}"
                    )
                if actual_end > total_pages:
                    actual_end = total_pages

                # Update total pages in job
                self._update_job(total_pages=total_pages)

                # Iterate page-by-page (generator-style, never holding all pages)
                for page_num in range(self.start_page, actual_end + 1):
                    if self._cancelled:
                        logger.info(f"[Job {self.job_id}] Cancelled at page {page_num}")
                        self._update_job(status='CANCELLED')
                        break

                    page = pdf.pages[page_num - 1]  # pdfplumber is 0-indexed

                    try:
                        self._process_page(page, page_num)
                    except Exception as e:
                        logger.error(f"[Job {self.job_id}] Page {page_num} error: {e}")
                        self.failed_pages.append(page_num)
                        self.error_log.append({
                            'page': page_num,
                            'error': str(e),
                        })
                        self.rows_failed += 1

                    # Explicitly delete page and garbage collect
                    del page
                    if page_num % 5 == 0:
                        gc.collect()

                    # Checkpoint + log periodically
                    if page_num % CHECKPOINT_INTERVAL == 0:
                        self._save_checkpoint(page_num)
                    if page_num % LOG_INTERVAL == 0:
                        self._log_progress(page_num, total_pages)

                # Flush remaining buffer
                self._flush_buffer()

            # Final checkpoint
            self._save_checkpoint(actual_end)

            # Determine final status
            if not self._cancelled:
                if self.failed_pages and len(self.failed_pages) >= total_pages * 0.5:
                    status = 'FAILED'
                    err_msg = f"{len(self.failed_pages)}/{total_pages} pages failed"
                    self._update_job(status=status, error_message=err_msg)
                else:
                    status = 'COMPLETED'
                    approval_status = 'pending_approval'
                    self._update_job(status=status)
                    # Set approval_status to pending_approval — waits for admin review
                    try:
                        job = self.db.get(ImportJob, self.job_id) if hasattr(self, 'db') else None
                        if job:
                            job.approval_status = 'pending_approval'
                            self.db.commit()
                    except Exception:
                        self.db.rollback()

            # Track memory
            self._track_memory()

            elapsed = time.time() - self._start_time
            summary = {
                'status': status if not self._cancelled else 'CANCELLED',
                'total_pages': total_pages,
                'processed_pages': self.processed_pages,
                'rows_extracted': self.rows_extracted,
                'rows_imported': self.rows_imported,
                'rows_failed': self.rows_failed,
                'failed_pages': self.failed_pages,
                'peak_memory_mb': round(self.peak_memory_mb, 1),
                'elapsed_seconds': round(elapsed, 1),
            }

            logger.info(
                f"[Job {self.job_id}] {summary['status']}: "
                f"{summary['rows_imported']} rows from {summary['processed_pages']} pages "
                f"in {summary['elapsed_seconds']}s, "
                f"memory={summary['peak_memory_mb']}MB"
            )

            return summary

        except Exception as e:
            logger.error(f"[Job {self.job_id}] Fatal error: {e}")
            return self._fail(str(e))

    def cancel(self):
        """Signal the engine to stop processing at the next page boundary."""
        self._cancelled = True

    # ── Page Processing ────────────────────────────────────────────────────

    def _process_page(self, page, page_num: int):
        """Process a single PDF page: extract text, parse, buffer rows."""
        # 1. Extract text
        text = page.extract_text() or ''
        text = text.strip()

        # 2. Check if page has enough text for parsing
        if len(text) < MIN_TEXT_LENGTH:
            logger.warning(
                f"[Job {self.job_id}] Page {page_num}: "
                f"insufficient text ({len(text)} chars) — marking OCR_REQUIRED"
            )
            self.failed_pages.append(page_num)
            self.error_log.append({
                'page': page_num,
                'error': f'OCR_REQUIRED: only {len(text)} chars extracted',
                'text_snippet': text[:200],
            })
            self.rows_failed += 1
            self.processed_pages += 1
            return

        # 3. Parse using the existing DSE text-block parser
        from admin.pdf_extractor import parse_page_text

        rows_page, debug = parse_page_text(
            text, page_num, self.year, self.round_number
        )

        if not rows_page:
            logger.warning(
                f"[Job {self.job_id}] Page {page_num}: "
                f"parser returned 0 rows — marking failed"
            )
            self.failed_pages.append(page_num)
            self.error_log.append({
                'page': page_num,
                'error': 'Parser returned 0 rows',
                'text_snippet': text[:300],
            })
            self.rows_failed += 1
            self.processed_pages += 1
            return

        # 4. Validate rows
        valid_rows = []
        for row in rows_page:
            try:
                self._validate_row(row)
                valid_rows.append(row)
            except ValueError as ve:
                self.rows_failed += 1
                logger.debug(f"[Job {self.job_id}] Page {page_num} invalid row: {ve}")

        # 5. Add to buffer
        for row in valid_rows:
            self.buffer.append({
                'year': row.get('year', self.year),
                'round': row.get('round', self.round_number),
                'college_code': str(row['college_code']),
                'college_name': str(row['college_name'])[:300],
                'course_code': str(row['course_code']),
                'course_name': str(row['course_name'])[:200],
                'category': str(row['category']).upper(),
                'rank': row.get('rank'),
                'percentile': row.get('percentile'),
                'source_file_id': self.source_file_id,
                'imported_at': self.imported_at,
            })

        self.rows_extracted += len(valid_rows)
        self.processed_pages += 1

        # 6. Flush if buffer is full
        if len(self.buffer) >= BATCH_SIZE:
            self._flush_buffer()

        # 7. Track memory
        self._track_memory()

    def _validate_row(self, row: dict):
        """Validate a single parsed row. Raises ValueError on failure."""
        if not row.get('college_code'):
            raise ValueError('Missing college_code')
        if not row.get('college_name'):
            raise ValueError('Missing college_name')
        if not row.get('course_code'):
            raise ValueError('Missing course_code')
        if not row.get('course_name'):
            raise ValueError('Missing course_name')
        if not row.get('category'):
            raise ValueError('Missing category')

        pctl = row.get('percentile')
        if pctl is not None:
            pctl = float(pctl)
            if pctl < 0 or pctl > 100:
                raise ValueError(f'Invalid percentile: {pctl}')
            row['percentile'] = round(pctl, 2)

        rank = row.get('rank')
        if rank is not None:
            try:
                rank = int(rank)
            except (ValueError, TypeError):
                row['rank'] = None

    # ── Database Operations ────────────────────────────────────────────────

    def _flush_buffer(self):
        """Batch-insert buffered rows using bulk_insert_mappings (optimised)."""
        if not self.buffer:
            return

        chunk = self.buffer[:BATCH_SIZE]
        self.buffer = self.buffer[BATCH_SIZE:]

        from models import CollegeCutoff

        try:
            # First pass: try bulk insert via raw SQL for conflict handling
            batch_imported = 0
            for rec in chunk:
                try:
                    result = self.db.execute(INSERT_SQL, rec)
                    if result.rowcount > 0:
                        batch_imported += 1
                except Exception as e:
                    logger.warning(
                        f"[Job {self.job_id}] Insert error for {rec.get('college_code')}: {e}"
                    )
                    self._store_error_row(rec, str(e))
                    self.rows_failed += 1

            self.db.commit()
            self.rows_imported += batch_imported

            logger.info(
                f"[Job {self.job_id}] Flushed {len(chunk)} rows "
                f"({batch_imported} new, {len(chunk) - batch_imported} duplicates)"
            )

        except Exception as e:
            self.db.rollback()
            logger.error(f"[Job {self.job_id}] Batch insert failed: {e}")
            # Retry one by one
            for rec in chunk:
                try:
                    result = self.db.execute(INSERT_SQL, rec)
                    self.db.commit()
                    if result.rowcount > 0:
                        self.rows_imported += 1
                except Exception as e2:
                    self.db.rollback()
                    self._store_error_row(rec, str(e2))
                    self.rows_failed += 1

        # Clear memory after flush
        del chunk
        gc.collect()

    def _store_error_row(self, rec: dict, error_reason: str):
        """Store a failed row in the import_error_records table."""
        try:
            from models import ImportErrorRecord

            err = ImportErrorRecord(
                job_id=self.job_id,
                college_code=rec.get('college_code'),
                college_name=rec.get('college_name'),
                course_code=rec.get('course_code'),
                course_name=rec.get('course_name'),
                category=rec.get('category'),
                rank=rec.get('rank'),
                percentile=rec.get('percentile'),
                error_reason=error_reason[:500],
            )
            self.db.add(err)
            self.db.commit()
        except Exception as e:
            logger.warning(f"[Job {self.job_id}] Failed to store error row: {e}")
            self.db.rollback()

    # ── Job State Management ──────────────────────────────────────────────

    def _update_job(self, status=None, total_pages=None, error_message=None):
        """Update the ImportJob record in the database."""
        try:
            from models import ImportJob, UploadedFile

            job = self.db.get(ImportJob, self.job_id)
            if not job:
                logger.error(f"[Job {self.job_id}] Not found in database")
                return

            if status:
                job.status = status
            if total_pages is not None:
                job.total_pages = total_pages
            if error_message:
                job.error_message = error_message[:1000]

            if status == 'PROCESSING' and not job.started_at:
                job.started_at = datetime.now(timezone.utc)
            if status in ('COMPLETED', 'FAILED', 'CANCELLED'):
                job.completed_at = datetime.now(timezone.utc)

            # Sync UploadedFile.processed_status when import reaches terminal state
            if status in ('COMPLETED', 'FAILED') and job.file_id:
                file_record = self.db.get(UploadedFile, job.file_id)
                if file_record:
                    if status == 'COMPLETED':
                        file_record.processed_status = 'committed'
                    elif status == 'FAILED':
                        file_record.processed_status = 'failed'

            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f"[Job {self.job_id}] Update failed: {e}")

    def _save_checkpoint(self, page_num: int):
        """Save the current state as a checkpoint for resume capability."""
        try:
            from models import ImportJob

            job = self.db.get(ImportJob, self.job_id)
            if not job:
                return

            job.checkpoint_page = page_num
            job.processed_pages = self.processed_pages
            job.rows_extracted = self.rows_extracted
            job.rows_imported = self.rows_imported
            job.rows_failed = self.rows_failed
            job.failed_pages = self.failed_pages
            job.error_log = self.error_log[-100:]  # Keep last 100 errors
            job.memory_usage_mb = round(self.current_memory_mb, 1)

            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(f"[Job {self.job_id}] Checkpoint save failed: {e}")

    # ── Memory Tracking ───────────────────────────────────────────────────

    def _track_memory(self):
        """Track current and peak memory usage."""
        try:
            if tracemalloc.is_tracing():
                snapshot = tracemalloc.take_snapshot()
                stats = snapshot.statistics('lineno')
                total_size = sum(stat.size for stat in stats)
                self.current_memory_mb = total_size / (1024 * 1024)
                if self.current_memory_mb > self.peak_memory_mb:
                    self.peak_memory_mb = self.current_memory_mb
        except Exception:
            # Fallback: use /proc/self/status on Linux
            try:
                with open('/proc/self/status') as f:
                    for line in f:
                        if line.startswith('VmRSS:'):
                            parts = line.split()
                            if len(parts) >= 2:
                                self.current_memory_mb = int(parts[1]) / 1024
                                if self.current_memory_mb > self.peak_memory_mb:
                                    self.peak_memory_mb = self.current_memory_mb
                            break
            except Exception:
                pass

    # ── Reporting ─────────────────────────────────────────────────────────

    def _log_progress(self, page_num: int, total_pages: int):
        """Log a progress line with stats."""
        elapsed = time.time() - self._start_time if self._start_time else 0
        rate = page_num / elapsed if elapsed > 0 else 0
        remaining_pages = total_pages - page_num
        eta = remaining_pages / rate if rate > 0 else 0

        logger.info(
            f"[Job {self.job_id}] Processed {page_num}/{total_pages} pages | "
            f"Rows: {self.rows_imported} | "
            f"Memory: {self.current_memory_mb:.0f}MB | "
            f"Rate: {rate:.1f} pg/s | "
            f"ETA: {eta:.0f}s | "
            f"Failed: {len(self.failed_pages)}"
        )

    def _fail(self, error_msg: str) -> dict:
        """Mark the job as failed and return a failure summary."""
        self._update_job(status='FAILED', error_message=error_msg[:1000])

        self._save_checkpoint(self.processed_pages)

        logger.error(f"[Job {self.job_id}] FAILED: {error_msg}")

        return {
            'status': 'FAILED',
            'error': error_msg,
            'processed_pages': self.processed_pages,
            'rows_extracted': self.rows_extracted,
            'rows_imported': self.rows_imported,
            'rows_failed': self.rows_failed,
            'failed_pages': self.failed_pages,
            'peak_memory_mb': round(self.peak_memory_mb, 1),
        }


# ── Standalone Helper ─────────────────────────────────────────────────────────

def get_pdf_page_count(filepath: str) -> int:
    """Get the total number of pages in a PDF without loading it fully."""
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            return len(pdf.pages)
    except ImportError:
        logger.error("pdfplumber not installed")
        return 0
    except Exception as e:
        logger.error(f"Failed to count pages in {filepath}: {e}")
        return 0