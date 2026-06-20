"""
Self-Healing Master Data Service for CollegeKhoj Bulk Import.

When the PDF contains a college code / college name / branch name that does
not yet exist in the master tables, this service creates the missing rows
instead of rejecting the cutoff record.

OPTIMIZED FOR BULK IMPORTS:
- All existing records are preloaded into memory dicts before validation starts
- Zero DB queries in the hot path for existing colleges/branches
- Only NEW records trigger a DB INSERT

Usage:
    from admin.self_healing import get_or_create_college, get_or_create_branch
    from admin.self_healing import bulk_preload_colleges, bulk_preload_branches
    
    # Call ONCE before validation loop:
    college_map = bulk_preload_colleges()
    branch_map = bulk_preload_branches()
    
    # Inside loop, use cache for O(1) lookup:
    college_id = college_map.get(college_code)
"""
import logging
import re
from typing import Optional, Dict, Set

from database import db

logger = logging.getLogger(__name__)

# Module-level caches — populated ONCE per import run
_college_cache: Dict[str, Optional[int]] = {}
_branch_exact_cache: Dict[str, Optional[int]] = {}
_branch_code_counter: Dict[str, int] = {}  # branch_code -> next_suffix


def _slugify(text: str, max_len: int = 20) -> str:
    """Convert text to a short uppercase code suitable for a DB code column."""
    clean = re.sub(r"[^A-Za-z0-9]+", "_", text).upper().strip("_")
    return clean[:max_len]


# ---------------------------------------------------------------------------
# Bulk preloaders (call ONCE before validation loop)
# ---------------------------------------------------------------------------

def bulk_preload_colleges() -> Dict[str, Optional[int]]:
    """Load all colleges into memory once: college_code -> id."""
    from models import College
    if not _college_cache:
        _college_cache.update({
            c.college_code: c.id for c in College.query.with_entities(
                College.college_code, College.id
            ).all()
        })
        logger.info(f"[Preload] Loaded {len(_college_cache)} colleges into cache")
    return _college_cache


def bulk_preload_branches() -> Dict[str, Optional[int]]:
    """Load all branches into memory once: branch_name.lower() -> id."""
    from models import Branch
    if not _branch_exact_cache:
        _branch_exact_cache.update({
            b.branch_name.lower(): b.id for b in Branch.query.with_entities(
                Branch.branch_name, Branch.id
            ).all()
        })
        logger.info(f"[Preload] Loaded {len(_branch_exact_cache)} branches into cache")
    return _branch_exact_cache


def reset_caches():
    """Clear caches between separate import runs."""
    _college_cache.clear()
    _branch_exact_cache.clear()
    _branch_code_counter.clear()


# ---------------------------------------------------------------------------
# College self-healing
# ---------------------------------------------------------------------------

def get_or_create_college(
    college_code: str,
    college_name: str,
) -> Optional[int]:
    """Return the id of the college matching *college_code*.
    
    FAULT-TOLERANT: Never raises.
    - If code is empty, return None
    - If DB insert fails, log and return None
    """
    from models import College

    if not college_code:
        logger.warning("[SelfHeal] get_or_create_college called with empty code")
        return None
    
    college_code = str(college_code).strip()
    college_name = (college_name or f"College {college_code}").strip()
    
    # Fast path: cache hit (no DB query)
    cached = _college_cache.get(college_code)
    if cached is not None:
        return cached
    
    # Slow path: NOT in cache -> must create new college
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
        db.session.flush()  # Populate id without committing
        _college_cache[college_code] = new_college.id
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
    
    FAULT-TOLERANT: Never raises.
    """
    from models import Branch
    from admin.branch_normalizer import normalize_branch, canonical_branch_code

    if not branch_name:
        logger.warning("[SelfHeal] get_or_create_branch called with empty name")
        return None

    # Normalize through alias table first
    canonical = normalize_branch(branch_name)
    canonical_lower = canonical.lower()

    # 1. Fast path: exact match in preloaded cache (O(1), NO DB query)
    branch_id = _branch_exact_cache.get(canonical_lower)
    if branch_id is not None:
        return branch_id

    # 2. Slow path: NOT in cache -> must create new branch
    branch_code = canonical_branch_code(canonical)
    
    # Handle code collisions
    if branch_code in _branch_code_counter:
        branch_code = f"{branch_code}_{_branch_code_counter[branch_code]}"
    _branch_code_counter[branch_code] = _branch_code_counter.get(branch_code, 2) + 1

    logger.info(
        f"[SelfHeal] Auto-creating branch: code={branch_code!r} name={canonical!r} "
        f"(raw='{branch_name}')"
    )
    try:
        new_branch = Branch(branch_code=branch_code, branch_name=canonical)
        db.session.add(new_branch)
        db.session.flush()
        branch_id = new_branch.id
        _branch_exact_cache[canonical_lower] = branch_id
        logger.info(f"[SelfHeal] Created Branch id={branch_id} code={branch_code!r}")
        return branch_id
    except Exception as e:
        try:
            db.session.begin_nested()
        except Exception:
            pass
        logger.error(f"[SelfHeal] Failed to create branch {canonical!r}: {e}")
        return None