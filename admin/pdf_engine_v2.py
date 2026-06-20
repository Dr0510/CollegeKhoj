"""
Admin v2 PDF Extraction Engine — 3-Stage Pipeline.

Stage 1: pdfplumber (fast text extraction — works for DSE PDFs)
Stage 2: Camelot (table-based extraction — works for MHT CET Engineering PDFs)
Stage 3: Tesseract OCR (fallback — for scanned/image-based PDFs)

Auto-detects admission type, academic year, and CAP round from filename and content.
"""
import os
import re
import hashlib
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
try:
    PDF_MAX_WORKERS = int(os.environ.get('PDF_MAX_WORKERS', '1'))
except (ValueError, TypeError):
    PDF_MAX_WORKERS = 1

# ── Filename patterns for auto-detection ──────────────────────────────────
ADMISSION_PATTERNS = [
    (re.compile(r'\bDSE\b', re.I), 'DSE'),
    (re.compile(r'\bPOLY\b|\bPOLYTECHNIC\b', re.I), 'POLY'),
    (re.compile(r'\bENGG\b|\bENGINEERING\b|\bMHT[-\s]?CET\b|\bCAP\b', re.I), 'ENGG'),
]

ROUND_PATTERNS = [
    re.compile(r'(?:CAP|Round|R)\s*[:_-]?\s*(I{1,3}|IV|V|1|2|3|4|5)\b', re.I),
    re.compile(r'Round\s*(I{1,3}|IV|V)\b', re.I),
]

YEAR_PATTERNS = [
    re.compile(r'(20[2-9]\d)[-_](20[2-9]\d)', re.I),
    re.compile(r'(20[2-9]\d)'),
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

# ── Known category labels (DSE format) ──
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

def _normalise_branch(raw: str) -> str:
    """Map a raw branch string to its canonical form via BranchNormalizer."""
    from admin.branch_normalizer import normalize_branch
    return normalize_branch(raw)


# ── Auto-detection ────────────────────────────────────────────────────────

def detect_admission_type(filename: str, text: str = '') -> Optional[str]:
    """Detect admission type from filename and PDF text content."""
    combined = filename + ' ' + text[:2000]
    for pattern, atype in ADMISSION_PATTERNS:
        if pattern.search(combined):
            return atype
    return None


def detect_year(filename: str, text: str = '') -> Optional[str]:
    """Detect academic year string like '2025-26' or '2026-27'."""
    combined = filename + ' ' + text[:1000]

    # Try full year-year pattern first
    m = re.search(r'(20[2-9]\d)[-_](20[2-9]\d)', combined)
    if m:
        y1, y2 = m.group(1), m.group(2)
        if int(y2) == int(y1) + 1:
            return f"{y1}-{y2[-2:]}"

    # Try single year
    m = re.search(r'\b(20[2-9]\d)\b', combined)
    if m:
        y = m.group(1)
        return f"{y}-{str(int(y) + 1)[-2:]}"

    return None


def detect_cap_round(filename: str, text: str = '') -> Optional[str]:
    """Detect CAP round from filename and text content."""
    combined = filename + ' ' + text[:1000]

    roman_map = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5}
    for pattern in ROUND_PATTERNS:
        m = pattern.search(combined)
        if m:
            val = m.group(1).upper()
            if val in roman_map:
                return f"Round {roman_map[val]}"
            if val.isdigit():
                return f"Round {int(val)}"
    return None


def compute_file_hash(filepath: str) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# ── Stage 1: pdfplumber text extraction ───────────────────────────────────

