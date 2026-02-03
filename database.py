#!/usr/bin/env python3
"""
database.py

This module handles the database operations for plex-watchlister.
It uses SQLite to store metadata about Plex movies, including timestamps
for when a movie was last processed or searched.
"""

import sqlite3
import datetime
import logging

DATABASE_FILE = "plex_watchlister.db"

def get_db_connection():
    """Establish and return a database connection with row access by column name."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    """Create the plex_metadata table if it does not already exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    # The table now uses a synthetic primary key (id) so that even if rating_key is NULL,
    # we can have multiple rows. However, rating_key remains UNIQUE when provided.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plex_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rating_key TEXT UNIQUE,
            imdb_id TEXT,
            tmdb_id TEXT,
            title TEXT,
            year INTEGER,
            last_radarr_search TEXT,
            last_processed TEXT
        )
    ''')
    conn.commit()
    conn.close()
    logging.info("Database initialized")

def get_plex_metadata(rating_key=None, imdb_id=None, tmdb_id=None):
    """
    Retrieve a plex_metadata record using any available identifier.
    At least one identifier must be provided.
    Returns a dictionary instead of a sqlite3.Row object.
    """
    # Clean up and check inputs
    if rating_key:
        rating_key = str(rating_key).strip()
    if imdb_id:
        imdb_id = str(imdb_id).strip()
    if tmdb_id:
        tmdb_id = str(tmdb_id).strip()

    # Check if we have any valid identifiers after cleaning
    if not (rating_key or imdb_id or tmdb_id):
        logging.warning("get_plex_metadata called with no valid identifiers")
        return None

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        conditions = []
        params = []
        if rating_key:
            conditions.append("rating_key = ?")
            params.append(rating_key)
        if imdb_id:
            conditions.append("imdb_id = ?")
            params.append(imdb_id)
        if tmdb_id:
            conditions.append("tmdb_id = ?")
            params.append(tmdb_id)

        query = "SELECT * FROM plex_metadata WHERE " + " OR ".join(conditions)
        cursor.execute(query, params)
        row = cursor.fetchone()

        # Convert sqlite3.Row to dictionary before returning
        return dict(row) if row else None
    except Exception as e:
        logging.error(f"Error in get_plex_metadata: {e}")
        return None
    finally:
        if conn:
            conn.close()

def save_plex_metadata(movie_data):
    """
    Save or update plex metadata.
    Expects movie_data to be a dict with keys:
    ratingKey, imdbId, tmdbId, title, and year.
    Uses UPSERT on rating_key.
    Returns True on success, False on failure.
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Convert empty strings to None
        rating_key = movie_data.get('ratingKey') or None
        imdb_id = movie_data.get('imdbId') or None
        tmdb_id = movie_data.get('tmdbId')
        tmdb_id = str(tmdb_id) if tmdb_id is not None and tmdb_id != "" else None
        title = movie_data.get('title') or "Unknown"
        year = movie_data.get('year') or 0

        # IMPORTANT: Don't set last_radarr_search here - we do that in mark_movie_as_searched
        # Use UPSERT: if a record with the given rating_key exists, update it
        cursor.execute('''
            INSERT INTO plex_metadata (rating_key, imdb_id, tmdb_id, title, year)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(rating_key) DO UPDATE SET
                imdb_id = excluded.imdb_id,
                tmdb_id = excluded.tmdb_id,
                title = excluded.title,
                year = excluded.year
        ''', (rating_key, imdb_id, tmdb_id, title, year))

        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error in save_plex_metadata: {e}")
        return False
    finally:
        if conn:
            conn.close()

def mark_movie_as_searched(movie):
    """
    Mark a movie as searched with the current timestamp.
    Works with both Plex and Radarr movie objects.
    Returns True on success, False on failure.
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get identifiers - handle both Plex and Radarr objects
        rating_key = movie.get('ratingKey') or None
        imdb_id = movie.get('imdbId') or None
        tmdb_id = movie.get('tmdbId')
        tmdb_id = str(tmdb_id) if tmdb_id is not None and tmdb_id != "" else None
        title = movie.get('title') or "Unknown"
        year = movie.get('year') or 0
        timestamp = datetime.datetime.now().isoformat()

        # Find existing record first to update by ANY available ID
        existing_id = None
        if rating_key or imdb_id or tmdb_id:
            conditions = []
            params = []
            if rating_key:
                conditions.append("rating_key = ?")
                params.append(rating_key)
            if imdb_id:
                conditions.append("imdb_id = ?")
                params.append(imdb_id)
            if tmdb_id:
                conditions.append("tmdb_id = ?")
                params.append(tmdb_id)

            query = "SELECT id FROM plex_metadata WHERE " + " OR ".join(conditions)
            cursor.execute(query, params)
            result = cursor.fetchone()
            if result:
                existing_id = result[0]

        # If found, update the existing record
        if existing_id:
            update_query = """
                UPDATE plex_metadata
                SET imdb_id = ?, tmdb_id = ?, title = ?, year = ?, last_radarr_search = ?
                WHERE id = ?
            """
            cursor.execute(update_query, (imdb_id, tmdb_id, title, year, timestamp, existing_id))
            logging.debug(f"Updated search timestamp for movie '{title}'")
        else:
            # Otherwise insert a new record
            insert_query = """
                INSERT INTO plex_metadata
                (rating_key, imdb_id, tmdb_id, title, year, last_radarr_search)
                VALUES (?, ?, ?, ?, ?, ?)
            """
            cursor.execute(insert_query, (rating_key, imdb_id, tmdb_id, title, year, timestamp))
            logging.debug(f"Inserted new record with search timestamp for movie '{title}'")

        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error in mark_movie_as_searched for '{movie.get('title', 'Unknown')}': {e}")
        return False
    finally:
        if conn:
            conn.close()

def mark_movie_as_processed(movie):
    """
    Mark a movie as processed with the current timestamp.
    Works with both Plex and Radarr movie objects.
    Returns True on success, False on failure.
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get identifiers - handle both Plex and Radarr objects
        rating_key = movie.get('ratingKey') or None
        imdb_id = movie.get('imdbId') or None
        tmdb_id = movie.get('tmdbId')
        tmdb_id = str(tmdb_id) if tmdb_id is not None and tmdb_id != "" else None
        title = movie.get('title') or "Unknown"
        year = movie.get('year') or 0
        timestamp = datetime.datetime.now().isoformat()

        # Find existing record first to update by ANY available ID
        existing_id = None
        if rating_key or imdb_id or tmdb_id:
            conditions = []
            params = []
            if rating_key:
                conditions.append("rating_key = ?")
                params.append(rating_key)
            if imdb_id:
                conditions.append("imdb_id = ?")
                params.append(imdb_id)
            if tmdb_id:
                conditions.append("tmdb_id = ?")
                params.append(tmdb_id)

            query = "SELECT id FROM plex_metadata WHERE " + " OR ".join(conditions)
            cursor.execute(query, params)
            result = cursor.fetchone()
            if result:
                existing_id = result[0]

        # If found, update the existing record
        if existing_id:
            update_query = """
                UPDATE plex_metadata
                SET imdb_id = ?, tmdb_id = ?, title = ?, year = ?, last_processed = ?
                WHERE id = ?
            """
            cursor.execute(update_query, (imdb_id, tmdb_id, title, year, timestamp, existing_id))
            logging.debug(f"Updated processing timestamp for movie '{title}'")
        else:
            # Otherwise insert a new record
            insert_query = """
                INSERT INTO plex_metadata
                (rating_key, imdb_id, tmdb_id, title, year, last_processed)
                VALUES (?, ?, ?, ?, ?, ?)
            """
            cursor.execute(insert_query, (rating_key, imdb_id, tmdb_id, title, year, timestamp))
            logging.debug(f"Inserted new record with processing timestamp for movie '{title}'")

        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error in mark_movie_as_processed for '{movie.get('title', 'Unknown')}': {e}")
        return False
    finally:
        if conn:
            conn.close()

# For testing purposes when run as a script
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    initialize_database()
    
# Initialize the database when this module is imported
initialize_database()