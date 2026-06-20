"""
Branch Normalization Service for CollegeKhoj.

Provides canonical branch name resolution from raw PDF-extracted strings.
Uses alias mapping, case-insensitive exact matching, and fuzzy substring
matching as a last resort.  Never raises — always returns a string.
"""
import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical alias table.
# Key   = any raw string that should map to that branch
# Value = the canonical branch_name stored in the `branches` table
# ---------------------------------------------------------------------------
BRANCH_ALIASES: dict[str, str] = {
    # ── Computer / IT ───────────────────────────────────────────────────────
    "computer science and engineering":                    "Computer Engineering",
    "computer science & engineering":                      "Computer Engineering",
    "computer engineering":                                "Computer Engineering",
    "computer science":                                    "Computer Engineering",
    "cse":                                                 "Computer Engineering",
    "cs":                                                  "Computer Engineering",
    "b.tech computer science":                             "Computer Engineering",
    "information technology":                              "Information Technology",
    "it":                                                  "Information Technology",
    # ── E&TC ────────────────────────────────────────────────────────────────
    "electronics and telecommunication engineering":       "Electronics and Telecommunication Engineering",
    "electronics and telecommunication engg":              "Electronics and Telecommunication Engineering",
    "electronics & telecommunication engg":                "Electronics and Telecommunication Engineering",
    "electronics & telecommunication engineering":         "Electronics and Telecommunication Engineering",
    "e&tc":                                                "Electronics and Telecommunication Engineering",
    "entc":                                                "Electronics and Telecommunication Engineering",
    "electronics and telecommunication":                   "Electronics and Telecommunication Engineering",
    # ── Electrical ──────────────────────────────────────────────────────────
    "electrical engineering":                              "Electrical Engineering",
    "electrical engg":                                     "Electrical Engineering",
    "electrical engg[electronics and power]":              "Electrical Engineering",
    "electrical engineering (electronics and power)":      "Electrical Engineering",
    "electrical":                                          "Electrical Engineering",
    # ── Mechanical ──────────────────────────────────────────────────────────
    "mechanical engineering":                              "Mechanical Engineering",
    "mechanical engg":                                     "Mechanical Engineering",
    "mechanical":                                          "Mechanical Engineering",
    "mech":                                                "Mechanical Engineering",
    # ── Civil ───────────────────────────────────────────────────────────────
    "civil engineering":                                   "Civil Engineering",
    "civil engg":                                          "Civil Engineering",
    "civil":                                               "Civil Engineering",
    # ── Chemical ────────────────────────────────────────────────────────────
    "chemical engineering":                                "Chemical Engineering",
    "chemical engg":                                       "Chemical Engineering",
    "chemical":                                            "Chemical Engineering",
    # ── AI / Data Science ───────────────────────────────────────────────────
    "artificial intelligence (ai) and data science":       "Artificial Intelligence and Data Science",
    "artificial intelligence and data science":            "Artificial Intelligence and Data Science",
    "artificial intelligence & data science":              "Artificial Intelligence and Data Science",
    "ai & ds":                                             "Artificial Intelligence and Data Science",
    "ai and ds":                                           "Artificial Intelligence and Data Science",
    "aids":                                                "Artificial Intelligence and Data Science",
    "ai":                                                  "Artificial Intelligence and Data Science",
    "data science":                                        "Artificial Intelligence and Data Science",
    # ── AI & ML ─────────────────────────────────────────────────────────────
    "artificial intelligence and machine learning":        "Artificial Intelligence and Machine Learning",
    "artificial intelligence (ai) and machine learning":   "Artificial Intelligence and Machine Learning",
    "ai & ml":                                             "Artificial Intelligence and Machine Learning",
    "aiml":                                                "Artificial Intelligence and Machine Learning",
    # ── CSE (IoT / Cyber Security / specialisations) ────────────────────────
    "computer science and engineering (iot)":              "Computer Science and Engineering (IoT)",
    "computer science and engineering(iot)":               "Computer Science and Engineering (IoT)",
    "cse (iot)":                                           "Computer Science and Engineering (IoT)",
    "computer science and engineering (cyber security)":   "Computer Science and Engineering (Cyber Security)",
    "computer science and engineering (data science)":     "Computer Science and Engineering (Data Science)",
    # ── Electronics ─────────────────────────────────────────────────────────
    "electronics engineering":                             "Electronics Engineering",
    "electronics":                                         "Electronics Engineering",
    # ── Instrumentation ─────────────────────────────────────────────────────
    "instrumentation engineering":                         "Instrumentation Engineering",
    "instrumentation":                                     "Instrumentation Engineering",
    # ── Production / Manufacturing ───────────────────────────────────────────
    "production engineering":                              "Production Engineering",
    "manufacturing engineering":                           "Manufacturing Engineering",
    # ── Textile ─────────────────────────────────────────────────────────────
    "textile engineering":                                 "Textile Engineering",
    "textile technology":                                  "Textile Technology",
    # ── Food Technology ─────────────────────────────────────────────────────
    "food technology":                                     "Food Technology",
    # ── Biomedical ──────────────────────────────────────────────────────────
    "biomedical engineering":                              "Biomedical Engineering",
    # ── Printing ────────────────────────────────────────────────────────────
    "printing and packaging technology":                   "Printing and Packaging Technology",
    # ── MCA / MBA (DSE / direct) ────────────────────────────────────────────
    "master of computer applications":                     "Master of Computer Applications",
    "mca":                                                 "Master of Computer Applications",
    "master of business administration":                   "Master of Business Administration",
    "mba":                                                 "Master of Business Administration",
}