def _safe_extract_page(page_num: int, page, fallback_pdf_path: str = None) -> dict:
    """Extract text from a single page with error handling + fallback.
    
    Args:
        page_num: 1-based page number (for logging)
        page: pdfplumber Page object
        fallback_pdf_path: path to PDF for pdfminer fallback
    
    Returns:
        dict with keys: page, text, success, fallback (bool), error (str)
    """
    try:
        text = page.extract_text() or ''
        return {
            'page': page_num,
            'text': text.strip(),
            'success': True,
            'fallback': False,
            'error': None
        }
    except Exception as e:
        error_type = type(e).__name__
        logger.error(f"Page {page_num} FAILED: {error_type}: {e}")
        
        # Fallback: try pdfminer
        if fallback_pdf_path:
            try:
                from pdfminer.high_level import extract_text as pdfminer_extract
                # pdfminer uses 0-based page numbers
                text = pdfminer_extract(fallback_pdf_path, page_numbers=[page_num - 1])
                if text and text.strip():
                    logger.info(f"Page {page_num} recovered via pdfminer fallback")
                    return {
                        'page': page_num,
                        'text': text.strip(),
                        'success': True,
                        'fallback': True,
                        'error': None
                    }
            except Exception as e2:
                logger.error(f"Page {page_num} fallback (pdfminer) also failed: {type(e2).__name__}: {e2}")
        
        return {
            'page': page_num,
            'text': '',
            'success': False,
            'fallback': False,
            'error': f"{error_type}: {str(e)}"
        }


def _parallel_worker(args: tuple) -> dict:
    """Worker function for parallel extraction.
    
    Each worker opens its OWN PDF handle (pdfplumber is NOT thread-safe).
    
    Args:
        args: (page_num, pdf_path, total_pages)
    
    Returns:
        dict with keys: page, text, success, fallback, error
    """
    page_num, pdf_path = args
    try:
        with plumber.open(pdf_path) as pdf:
            if page_num <= len(pdf.pages):
                page = pdf.pages[page_num - 1]  # 0-indexed
                return _safe_extract_page(page_num, page, fallback_pdf_path=pdf_path)
            else:
                return {
                    'page': page_num,
                    'text': '',
                    'success': False,
                    'fallback': False,
                    'error': f"Page {page_num} out of range (PDF has {len(pdf.pages)} pages)"
                }
    except Exception as e:
        logger.error(f"Worker for page {page_num} crashed: {type(e).__name__}: {e}")
        return {
            'page': page_num,
            'text': '',
            'success': False,
            'fallback': False,
            'error': f"Worker crash: {type(e).__name__}: {str(e)}"
        }


def extract_with_pdfplumber(filepath: str, max_workers: int = 1) -> Tuple[List[str], str, dict]:
    """Extract text from PDF pages using pdfplumber with error handling.
    
    Args:
        filepath: PDF path
        max_workers: parallel workers (1=sequential, >1=thread pool)
                     Each worker opens its own PDF handle for thread safety.
    
    Returns:
        (page_texts, raw_text, stats)
        where stats = {
            'pages_total': int,
            'pages_processed': int,
            'pages_failed': int,
            'pages_fallback': int,
            'failed_pages': list of {'page': int, 'reason': str}
        }
    """
    try:
        global plumber
        import pdfplumber as plumber
    except ImportError:
        logger.error("pdfplumber not installed")
        return [], '', {
            'pages_total': 0,
            'pages_processed': 0,
            'pages_failed': 0,
            'pages_fallback': 0,
            'failed_pages': []
        }

    try:
        with plumber.open(filepath) as pdf:
            total_pages = len(pdf.pages)
    except Exception as e:
        logger.error(f"Cannot open PDF: {e}")
        return [], '', {
            'pages_total': 0,
            'pages_processed': 0,
            'pages_failed': 0,
            'pages_fallback': 0,
            'failed_pages': [{'page': 0, 'reason': f"Cannot open PDF: {e}"}]
        }

    # Sequential extraction
    if max_workers <= 1:
        page_texts = []
        failed_pages = []
        pages_fallback = 0
        
        with plumber.open(filepath) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                result = _safe_extract_page(page_num, page, fallback_pdf_path=filepath)
                if result['success']:
                    page_texts.append(result['text'])
                    if result.get('fallback'):
                        pages_fallback += 1
                else:
                    page_texts.append('')
                    failed_pages.append({'page': page_num, 'reason': result['error']})
        
        stats = {
            'pages_total': total_pages,
            'pages_processed': len(page_texts) - len(failed_pages),
            'pages_failed': len(failed_pages),
            'pages_fallback': pages_fallback,
            'failed_pages': failed_pages
        }
        
        logger.info(
            f"Extraction complete: {stats['pages_processed']}/{stats['pages_total']} pages, "
            f"{stats['pages_failed']} failed, {stats['pages_fallback']} fallback"
        )
        
        return page_texts, '\n'.join(page_texts), stats
    
    # Parallel extraction - each worker opens its own PDF handle
    else:
        logger.info(f"Starting parallel extraction with {max_workers} workers, {total_pages} pages")
        
        args = [(i, filepath) for i in range(1, total_pages + 1)]
        results = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_parallel_worker, arg) for arg in args]
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    # This should never happen - worker catches all exceptions
                    logger.error(f"Unexpected worker exception: {e}")
        
        # Sort by page number to maintain order
        results.sort(key=lambda r: r['page'])
        
        page_texts = [r['text'] for r in results]
        failed_pages = [{'page': r['page'], 'reason': r['error']} for r in results if not r['success']]
        pages_fallback = sum(1 for r in results if r.get('fallback'))
        
        stats = {
            'pages_total': total_pages,
            'pages_processed': sum(1 for r in results if r['success']),
            'pages_failed': len(failed_pages),
            'pages_fallback': pages_fallback,
            'failed_pages': failed_pages
        }
        
        logger.info(
            f"Parallel extraction complete: {stats['pages_processed']}/{stats['pages_total']} pages, "
            f"{stats['pages_failed']} failed, {stats['pages_fallback']} fallback"
        )
        
        if stats['pages_failed'] > 0:
            logger.warning(f"Failed pages: {[p['page'] for p in failed_pages]}")
        
        return page_texts, '\n'.join(page_texts), stats


