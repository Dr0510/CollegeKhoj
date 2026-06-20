"""
DSE / MHT CET CAP Round PDF Extraction Service.

The DSE PDFs are NOT table-based. They use a text-block layout with
per-college-per-course blocks. pdfplumber.extract_tables() returns empty.

This engine works exclusively via text extraction + regex parsing.

PDF Block Structure (confirmed from actual DSE PDFs):

    1002 Government College of Engineering, Amravati (Government Autonomous)
    Choice Code : 100219110 Course Name : Civil Engineering
    GOPEN    GST    GOBC    LOPEN    LSC    LSEBC    EWS
    1282     28609  1927    1147     2355   5376     4977
    Stage-I
    (92.74%) (76.79%) (91.79%) (93.00%) (91.26%) (88.53%) (88.84%)

Extracts: year, round, college_code, college_name, course_code, course_name,
          category, rank, percentile
"""
import os
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Filename patterns ────────────────────────────────────────────────────────
FILENAME_PATTERNS = [
    re.compile(r'(?:cap|round)\s*[:_-]?\s*(\d)\s*(?:of)?\s*(\d{4})', re.I),
    re.compile(r'(\d{4})\s*[_-]\s*(?:round|r)\s*(\d)', re.I),
    re.compile(r'round[_-]?(\d)[_-]?(\d{4})', re.I),
]

# ── College code / name ──
COLLEGE_RE = re.compile(r'^(\d{4})\s+(.+)$')

# ── Choice code + Course name ──
CHOICE_COURSE_RE = re.compile(
    r'Choice\s*Code\s*:\s*(\d+)\s+Course\s*Name\s*:\s*(.+)',
    re.I
)

# ── Percentile inside parentheses, e.g. (94.00%) ──
PCT_RE = re.compile(r'\((\d+\.?\d*)%\)')

# ── Known category prefixes ──
CATEGORY_LABELS = [
    'GOPEN', 'GSC', 'GST', 'GOBC', 'GSEBC', 'GNT', 'GTFWS',
    'LOPEN', 'LSC', 'LST', 'LOBC', 'LSEBC', 'LNT', 'LTFWS',
    'OPEN', 'OBC', 'SC', 'ST', 'NT', 'EWS', 'SEBC',
    'DEF', 'PWD', 'MI', 'R-OBC', 'R-SC', 'R-ST',
    'PWDR-OBC', 'PWDR-SC', 'PWDR-ST',
    'GNTA', 'GNTC', 'GNTB', 'GNTD',
    'LNTA', 'LNTC', 'LNTB', 'LNTD',
    'PWD-OBC', 'PWD-SC', 'PWD-ST',
    'DEFENCE', 'MI',
]

# ── Branch normalisation ──
BRANCH_MAP = {
    'computer': 'Computer Engineering',
    'computer science': 'Computer Engineering',
    'computer science and engineering': 'Computer Engineering',
    'cse': 'Computer Engineering',
    'cs': 'Computer Engineering',
    'it': 'Information Technology',
    'information technology': 'Information Technology',
    'mechanical': 'Mechanical Engineering',
    'mechanical engineering': 'Mechanical Engineering',
    'mech': 'Mechanical Engineering',
    'civil': 'Civil Engineering',
    'civil engineering': 'Civil Engineering',
    'electrical': 'Electrical Engineering',
    'electrical engineering': 'Electrical Engineering',
    'electronics': 'Electronics Engineering',
    'electronics and telecommunication': 'Electronics & Telecommunication Engg',
    'electronics and telecommunication engg': 'Electronics & Telecommunication Engg',
    'entc': 'Electronics & Telecommunication Engg',
    'e&tc': 'Electronics & Telecommunication Engg',
    'chemical': 'Chemical Engineering',
    'chemical engineering': 'Chemical Engineering',
    'ai': 'Artificial Intelligence & Data Science',
    'ai & ds': 'Artificial Intelligence & Data Science',
    'aids': 'Artificial Intelligence & Data Science',
    'data science': 'Artificial Intelligence & Data Science',
    'ds': 'Artificial Intelligence & Data Science',
    'instrumentation': 'Instrumentation Engineering',
    'instrumentation engineering': 'Instrumentation Engineering',
    'food technology': 'Food Technology',
    'textile': 'Textile Engineering',
    'production': 'Production Engineering',
    'production engineering': 'Production Engineering',
    '5g': '5G Technology',
    '5g technology': '5G Technology',
    'plastic and polymer engineering': 'Plastic and Polymer Engineering',
    'plastic & polymer engineering': 'Plastic and Polymer Engineering',
    'polymer engineering': 'Plastic and Polymer Engineering',
    'agricultural engineering': 'Agricultural Engineering',
    'agri engineering': 'Agricultural Engineering',
    'agriculture engineering': 'Agricultural Engineering',
    'artificial intelligence and machine learning': 'Artificial Intelligence and Machine Learning',
    'ai & ml': 'Artificial Intelligence and Machine Learning',
    'ai and ml': 'Artificial Intelligence and Machine Learning',
    'aiml': 'Artificial Intelligence and Machine Learning',
    'artificial intelligence & machine learning': 'Artificial Intelligence and Machine Learning',
    'ai and ds': 'Artificial Intelligence & Data Science',
    'ai & ds': 'Artificial Intelligence & Data Science',
    'aids': 'Artificial Intelligence & Data Science',
    'data science': 'Artificial Intelligence & Data Science',
    'electronics and telecommunication engineering': 'Electronics & Telecommunication Engineering',
    'electronics & telecommunication': 'Electronics & Telecommunication Engineering',
    'electronics and telecommunication': 'Electronics & Telecommunication Engineering',
    'entc': 'Electronics & Telecommunication Engineering',
    'e&tc': 'Electronics & Telecommunication Engineering',
    'e and tc': 'Electronics & Telecommunication Engineering',
    'computer science and engineering(artificial intelligence and machine learning)': 'Artificial Intelligence and Machine Learning',
    'computer science and engineering (artificial intelligence and machine learning)': 'Artificial Intelligence and Machine Learning',
    'cse (ai & ml)': 'Artificial Intelligence and Machine Learning',
    'cse (aiml)': 'Artificial Intelligence and Machine Learning',
}


