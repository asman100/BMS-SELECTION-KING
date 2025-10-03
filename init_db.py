#!/usr/bin/env python3
"""
Database initialization script for Railway deployment.
This ensures the database is properly set up on first deployment.
"""

from app import app, db, setup_database

if __name__ == '__main__':
    print("Initializing database...")
    with app.app_context():
        setup_database(app)
    print("Database initialization complete!")