# ── DSE text-block parser (from original pdf_extractor.py) ────────────────

def _is_category_line(line: str) -> bool:
    tokens = line.strip().split()
    if len(tokens) < 2:
        return False
    label_count = sum(1 for t in tokens if t.upper() in CATEGORY_LABELS)
    return label_count >= 2


def _is_percentile_line(line: str) -> bool:
    return bool(PCT_RE.search(line))


def _extract_percentiles(line: str) -> List[float]:
    return [float(m) for m in PCT_RE.findall(line)]


def _extract_categories(line: str) -> List[str]:
    tokens = line.strip().split()
    return [t for t in tokens if t.upper() in CATEGORY_LABELS]


def _is_rank_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith('('):
        return False
    tokens = stripped.split()
    if len(tokens) < 2:
        return False
    numeric = sum(1 for t in tokens if t.replace(',', '').isdigit())
    return numeric >= 2


def _extract_ranks(line: str) -> List[int]:
    tokens = line.strip().split()
    ranks = []
    for t in tokens:
        cleaned = t.replace(',', '')
        if cleaned.isdigit():
            ranks.append(int(cleaned))
    return ranks


def parse_dse_page(page_text: str, page_num: int) -> List[Dict]:
    """Parse a single page of DSE PDF text into cutoff records.

    Returns list of dicts with keys:
        college_code, college_name, course_code, course_name,
        category, rank, percentile, seat_type
    """
    if not page_text or len(page_text) < 100:
        return []
    
    lines = page_text.split('\n')
    records: List[Dict] = []

    current_college_code = None
    current_college_name = None
    current_course_code = None
    current_course_name = None
    current_categories = []
    current_ranks = []
    current_percentiles = []
    block_state = 'IDLE'

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if not line:
            if block_state in ('RANKS', 'PCT') and current_categories and current_percentiles:
                _emit_records(records, current_college_code, current_college_name,
                              current_course_code, current_course_name,
                              current_categories, current_ranks, current_percentiles)
            block_state = 'IDLE'
            current_categories = []
            current_ranks = []
            current_percentiles = []
            i += 1
            continue

        # Skip footer lines
        if line.startswith('L - Ladies') or line.startswith('STATE CET CELL') or \
           'Provisional cutoff List' in line or 'GOVERNMENT OF MAHARASHTRA' in line or \
           line.startswith('Address') or 'Page' in line or line.startswith('Provisional cutoff'):
            i += 1
            continue

        # College line
        college_match = COLLEGE_RE.match(line)
        if college_match and not line.startswith('Choice'):
            if block_state in ('RANKS', 'PCT') and current_categories and current_percentiles:
                _emit_records(records, current_college_code, current_college_name,
                              current_course_code, current_course_name,
                              current_categories, current_ranks, current_percentiles)
                current_categories = []
                current_ranks = []
                current_percentiles = []

            current_college_code = college_match.group(1)
            current_college_name = college_match.group(2).strip()
            block_state = 'COLLEGE'
            i += 1
            continue

        # Choice Code / Course Name
        choice_match = CHOICE_COURSE_RE.search(line)
        if choice_match and block_state in ('COLLEGE', 'IDLE', 'COURSE'):
            current_course_code = choice_match.group(1)
            current_course_name = _normalise_branch(choice_match.group(2))
            block_state = 'COURSE'
            if _is_category_line(line):
                current_categories = _extract_categories(line)
                block_state = 'CATEGORIES'
            i += 1
            continue

        # Category header
        if _is_category_line(line) and block_state in ('COURSE', 'COLLEGE', 'IDLE', 'CATEGORIES'):
            current_categories = _extract_categories(line)
            block_state = 'CATEGORIES'
            i += 1
            continue

        # Rank numbers
        if _is_rank_line(line) and block_state in ('CATEGORIES', 'RANKS'):
            current_ranks = _extract_ranks(line)
            block_state = 'RANKS'
            i += 1
            continue

        # Stage/Round marker
        if line.startswith('Stage-') or line.startswith('Round'):
            if block_state in ('RANKS', 'CATEGORIES'):
                block_state = 'WAITING_PCT'
            i += 1
            continue

        # Percentile line
        if _is_percentile_line(line) and block_state in ('RANKS', 'CATEGORIES', 'WAITING_PCT', 'PCT'):
            current_percentiles = _extract_percentiles(line)
            block_state = 'PCT'
            if current_categories and current_percentiles:
                _emit_records(records, current_college_code, current_college_name,
                              current_course_code, current_course_name,
                              current_categories, current_ranks, current_percentiles)
                current_categories = []
                current_ranks = []
                current_percentiles = []
                block_state = 'IDLE'
            i += 1
            continue

        if block_state in ('COURSE', 'CATEGORIES', 'RANKS', 'WAITING_PCT', 'PCT') and not line.startswith('('):
            block_state = 'IDLE'
            current_categories = []
            current_ranks = []
            current_percentiles = []

        i += 1

    # Flush last block
    if block_state in ('RANKS', 'PCT', 'WAITING_PCT') and current_categories and current_percentiles:
        _emit_records(records, current_college_code, current_college_name,
                      current_course_code, current_course_name,
                      current_categories, current_ranks, current_percentiles)

    return records


