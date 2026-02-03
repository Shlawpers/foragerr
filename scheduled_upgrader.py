#!/usr/bin/env python3
"""
scheduled_upgrader.py

This module handles periodic upgrades of movies in Radarr.
It operates independently from Plex, focusing solely on:
  1. Fetching movies with the upgrade tag directly from Radarr.
  2. For each movie, checking Radarr's file size information:
       - If the file size meets/exceeds the minimum threshold, clear the upgrade tag.
       - If the file size is below threshold, consider the movie for a search.
  3. Using conditional logic to prioritize movies that haven't been searched recently.
  4. Triggering searches for eligible movies while enforcing per-run and daily search limits.
"""

import time
import schedule
import yaml
import logging
from logging.handlers import RotatingFileHandler
import sys
import json
import os
from datetime import datetime, timedelta

# Import functions from our modules
from radarr_api import update_movie, trigger_search, get_all_movies
from database import get_plex_metadata, mark_movie_as_searched, mark_movie_as_processed
from search_conditions import increment_daily_search_count, read_daily_search_count

# Load configuration from config.yaml with fallbacks
try:
    # Try common locations in order of preference
    config_paths = [
        os.path.join(os.path.dirname(__file__), "config.yaml"),
        os.path.join(os.path.dirname(__file__), "data", "config.yaml"),
        "/app/config.yaml",
        "/app/data/config.yaml"
    ]
    
    config = None
    loaded_path = None
    
    for path in config_paths:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    config = yaml.safe_load(f)
                    loaded_path = path
                    break
        except (IOError, yaml.YAMLError) as e:
            logging.warning(f"Could not load config from {path}: {e}")
            continue
    
    if not config:
        raise FileNotFoundError(f"Could not find config.yaml in any of: {config_paths}")
        
    logging.info(f"Loaded configuration from {loaded_path}")
    
except Exception as e:
    logging.error(f"Failed to load configuration: {str(e)}")
    sys.exit(1)

# Set up logging with rotation to file and console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        RotatingFileHandler('watchlist_sync.log', maxBytes=10485760, backupCount=3),
        logging.StreamHandler(sys.stdout)
    ]
)
if config.get("execution", {}).get("debug_mode", False):
    logging.getLogger().setLevel(logging.DEBUG)
    logging.debug("Debug mode enabled")

def save_daily_search_count(file_path, count):
    """Save the updated daily search count along with today's date."""
    data = {"date": datetime.now().date().isoformat(), "count": count}
    try:
        with open(file_path, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logging.error(f"Error writing daily search count file: {e}")

def fix_paths_for_radarr(movie):
    """
    Ensures that the movie's 'path' and 'rootFolderPath' are correctly set.
    If 'path' is non-empty, attempts to derive the root folder by extracting the portion
    up to and including the "Movies" directory. Otherwise, falls back to the default
    root folder from the configuration.
    """
    path = movie.get("path", "")
    if not path:
        movie["rootFolderPath"] = config["radarr"]["root_folder"]
        return movie
    parts = path.split('/')
    root_folder = ""
    for i, part in enumerate(parts):
        if part.lower() == "movies" and i > 0:
            root_folder = '/'.join(parts[:i+1]) + "/"
            break
    if not root_folder:
        root_folder = config["radarr"]["root_folder"]
    movie["rootFolderPath"] = root_folder
    return movie

def remove_tag_from_movie(movie, tag_id):
    """Remove a tag from a movie using direct API call."""
    import requests

    movie_id = movie["id"]
    title = movie.get("title", f"Unknown (ID: {movie_id})")

    # Get Radarr configuration from loaded config
    radarr_config = config.get("radarr", {})
    raw_url = radarr_config.get("url") or radarr_config.get("base_url")

    # Validate URL before using
    if not raw_url or not str(raw_url).strip():
        logging.error(f"Cannot update movie '{title}': Radarr URL not configured")
        return False
    radarr_url = str(raw_url).strip().rstrip('/')

    api_key = radarr_config.get("apikey")
    if not api_key or not str(api_key).strip():
        logging.error(f"Cannot update movie '{title}': Radarr API key not configured")
        return False

    # Request timeout (default 30s)
    request_timeout = config.get("radarr", {}).get("request_timeout", 30)

    # Make a copy of the movie object
    update_payload = dict(movie)

    # Update tags - remove the specified tag
    current_tags = movie.get("tags", [])
    new_tags = [tag for tag in current_tags if tag != tag_id]

    update_payload["tags"] = new_tags

    logging.debug(f"Movie '{title}' - Original tags: {current_tags}")
    logging.debug(f"Movie '{title}' - New tags: {new_tags}")

    # Make the direct API call
    url = f"{radarr_url}/api/v3/movie/{movie_id}"
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json"
    }

    try:
        response = requests.put(url, headers=headers, json=update_payload, timeout=request_timeout)
        response.raise_for_status()
        logging.info(f"Successfully updated movie '{title}'")
        return True
    except requests.exceptions.Timeout:
        logging.error(f"Request timed out updating movie '{title}'")
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to update movie '{title}': {str(e)}")
        if 'response' in locals() and response is not None:
            logging.error(f"Response status: {response.status_code}")
            logging.error(f"Response text: {response.text}")
        return False

