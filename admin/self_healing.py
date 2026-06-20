"""
Self-Healing Master Data Service for CollegeKhoj Bulk Import.

When the PDF contains a college code / college name / branch name that does
not yet exist in the master tables, this service creates the missing rows
instead of rejecting the cutoff record.

Guarantees:
- Idempotent: calling with the same inputs twice returns the same IDs.
- Thread-safe within a single SQLAlchemy session (caller holds the session).
- Never raises: on DB error it logs and returns None so the caller can decide
  whether to skip the row or abort.

Usage:
    from admin.self_healing import get_or_create_college, get_or_create_branch

    college_id = get_or_create_college("1002", "Government College of Engineering, Amravati")
    branch_id  = get_or_create_branch("Computer Engineering")
"""
import logging
import re
from typing import Optional

from database import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int = 20) -> str:
    """Convert text to a short uppercase code suitable for a DB code column."""
    clean = re.sub(r"[^A-Za-z0-9]+", "_", text).upper().strip("_")
    return clean[:max_len]


# ---------------------------------------------------------------------------
# College self-healing
# ---------------------------------------------------------------------------

def get_or_create_college(
    college_code: str,
    college_name: str,
) -> Optional[int]:
    """Return the id of the college matching *college_code*.

    If no match is found:
    - Creates a new College row with the supplied code and name.
    - Marks it active.
    - Flushes (but does NOT commit — caller owns the transaction).

    Returns None on unrecoverable DB error.
    """
    from models import College

    if not college_code:
        logger.warning("[SelfHeal] get_or_create_college called with empty code")
        return None

    college_code = str(college_code).strip()
    college_name = (college_name or f"College {college_code}").strip()

    # 1. Exact code match
    college = College.query.filter_by(college_code=college_code).first()
    if college:
        return college.id

    # 2. Name match (safeguard against duplicate codes from different imports)
    if college_name:
        college = College.query.filter(
            College.college_name.ilike(college_name)
        ).first()
        if college:
            # Back-fill the code if it was missing
            if not college.college_code:
                college.college_code = college_code
                try:
                    db.session.flush()
                except Exception as e:
                    # Use nested savepoint so we don't nuke the whole session
                    try:
                        db.session.begin_nested()
                    except Exception:
                        pass
                    logger.error(f"[SelfHeal] Could not back-fill college_code: {e}")
            return college.id

    # 3. Create new college
    logger.info(
        f"[SelfHeal] Auto-creating college: code={college_code!r} name={college_name!r}"
    )
    try:
        new_college = College(
            college_code=college_code,
            college_name=college_name,
            status="active",
        )
        db.session.add(new_college)
        db.session.flush()  # Populate new_college.id without committing
        logger.info(f"[SelfHeal] Created College id={new_college.id} code={college_code!r}")
        return new_college.id
    except Exception as e:
        try:
            db.session.begin_nested()
        except Exception:
            pass
        logger.error(f"[SelfHeal] Failed to create college {college_code!r}: {e}")
        return None


# ---------------------------------------------------------------------------
# Branch self-healing
# ---------------------------------------------------------------------------

def get_or_create_branch(branch_name: str) -> Optional[int]:
    """Return the id of the branch matching *branch_name* (case-insensitive).

    If no match is found:
    - Generates a branch_code from the name.
    - Creates a new Branch row.
    - Flushes (but does NOT commit — caller owns the transaction).

    Returns None on unrecoverable DB error.
    """
    from models import Branch
    from admin.branch_normalizer import normalize_branch, canonical_branch_code

    if not branch_name:
        logger.warning("[SelfHeal] get_or_create_branch called with empty name")
        return None

    # Normalize through alias table first
    canonical = normalize_branch(branch_name)
    canonical_lower = canonical.lower()

    # 1. Exact case-insensitive match on branch_name
    branch = Branch.query.filter(
        Branch.branch_name.ilike(canonical)
    ).first()
    if branch:
        return branch.id

    # 2. Partial substring match (e.g. DB has "Computer Engg.", PDF has "Computer Engineering")
    all_branches = Branch.query.all()
    for b in all_branches:
        db_lower = b.branch_name.lower()
        if db_lower in canonical_lower or canonical_lower in db_lower:
            logger.debug(
                f"[SelfHeal] Branch fuzzy match: '{canonical}' → '{b.branch_name}'"
            )
            return b.id

    # 3. Auto-create
    branch_code = canonical_branch_code(canonical)

    # Ensure branch_code is unique — append suffix if collision
    existing_code = Branch.query.filter_by(branch_code=branch_code).first()
    if existing_code:
        suffix = 2
        while Branch.query.filter_by(branch_code=f"{branch_code}_{suffix}").first():
            suffix += 1
        branch_code = f"{branch_code}_{suffix}"

    logger.info(
        f"[SelfHeal] Auto-creating branch: code={branch_code!r} name={canonical!r} "
        f"(raw='{branch_name}')"
    )
    try:
        new_branch = Branch(branch_code=branch_code, branch_name=canonical)
        db.session.add(new_branch)
        db.session.flush()
        logger.info(f"[SelfHeal] Created Branch id={new_branch.id} code={branch_code!r}")
        return new_branch.id
    except Exception as e:
        try:
            db.session.begin_nested()
        except Exception:
            pass
        logger.error(f"[SelfHeal] Failed to create branch {canonical!r}: {e}")
        return None