def _emit_records(records: List, college_code: str, college_name: str,
                  course_code: str, course_name: str,
                  categories: List[str], ranks: List[int], percentiles: List[float]):
    """Map categories to percentiles/ranks and append records."""
    n = min(len(categories), len(percentiles))
    if n == 0:
        return

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
            'seat_type': cat.upper(),
            'rank': rnk,
            'percentile': round(pctl, 2),
        })


# ── Stage 2: Camelot table extraction ─────────────────────────────────────

def extract_with_camelot(filepath: str) -> List[Dict]:
    """Extract tabular data from PDF using Camelot.

    Works well for MHT CET Engineering PDFs with proper table structures.
    Falls back gracefully if Camelot is not installed.
    """
    try:
        import camelot
    except ImportError:
        logger.warning("Camelot not installed, skipping Stage 2")
        return []

    records = []
    try:
        tables = camelot.read_pdf(filepath, pages='all', flavor='lattice')
        if len(tables) == 0:
            tables = camelot.read_pdf(filepath, pages='all', flavor='stream')

        for table in tables:
            df = table.df
            # Try to detect columns
            headers = df.iloc[0].tolist() if len(df) > 0 else []
            for row_idx in range(1, len(df)):
                row = df.iloc[row_idx].tolist()
                record = _parse_camelot_row(row, headers)
                if record:
                    records.append(record)

        logger.info(f"Camelot extracted {len(records)} rows")
    except Exception as e:
        logger.warning(f"Camelot extraction failed: {e}")

    return records


