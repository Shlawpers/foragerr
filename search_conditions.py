#!/usr/bin/env python3
"""
search_conditions.py

This module contains functions for determining whether a search should be triggered
for a movie, and for tracking the number of searches triggered per day.
"""

import os
import json
import datetime
import logging
from database import get_plex_metadata

def should_trigger_search(movie):
    """
    Determines whether a search should be triggered for a movie based on:
    1. Whether it has been searched before
    2. If searched before, whether enough time has passed since the last search
    
    Returns True if a search should be triggered, False otherwise.
    """
    # Get identifiers to look up in database
    rating_key = movie.get('ratingKey', '')
    imdb_id = movie.get('imdbId', '')
    tmdb_id = str(movie.get('tmdbId', ''))
    
    # Try to get the movie's metadata from the database
    db_movie = None
    if rating_key:
        db_movie = get_plex_metadata(rating_key=rating_key)
    elif imdb_id:
        db_movie = get_plex_metadata(imdb_id=imdb_id)
    elif tmdb_id:
        db_movie = get_plex_metadata(tmdb_id=tmdb_id)
    
    # If we can't find the movie in the database, assume it's never been searched
    if not db_movie:
        logging.debug(f"Movie '{movie.get('title')}' not found in database, triggering first search.")
        return True
    
    # If the movie has never been searched, trigger a search
    if not db_movie.get('last_radarr_search'):
        logging.debug(f"Movie '{movie.get('title')}' has no recorded search, triggering first search.")
        return True
    
    # If the movie has been searched before, check if enough time has passed
    last_search_str = db_movie.get('last_radarr_search')
    try:
        last_search = datetime.datetime.fromisoformat(last_search_str)
        now = datetime.datetime.now()
        days_since_last_search = (now - last_search).days
        
        # Allow a new search if it's been more than 7 days since the last search
        # You can adjust this threshold as needed
        if days_since_last_search >= 7:
            logging.info(f"Movie '{movie.get('title')}' was last searched {days_since_last_search} days ago, allowing new search.")
            return True
        else:
            logging.info(f"Movie '{movie.get('title')}' was searched only {days_since_last_search} days ago, skipping search.")
            return False
    except (ValueError, TypeError):
        logging.warning(f"Invalid last_radarr_search value for movie '{movie.get('title')}': {last_search_str}")
        return True  # If we can't parse the date, allow a search to be safe

def read_daily_search_count(file_path):
    """Read the daily search count from the JSON file."""
    today = datetime.date.today().isoformat()
    if not os.path.exists(file_path):
        return 0
    
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        # Reset count if the date has changed
        if data.get("date") != today:
            return 0
        return data.get("count", 0)
    except Exception as e:
        logging.error(f"Error reading daily search count file: {e}")
        return 0

def increment_daily_search_count(file_path):
    """Increment the daily search count in the JSON file."""
    today = datetime.date.today().isoformat()
    count = read_daily_search_count(file_path) + 1
    
    try:
        with open(file_path, 'w') as f:
            json.dump({"date": today, "count": count}, f)
        return count
    except Exception as e:
        logging.error(f"Error writing daily search count file: {e}")
        return count

if __name__ == "__main__":
    # Simple test to verify functionality.
    import sys
    logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
    
    # Simulate a movie that hasn't been searched yet.
    test_movie = {"title": "Test Movie", "last_radarr_search": ""}
    trigger = should_trigger_search(test_movie)
    print(f"Should trigger search (no previous search): {trigger}")

    # Simulate a movie with a recent search.
    test_movie["last_radarr_search"] = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    trigger = should_trigger_search(test_movie)
    print(f"Should trigger search (1 hour ago): {trigger}")

    # Test daily search count increment.
    current_count = read_daily_search_count()
    print(f"Current daily search count: {current_count}")
    new_count = increment_daily_search_count()
    print(f"New daily search count: {new_count}")