def _normalise_branch(raw: str) -> str:
    """Map a raw branch string to a standard form."""
    b = raw.strip().lower()
    for key, val in BRANCH_MAP.items():
        if key in b:
            return val
    return raw.strip().title() if raw.strip().isupper() else raw.strip()


# ── Year / round detection ───────────────────────────────────────────────────

def detect_year_round_from_filename(filename: str) -> tuple:
    """Auto-detect year and round from the PDF filename."""
    base = os.path.splitext(os.path.basename(filename))[0]
    for pattern in FILENAME_PATTERNS:
        match = pattern.search(base)
        if match:
            groups = match.groups()
            if len(groups) == 2:
                a, b = groups
                try:
                    num_a, num_b = int(a), int(b)
                except ValueError:
                    continue
                if 2020 <= num_a <= 2030 and 1 <= num_b <= 5:
                    return num_a, num_b
                if 2020 <= num_b <= 2030 and 1 <= num_a <= 5:
                    return num_b, num_a
        year_match = re.search(r'\b(20[2-9]\d)\b', base)
        round_match = re.search(r'(?:round|r)\s*(\d)', base, re.I)
        if year_match and round_match:
            try:
                yr = int(year_match.group(1))
                rd = int(round_match.group(1))
                if 2020 <= yr <= 2030 and 1 <= rd <= 5:
                    return yr, rd
            except ValueError:
                pass
    return None, None


def detect_year_round_from_pdf_text(text: str) -> tuple:
    """Detect year/round from PDF text content."""
    lines = text.split('\n')
    for line in lines[:50]:
        line_lower = line.lower()
        m = re.search(r'(?:cap\s*)?round\s*[:\s]*(\d)\s*(?:of|\-|–|\(|,)?\s*(20[2-9]\d)', line_lower)
        if m:
            return int(m.group(2)), int(m.group(1))
        m = re.search(r'(20[2-9]\d)\s*(?:round|r)\s*(\d)', line_lower)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None, None


# ── Line-level helpers ───────────────────────────────────────────────────────

def _is_category_line(line: str) -> bool:
    """Return True if the line looks like a DSE category header row."""
    tokens = line.strip().split()
    if len(tokens) < 2:
        return False
    label_count = sum(1 for t in tokens if t.upper() in CATEGORY_LABELS)
    return label_count >= 2


def _is_percentile_line(line: str) -> bool:
    """Return True if the line contains parenthesised percentages."""
    return bool(PCT_RE.search(line))


def _extract_percentiles(line: str) -> list[float]:
    """Extract all (XX.XX%) values from a line, preserving order."""
    return [float(m) for m in PCT_RE.findall(line)]


def _extract_categories(line: str) -> list[str]:
    """Split a category header line into individual category labels."""
    tokens = line.strip().split()
    return [t for t in tokens if t.upper() in CATEGORY_LABELS]


