# College Recommendation System

## Overview

A web-based college recommendation system that helps students find suitable colleges based on their preferences and criteria. The system uses machine learning algorithms to analyze college data including fees, placement rates, NIRF rankings, and ratings to provide personalized recommendations through cosine similarity matching.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture
- **Template Engine**: Jinja2 templates with Bootstrap 5 for responsive UI
- **Styling**: Bootstrap dark theme with Font Awesome icons
- **Client-side**: Vanilla JavaScript with Bootstrap components for interactivity

### Backend Architecture
- **Framework**: Flask web framework with Python
- **Application Structure**: Modular design separating concerns into distinct files:
  - `app.py`: Main application configuration and routes
  - `models.py`: Database models and schema definitions
  - `recommender.py`: Machine learning recommendation engine
  - `main.py`: Application entry point

### Data Storage
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Models**: Single College model storing institution data including:
  - Basic info (name, location, branch)
  - Financial data (fees)
  - Performance metrics (placement rate, NIRF rank, rating)
- **Connection Management**: Connection pooling with health checks

### Recommendation Engine
- **Algorithm**: Cosine similarity-based matching using scikit-learn
- **Feature Engineering**: 
  - Inverts NIRF rankings (lower rank = higher score)
  - Converts fees to affordability scores
  - Normalizes all features using MinMaxScaler
- **Data Processing**: Pandas for data manipulation and NumPy for numerical operations

### File Upload System
- **Security**: Werkzeug secure filename handling
- **Size Limits**: 16MB maximum file upload size
- **Format Support**: CSV file processing for bulk college data import

## External Dependencies

### Core Framework Dependencies
- **Flask**: Web framework with SQLAlchemy extension for database operations
- **Werkzeug**: WSGI utilities and security features including ProxyFix middleware

### Machine Learning Stack
- **scikit-learn**: MinMaxScaler for feature normalization and cosine similarity calculations
- **pandas**: Data manipulation and analysis
- **numpy**: Numerical computing operations

### Database Technology
- **PostgreSQL**: Primary database system
- **SQLAlchemy**: ORM layer for database abstraction and relationship management

### Frontend Libraries
- **Bootstrap 5**: CSS framework for responsive design and dark theme
- **Font Awesome 6**: Icon library for UI elements

### Development Environment
- **Environment Variables**: Configuration management for database URLs and session secrets
- **Logging**: Python's built-in logging module for debugging and monitoring