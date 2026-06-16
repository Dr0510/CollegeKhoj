"""
Configuration settings for the PDF extraction and database import pipeline.
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    # Database configuration
    DATABASE_URL = os.getenv('NEON_DATABASE_URL') or os.getenv('DATABASE_URL') or 'postgresql://localhost/college_recommendation'
    
    # PDF directory
    PDF_DIR = 'uploads/cutoffs'
    
    # Logging configuration
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    
    # Table name for college cutoffs
    TABLE_NAME = 'college_cutoffs'