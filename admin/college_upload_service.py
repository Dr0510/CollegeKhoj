"""College Master Upload Service.

Handles CSV/Excel upload for college master data.
Supports preview, validation, update existing, create missing.
"""
import os
import io
import csv
import logging
import tempfile
from typing import Optional
from datetime import datetime

import pandas as pd
from werkzeug.datastructures import FileStorage

from database import db

logger = logging.getLogger(__name__)

# Required columns for college master upload
REQUIRED_COLUMNS = ['Name', 'Location', 'Branch', 'Fees', 'NIRF', 'Rating', 'Placement']

# Allowed column name variants (case-insensitive)
COLUMN_ALIASES = {
    'name': ['Name', 'College', 'College Name', 'Institute', 'Institute Name', 'college', 'college_name', 'institute'],
    'location': ['Location', 'City', 'City/Town', 'Place', 'location', 'city'],
    'branch': ['Branch', 'Course', 'Program', 'Stream', 'Discipline', 'branch', 'course'],
    'fees': ['Fees', 'Fee', 'Tuition Fee', 'Tuition Fees', 'Annual Fees', 'fees', 'fee', 'tuition_fee'],
    'nirf': ['NIRF', 'NIRF Rank', 'Rank', 'National Ranking', 'nirf_rank', 'rank'],
    'rating': ['Rating', 'Score', 'Overall Rating', 'rating', 'score'],
    'placement': ['Placement', 'Placement Rate', 'Placement %', 'Placement Percentage', 
                  'placement_rate', 'placement_percentage', 'placements'],
}

# Additional optional columns
OPTIONAL_COLUMNS = ['ID', 'id', 'college_code', 'Code', 'code']


