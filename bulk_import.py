"""
Main bulk import script for PDF extraction and database insertion.
"""
import os
import sys
import logging
from datetime import datetime
import argparse

# Add the project root to the path
sys.path.insert(0, '.')

from config import Config
from parser import parse_all_pdfs
from database import create_table_if_not_exists, insert_records
from flask import Flask

# Configure logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def setup_flask_app():
    """Set up Flask app for database operations."""
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = Config.DATABASE_URL
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "pool_size": 20,
        "max_overflow": 40,
    }
    from database import db
    db.init_app(app)
    return app

def main():
    """Main function to orchestrate PDF parsing and database import."""
    logger.info("Starting PDF extraction and database import pipeline")
    
    # Setup Flask app
    app = setup_flask_app()
    
    try:
        # Create table if it doesn't exist
        create_table_if_not_exists(app)
        
        # Parse all PDFs
        logger.info("Starting PDF parsing...")
        records = parse_all_pdfs(Config.PDF_DIR)
        
        if not records:
            logger.warning("No records extracted from PDFs")
            return
        
        logger.info(f"Successfully extracted {len(records)} records")
        
        # Insert records into database
        logger.info("Starting database import...")
        stats = insert_records(records, app)
        
        # Print summary
        print("\n=== Import Summary ===")
        print(f"Total Records Imported: {stats['inserted']}")
        print(f"Duplicate Records Removed: {stats['duplicates']}")
        print(f"Total Colleges: {stats['colleges']}")
        print(f"Total Courses: {stats['courses']}")
        print(f"Total Records in DB: {stats['total']}")
        
        logger.info("Pipeline completed successfully")
        
    except Exception as e:
        logger.error(f"Pipeline failed with error: {str(e)}")
        raise

if __name__ == "__main__":
    main()
