"""
Simple bulk import script for PDF extraction and database insertion.
This version avoids circular imports by using the same approach as test_import.py
"""
import os
import sys
import logging
from datetime import datetime, timezone
from sqlalchemy import text
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Add the project root to the path
sys.path.insert(0, '.')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def setup_database():
    """Setup database connection using the same approach as the existing code."""
    from flask import Flask
    from database import init_database
    
    app = Flask(__name__)
    db = init_database(app)
    return app, db

def main():
    """Main function to orchestrate PDF parsing and database import."""
    logger.info("Starting PDF extraction and database import pipeline")
    
    # Setup database
    app, db = setup_database()
    
    with app.app_context():
        try:
            # Check current state
            total = db.session.execute(text("SELECT count(*) FROM college_cutoffs")).scalar()
            logger.info(f"Current records in college_cutoffs: {total}")
            
            # Parse all PDFs
            pdf_dir = 'uploads/cutoffs'
            pdf_files = [f for f in os.listdir(pdf_dir) if f.endswith('.pdf')]
            
            if not pdf_files:
                logger.warning("No PDFs found in uploads/cutoffs/")
                return
            
            logger.info(f"Found {len(pdf_files)} PDF files to process")
            
            # Process each PDF file
            total_inserted = 0
            total_duplicates = 0
            all_records = []
            
            for pdf_file in pdf_files:
                pdf_path = os.path.join(pdf_dir, pdf_file)
                logger.info(f"Parsing: {pdf_file}")
                
                # Import here to avoid circular imports
                from admin.pdf_extractor import extract_pdf
                
                extraction = extract_pdf(pdf_path, pdf_file)
                rows = extraction.get('rows', [])
                logger.info(f"Parsed {len(rows)} rows from {pdf_file}")
                logger.info(f"Year: {extraction['year']}, Round: {extraction['round']}")
                logger.info(f"Method: {extraction['method']}, Confidence: {extraction['confidence']}%")
                
                if rows:
                    all_records.extend(rows)
                else:
                    logger.warning(f"No rows extracted from {pdf_file}")
            
            if not all_records:
                logger.warning("No records extracted from any PDFs")
                return
            
            logger.info(f"Total records to import: {len(all_records)}")
            
            # Bulk insert with ON CONFLICT handling
            inserted = 0
            duplicates = 0
            now = datetime.now(timezone.utc)
            
            for row in all_records:
                try:
                    result = db.session.execute(
                        text("""
                            INSERT INTO college_cutoffs 
                            (year, round, college_code, college_name, course_code, course_name,
                             category, rank, percentile, imported_at)
                            VALUES (:year, :round, :college_code, :college_name, :course_code,
                                    :course_name, :category, :rank, :percentile, :imported_at)
                            ON CONFLICT (year, round, college_code, course_code, category)
                            DO NOTHING
                        """),
                        {
                            'year': row['year'],
                            'round': row['round'],
                            'college_code': row['college_code'],
                            'college_name': row['college_name'],
                            'course_code': row['course_code'],
                            'course_name': row['course_name'],
                            'category': row['category'],
                            'rank': row.get('rank'),
                            'percentile': row.get('percentile'),
                            'imported_at': now,
                        }
                    )
                    if result.rowcount > 0:
                        inserted += 1
                    else:
                        duplicates += 1
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Error on row: {e}")
                    raise
            
            db.session.commit()
            
            # Final stats
            total = db.session.execute(text("SELECT count(*) FROM college_cutoffs")).scalar()
            colleges = db.session.execute(text("SELECT count(DISTINCT college_code) FROM college_cutoffs")).scalar()
            courses = db.session.execute(text("SELECT count(DISTINCT course_code) FROM college_cutoffs")).scalar()
            
            print(f"\n=== Import Summary ===")
            print(f"Total Records Imported: {inserted}")
            print(f"Duplicate Records Skipped: {duplicates}")
            print(f"Total Colleges: {colleges}")
            print(f"Total Courses: {courses}")
            print(f"Total in DB: {total}")
            
            logger.info("Pipeline completed successfully")
            
        except Exception as e:
            logger.error(f"Pipeline failed with error: {str(e)}")
            raise

if __name__ == "__main__":
    main()