def job_upgrade(dry_run=False):
    """
    The main upgrade job that:
      - Retrieves movies flagged for upgrade from Radarr
      - Based on file size from Radarr:
         * If file size >= min threshold, update Radarr to clear the upgrade tag
         * If file size < min threshold, consider triggering a search
      - Enforce per-run and daily search limits
    """
    try:
        logging.info("Scheduled upgrade job started.")

        # Retrieve movies from Radarr that have the upgrade tag.
        upgrade_tag = int(config["upgrade"]["plex_upgrade_tag"])
        all_radarr_movies = get_all_movies()
        movies_from_radarr = [movie for movie in all_radarr_movies if upgrade_tag in movie.get("tags", [])]
        
        if not movies_from_radarr:
            logging.info("No movies with upgrade tag found in Radarr. Upgrade job completed.")
            return

        logging.info(f"Found {len(movies_from_radarr)} movies with upgrade tag.")

        # Define minimum file size threshold (in bytes) for clearing the upgrade tag.
        min_size_gb = config.get("upgrade", {}).get("min_file_size_gb", 0)
        min_size_bytes = min_size_gb * (1024 ** 3)  # Convert GB to bytes

        # PHASE 1: Process all movies for tag removal if they meet the size threshold
        tag_removal_count = 0
        for movie in movies_from_radarr:
            movie_id = movie.get("id")
            if not movie_id:
                logging.warning(f"Movie has no valid ID: {movie}")
                continue
                
            movie_title = movie.get("title", f"Unknown (ID: {movie_id})")
            size_bytes = movie.get("sizeOnDisk", 0)
            
            # If file size is above threshold, remove the upgrade tag
            if size_bytes >= min_size_bytes:
                logging.info(f"Movie '{movie_title}' meets file size threshold ({size_bytes / (1024**3):.2f} GB). Clearing upgrade tag.")
                
                # Only make API call if the tag actually exists on the movie
                if upgrade_tag in movie.get("tags", []):
                    if not dry_run:
                        result = remove_tag_from_movie(movie, upgrade_tag)
                        if result:
                            mark_movie_as_processed(movie)
                            logging.info(f"Cleared upgrade tag for movie '{movie_title}'.")
                            tag_removal_count += 1
                        else:
                            logging.error(f"Failed to update movie '{movie_title}' for upgrade clearance.")
                    else:
                        logging.info(f"[Dry Run] Would update movie '{movie_title}' to clear upgrade tag.")
                        tag_removal_count += 1
                else:
                    logging.info(f"Movie '{movie_title}' doesn't have the upgrade tag, no API call needed.")

        logging.info(f"Completed tag removal phase: processed {tag_removal_count} movies for tag removal.")

        # PHASE 2: Process eligible movies for search, respecting limits
        # Load daily search count and limits.
        daily_search_file = config.get("upgrade", {}).get("daily_search_count_file", "daily_search_count.json")
        daily_search_count = read_daily_search_count(daily_search_file)
        global_daily_search_limit = config.get("schedule", {}).get("max_daily_searches", None)
        per_run_search_limit = config.get("schedule", {}).get("searches_per_run", 3)
        run_search_count = 0

        # Filter for movies that still need upgrading (below threshold) and haven't been searched recently
        search_candidates = []
        for movie in movies_from_radarr:
            # Skip movies without ID
            movie_id = movie.get("id")
            if not movie_id:
                continue
                
            # Skip movies that meet size threshold (already processed in Phase 1)
            size_bytes = movie.get("sizeOnDisk", 0)
            if size_bytes >= min_size_bytes:
                continue
                
            # Get search history from our database
            db_record = None
            tmdb_id = str(movie.get("tmdbId", "")).strip()
            imdb_id = movie.get("imdbId", "").strip()
            
            if tmdb_id:
                db_record = get_plex_metadata(tmdb_id=tmdb_id)
            
            if not db_record and imdb_id:
                db_record = get_plex_metadata(imdb_id=imdb_id)
            
            last_search_time = datetime.min
            if db_record and db_record.get("last_radarr_search"):
                try:
                    last_search_time = datetime.fromisoformat(db_record.get("last_radarr_search"))
                except (ValueError, TypeError) as e:
                    logging.error(f"Error parsing last_radarr_search for movie {movie.get('title')}: {e}")
            
            # Check if enough time has passed since last search
            hours_since_last = (datetime.now() - last_search_time).total_seconds() / 3600
            if hours_since_last < 24:
                logging.info(f"Skipping movie '{movie.get('title')}' due to recent search ({hours_since_last:.1f} hours ago).")
                continue
                
            # Add to candidates list
            search_candidates.append((movie, size_bytes, last_search_time))

        # Prioritize movies: first by last search time (oldest first), then by file size (smallest first).
        search_candidates.sort(key=lambda x: (x[2], x[1]))
        logging.info(f"Found {len(search_candidates)} movies eligible for upgrade search.")
        
        # Check search limits before starting searches
        if global_daily_search_limit is not None and daily_search_count >= global_daily_search_limit:
            logging.info(f"Global daily search limit reached ({global_daily_search_limit}). No searches will be triggered.")
            search_candidates = []  # Clear the list to skip processing
            
        if per_run_search_limit <= 0:
            logging.info("Per-run search limit is zero. No searches will be triggered.")
            search_candidates = []  # Clear the list to skip processing

        # Process search candidates up to the search limit
        for i, (movie, _, _) in enumerate(search_candidates):
            if i >= per_run_search_limit:
                logging.info(f"Per-run search limit reached ({per_run_search_limit}). Stopping further search triggers.")
                break
                
            if global_daily_search_limit is not None and daily_search_count >= global_daily_search_limit:
                logging.info(f"Global daily search limit reached ({global_daily_search_limit}). Stopping further search triggers.")
                break

            movie_id = movie.get("id")
            movie_title = movie.get("title", f"Unknown (ID: {movie_id})")

            # Trigger search
            logging.info(f"Triggering search for movie '{movie_title}'")
            if not dry_run:
                search_result = trigger_search(movie_id)
                if search_result:
                    logging.info(f"Triggered search for movie '{movie_title}'.")
                    mark_movie_as_searched(movie)
                    run_search_count += 1
                    daily_search_count += 1
                    increment_daily_search_count(daily_search_file)
                else:
                    logging.error(f"Failed to trigger search for movie '{movie_title}'.")
            else:
                logging.info(f"[Dry Run] Would trigger search for movie '{movie_title}'.")
                run_search_count += 1  # Still count it for reporting purposes

        # Update daily search count file
        save_daily_search_count(daily_search_file, daily_search_count)
        logging.info(f"Scheduled upgrade job completed. Processed {len(movies_from_radarr)} movies, removed tags from {tag_removal_count} movies, triggered {run_search_count} searches.")
        
    except Exception as e:
         logging.exception(f"Exception in job_upgrade: {e}")

if __name__ == "__main__":
    # Set up basic logging with rotation before anything else
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s %(levelname)s: %(message)s',
        handlers=[
            RotatingFileHandler('upgrader_standalone.log', maxBytes=10485760, backupCount=3),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    logging.info("Starting scheduled_upgrader in standalone mode")
    
    # Run the job with default settings
    try:
        job_upgrade(dry_run=False)
    except Exception as e:
        logging.exception(f"Failed to run standalone job: {e}")