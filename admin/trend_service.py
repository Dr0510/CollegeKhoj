"""Trend calculation service for cutoff history.

Computes 3-year trends, branch popularity, and college ranking trends
from the CAPCutoff table.
"""
import logging
from collections import defaultdict
from database import db

logger = logging.getLogger(__name__)


def compute_college_trends(college_code: str = None, limit: int = 10):
    """Compute year-over-year cutoff trends for colleges.

    Args:
        college_code: Optional specific college code to filter
        limit: Max results

    Returns:
        list of dicts: { college_code, college_name, branch, category, trends }
    """
    from models import CAPCutoff, College

    query = db.session.query(
        CAPCutoff.college_code,
        CAPCutoff.college_id,
        CAPCutoff.branch,
        CAPCutoff.category,
        CAPCutoff.year,
        db.func.avg(CAPCutoff.cutoff_percentile).label('avg_cutoff')
    ).filter(
        CAPCutoff.college_code.isnot(None),
        CAPCutoff.validation_status == 'validated'
    )

    if college_code:
        query = query.filter(CAPCutoff.college_code == college_code)

    query = query.group_by(
        CAPCutoff.college_code, CAPCutoff.college_id,
        CAPCutoff.branch, CAPCutoff.category, CAPCutoff.year
    ).order_by(
        CAPCutoff.college_code, CAPCutoff.branch, CAPCutoff.category, CAPCutoff.year
    )

    rows = query.all()

    # Group by college_code + branch + category
    grouped = defaultdict(lambda: defaultdict(dict))
    college_names = {}

    for row in rows:
        key = (row.college_code, row.branch, row.category)
        grouped[key][row.year] = round(float(row.avg_cutoff), 2)

        # Get college name
        if row.college_code not in college_names:
            college = db.session.get(College, row.college_id)
            if college:
                college_names[row.college_code] = college.college

    results = []
    for (code, branch, cat), year_data in grouped.items():
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
            'college_code': code,
            'college_name': college_names.get(code, ''),
            'branch': branch,
            'category': cat,
            'trends': trend_data,
            'years': [str(y) for y in sorted_years],
            'difference': diff,
            'direction': direction,
        })

    # Sort by number of years available (more data = higher priority)
    results.sort(key=lambda x: len(x['years']), reverse=True)
    return results[:limit]


def compute_branch_popularity(year: int = None, top_n: int = 10):
    """Rank branches by their average cutoff (higher = more popular)."""
    from models import CAPCutoff

    query = db.session.query(
        CAPCutoff.branch,
        db.func.avg(CAPCutoff.cutoff_percentile).label('avg_cutoff'),
        db.func.count(CAPCutoff.id).label('count')
    ).filter(
        CAPCutoff.branch.isnot(None),
        CAPCutoff.branch != '',
        CAPCutoff.validation_status == 'validated'
    )

    if year:
        query = query.filter(CAPCutoff.year == year)

    query = query.group_by(CAPCutoff.branch).order_by(
        db.desc('avg_cutoff')
    ).limit(top_n)

    return [
        {
            'branch': row.branch,
            'avg_cutoff': round(float(row.avg_cutoff), 2),
            'college_count': row.count,
        }
        for row in query.all()
    ]


def get_safe_moderate_dream(student_percentile: float, category: str,
                            gender: str = 'Gender-Neutral', top_n: int = 20):
    """Classify colleges into Safe / Moderate / Dream based on 3-year trends.

    Uses the latest available cutoff for each college+branch+category.
    """
    from models import CAPCutoff, College

    # Get the latest year available
    latest_year_row = db.session.query(
        db.func.max(CAPCutoff.year)
    ).filter(
        CAPCutoff.validation_status == 'validated',
        CAPCutoff.category == category,
        CAPCutoff.gender == gender,
    ).scalar()

    if not latest_year_row:
        return {'safe': [], 'moderate': [], 'dream': []}

    latest_year = int(latest_year_row)

    # Get all cutoffs for the latest year
    cutoffs = db.session.query(CAPCutoff).filter(
        CAPCutoff.year == latest_year,
        CAPCutoff.category == category,
        CAPCutoff.gender == gender,
        CAPCutoff.validation_status == 'validated',
    ).all()

    safe, moderate, dream = [], [], []

    for cutoff in cutoffs:
        college = db.session.get(College, cutoff.college_id)
        if not college:
            continue

        entry = {
            'college_code': cutoff.college_code,
            'college_name': college.college,
            'location': college.location,
            'branch': cutoff.branch if hasattr(cutoff, 'branch') else college.branch,
            'cutoff': cutoff.cutoff_percentile,
            'nirf_rank': college.nirf_rank,
        }

        diff = student_percentile - cutoff.cutoff_percentile

        if diff >= 2.0:
            safe.append(entry)
        elif diff >= -2.0:
            moderate.append(entry)
        else:
            dream.append(entry)

    # Sort each category by NIRF rank
    for lst in [safe, moderate, dream]:
        lst.sort(key=lambda x: x['nirf_rank'])

    return {
        'safe': safe[:top_n],
        'moderate': moderate[:top_n],
        'dream': dream[:top_n],
        'latest_year': latest_year,
    }


def recalculate_all_trends():
    """Recalculate and log all trends (called after import commit)."""
    from admin.audit import log_action

    try:
        trends = compute_college_trends(limit=1000)
        pop = compute_branch_popularity()
        logger.info(f"Trends calculated: {len(trends)} college trends, {len(pop)} branch trends")
        return {'trends_count': len(trends), 'branches_count': len(pop)}
    except Exception as e:
        logger.error(f"Trend calculation error: {e}")
        return {'error': str(e)}