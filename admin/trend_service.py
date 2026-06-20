"""Trend calculation service for Admin v2.

Computes:
- 3-year cutoff trends per college+branch+category
- Dream/Moderate/Safe college classification
- Branch popularity ranking
- Precomputed analytics stored for fast dashboard loading
"""
import logging
from collections import defaultdict
from typing import List, Dict, Optional
from database import db

logger = logging.getLogger(__name__)


def compute_college_trends(
    admission_type_id: Optional[int] = None,
    college_id: Optional[int] = None,
    branch_id: Optional[int] = None,
    limit: int = 100
) -> List[Dict]:
    """Compute year-over-year cutoff trends for colleges.

    Uses the unified cutoffs table with FK relationships.

    Args:
        admission_type_id: Optional admission type filter
        college_id: Optional college filter
        branch_id: Optional branch filter
        limit: Max results

    Returns:
        list of dicts with trend data
    """
    from models import Cutoff, College, Branch, AcademicYear

    query = db.session.query(
        Cutoff.college_id,
        College.college_name,
        College.college_code,
        Cutoff.branch_id,
        Branch.branch_name,
        Cutoff.category,
        Cutoff.academic_year_id,
        AcademicYear.academic_year,
        db.func.avg(Cutoff.cutoff_percentile).label('avg_cutoff')
    ).join(
        College, Cutoff.college_id == College.id
    ).join(
        Branch, Cutoff.branch_id == Branch.id
    ).join(
        AcademicYear, Cutoff.academic_year_id == AcademicYear.id
    ).filter(
        Cutoff.cutoff_percentile.isnot(None)
    )

    if admission_type_id:
        query = query.filter(Cutoff.admission_type_id == admission_type_id)
    if college_id:
        query = query.filter(Cutoff.college_id == college_id)
    if branch_id:
        query = query.filter(Cutoff.branch_id == branch_id)

    query = query.group_by(
        Cutoff.college_id, College.college_name, College.college_code,
        Cutoff.branch_id, Branch.branch_name,
        Cutoff.category, Cutoff.academic_year_id, AcademicYear.academic_year
    ).order_by(
        Cutoff.college_id, Cutoff.branch_id, Cutoff.category, AcademicYear.academic_year
    )

    rows = query.all()

    # Group by college + branch + category
    grouped = {}
    college_info = {}
    branch_info = {}

    for row in rows:
        key = (row.college_id, row.branch_id, row.category)
        if key not in grouped:
            grouped[key] = {}
        if row.avg_cutoff is not None:
            grouped[key][row.academic_year] = round(float(row.avg_cutoff), 2)
        college_info[row.college_id] = {
            'name': row.college_name,
            'code': row.college_code,
        }
        branch_info[(row.college_id, row.branch_id)] = row.branch_name

    results = []
    for (cid, bid, cat), year_data in grouped.items():
        sorted_years = sorted(year_data.keys())
        if len(sorted_years) < 1:
            continue

        trend_data = {str(y): year_data[y] for y in sorted_years}

        # Calculate direction
        if len(sorted_years) >= 2:
            first = year_data[sorted_years[0]]
            last = year_data[sorted_years[-1]]
            diff = round(last - first, 2)
            if diff > 1.0:
                direction = 'increasing'
            elif diff < -1.0:
                direction = 'decreasing'
            else:
                direction = 'stable'
        else:
            diff = 0
            direction = 'stable'

        results.append({
            'college_id': cid,
            'college_code': college_info.get(cid, {}).get('code', ''),
            'college_name': college_info.get(cid, {}).get('name', ''),
            'branch_id': bid,
            'branch_name': branch_info.get((cid, bid), None),
            'category': cat,
            'trends': trend_data,
            'years': sorted_years,
            'difference': diff,
            'direction': direction,
        })

    results.sort(key=lambda x: len(x['years']), reverse=True)
    return results[:limit]