def _parse_camelot_row(row: List[str], headers: List[str]) -> Optional[Dict]:
    """Parse a single Camelot row into our standard format."""
    if not row or all(cell.strip() == '' for cell in row):
        return None

    # Try to identify columns by position or header names
    # Common column order: College Code, College Name, Branch/Course, Category, Rank, Percentile
    record = {}

    # Simple positional parsing (customise per PDF format)
    cleaned = [cell.strip() for cell in row]

    if len(cleaned) >= 2:
        # Check if first column is a 4-digit code
        if re.match(r'^\d{4}$', cleaned[0]):
            record['college_code'] = cleaned[0]
            record['college_name'] = cleaned[1] if len(cleaned) > 1 else ''
        else:
            return None

    if len(cleaned) >= 3:
        record['course_name'] = _normalise_branch(cleaned[2])
        record['course_code'] = f"{record.get('college_code', '0000')}000"

    # Try to find category, rank, percentile in remaining columns
    for cell in cleaned[3:]:
        # Check if it's a category label
        if cell.upper() in CATEGORY_LABELS:
            record['category'] = cell.upper()
            record['seat_type'] = cell.upper()
        # Check if it's a percentile value
        elif re.match(r'^[\d.]+%?$', cell):
            try:
                val = float(cell.replace('%', ''))
                if 0 <= val <= 100:
                    record['percentile'] = val
            except ValueError:
                pass
        # Check if it's a rank
        elif cell.replace(',', '').isdigit():
            record['rank'] = int(cell.replace(',', ''))

    if record.get('college_code') and record.get('course_name'):
        return record

    return None


# ── Stage 3: OCR fallback ─────────────────────────────────────────────────

