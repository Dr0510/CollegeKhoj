"""
MHT CET CAP Round PDF Extraction Service.

Primary: pdfplumber for structural table extraction.
Fallback: raw text extraction with regex if tables not detected.
AI fallback (OpenAI) only used as last resort if confidence < 80%.
"""
import os
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Pattern helpers ──────────────────────────────────────────────────────────

# Filename patterns for year/round auto-detection
FILENAME_PATTERNS = [
    re.compile(r'(?:cap|round)\s*[:_-]?\s*(\d)\s*(?:of)?\s*(\d{4})', re.I),
    re.compile(r'(\d{4})\s*[_-]\s*(?:round|r)\s*(\d)', re.I),
    re.compile(r'round[_-]?(\d)[_-]?(\d{4})', re.I),
]

# Table header keywords (any casing)
HEADER_KEYWORDS = [
    'college code', 'institute code', 'college name', 'institute name',
    'branch', 'course', 'category', 'open', 'obc', 'sc', 'st',
    'nt', 'ews', 'percentile', 'cutoff', 'rank',
]

# Category column detection
CATEGORY_HEADERS = ['open', 'obc', 'sc', 'st', 'nt', 'ews', 'nt1', 'nt2', 'nt3', 'tfws']


def detect_year_round_from_filename(filename: str) -> tuple:
    """Auto-detect year and round from the PDF filename.

    Returns:
        (year, round_number) or (None, None)
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    for pattern in FILENAME_PATTERNS:
        match = pattern.search(base)
        if match:
            groups = match.groups()
            if len(groups) == 2:
                # Try to determine which is year vs round
                a, b = groups
                try:
                    num_a, num_b = int(a), int(b)
                except ValueError:
                    continue
                if 2020 <= num_a <= 2030 and 1 <= num_b <= 5:
                    return num_a, num_b
                if 2020 <= num_b <= 2030 and 1 <= num_a <= 5:
                    return num_b, num_a
        # Also try: "2023" or "2024" standalone in filename
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
    """Fallback: detect year/round from PDF text content."""
    lines = text.split('\n')
    for line in lines[:50]:  # Check first 50 lines
        line_lower = line.lower()
        # "CAP Round 2 - 2024" or "Round 1 (2023)"
        m = re.search(r'(?:cap\s*)?round\s*[:\s]*(\d)\s*(?:of|\-|–|\(|,)?\s*(20[2-9]\d)', line_lower)
        if m:
            return int(m.group(2)), int(m.group(1))
        m = re.search(r'(20[2-9]\d)\s*(?:round|r)\s*(\d)', line_lower)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None, None


def _clean_text(text: str) -> str:
    """Normalize whitespace and special chars."""
    text = text.replace('\u00a0', ' ')  # non-breaking space
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _is_header_row(row_text: str) -> bool:
    """Check if a row text looks like a table header."""
    lower = row_text.lower()
    keyword_count = sum(1 for kw in HEADER_KEYWORDS if kw in lower)
    return keyword_count >= 2


def _normalize_branch(branch: str) -> str:
    """Normalize branch names to standard forms."""
    b = branch.strip()
    mappings = {
        'computer': 'Computer Engineering',
        'computer science': 'Computer Engineering',
        'cse': 'Computer Engineering',
        'cs': 'Computer Engineering',
        'it': 'Information Technology',
        'information technology': 'Information Technology',
        'mechanical': 'Mechanical Engineering',
        'mech': 'Mechanical Engineering',
        'civil': 'Civil Engineering',
        'electrical': 'Electrical Engineering',
        'electrical engineering': 'Electrical Engineering',
        'electronics': 'Electronics Engineering',
        'electronics and telecommunication': 'Electronics & Telecommunication Engg',
        'entc': 'Electronics & Telecommunication Engg',
        'e&tc': 'Electronics & Telecommunication Engg',
        'chemical': 'Chemical Engineering',
        'ai': 'Artificial Intelligence & Data Science',
        'ai & ds': 'Artificial Intelligence & Data Science',
        'aids': 'Artificial Intelligence & Data Science',
        'data science': 'Artificial Intelligence & Data Science',
        'ds': 'Artificial Intelligence & Data Science',
    }
    for key, val in mappings.items():
        if key in b.lower():
            return val
    return b.title() if b.isupper() else b


def extract_tables_with_pdfplumber(filepath: str):
    """Primary extraction: use pdfplumber to extract tables.

    Returns:
        list of dicts: parsed rows with keys:
            college_code, college_name, branch, category, cutoff_percentile,
            year, round_number, gender
    """
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed")
        return [], 0.0

    results = []
    total_cells = 0
    parsed_cells = 0

    with pdfplumber.open(filepath) as pdf:
        for page_num, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            if not tables:
                continue

            for table in tables:
                if not table or len(table) < 2:
                    continue

                header_row = table[0]
                data_rows = table[1:]

                if not _is_header_row(' '.join(str(c or '') for c in header_row)):
                    # Try second row as header
                    if len(table) > 2:
                        header_row = table[1]
                        data_rows = table[2:]
                    else:
                        continue

                # Detect category columns from header
                header_lower = [str(h or '').strip().lower() for h in header_row]
                cat_col_indices = {}
                for idx, h in enumerate(header_lower):
                    for cat in CATEGORY_HEADERS:
                        if cat in h:
                            cat_col_indices[cat.capitalize()] = idx

                # Detect code and name columns
                code_col = name_col = branch_col = -1
                for idx, h in enumerate(header_lower):
                    if 'code' in h and code_col < 0:
                        code_col = idx
                    elif 'name' in h or 'institute' in h:
                        name_col = idx if name_col < 0 else name_col

                # Branch column: often after name, before categories
                for idx, h in enumerate(header_lower):
                    if 'branch' in h or 'course' in h:
                        branch_col = idx

                # If no explicit branch col, assume column 2 (common MHT CET layout)
                if branch_col < 0:
                    branch_col = 2

                # Parse data rows
                for row in data_rows:
                    if not row or all(not cell for cell in row):
                        continue

                    cells = [str(c or '').strip() for c in row]
                    total_cells += len(cells)

                    # Extract college code
                    college_code = ''
                    if code_col >= 0 and code_col < len(cells):
                        raw_code = cells[code_col]
                        code_match = re.search(r'(\d{4})', raw_code)
                        if code_match:
                            college_code = code_match.group(1)

                    # Extract college name
                    college_name = ''
                    if name_col >= 0 and name_col < len(cells):
                        college_name = cells[name_col]
                    elif code_col >= 0 and code_col + 1 < len(cells):
                        college_name = cells[code_col + 1]

                    # Extract branch
                    branch = ''
                    if branch_col >= 0 and branch_col < len(cells):
                        branch = _normalize_branch(cells[branch_col])

                    if not college_code and not college_name:
                        continue

                    # Generate records for each category column found
                    for cat_name, col_idx in cat_col_indices.items():
                        if col_idx >= len(cells):
                            continue
                        raw_val = cells[col_idx]
                        if not raw_val or raw_val in ('-', '--', 'NA', ''):
                            continue

                        try:
                            pctl = float(raw_val.replace(',', ''))
                        except ValueError:
                            # Try extracting number from string like "95.23*"
                            num_match = re.search(r'(\d+\.?\d*)', raw_val)
                            if num_match:
                                try:
                                    pctl = float(num_match.group(1))
                                except ValueError:
                                    continue
                            else:
                                continue

                        if pctl > 100 or pctl < 0:
                            continue

                        parsed_cells += 1
                        results.append({
                            'college_code': college_code,
                            'college_name': college_name,
                            'branch': branch,
                            'category': cat_name,
                            'cutoff_percentile': pctl,
                            'year': None,  # filled by caller
                            'round_number': None,
                            'gender': 'Gender-Neutral',
                        })

    confidence = (parsed_cells / max(total_cells, 1)) * 100 if total_cells > 0 else 0
    logger.info(f"pdfplumber extracted {len(results)} records (confidence {confidence:.1f}%)")
    return results, confidence


def extract_raw_text(filepath: str) -> str:
    """Extract all raw text from a PDF (fallback method).

    Returns:
        Full text content as string.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed, cannot extract text")
        return ''

    text_parts = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
    return '\n'.join(text_parts)