def _is_rank_line(line: str) -> bool:
    """Return True if line is just numbers (the rank numbers row)."""
    stripped = line.strip()
    if not stripped or stripped.startswith('('):
        return False
    tokens = stripped.split()
    if len(tokens) < 2:
        return False
    numeric = sum(1 for t in tokens if t.replace(',', '').isdigit())
    return numeric >= 2


def _extract_ranks(line: str) -> list[int]:
    """Extract numeric rank values from a line, preserving order."""
    tokens = line.strip().split()
    ranks = []
    for t in tokens:
        cleaned = t.replace(',', '')
        if cleaned.isdigit():
            ranks.append(int(cleaned))
    return ranks


# ── Per-page text-block parser ──────────────────────────────────────────────

def parse_page_text(page_text: str, page_num: int, year: int, round_number: int) -> tuple[list[dict], dict]:
    """Parse a single page of DSE PDF text into cutoff records.

    Args:
        page_text: The raw text extracted from one PDF page.
        page_num:  1-based page number (for logging).
        year:      Detected year.
        round_number: Detected round number.

    Returns:
        (records, debug_info)
        records: list of dicts with keys:
            college_code, college_name, course_code, course_name,
            category, rank, percentile, year, round
        debug_info: dict with page-level diagnostic information.
    """
    lines = page_text.split('\n')
    records: list[dict] = []
    debug = {
        'page': page_num,
        'college_found': False,
        'course_found': 0,
        'categories_found': 0,
        'records_generated': 0,
        'blocks_found': 0,
    }

    current_college_code = None
    current_college_name = None
    current_course_code = None
    current_course_name = None
    current_categories = []
    current_ranks = []
    current_percentiles = []
    block_state = 'IDLE'  # IDLE → COLLEGE → COURSE → CATEGORIES → RANKS → PCT → IDLE
    page_blocks = 0
    seen_ranks = False
    seen_pcts = False

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        # ── Skip empty / footer lines ──
        if not line:
            # If we were processing data, flush if we have complete data
            if block_state in ('RANKS', 'PCT') and current_categories and current_percentiles:
                _emit_records_new(records, current_college_code, current_college_name,
                                  current_course_code, current_course_name,
                                  current_categories, current_ranks, current_percentiles,
                                  year, round_number)
                page_blocks += 1
            block_state = 'IDLE'
            current_categories = []
            current_ranks = []
            current_percentiles = []
            seen_ranks = False
            seen_pcts = False
            i += 1
            continue

        # Skip footer lines
        if line.startswith('L - Ladies') or line.startswith('STATE CET CELL') or \
           'Provisional cutoff List' in line or 'GOVERNMENT OF MAHARASHTRA' in line or \
           line.startswith('Address') or 'Page' in line or \
           line.startswith('Provisional cutoff'):
            i += 1
            continue

        # ── Line type detection ──

        # College line: starts with 4-digit code
        college_match = COLLEGE_RE.match(line)
        if college_match and not line.startswith('Choice'):
            # Flush previous block if any
            if block_state in ('RANKS', 'PCT') and current_categories and current_percentiles:
                _emit_records_new(records, current_college_code, current_college_name,
                                  current_course_code, current_course_name,
                                  current_categories, current_ranks, current_percentiles,
                                  year, round_number)
                page_blocks += 1
                current_categories = []
                current_ranks = []
                current_percentiles = []
                seen_ranks = False
                seen_pcts = False

            current_college_code = college_match.group(1)
            current_college_name = college_match.group(2).strip()
            debug['college_found'] = True
            block_state = 'COLLEGE'
            i += 1
            continue

        # Choice Code / Course Name line
        choice_match = CHOICE_COURSE_RE.search(line)
        if choice_match and block_state in ('COLLEGE', 'IDLE', 'COURSE'):
            current_course_code = choice_match.group(1)
            current_course_name = _normalise_branch(choice_match.group(2))
            debug['course_found'] += 1
            block_state = 'COURSE'
            # Check if categories also on same line
            if _is_category_line(line):
                current_categories = _extract_categories(line)
                debug['categories_found'] += len(current_categories)
                block_state = 'CATEGORIES'
            i += 1
            continue

        # Category header line
        if _is_category_line(line) and block_state in ('COURSE', 'COLLEGE', 'IDLE', 'CATEGORIES'):
            current_categories = _extract_categories(line)
            debug['categories_found'] += len(current_categories)
            block_state = 'CATEGORIES'
            i += 1
            continue

        # Rank numbers line (skip it but store)
        if _is_rank_line(line) and block_state in ('CATEGORIES', 'RANKS'):
            ranks = _extract_ranks(line)
            current_ranks = ranks
            seen_ranks = True
            block_state = 'RANKS'
            i += 1
            continue

        # Stage-I / Stage-II / Round markers
        if line.startswith('Stage-') or line.startswith('Round'):
            # If ranks were found and we're now seeing stage, transition to waiting for pcts
            if block_state in ('RANKS', 'CATEGORIES'):
                block_state = 'WAITING_PCT'
            i += 1
            continue

        # Percentile line
        if _is_percentile_line(line) and block_state in ('RANKS', 'CATEGORIES', 'WAITING_PCT', 'PCT'):
            current_percentiles = _extract_percentiles(line)
            seen_pcts = True
            block_state = 'PCT'
            # If we have categories and percentiles, this block is complete
            if current_categories and current_percentiles:
                _emit_records_new(records, current_college_code, current_college_name,
                                  current_course_code, current_course_name,
                                  current_categories, current_ranks, current_percentiles,
                                  year, round_number)
                page_blocks += 1
                debug['records_generated'] = len(records)
                current_categories = []
                current_ranks = []
                current_percentiles = []
                seen_ranks = False
                seen_pcts = False
                block_state = 'IDLE'
            i += 1
            continue

        # Fallback: if we see another college while in a block, flush
        if block_state in ('COURSE', 'CATEGORIES', 'RANKS', 'WAITING_PCT', 'PCT') and not line.startswith('('):
            block_state = 'IDLE'
            current_categories = []
            current_ranks = []
            current_percentiles = []
            seen_ranks = False
            seen_pcts = False

        i += 1

    # Flush last block at end of page
    if block_state in ('RANKS', 'PCT', 'WAITING_PCT') and current_categories and current_percentiles:
        _emit_records_new(records, current_college_code, current_college_name,
                          current_course_code, current_course_name,
                          current_categories, current_ranks, current_percentiles,
                          year, round_number)
        page_blocks += 1

    debug['blocks_found'] = page_blocks
    debug['records_generated'] = len(records)

    logger.info(
        f"Page {page_num}: college_found={debug['college_found']} "
        f"course_found={debug['course_found']} "
        f"categories_found={debug['categories_found']} "
        f"blocks_found={page_blocks} "
        f"records_generated={len(records)}"
    )

    return records, debug


