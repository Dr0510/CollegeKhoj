"""Trend calculation service for cutoff history.

Computes 3-year trends, branch popularity, and college ranking trends
from the CollegeCutoff table (unified single source of truth).
Stores computed trend results in the college_trends table.
"""
import logging
from collections import defaultdict
from database import db

logger = logging.getLogger(__name__)


def compute_college_trends(college_code: str = None, limit: int = 10):
    """Compute year-over-year cutoff trends for colleges.

    Uses the unified college_cutoffs table.

    Args:
        college_code: Optional specific college code to filter
        limit: Max results

    Returns:
        list of dicts: { college_code, college_name, branch, category, trends }
    """
    from models import CollegeCutoff

    query = db.session.query(
        CollegeCutoff.college_code,
        CollegeCutoff.college_name,
        CollegeCutoff.course_name,
        CollegeCutoff.category,
        CollegeCutoff.year,
        db.func.avg(CollegeCutoff.percentile).label('avg_cutoff')
    ).filter(
        CollegeCutoff.college_code.isnot(None),
        CollegeCutoff.exam_type == 'MHT-CET',
    )

    if college_code:
        query = query.filter(CollegeCutoff.college_code == college_code)

    query = query.group_by(
        CollegeCutoff.college_code, CollegeCutoff.college_name,
        CollegeCutoff.course_name, CollegeCutoff.category, CollegeCutoff.year
    ).order_by(
        CollegeCutoff.college_code, CollegeCutoff.course_name, CollegeCutoff.category, CollegeCutoff.year
    )

    rows = query.all()

    # Group by college_code + course_name + category
    grouped = defaultdict(lambda: defaultdict(dict))
    college_names = {}

    for row in rows:
        key = (row.college_code, row.course_name, row.category)
        if row.avg_cutoff is not None:
            grouped[key][row.year] = round(float(row.avg_cutoff), 2)
        college_names[row.college_code] = row.college_name or college_names.get(row.college_code, '')

    results = []
    for (code, course, cat), year_data in grouped.items():
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
            'branch': course,
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
    """Rank branches by their average cutoff (higher = more popular).

    Uses the unified college_cutoffs table.
    """
    from models import CollegeCutoff

    query = db.session.query(
        CollegeCutoff.course_name,
        db.func.avg(CollegeCutoff.percentile).label('avg_cutoff'),
        db.func.count(CollegeCutoff.id).label('count')
    ).filter(
        CollegeCutoff.course_name.isnot(None),
        CollegeCutoff.course_name != '',
        CollegeCutoff.exam_type == 'MHT-CET',
    )

    if year:
        query = query.filter(CollegeCutoff.year == year)

    query = query.group_by(CollegeCutoff.course_name).order_by(
        db.desc('avg_cutoff')
    ).limit(top_n)

    return [
        {
            'branch': row.course_name,
            'avg_cutoff': round(float(row.avg_cutoff), 2) if row.avg_cutoff else 0,
            'college_count': row.count,
        }
        for row in query.all()
    ]


def get_safe_moderate_dream(student_percentile: float, category: str,
                            gender: str = 'Gender-Neutral', top_n: int = 20):
    """Classify colleges into Safe / Moderate / Dream based on 3-year trends.

    Uses the unified college_cutoffs table.
    """
    from models import CollegeCutoff, College

    # Get the latest year available
    latest_year_row = db.session.query(
        db.func.max(CollegeCutoff.year)
    ).filter(
        CollegeCutoff.category == category,
        CollegeCutoff.gender == gender,
        CollegeCutoff.exam_type == 'MHT-CET',
    ).scalar()

    if not latest_year_row:
        return {'safe': [], 'moderate': [], 'dream': []}

    latest_year = int(latest_year_row)

    # Get all cutoffs for the latest year
    cutoffs = db.session.query(CollegeCutoff).filter(
        CollegeCutoff.year == latest_year,
        CollegeCutoff.category == category,
        CollegeCutoff.gender == gender,
        CollegeCutoff.exam_type == 'MHT-CET',
    ).all()

    safe, moderate, dream = [], [], []

    for cutoff in cutoffs:
        # Try to find matching college by code or name
        college = College.query.filter(
            College.college.ilike(f'%{cutoff.college_name}%')
        ).first()

        if not college:
            continue

        percentile_val = float(cutoff.percentile) if cutoff.percentile else 0

        entry = {
            'college_code': cutoff.college_code,
            'college_name': college.college,
            'location': college.location,
            'branch': cutoff.course_name or cutoff.branch or college.branch,
            'cutoff': percentile_val,
            'nirf_rank': college.nirf_rank,
        }

        diff = student_percentile - percentile_val

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


def store_trend_results():
    """Compute all trends and store/update them in the college_trends table.

    This is called after every import commit to keep trend data current.
    Reads data from college_cutoffs table and writes to college_trends table.
    """
    from models import CollegeTrend
    from datetime import datetime

    try:
        # Compute trends (large limit to capture all data)
        trends = compute_college_trends(limit=5000)

        # Clear existing stored trends before refreshing
        CollegeTrend.query.delete()

        # Bulk insert new trend results
        trend_objects = []
        for t in trends:
            trend_objects.append(CollegeTrend(
                college_code=t['college_code'],
                college_name=t.get('college_name', ''),
                branch=t.get('branch', ''),
                category=t.get('category', ''),
                trend_data={str(k): v for k, v in t.get('trends', {}).items()},
                direction=t.get('direction', 'stable'),
                difference=t.get('difference', 0),
                computed_at=datetime.utcnow(),
            ))

        if trend_objects:
            db.session.bulk_save_objects(trend_objects)
            db.session.commit()
            logger.info(f"Stored {len(trend_objects)} trend results in college_trends table")
        else:
            db.session.commit()
            logger.info("No trend results to store")

        # Also compute branch popularity
        pop = compute_branch_popularity()
        logger.info(f"Trends calculated: {len(trends)} college trends, {len(pop)} branch trends")

        return {'trends_count': len(trends), 'branches_count': len(pop)}

    except Exception as e:
        db.session.rollback()
        logger.error(f"Trend storage error: {e}")
        return {'error': str(e)}


def recalculate_all_trends():
    """Legacy wrapper — recalculate and log all trends (called after import commit).

    Now delegates to store_trend_results() for persistent storage.
    """
    return store_trend_results()