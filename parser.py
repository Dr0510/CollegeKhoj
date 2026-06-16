"""
PDF Parser for extracting MHT-CET/DSE CAP cutoff data from PDF files.
"""
import os
import re
import logging
from datetime import datetime
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)

def parse_all_pdfs(pdf_directory: str) -> List[Dict]:
    """
    Parse all PDF files in the specified directory.
    
    Args:
        pdf_directory: Path to directory containing PDF files
        
    Returns:
        List of dictionaries containing all extracted records
    """
    # Lazy import to avoid circular import chain
    from admin.pdf_extractor import extract_pdf
    
    all_records = []
    
    # Get all PDF files in the directory
    pdf_files = [f for f in os.listdir(pdf_directory) if f.endswith('.pdf')]
    
    if not pdf_files:
        logger.warning(f"No PDF files found in {pdf_directory}")
        return all_records
    
    logger.info(f"Found {len(pdf_files)} PDF files to process")
    
    for pdf_file in pdf_files:
        pdf_path = os.path.join(pdf_directory, pdf_file)
        logger.info(f"Processing {pdf_file}")
        
        try:
            # Extract data from PDF
            result = extract_pdf(pdf_path, pdf_file)
            
            if 'error' in result:
                logger.error(f"Error extracting data from {pdf_file}: {result['error']}")
                continue
                
            records = result.get('rows', [])
            if records:
                logger.info(f"Extracted {len(records)} records from {pdf_file}")
                all_records.extend(records)
            else:
                logger.warning(f"No records extracted from {pdf_file}")
                
        except Exception as e:
            logger.error(f"Error processing {pdf_file}: {str(e)}")
            continue
    
    logger.info(f"Total records extracted: {len(all_records)}")
    return all_records