def parse_from_text(text: str, year: int, round_number: int) -> list:
    """Regex-based parsing from raw text (when table extraction fails).

    Tries to find patterns like:
        1001 | COEP Pune | Computer Engineering | 99.88 | 95.20 | 85.00
    where columns after branch are category percentiles.
    """
    results = []
    lines = text.split('\n')
    cat_order = ['Open', 'OBC', 'SC', 'ST', 'NT', 'EWS']

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Look for lines starting with 4-digit code
        code_match = re.match(r'^(\d{4})\s+(.+)$', line)
        if code_match:
            code = code_match.group(1)
            rest = code_match.group(2)

            # Next few lines might contain the data
            data_block = rest
            j = i + 1
            while j < len(lines) and j < i + 3:
                next_line = lines[j].strip()
                if next_line and re.match(r'^\d', next_line):
                    break
                if next_line:
                    data_block += ' ' + next_line
                j += 1

            # Try to parse: name, branch, then numbers
            parts = re.split(r'\s{2,}', data_block)
            if len(parts) < 3:
                i += 1
                continue

            name = parts[0].strip()
            branch_raw = parts[1].strip() if len(parts) > 1 else ''
            branch = _normalize_branch(branch_raw)

            # Remaining parts should be percentile values
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
                            'branch': branch,
                            'category': cat,
                            'cutoff_percentile': pctl,
                            'year': year,
                            'round_number': round_number,
                            'gender': 'Gender-Neutral',
                        })
        i += 1

    return results