def extract_with_ocr(filepath: str) -> Tuple[List[str], str]:
    """Extract text from PDF using Tesseract OCR fallback.

    Converts PDF pages to images, then runs OCR.

    Returns:
        (page_texts, raw_text)
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError:
        logger.warning("pdf2image or pytesseract not installed, skipping OCR")
        return [], ''

    page_texts = []
    try:
        images = convert_from_path(filepath, dpi=300)
        for img in images:
            text = pytesseract.image_to_string(img, lang='eng')
            page_texts.append(text.strip())
        logger.info(f"OCR extracted {len(page_texts)} pages")
    except Exception as e:
        logger.warning(f"OCR extraction failed: {e}")

    return page_texts, '\n'.join(page_texts)


# ── Main extraction pipeline ──────────────────────────────────────────────

def extract_pdf(filepath: str, filename: str = '', max_workers: int = None) -> Dict:
    """Main entry point: extract cutoff data from a PDF using 3-stage pipeline.

    Args:
        filepath: Absolute path to the PDF file.
        filename: Original filename (for year/round/admission_type detection).
        max_workers: Number of parallel workers (default: PDF_MAX_WORKERS env var or 1).
                     WARNING: Parallel extraction disabled by default due to pdfplumber
                     thread-safety issues. Set PDF_MAX_WORKERS > 1 to enable.
    
    Returns:
        dict with keys:
            rows: list of parsed row dicts
            admission_type: detected admission type or None
            academic_year: detected academic year string or None
            cap_round: detected cap round string or None
            method: extraction method used
            confidence: confidence score (0-100)
            total_pages: total pages in PDF
            error: error message if extraction failed
            extraction_stats: dict with page-level statistics
    """
    if max_workers is None:
        max_workers = PDF_MAX_WORKERS
    
    result = {
        'rows': [],
        'admission_type': None,
        'academic_year': None,
        'cap_round': None,
        'method': 'none',
        'confidence': 0.0,
        'total_pages': 0,
        'error': None,
        'extraction_stats': {},
    }

    if not os.path.exists(filepath):
        result['error'] = f'File not found: {filepath}'
        return result

    # Compute file hash
    result['file_hash'] = compute_file_hash(filepath)

    # Auto-detect metadata from filename
    result['admission_type'] = detect_admission_type(filename)
    result['academic_year'] = detect_year(filename)
    result['cap_round'] = detect_cap_round(filename)

    file_size = os.path.getsize(filepath)
    result['file_size'] = file_size

    # ── STAGE 1: pdfplumber ──
    try:
        page_texts, raw_text, stats = extract_with_pdfplumber(filepath, max_workers=max_workers)
        result['total_pages'] = len(page_texts)
        result['extraction_stats'] = stats
    except Exception as e:
        logger.error(f"pdfplumber extraction failed completely: {e}")
        result['error'] = f"pdfplumber failed: {e}"
        page_texts = []
        raw_text = ''

    # Try DSE text-block parser on pdfplumber output
    all_rows = []
    for page_num, text in enumerate(page_texts, start=1):
        if text and len(text) > 100:
            try:
                page_rows = parse_dse_page(text, page_num)
                all_rows.extend(page_rows)
            except Exception as e:
                logger.error(f"Page {page_num} parsing failed: {e}")

    method = 'pdfplumber_dse'
    if all_rows:
        logger.info(f"Stage 1 (pdfplumber+DSE): {len(all_rows)} rows extracted")

    # ── STAGE 2: Camelot (if Stage 1 produced few or no rows) ──
    if len(all_rows) < 10:
        try:
            camelot_rows = extract_with_camelot(filepath)
            if camelot_rows:
                all_rows = camelot_rows
                method = 'camelot'
                logger.info(f"Stage 2 (Camelot): {len(all_rows)} rows extracted")
        except Exception as e:
            logger.warning(f"Camelot extraction failed: {e}")

    # ── STAGE 3: OCR (if Stages 1 & 2 produced no rows) ──
    if len(all_rows) == 0:
        try:
            ocr_texts, ocr_raw = extract_with_ocr(filepath)
            if ocr_texts:
                for page_num, text in enumerate(ocr_texts, start=1):
                    if text and len(text) > 50:
                        try:
                            page_rows = parse_dse_page(text, page_num)
                            all_rows.extend(page_rows)
                        except Exception as e:
                            logger.error(f"OCR page {page_num} parsing failed: {e}")
                method = 'ocr'
                # Also try to detect metadata from OCR text
                if not result['admission_type']:
                    result['admission_type'] = detect_admission_type(filename, ocr_raw)
                if not result['academic_year']:
                    result['academic_year'] = detect_year(filename, ocr_raw)
                if not result['cap_round']:
                    result['cap_round'] = detect_cap_round(filename, ocr_raw)
                logger.info(f"Stage 3 (OCR): {len(all_rows)} rows extracted")
        except Exception as e:
            logger.warning(f"OCR extraction failed: {e}")

    # Try to detect metadata from PDF text if not found in filename
    if not result['admission_type']:
        result['admission_type'] = detect_admission_type(filename, raw_text)
    if not result['academic_year']:
        result['academic_year'] = detect_year(filename, raw_text)
    if not result['cap_round']:
        result['cap_round'] = detect_cap_round(filename, raw_text)

    # Fill in missing metadata fields
    if not result['academic_year']:
        # Default to current year
        now = datetime.utcnow()
        y = now.year
        result['academic_year'] = f"{y}-{str(int(y) + 1)[-2:]}"
    if not result['cap_round']:
        result['cap_round'] = 'Round I'
    if not result['admission_type']:
        result['admission_type'] = 'ENGG'

    # Calculate confidence
    total_pages_with_text = sum(1 for pt in page_texts if pt.strip())
    if total_pages_with_text > 0 and all_rows:
        confidence = min(100.0, (len(all_rows) / (total_pages_with_text * 20)) * 100)
    else:
        confidence = 0.0

    result['rows'] = all_rows
    result['method'] = method
    result['confidence'] = round(confidence, 1)

    if not all_rows:
        result['error'] = (
            f"All extraction methods returned 0 rows ({total_pages_with_text} pages with text). "
            f"First 500 chars: {raw_text[:500]}"
        )
        logger.error(result['error'])

    return result