class CollegeUploadResult:
    """Container for college upload results."""

    def __init__(self):
        self.total_rows = 0
        self.valid_rows = 0
        self.error_rows = 0
        self.to_create = 0
        self.to_update = 0
        self.created = 0
        self.updated = 0
        self.errors = []
        self.preview_rows = []

    def to_dict(self):
        return {
            'total_rows': self.total_rows,
            'valid_rows': self.valid_rows,
            'error_rows': self.error_rows,
            'to_create': self.to_create,
            'to_update': self.to_update,
            'created': self.created,
            'updated': self.updated,
            'errors': self.errors[:50],  # Limit errors in response
            'preview_rows': self.preview_rows[:50],
        }


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to canonical form."""
    col_map = {}
    for col in df.columns:
        col_lower = col.strip().lower()
        for canonical, aliases in COLUMN_ALIASES.items():
            if col_lower in [a.lower() for a in aliases]:
                col_map[col] = canonical
                break
        if col not in col_map:
            col_map[col] = col  # Keep original if no match
    return df.rename(columns=col_map)


def validate_row(row: dict, row_num: int) -> tuple:
    """Validate a single college row.

    Returns:
        (is_valid, error_message, college_data_dict)
    """
    errors = []
    
    name = str(row.get('Name', '') or '').strip()
    location = str(row.get('Location', '') or '').strip()
    branch = str(row.get('Branch', '') or '').strip()
    fees_str = str(row.get('Fees', '') or '').strip()
    nirf_str = str(row.get('NIRF', '') or '').strip()
    rating_str = str(row.get('Rating', '') or '').strip()
    placement_str = str(row.get('Placement', '') or '').strip()
    
    if not name:
        errors.append('Name is required')
    if not location:
        errors.append('Location is required')
    if not branch:
        errors.append('Branch is required')
    
    fees = None
    if fees_str:
        try:
            fees = float(fees_str.replace(',', '').replace('₹', '').replace('Rs', '').strip())
            if fees < 0:
                errors.append(f'Fees cannot be negative: {fees}')
        except (ValueError, TypeError):
            errors.append(f'Invalid Fees value: {fees_str}')
    else:
        errors.append('Fees is required')
    
    nirf = None
    if nirf_str:
        try:
            nirf = int(float(nirf_str.replace(',', '').strip()))
            if nirf < 0:
                errors.append(f'NIRF rank cannot be negative: {nirf}')
        except (ValueError, TypeError):
            errors.append(f'Invalid NIRF Rank value: {nirf_str}')
    
    rating = None
    if rating_str:
        try:
            rating = float(rating_str.strip())
            if rating < 0 or rating > 5:
                errors.append(f'Rating must be between 0 and 5: {rating}')
        except (ValueError, TypeError):
            errors.append(f'Invalid Rating value: {rating_str}')
    else:
        errors.append('Rating is required')
    
    placement = None
    if placement_str:
        try:
            placement = float(placement_str.replace('%', '').strip())
            if placement < 0 or placement > 100:
                errors.append(f'Placement rate must be between 0 and 100: {placement}')
        except (ValueError, TypeError):
            errors.append(f'Invalid Placement value: {placement_str}')
    
    college_data = {
        'college': name,
        'location': location,
        'branch': branch,
        'fees': fees,
        'nirf_rank': nirf,
        'rating': rating,
        'placement_rate': placement,
    }
    
    if errors:
        return False, '; '.join(errors), college_data
    return True, None, college_data


def parse_upload(file: FileStorage) -> CollegeUploadResult:
    """Parse uploaded CSV/Excel file and return preview results."""
    from models import College
    result = CollegeUploadResult()
    
    try:
        filename = file.filename.lower()
        
        # Read file into pandas DataFrame
        if filename.endswith('.csv'):
            df = pd.read_csv(io.StringIO(file.stream.read().decode('utf-8', errors='replace')))
        elif filename.endswith(('.xls', '.xlsx')):
            file.stream.seek(0)
            df = pd.read_excel(file.stream, engine='openpyxl' if filename.endswith('.xlsx') else 'xlrd')
        else:
            result.errors.append('Unsupported file format. Please upload CSV or Excel (.xls/.xlsx).')
            return result
        
        if df.empty:
            result.errors.append('File is empty.')
            return result
        
        # Normalize columns
        df = _normalize_df(df)
        
        # Check required columns exist
        missing = [c for c in ['Name', 'Location', 'Branch', 'Fees', 'NIRF', 'Rating', 'Placement'] 
                   if c not in df.columns]
        if missing:
            result.errors.append(f'Missing required columns: {", ".join(missing)}')
            return result
        
        result.total_rows = len(df)
        
        for idx, row in df.iterrows():
            row_dict = row.to_dict()
            is_valid, error, college_data = validate_row(row_dict, idx + 2)  # +2 for header + 0-index
            
            preview_entry = {**college_data, '_row': idx + 2, '_valid': is_valid}
            
            if not is_valid:
                preview_entry['_error'] = error
                result.error_rows += 1
                result.errors.append(f"Row {idx + 2}: {error}")
            else:
                # Check if college already exists
                existing = College.query.filter_by(
                    college=college_data['college'],
                    location=college_data['location'],
                    branch=college_data['branch']
                ).first()
                
                if existing:
                    preview_entry['_status'] = 'update'
                    result.to_update += 1
                else:
                    preview_entry['_status'] = 'create'
                    result.to_create += 1
                
                result.valid_rows += 1
            
            result.preview_rows.append(preview_entry)
        
        return result
        
    except Exception as e:
        logger.error(f"Error parsing college upload: {e}")
        result.errors.append(f'Error parsing file: {str(e)}')
        return result


def commit_upload(preview_result: CollegeUploadResult) -> CollegeUploadResult:
    """Commit the validated rows to the database (upsert)."""
    from models import College
    
    result = CollegeUploadResult()
    result.total_rows = preview_result.total_rows
    result.valid_rows = preview_result.valid_rows
    result.error_rows = preview_result.error_rows
    
    for entry in preview_result.preview_rows:
        if not entry.get('_valid'):
            continue
        
        try:
            existing = College.query.filter_by(
                college=entry['college'],
                location=entry['location'],
                branch=entry['branch']
            ).first()
            
            if existing:
                # Update existing
                if entry.get('fees') is not None:
                    existing.fees = entry['fees']
                if entry.get('placement_rate') is not None:
                    existing.placement_rate = entry['placement_rate']
                if entry.get('nirf_rank') is not None:
                    existing.nirf_rank = entry['nirf_rank']
                if entry.get('rating') is not None:
                    existing.rating = entry['rating']
                result.updated += 1
            else:
                # Create new
                new_college = College(
                    college=entry['college'],
                    location=entry['location'],
                    branch=entry['branch'],
                    fees=entry.get('fees', 0),
                    placement_rate=entry.get('placement_rate', 0),
                    nirf_rank=entry.get('nirf_rank', 999),
                    rating=entry.get('rating', 0),
                )
                db.session.add(new_college)
                result.created += 1
                
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error committing college row: {e}")
            result.errors.append(f"Error saving {entry.get('college')}: {str(e)}")
            result.error_rows += 1
    
    if result.created > 0 or result.updated > 0:
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error committing college upload: {e}")
            result.errors.append(f'Database commit error: {str(e)}')
    
    result.to_create = result.created
    result.to_update = result.updated
    result.preview_rows = preview_result.preview_rows[:50]
    
    return result