# Pre-build a lowercase-keyed dict for O(1) lookup
_ALIAS_LOWER: dict[str, str] = {k.lower(): v for k, v in BRANCH_ALIASES.items()}

# Normalise whitespace helper
_WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    """Strip and collapse internal whitespace."""
    return _WS.sub(" ", s.strip())


def normalize_branch(raw: str) -> str:
    """Return the canonical branch name for *raw*.

    Resolution order:
    1. Exact match (case-insensitive) in BRANCH_ALIASES
    2. Substring containment — find the longest alias key that is
       a substring of *raw* (avoids short keys like "cs" matching
       "Computer Science and Engineering (Cyber Security)")
    3. Fall back to *raw* cleaned up (title-cased if all-upper)

    This function never raises.
    """
    if not raw:
        return ""

    cleaned = _clean(raw)
    lower = cleaned.lower()

    # 1. Exact lookup
    if lower in _ALIAS_LOWER:
        return _ALIAS_LOWER[lower]

    # 2. Longest-key substring match (greedy — avoids ambiguous short keys)
    best_key = ""
    best_canonical = ""
    for key, canonical in _ALIAS_LOWER.items():
        if key in lower and len(key) > len(best_key):
            best_key = key
            best_canonical = canonical

    if best_canonical:
        logger.debug(f"[BranchNormalizer] '{raw}' → '{best_canonical}' (via substring '{best_key}')")
        return best_canonical

    # 3. Passthrough — clean original
    fallback = cleaned.title() if cleaned.isupper() else cleaned
    logger.debug(f"[BranchNormalizer] '{raw}' → passthrough '{fallback}'")
    return fallback


def canonical_branch_code(branch_name: str) -> str:
    """Generate a stable, URL-safe code from a canonical branch name.

    Example: "Computer Engineering" → "COMPUTER_ENGINEERING"
             "Electronics and Telecommunication Engineering" → "ELEC_TELECOMM_ENGG"
    """
    # Fixed short codes for known branches (keeps DB codes compact)
    _SHORT_CODES: dict[str, str] = {
        "Computer Engineering":                              "COMP_ENGG",
        "Information Technology":                            "INFO_TECH",
        "Electronics and Telecommunication Engineering":     "ELEC_TC_ENGG",
        "Electrical Engineering":                            "ELEC_ENGG",
        "Mechanical Engineering":                            "MECH_ENGG",
        "Civil Engineering":                                 "CIVIL_ENGG",
        "Chemical Engineering":                              "CHEM_ENGG",
        "Artificial Intelligence and Data Science":          "AI_DS",
        "Artificial Intelligence and Machine Learning":      "AI_ML",
        "Computer Science and Engineering (IoT)":            "CSE_IOT",
        "Computer Science and Engineering (Cyber Security)": "CSE_CYBER",
        "Computer Science and Engineering (Data Science)":   "CSE_DS",
        "Electronics Engineering":                           "ELEC_ENGG2",
        "Instrumentation Engineering":                       "INST_ENGG",
        "Production Engineering":                            "PROD_ENGG",
        "Manufacturing Engineering":                         "MFG_ENGG",
        "Textile Engineering":                               "TEXT_ENGG",
        "Textile Technology":                                "TEXT_TECH",
        "Food Technology":                                   "FOOD_TECH",
        "Biomedical Engineering":                            "BIO_ENGG",
        "Printing and Packaging Technology":                 "PRINT_PKG",
        "Master of Computer Applications":                   "MCA",
        "Master of Business Administration":                 "MBA",
    }
    if branch_name in _SHORT_CODES:
        return _SHORT_CODES[branch_name]

    # Auto-generate from name: upper snake-case, max 20 chars
    code = re.sub(r"[^A-Za-z0-9]+", "_", branch_name).upper()[:20].strip("_")
    return code
