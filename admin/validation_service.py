"""Validation service for PDF-imported cutoff data.

Runs row-by-row validation and returns a structured report.
Validates against the unified college_cutoffs table.
"""
import logging
import re
from database import db

logger = logging.getLogger(__name__)

# Known MHT CET institute code pattern (4-digit numeric)
COLLEGE_CODE_RE = re.compile(r'^\d{4}$')

# Valid category list
VALID_CATEGORIES = {'Open', 'OBC', 'SC', 'ST', 'NT', 'EWS', 'NT1', 'NT2', 'NT3', 'TFWS',
                    'GOPEN', 'GSC', 'GST', 'GOBC', 'GSEBC', 'GNT', 'GTFWS',
                    'LOPEN', 'LSC', 'LST', 'LOBC', 'LSEBC', 'LNT', 'LTFWS'}

# Valid gender values
VALID_GENDERS = {'Male', 'Female', 'Other', 'Gender-Neutral'}

# Valid exam types
VALID_EXAM_TYPES = {'MHT-CET', 'POLYTECHNIC'}


class ValidationResult:
    """Container for validation results."""

    def __init__(self):
        self.valid_rows = []
        self.rejected_rows = []  # list of (row_dict, reason)
        self.duplicate_rows = []  # list of (row_dict, matched_id)
        self.total_rows = 0
        self.summary = {
            'total': 0,
            'valid': 0,
            'rejected': 0,
            'duplicates': 0,
            'errors': [],
        }

    @property
    def has_errors(self):
        return len(self.rejected_rows) > 0 or len(self.duplicate_rows) > 0

    def to_dict(self):
        return {
            'total': self.summary['total'],
            'valid': self.summary['valid'],
            'rejected': self.summary['rejected'],
            'duplicates': self.summary['duplicates'],
            'errors': self.summary['errors'],
            'valid_rows': self.valid_rows,
            'rejected_rows': [{'row': r[0], 'reason': r[1]} for r in self.rejected_rows],
            'duplicate_rows': [{'row': d[0], 'matched_id': d[1]} for d in self.duplicate_rows],
        }


def validate_row(row, seen_keys):
    """Validate a single parsed row. Returns (is_valid, reason_if_invalid)."""
    errors = []

    # Required fields
    if not row.get('college_code'):
        errors.append('Missing college code')

    if not row.get('course_name') and not row.get('branch'):
        errors.append('Missing course/branch name')

    if not row.get('category'):
        errors.append('Missing category')
    elif row['category'] not in VALID_CATEGORIES:
        errors.append(f"Invalid category '{row['category']}'")

    if not row.get('gender'):
        errors.append('Missing gender')
    elif row['gender'] not in VALID_GENDERS:
        errors.append(f"Invalid gender '{row['gender']}'")

    # Percentile validation
    pctl = row.get('percentile')
    if pctl is None:
        errors.append('Missing percentile')
    elif not isinstance(pctl, (int, float)):
        errors.append(f"Invalid percentile value type: {type(pctl).__name__}")
    elif pctl < 0:
        errors.append(f"Negative percentile: {pctl}")
    elif pctl > 100:
        errors.append(f"Percentile exceeds 100: {pctl}")

    # College code format
    code = row.get('college_code', '')
    if code and not COLLEGE_CODE_RE.match(str(code)):
        errors.append(f"Invalid college code format: {code}")

    # Year
    year = row.get('year')
    if year is None:
        errors.append('Missing year')
    elif not isinstance(year, int) or year < 2020 or year > 2030:
        errors.append(f"Invalid year: {year}")

    # Round
    rnd = row.get('round')
    if rnd is None:
        errors.append('Missing round number')
    elif not isinstance(rnd, int) or rnd < 1 or rnd > 10:
        errors.append(f"Invalid round number: {rnd}")

    # Exam type validation
    exam_type = row.get('exam_type', 'MHT-CET')
    if exam_type not in VALID_EXAM_TYPES:
        errors.append(f"Invalid exam_type: {exam_type}")

    # Duplicate detection
    dup_key = (row.get('year'), row.get('round'),
               row.get('college_code'), row.get('course_code'),
               row.get('category'), row.get('gender'))
    is_duplicate = dup_key in seen_keys

    if errors:
        return False, '; '.join(errors), is_duplicate, dup_key

    return True, None, is_duplicate, dup_key


def validate_all(parsed_rows, year, round_number):
    """Run full validation on a set of parsed rows.

    Args:
        parsed_rows: List of dicts from the PDF extractor
        year: Detected year for the import
        round_number: Detected round for the import

    Returns:
        ValidationResult instance
    """
    from models import CollegeCutoff

    result = ValidationResult()
    result.total_rows = len(parsed_rows)
    result.summary['total'] = len(parsed_rows)

    # Gather existing keys from DB to detect cross-import duplicates
    seen_db_keys = set()
    existing = db.session.query(
        CollegeCutoff.year, CollegeCutoff.round,
        CollegeCutoff.college_code, CollegeCutoff.course_code,
        CollegeCutoff.category, CollegeCutoff.gender
    ).distinct().all()
    for row in existing:
        seen_db_keys.add(tuple(row))

    seen_this_import = set()

    for row in parsed_rows:
        # Fill in year/round from import context if not in row
        if not row.get('year'):
            row['year'] = year
        if not row.get('round'):
            row['round'] = round_number
        if not row.get('gender'):
            row['gender'] = 'Gender-Neutral'
        if not row.get('exam_type'):
            row['exam_type'] = 'MHT-CET'

        is_valid, reason, is_dup, dup_key = validate_row(row, seen_db_keys | seen_this_import)

        if not is_valid:
            result.rejected_rows.append((row, reason))
            result.summary['rejected'] += 1
            result.summary['errors'].append({'row': row, 'reason': reason})
        elif is_dup:
            result.duplicate_rows.append((row, None))
            result.summary['duplicates'] += 1
        else:
            result.valid_rows.append(row)
            result.summary['valid'] += 1
            if dup_key:
                seen_this_import.add(dup_key)

    return result