def get_safe_moderate_dream(
    student_percentile: float,
    category: str = 'OPEN',
    gender: str = 'Gender-Neutral',
    admission_type_id: Optional[int] = None,
    top_n: int = 20
) -> Dict:
    """Classify colleges into Safe/Moderate/Dream based on student percentile.

    Args:
        student_percentile: Student's percentile (0-100)
        category: Student's category (OPEN, OBC, SC, ST, etc.)
        gender: Student's gender
        admission_type_id: Optional admission type filter
        top_n: Max colleges per category

    Returns:
        dict with 'safe', 'moderate', 'dream' lists and 'latest_year'
    """
    from models import Cutoff, College, Branch, AcademicYear

    # Get the latest academic year
    latest_year = AcademicYear.query.order_by(AcademicYear.id.desc()).first()
    if not latest_year:
        return {'safe': [], 'moderate': [], 'dream': [], 'latest_year': None}

    latest_year_id = latest_year.id

    # Get all distinct college+branch combinations from latest year
    query = db.session.query(
        Cutoff.college_id, College.college_name, College.college_code,
        Cutoff.branch_id, Branch.branch_name,
        Cutoff.cutoff_percentile
    ).join(
        College, Cutoff.college_id == College.id
    ).join(
        Branch, Cutoff.branch_id == Branch.id
    ).filter(
        Cutoff.academic_year_id == latest_year_id,
        Cutoff.cutoff_percentile.isnot(None),
        Cutoff.category == category.upper(),
    )

    if admission_type_id:
        query = query.filter(Cutoff.admission_type_id == admission_type_id)

    query = query.order_by(Cutoff.cutoff_percentile.desc())
    rows = query.all()

    safe = []
    moderate = []
    dream = []

    for row in rows:
        pctl = float(row.cutoff_percentile)
        entry = {
            'college_id': row.college_id,
            'college_code': row.college_code,
            'college_name': row.college_name,
            'branch_id': row.branch_id,
            'branch_name': row.branch_name,
            'cutoff_percentile': pctl,
        }

        # Classification:
        # Dream: cutoff is 10+ points above student percentile
        # Moderate: cutoff is within 10 points of student percentile
        # Safe: cutoff is below student percentile
        if pctl > student_percentile + 10:
            dream.append(entry)
        elif pctl > student_percentile:
            moderate.append(entry)
        else:
            safe.append(entry)

    return {
        'safe': safe[:top_n],
        'moderate': moderate[:top_n],
        'dream': dream[:top_n],
        'latest_year': latest_year.academic_year,
    }


def compute_branch_popularity(
    admission_type_id: Optional[int] = None,
    top_n: int = 15
) -> List[Dict]:
    """Compute branch popularity ranking based on cutoff volume and percentiles.

    Args:
        admission_type_id: Optional admission type filter
        top_n: Number of top branches to return

    Returns:
        list of dicts: { branch_id, branch_name, count, avg_percentile }
    """
    from models import Cutoff, Branch, AcademicYear

    query = db.session.query(
        Cutoff.branch_id,
        Branch.branch_name,
        db.func.count(Cutoff.id).label('count'),
        db.func.avg(Cutoff.cutoff_percentile).label('avg_percentile')
    ).join(
        Branch, Cutoff.branch_id == Branch.id
    ).filter(
        Cutoff.cutoff_percentile.isnot(None)
    )

    if admission_type_id:
        query = query.filter(Cutoff.admission_type_id == admission_type_id)

    query = query.group_by(
        Cutoff.branch_id, Branch.branch_name
    ).order_by(
        db.func.count(Cutoff.id).desc()
    )

    results = []
    for row in query.limit(top_n).all():
        results.append({
            'branch_id': row.branch_id,
            'branch_name': row.branch_name,
            'count': row.count,
            'avg_percentile': round(float(row.avg_percentile), 2) if row.avg_percentile else 0,
        })

    return results


def get_dashboard_stats() -> Dict:
    """Get aggregate stats for admin dashboard.

    Returns:
        dict with counts and summary data.
        All values default to 0 on error — never crashes.
    """
    from models import Cutoff, College, Branch, UploadJob, AdmissionType

    total_colleges = 0
    total_branches = 0
    total_cutoffs = 0
    records_by_type = {}
    pending_imports = 0
    failed_imports = 0
    today_uploads = 0
    branch_pop = []

    try:
        total_colleges = College.query.count()
    except Exception as e:
        logger.warning(f"Dashboard stats: College.query.count() failed: {e}")

    try:
        total_branches = Branch.query.count()
    except Exception as e:
        logger.warning(f"Dashboard stats: Branch.query.count() failed: {e}")

    try:
        total_cutoffs = Cutoff.query.count()
    except Exception as e:
        logger.warning(f"Dashboard stats: Cutoff.query.count() failed: {e}")

    try:
        for at in AdmissionType.query.all():
            count = Cutoff.query.filter(Cutoff.admission_type_id == at.id).count()
            if count > 0:
                records_by_type[at.code] = count
    except Exception as e:
        logger.warning(f"Dashboard stats: records_by_type query failed: {e}")

    try:
        pending_imports = UploadJob.query.filter(UploadJob.status == 'PENDING').count()
    except Exception as e:
        logger.warning(f"Dashboard stats: pending_imports query failed: {e}")

    try:
        failed_imports = UploadJob.query.filter(UploadJob.status == 'FAILED').count()
    except Exception as e:
        logger.warning(f"Dashboard stats: failed_imports query failed: {e}")

    try:
        today_uploads = UploadJob.query.filter(
            db.func.date(UploadJob.created_at) == db.func.current_date()
        ).count()
    except Exception as e:
        logger.warning(f"Dashboard stats: today_uploads query failed: {e}")

    try:
        branch_pop = compute_branch_popularity(top_n=10)
    except Exception as e:
        logger.warning(f"Dashboard stats: branch_popularity failed: {e}")

    return {
        'total_colleges': total_colleges,
        'total_branches': total_branches,
        'total_cutoffs': total_cutoffs,
        'records_by_type': records_by_type,
        'pending_imports': pending_imports,
        'failed_imports': failed_imports,
        'today_uploads': today_uploads,
        'popular_branches': branch_pop,
    }
