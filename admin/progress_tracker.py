"""
Progress tracking helpers for the admin PDF import progress monitor.

Provides helpers used by the bulk import pipeline to report progress at each
of the ten named processing steps and to compute extraction quality metrics.
``compute_accuracy`` is side-effect free; ``ProgressTracker`` operates on a
database session and commits progress to the ``upload_jobs`` row.
"""
import logging

logger = logging.getLogger(__name__)


# Ten Processing_Steps mapped to their (low, high) progress percentage ranges.
STEP_RANGES = {
    'UPLOAD_FILE':      (0,   5),
    'STORE_FILE':       (5,   10),
    'EXTRACT_TEXT':     (10,  40),
    'PARSE_COLLEGES':   (40,  55),
    'PARSE_BRANCHES':   (55,  65),
    'VALIDATE_DATA':    (65,  75),
    'IMPORT_DATABASE':  (75,  90),
    'GENERATE_TRENDS':  (90,  95),
    'UPDATE_ANALYTICS': (95,  100),
    'COMPLETED':        (100, 100),
}

STEP_ORDER = list(STEP_RANGES.keys())


def compute_accuracy(valid_rows: int, total_rows_extracted: int) -> int:
    """Return extraction accuracy as an integer percentage (0-100).

    Computes ``round(valid_rows / total_rows_extracted * 100)`` when
    ``total_rows_extracted`` is greater than zero; otherwise returns 0 to
    guard against division by zero.
    """
    if total_rows_extracted > 0:
        return round(valid_rows / total_rows_extracted * 100)
    return 0


class ProgressTracker:
    """Publishes progress for one Upload_Job to the ``upload_jobs`` row.

    The tracker commits ``current_step`` + ``progress_percentage`` (and any
    provided metric columns) at each step boundary so a polling client can read
    the latest committed state. Progress advances monotonically.
    """

    def __init__(self, db_session, job_id):
        self.db = db_session
        self.job_id = job_id

    def begin_step(self, step_name, **metrics):
        """Set current_step to the step's lower bound and commit."""
        low, _ = STEP_RANGES[step_name]
        self._apply(step_name, low, metrics)

    def update_within_step(self, step_name, fraction, **metrics):
        """Interpolate progress inside a step (e.g. per-page during EXTRACT_TEXT)."""
        low, high = STEP_RANGES[step_name]
        clamped = max(0.0, min(1.0, fraction))
        pct = int(low + (high - low) * clamped)
        self._apply(step_name, pct, metrics)

    def complete(self, **metrics):
        """Force 100 / COMPLETED."""
        self._apply('COMPLETED', 100, metrics)

    def _apply(self, step_name, pct, metrics):
        # Deferred import to match bulk_import_engine.py conventions and avoid
        # circular imports at module load time.
        from models import UploadJob

        job = self.db.get(UploadJob, self.job_id)
        if not job:
            return
        # Monotonic guard: never move progress backwards (Req 3.5, 1.4).
        job.progress_percentage = max(job.progress_percentage or 0, pct)
        job.current_step = step_name
        for key, val in metrics.items():
            if hasattr(job, key):
                setattr(job, key, val)
        self.db.commit()