def _emit_records_new(records: list, college_code: str, college_name: str,
                      course_code: str, course_name: str,
                      categories: list[str], ranks: list[int], percentiles: list[float],
                      year: int, round_number: int):
    """Map categories to percentiles (and ranks) by position and append records.

    Each record contains: college_code, college_name, course_code, course_name,
    category, rank, percentile, year, round.
    """
    n = min(len(categories), len(percentiles))
    if n == 0:
        return

    # Normalize ranks list length
    if len(ranks) < n:
        ranks = ranks + [None] * (n - len(ranks))

    for idx in range(n):
        cat = categories[idx]
        pctl = percentiles[idx]
        rnk = ranks[idx] if idx < len(ranks) else None

        if pctl < 0 or pctl > 100:
            continue

        records.append({
            'college_code': college_code,
            'college_name': college_name,
            'course_code': course_code,
            'course_name': course_name,
            'category': cat.upper(),
            'rank': rnk,
            'percentile': round(pctl, 2),
            'year': year,
            'round': round_number,
        })


# ── Full-page text extraction ───────────────────────────────────────────────

def extract_page_texts(filepath: str) -> list[str]:
    """Extract text from each page of a PDF.

    Returns:
        List of text strings, one per page.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed")
        return []

    texts = []
    with pdfplumber.open(filepath) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if text and text.strip():
                texts.append(text.strip())
            else:
                logger.warning(f"Page {page_num}: no extractable text")
                texts.append('')
    return texts


def extract_raw_text(filepath: str) -> str:
    """Extract all raw text from a PDF as a single string."""
    texts = extract_page_texts(filepath)
    return '\n'.join(texts)


# ── Old fallback (legacy) ────────────────────────────────────────────────────

def _old_text_parser(text: str, year: int, round_number: int) -> list:
    """Legacy regex parser kept as last-resort fallback.

    Yields records with the new field names (course_code, course_name).
    """
    results = []
    lines = text.split('\n')
    cat_order = ['OPEN', 'OBC', 'SC', 'ST', 'NT', 'EWS']

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        code_match = re.match(r'^(\d{4})\s+(.+)$', line)
        if code_match:
            code = code_match.group(1)
            rest = code_match.group(2)
            data_block = rest
            j = i + 1
            while j < len(lines) and j < i + 3:
                next_line = lines[j].strip()
                if next_line and re.match(r'^\d', next_line):
                    break
                if next_line:
                    data_block += ' ' + next_line
                j += 1
            parts = re.split(r'\s{2,}', data_block)
            if len(parts) < 3:
                i += 1
                continue
            name = parts[0].strip()
            branch_raw = parts[1].strip() if len(parts) > 1 else ''
            branch = _normalise_branch(branch_raw)
            for cat_idx, cat in enumerate(cat_order):
                val_idx = 2 + cat_idx
                if val_idx < len(parts):
                    try:
                        pctl = float(parts[val_idx].replace(',', '').replace('*', ''))
                    except ValueError:
                        continue
                    if 0 <= pctl <= 100:
                        results.append({
                            'college_code': code,
                            'college_name': name,
                            'course_code': f'{code}000',
                            'course_name': branch,
                            'category': cat,
                            'rank': None,
                            'percentile': pctl,
                            'year': year,
                            'round': round_number,
                        })
        i += 1
    return results


# ── Main entry point ─────────────────────────────────────────────────────────

def extract_pdf(filepath: str, filename: str = '') -> dict:
    """Main entry point: extract cutoff data from a DSE / MHT CET PDF.

    Strategy (text-first, no table dependency):

    1. Detect year/round from filename + PDF text.
    2. Extract text per page via ``page.extract_text()``.
    3. Run the DSE text-block parser (``parse_page_text``) on each page.
    4. If zero records after DSE parser, try the old regex fallback.
    5. If still zero records, return an error.

    Args:
        filepath: Absolute path to the PDF file.
        filename: Original filename (for year/round detection).

    Returns:
        dict with keys:
            rows: list of parsed row dicts
            year: detected year or None
            round: detected round or None
            method: extraction method used
            confidence: confidence score (0-100)
            total_pages: total pages in PDF
            page_debug: list of per-page debug dicts
            error: error message if extraction failed
    """
    # 1. Detect year and round
    year, round_number = detect_year_round_from_filename(filename)
    logger.info(f"Filename detection: year={year}, round={round_number}")

    # 2. Extract raw text
    page_texts = extract_page_texts(filepath)
    raw_text = '\n'.join(page_texts)
    total_pages = len(page_texts)

    if not year or not round_number:
        yr2, rd2 = detect_year_round_from_pdf_text(raw_text)
        year = year or yr2
        round_number = round_number or rd2

    if not year:
        year = 2025
    if not round_number:
        round_number = 1

    # 3. Run DSE text-block parser on every page
    all_rows: list[dict] = []
    page_debug = []
    total_errors = 0

    for page_num, page_text in enumerate(page_texts, start=1):
        if not page_text.strip():
            page_debug.append({
                'page': page_num,
                'error': 'No text on page',
                'records_generated': 0,
            })
            total_errors += 1
            continue

        rows_page, debug = parse_page_text(page_text, page_num, year, round_number)
        all_rows.extend(rows_page)
        page_debug.append(debug)

    # 4. If DSE parser returned zero, try old fallback
    method = 'dse_text_parser'
    if not all_rows:
        logger.warning("DSE text parser returned 0 rows — trying regex fallback")
        fallback_rows = _old_text_parser(raw_text, year, round_number)
        if fallback_rows:
            all_rows = fallback_rows
            method = 'regex_fallback'
            logger.info(f"Regex fallback extracted {len(all_rows)} rows")

    # 5. Calculate confidence
    total_pages_with_text = sum(1 for pt in page_texts if pt.strip())
    if total_pages_with_text > 0:
        pages_with_records = sum(1 for d in page_debug if d.get('records_generated', 0) > 0)
        confidence = (pages_with_records / total_pages_with_text) * 100
    else:
        confidence = 0.0

    # 6. Fill in year/round for all rows
    for row in all_rows:
        row['year'] = row.get('year') or year
        row['round'] = row.get('round') or round_number

    logger.info(
        f"Extraction complete: {len(all_rows)} rows, "
        f"method={method}, confidence={confidence:.1f}%, "
        f"pages={total_pages}"
    )

    result = {
        'rows': all_rows,
        'year': year,
        'round': round_number,
        'method': method,
        'confidence': round(confidence, 1),
        'total_pages': total_pages,
        'page_debug': page_debug,
    }

    if not all_rows:
        result['error'] = (
            f"Zero records extracted from {total_pages} pages. "
            f"First 500 chars: {raw_text[:500]}"
        )
        logger.error(result['error'])

    return result