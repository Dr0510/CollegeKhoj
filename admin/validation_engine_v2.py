"""
Validation engine for PDF-imported cutoff data — Admin v2.

Design principle
────────────────
A row is INVALID only when the *extracted data itself* is missing or
malformed (no college code, no branch name, non-numeric percentile,
percentile out of 0-100, etc.).

A row is NOT invalid simply because the college or branch is absent from
the master tables.  Instead, the self-healing service is called to
get-or-create the missing master record so the import always succeeds.

Validation passes:
1. Required-field presence    — college_code, branch/course_name, category
2. Percentile range           — 0 ≤ x ≤ 100 (None is allowed; treated as no-cutoff)
3. Rank coercion              — int or None
4. Duplicate detection        — in-memory + DB (skipped, not rejected)

Self-healing passes (run before duplicate detection):
5. College resolution         — get_or_create_college(college_code, college_name)
6. Branch resolution          — normalize_branch → get_or_create_branch(canonical_name)
"""
import logging
from typing import List, Dict, Optional, Tuple, Set

from database import db

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────

class ValidationResult:
    """Container for validation results."""

    def __init__(self):
        self.valid_rows: List[Dict] = []
        self.invalid_rows: List[Tuple[Dict, str]] = []
        self.duplicate_rows: List[Tuple[Dict, str]] = []
        self.healed_colleges: int = 0   # colleges auto-created
        self.healed_branches: int = 0   # branches auto-created
        self.total = 0
        self.valid = 0
        self.invalid = 0
        self.duplicates = 0

    @property
    def has_errors(self) -> bool:
        return len(self.invalid_rows) > 0 or len(self.duplicate_rows) > 0

    def to_dict(self) -> Dict:
        return {
            "total":          self.total,
            "valid":          self.valid,
            "invalid":        self.invalid,
            "duplicates":     self.duplicates,
            "healed_colleges": self.healed_colleges,
            "healed_branches": self.healed_branches,
            "valid_rows":     self.valid_rows,
            "invalid_rows":   [{"row": r[0], "reason": r[1]} for r in self.invalid_rows],
            "duplicate_rows": [{"row": d[0], "reason": d[1]} for d in self.duplicate_rows],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def validate_rows(
    parsed_rows: List[Dict],
    admission_type_id: int,
    academic_year_id: int,
    cap_round_id: int,
    existing_job_ids: Optional[List[int]] = None,
) -> ValidationResult:
    """Run full validation + self-healing on a set of parsed rows.

    Args:
        parsed_rows:       List of dicts from the PDF extractor.
        admission_type_id: FK to admission_types.
        academic_year_id:  FK to academic_years.
        cap_round_id:      FK to cap_rounds.
        existing_job_ids:  Optional list of upload job IDs to exclude from
                           duplicate check (e.g. when re-processing).

    Returns:
        ValidationResult instance.
    """
    from models import Cutoff
    from admin.branch_normalizer import normalize_branch
    from admin.self_healing import get_or_create_college, get_or_create_branch

    result = ValidationResult()
    result.total = len(parsed_rows)

    # ── Pre-load existing cutoff keys for duplicate detection ────────────────
    existing_keys: Set[Tuple] = set()
    dup_query = Cutoff.query.with_entities(
        Cutoff.admission_type_id,
        Cutoff.college_id,
        Cutoff.branch_id,
        Cutoff.academic_year_id,
        Cutoff.cap_round_id,
        Cutoff.category,
        Cutoff.seat_type,
    )
    if existing_job_ids:
        dup_query = dup_query.filter(Cutoff.upload_job_id.notin_(existing_job_ids))
    for row in dup_query.distinct().all():
        existing_keys.add(tuple(row))

    seen_this_batch: Set[Tuple] = set()

    # ── In-session caches to avoid repeated DB hits ──────────────────────────
    college_id_cache: Dict[str, Optional[int]] = {}   # college_code → id
    branch_id_cache:  Dict[str, Optional[int]] = {}   # canonical_name → id
    _newly_healed_colleges: Set[str] = set()          # codes of colleges just created
    _newly_healed_branches: Set[str] = set()          # names of branches just created

    for raw_row in parsed_rows:
        row = dict(raw_row)   # work on a copy
        errors: List[str] = []

        # ── Log extracted row details ─────────────────────────────────────
        logger.debug(
            f"[Validation] Processing row: "
            f"college_name={row.get('college_name','')!r} "
            f"college_code={row.get('college_code','')!r} "
            f"branch={row.get('course_name', row.get('branch',''))!r} "
            f"category={row.get('category','')!r} "
            f"rank={row.get('rank')} "
            f"percentile={row.get('percentile')} "
            f"choice_code={row.get('choice_code', row.get('course_code',''))!r}"
        )

        # ── 1. College code ──────────────────────────────────────────────────
        college_code = str(row.get("college_code", "")).strip()
        if not college_code:
            errors.append("Missing college code")
        else:
            if college_code not in college_id_cache:
                college_name = str(row.get("college_name", "")).strip()
                # Check BEFORE get_or_create to determine if this is truly new
                from models import College
                was_already_in_db = College.query.filter_by(college_code=college_code).first() is not None
                cid = get_or_create_college(college_code, college_name)
                if cid is None:
                    errors.append(
                        f"College code '{college_code}' could not be resolved "
                        f"and auto-creation failed"
                    )
                else:
                    # Count only BRAND NEW colleges (ones not previously in DB)
                    if not was_already_in_db and college_code not in _newly_healed_colleges:
                        _newly_healed_colleges.add(college_code)
                        result.healed_colleges += 1
                        logger.info(f"[Validation] Healing tracked: +1 college (id={cid}, code={college_code})")
                    college_id_cache[college_code] = cid
            cid = college_id_cache.get(college_code)
            if cid is not None:
                row["college_id"] = cid
            else:
                errors.append(f"College code '{college_code}' could not be resolved")

        # ── 2. Branch / course name ──────────────────────────────────────────
        raw_branch = str(
            row.get("course_name", row.get("branch", ""))
        ).strip()
        if not raw_branch:
            errors.append("Missing course/branch name")
        else:
            canonical = normalize_branch(raw_branch)
            if canonical not in branch_id_cache:
                # Check BEFORE get_or_create to determine if this is truly new
                from models import Branch
                branch_was_in_db = Branch.query.filter_by(branch_name=canonical).first() is not None
                bid = get_or_create_branch(canonical)
                branch_id_cache[canonical] = bid
                # Count only BRAND NEW branches
                if bid is not None and not branch_was_in_db and canonical not in _newly_healed_branches:
                    _newly_healed_branches.add(canonical)
                    result.healed_branches += 1
                    logger.info(f"[Validation] Healing tracked: +1 branch (id={bid}, name={canonical})")
            bid = branch_id_cache.get(canonical)
            if bid is None:
                errors.append(f"Branch '{raw_branch}' could not be resolved")
            else:
                row["branch_id"] = bid
                row["branch_name_resolved"] = canonical

        # ── 3. Category ──────────────────────────────────────────────────────
        category = str(row.get("category", "")).strip().upper()
        if not category:
            errors.append("Missing category")
        else:
            row["category"] = category

        # ── 4. Seat type (defaults to category) ─────────────────────────────
        seat_type = str(
            row.get("seat_type", row.get("category", ""))
        ).strip().upper()
        row["seat_type"] = seat_type or category

        # ── 5. Percentile ────────────────────────────────────────────────────
        pctl = row.get("percentile")
        if pctl is not None:
            try:
                pctl = float(pctl)
                if pctl < 0 or pctl > 100:
                    errors.append(f"Percentile {pctl} is outside 0-100")
                else:
                    row["percentile"] = round(pctl, 2)
            except (ValueError, TypeError):
                errors.append(f"Non-numeric percentile: {pctl!r}")

        # ── 6. Rank coercion ─────────────────────────────────────────────────
        rank = row.get("rank")
        if rank is not None:
            try:
                row["rank"] = int(rank)
            except (ValueError, TypeError):
                row["rank"] = None

        # ── Reject on data-quality errors ────────────────────────────────────
        if errors:
            result.invalid_rows.append((row, "; ".join(errors)))
            result.invalid += 1
            continue

        # ── 7. Duplicate detection ───────────────────────────────────────────
        dup_key = (
            admission_type_id,
            row["college_id"],
            row["branch_id"],
            academic_year_id,
            cap_round_id,
            row["category"],
            row["seat_type"],
        )

        if dup_key in existing_keys or dup_key in seen_this_batch:
            result.duplicate_rows.append(
                (row, "Duplicate record already exists in database")
            )
            result.duplicates += 1
            continue

        # ── 8. Pass — attach IDs and add to valid list ───────────────────────
        row["admission_type_id"] = admission_type_id
        row["academic_year_id"]  = academic_year_id
        row["cap_round_id"]      = cap_round_id

        result.valid_rows.append(row)
        result.valid += 1
        seen_this_batch.add(dup_key)

    logger.info(
        f"[Validation] total={result.total} valid={result.valid} "
        f"invalid={result.invalid} duplicates={result.duplicates}"
    )
    return result