def extract_pdf(filepath: str, filename: str = '') -> dict:
    """Main entry point: extract cutoff data from a PDF.

    Args:
        filepath: Absolute path to the PDF file
        filename: Original filename (for year/round detection)

    Returns:
        dict with keys:
            rows: list of parsed row dicts
            year: detected year or None
            round_number: detected round or None
            method: extraction method used
            confidence: confidence score (0-100)
            raw_text: full extracted text
    """
    # 1. Detect year and round
    year, round_number = detect_year_round_from_filename(filename)
    logger.info(f"Filename detection: year={year}, round={round_number}")

    # 2. Primary: pdfplumber table extraction
    rows, confidence = extract_tables_with_pdfplumber(filepath)

    # If filename didn't provide year/round, try PDF text
    if not year or not round_number:
        raw_text = extract_raw_text(filepath)
        yr2, rd2 = detect_year_round_from_pdf_text(raw_text)
        year = year or yr2
        round_number = round_number or rd2
    else:
        raw_text = extract_raw_text(filepath)

    # Fill in year/round for all rows
    for row in rows:
        row['year'] = row['year'] or year
        row['round_number'] = row['round_number'] or round_number

    method = 'pdfplumber'
    if confidence < 80 and rows:
        logger.info(f"pdfplumber confidence {confidence:.1f}% — trying fallback text parsing")
        text_rows = parse_from_text(raw_text, year, round_number)
        if len(text_rows) > len(rows):
            rows = text_rows
            confidence = 85.0
            method = 'text_fallback'
        else:
            method = 'pdfplumber'

    logger.info(f"Extraction complete: {len(rows)} rows, method={method}, confidence={confidence:.1f}%")

    return {
        'rows': rows,
        'year': year,
        'round_number': round_number,
        'method': method,
        'confidence': confidence,
        'raw_text': raw_text,
    }