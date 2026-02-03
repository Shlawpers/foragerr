#!/usr/bin/env python3
"""
main.py

This module synchronizes your Plex watchlists with Radarr.
It performs the following steps:
  1. Fetches personal (and optionally friends') watchlists from the remote Plex server.
  2. Merges these watchlists into a unique list.
  3. Builds a live Radarr index to compare against Plex watchlist items.
  4. For each Plex watchlist movie:
       - If it's not in Radarr, add it with the configured root folder and tags (watchlist tag and upgrade tag), 
         then trigger a search (if allowed by search limit counter)
       - If it is already in Radarr, compare the existing record with a new payload (using needs_update()).
         If the payload differs and the movie qualifies for upgrade (based on file size from Radarr),
         trigger an update and a search.
  5. Daily and per-run search limits are enforced.
  6. All processed movies are marked in the database.
  7. API calls are made only when necessary (when there's a difference between the current movie and the payload)
"""

import argparse
import logging
from logging.handlers import RotatingFileHandler
import yaml
import json
import datetime
import os
import sys

# Import functions from our modules.
from plex_api import get_personal_watchlist, get_friends_watchlist, merge_watchlists, enhance_friends_watchlist_metadata
from radarr_api import build_radarr_index, add_movie, trigger_search, get_all_movies, update_movie, get_or_create_tag
from database import get_plex_metadata, save_plex_metadata, mark_movie_as_searched, mark_movie_as_processed
from search_conditions import should_trigger_search, increment_daily_search_count, read_daily_search_count
from jellyseerr_api import fetch_user_mapping, get_username_for_plex_id

# Load configuration from config.yaml with fallbacks.
config = None
config_paths = [
    os.path.join(os.path.dirname(__file__), "config.yaml"),
    os.path.join(os.path.dirname(__file__), "data", "config.yaml"),
    "/app/config.yaml",
    "/app/data/config.yaml"
]

for path in config_paths:
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                config = yaml.safe_load(f)
                break
    except (IOError, yaml.YAMLError) as e:
        continue

if not config:
    print(f"ERROR: Could not find config.yaml in any of: {config_paths}", file=sys.stderr)
    sys.exit(1)

# Set up logging with rotation to file and console.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        RotatingFileHandler('watchlist_sync.log', maxBytes=10485760, backupCount=3),
        logging.StreamHandler()
    ]
)

def save_daily_search_count(file_path, count):
    """Save the updated daily search count along with today's date."""
    data = {"date": datetime.date.today().isoformat(), "count": count}
    try:
        with open(file_path, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logging.error(f"Error writing daily search count file: {e}")

def needs_update(current_movie, new_payload):
    """
    Compare key fields between the current Radarr movie record and the new payload.
    Returns True if any relevant field differs, indicating an update is needed.
    """
    keys_to_check = ["monitored", "qualityProfileId", "rootFolderPath", "path", "minimumAvailability"]
    for key in keys_to_check:
        if str(current_movie.get(key)) != str(new_payload.get(key)):
            return True
    # Compare tags as sets (order-independent).
    current_tags = set(current_movie.get("tags", []))
    new_tags = set(new_payload.get("tags", []))
    if current_tags != new_tags:
        return True
    # Compare the search option.
    current_options = current_movie.get("addOptions", {})
    new_options = new_payload.get("addOptions", {})
    if current_options.get("searchForMovie") != new_options.get("searchForMovie"):
        return True
    return False

def fix_paths_for_radarr(movie):
    """
    Ensures that the movie's file path and root folder are correctly set for Radarr.
    If the 'path' is non-empty, attempts to derive the root folder (e.g., the portion up to "Movies").
    If the 'path' is missing or invalid, falls back to the default root folder from config.
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

def test_radarr_connection():
    """Test the connection to Radarr by accessing the system status endpoint."""
    from radarr_api import _make_request
    endpoint = "/api/v3/system/status"
    result = _make_request("GET", endpoint)
    if result is not None:
        logging.info("Successfully connected to Radarr.")
        return True
    logging.error("Failed to connect to Radarr. Check that Radarr is running and your configuration is correct.")
    return False

def process_watchlist(dry_run=False, scheduled_run=False):
    """Main watchlist processing function that syncs Plex watchlist items with Radarr."""
    logging.info("Starting watchlist sync process.")

    # First, test connection to Radarr before proceeding
    if not test_radarr_connection():
        logging.error("Watchlist sync aborted due to Radarr connection failure.")
        return

    # Initialize friend tagging if enabled
    user_tagging_config = config.get("remotePlex", {}).get("friends_watchlist", {}).get("user_tagging", {})
    user_tagging_enabled = user_tagging_config.get("enabled", False)
    user_mapping = {}
    manual_mappings = {}
    tag_prefix = "friend-"
    default_tag_name = "unknown"

    if user_tagging_enabled:
        logging.info("Friend user tagging is enabled, fetching user mappings from Jellyseerr...")
        jellyseerr_url = user_tagging_config.get("jellyseerr_url", "")
        jellyseerr_api_key = user_tagging_config.get("jellyseerr_api_key", "")
        tag_prefix = user_tagging_config.get("tag_prefix", "friend-")
        default_tag_name = user_tagging_config.get("default_tag_name", "unknown")
        manual_mappings = user_tagging_config.get("manual_mappings", {})

        if jellyseerr_url and jellyseerr_api_key:
            user_mapping = fetch_user_mapping(jellyseerr_url, jellyseerr_api_key)
            logging.info(f"Loaded {len(user_mapping)} user mappings from Jellyseerr")
        else:
            logging.warning("Jellyseerr URL or API key not configured, using manual mappings only")

    # Step 1: Fetch remote watchlists.
    personal_list = get_personal_watchlist()
    logging.info(f"Fetched {len(personal_list)} items from personal Plex watchlist.")

    friends_list = []
    if config.get("remotePlex", {}).get("friends_watchlist", {}).get("enabled", False):
        if config["remotePlex"]["friends_watchlist"].get("method", "").lower() == "rss":
            feed_url = config["remotePlex"]["friends_watchlist"].get("feed_url")
            friends_list = get_friends_watchlist(feed_url)
            logging.info(f"Fetched {len(friends_list)} items from friends' watchlist RSS feed.")

            # Add this line to enhance friends' watchlist with TMDB IDs
            friends_list = enhance_friends_watchlist_metadata(friends_list)

    # Step 2: Merge watchlists.
    combined_list = merge_watchlists(personal_list, friends_list)
    logging.info(f"Total unique watchlist items to process: {len(combined_list)}")

    # Step 3: Build Radarr index from live Radarr API.
    radarr_index = build_radarr_index()
    logging.info(f"Radarr index built with {len(radarr_index)} movies.")

    # Step 4: Load daily search count and set limits.
    daily_search_file = config.get("upgrade", {}).get("daily_search_count_file", "daily_search_count.json")
    daily_search_count = read_daily_search_count(daily_search_file)
    global_daily_search_limit = config.get("schedule", {}).get("max_daily_searches", None)
    per_run_search_limit = config.get("schedule", {}).get("searches_per_run", 3)
    run_search_count = 0

    # Retrieve tag values.
    watchlist_tag = int(config["radarr"]["tags"]["watchlist"])
    upgrade_tag = int(config["upgrade"]["plex_upgrade_tag"])

    # Step 5: Process each movie in the combined watchlist.
    for movie in combined_list:
        # Save basic metadata to database
        save_plex_metadata(movie)

        imdb_id = (movie.get("imdbId") or "").strip().lower()
        tmdb_id = str(movie.get("tmdbId", "")).strip() if movie.get("tmdbId") else ""
        radarr_movie = None

        # Determine friend tag if this movie came from a friend's watchlist
        friend_tag_id = None
        if user_tagging_enabled and movie.get("plex_author_id"):
            plex_author_id = movie.get("plex_author_id")
            username = get_username_for_plex_id(plex_author_id, user_mapping, manual_mappings, default_tag_name)
            friend_tag_name = f"{tag_prefix}{username}"
            friend_tag_id = get_or_create_tag(friend_tag_name)
            if friend_tag_id:
                logging.debug(f"Movie '{movie.get('title')}' from friend '{username}' - will use tag '{friend_tag_name}' (ID: {friend_tag_id})")
            else:
                logging.warning(f"Failed to get/create friend tag '{friend_tag_name}' for movie '{movie.get('title')}'")
        
        # Find the movie in Radarr by IMDb ID or TMDB ID
        if imdb_id:
            radarr_movie = radarr_index.get(imdb_id)
        elif tmdb_id:
            for m in radarr_index.values():
                if str(m.get("tmdbId", "")).strip() == tmdb_id:
                    radarr_movie = m
                    break

        # Skip movies that already have the watchlist tag
        if radarr_movie and watchlist_tag in radarr_movie.get("tags", []):
            logging.info(f"Skipping movie '{radarr_movie['title']}' as it already has the watchlist tag.")
            continue

        # Handle new movies (not in Radarr)
        if not radarr_movie:
            logging.info(f"New movie detected: '{movie.get('title')}' (IMDb: '{imdb_id}', TMDB: '{tmdb_id}'). Adding to Radarr.")
            
            # VALIDATION: Skip movies missing a TMDB ID
            if not tmdb_id:
                logging.error(f"Cannot add movie '{movie.get('title')}' to Radarr: Missing required TMDB ID")
                logging.error(f"Movie details: IMDb: '{imdb_id}', Title: '{movie.get('title')}'")
                continue
            
            # Ensure TMDB ID is an integer for Radarr API
            try:
                tmdb_id_int = int(tmdb_id)
                # Build tags list - always include watchlist and upgrade tags, add friend tag if applicable
                movie_tags = [watchlist_tag, upgrade_tag]
                if friend_tag_id:
                    movie_tags.append(friend_tag_id)

                add_payload = {
                    "title": movie.get("title"),
                    "qualityProfileId": int(config["radarr"].get("default_quality_profile", 1)),
                    "rootFolderPath": config["radarr"]["root_folder"],
                    "monitored": True,
                    "minimumAvailability": "announced",
                    "tags": movie_tags,
                    "addOptions": {"searchForMovie": False},  # Will be set to True if eligible later
                    "tmdbId": tmdb_id_int  # Properly converted to integer
                }
            except ValueError:
                logging.error(f"Cannot add movie '{movie.get('title')}' to Radarr: Invalid TMDB ID format '{tmdb_id}'")
                continue
            
            if not dry_run:
                add_result = add_movie(add_payload)
                if add_result:
                    logging.info(f"Successfully added new movie '{movie.get('title')}' to Radarr.")
                    # Mark the movie as processed immediately
                    mark_movie_as_processed(add_result)
                    
                    # Trigger immediate search if allowed by limits
                    search_for_movie = True
                    if global_daily_search_limit is not None and daily_search_count >= global_daily_search_limit:
                        logging.info(f"Global daily search limit reached ({global_daily_search_limit}). Search not triggered for '{movie.get('title')}'.")
                        search_for_movie = False
                    if per_run_search_limit is not None and run_search_count >= per_run_search_limit:
                        logging.info(f"Per-run search limit reached ({per_run_search_limit}). Search not triggered for '{movie.get('title')}'.")
                        search_for_movie = False
                        
                    if search_for_movie:
                        search_result = trigger_search(add_result["id"])
                        if search_result:
                            logging.info(f"Triggered search for new movie '{add_result.get('title')}'.")
                            mark_movie_as_searched(add_result)
                            run_search_count += 1
                            daily_search_count += 1
                            increment_daily_search_count(daily_search_file)
                        else:
                            logging.error(f"Failed to trigger search for new movie '{add_result.get('title')}'.")
                else:
                    logging.error(f"Failed to add movie '{movie.get('title')}' to Radarr.")
            else:
                logging.info(f"[Dry Run] Would add new movie '{movie.get('title')}' to Radarr and trigger search if allowed.")
            continue

        # Handle existing movies
        logging.info(f"Found movie in Radarr for title '{radarr_movie['title']}'.")
        
        # Check file size directly from Radarr for upgrade eligibility
        size_bytes = radarr_movie.get("sizeOnDisk", 0)
        movie_size_gb = size_bytes / (1024 ** 3) if size_bytes else 0
        min_upgrade_gb = config.get("upgrade", {}).get("min_file_size_gb", 0)
        eligible_for_upgrade = (movie_size_gb < min_upgrade_gb)
        
        if eligible_for_upgrade:
            logging.info(f"Movie '{radarr_movie['title']}' qualifies for upgrade (size {movie_size_gb:.2f}GB < {min_upgrade_gb}GB).")
        else:
            logging.info(f"Movie '{radarr_movie['title']}' does NOT qualify for upgrade (size {movie_size_gb:.2f}GB >= {min_upgrade_gb}GB).")
        
        # Create tags list - always include watchlist tag, add upgrade tag if eligible, add friend tag if applicable
        tags = list(set(radarr_movie.get("tags", []) + [watchlist_tag]))
        if eligible_for_upgrade:
            tags = list(set(tags + [upgrade_tag]))
        else:
            # Remove upgrade tag if present and not eligible
            tags = [tag for tag in tags if tag != upgrade_tag]
        # Add friend tag if this movie came from a friend's watchlist
        if friend_tag_id and friend_tag_id not in tags:
            tags.append(friend_tag_id)
        
        # Build update payload
        payload = {
            "id": radarr_movie["id"],
            "title": radarr_movie["title"],
            "qualityProfileId": radarr_movie.get("qualityProfileId", int(config["radarr"].get("default_quality_profile", 1))),
            "rootFolderPath": radarr_movie.get("rootFolderPath", config["radarr"]["root_folder"]),
            "path": radarr_movie.get("path", ""),
            "monitored": radarr_movie.get("monitored", True),
            "minimumAvailability": radarr_movie.get("minimumAvailability", "released"),
            "tags": tags,
            "addOptions": {"searchForMovie": False}  # Will set to True if triggering a search
        }
        
        # Add IDs if available
        if imdb_id:
            payload["imdbId"] = imdb_id
        if tmdb_id:
            payload["tmdbId"] = tmdb_id
            
        # Remove empty ID fields
        for key in ["imdbId", "tmdbId"]:
            if key in payload and not payload.get(key, "").strip():
                del payload[key]

        # Fix paths if necessary
        payload = fix_paths_for_radarr(payload)
        
        # Only update if payload differs from current movie
        if not dry_run and needs_update(radarr_movie, payload):
            logging.info(f"Updating movie '{radarr_movie['title']}' with payload: {json.dumps(payload)}")
            result = update_movie(radarr_movie["id"], payload)
            if result:
                logging.info(f"Successfully updated movie '{radarr_movie['title']}'")
                mark_movie_as_processed(radarr_movie)
            else:
                logging.error(f"Failed to update movie '{radarr_movie['title']}'")
        else:
            if dry_run:
                logging.info(f"[Dry Run] Would update movie '{radarr_movie['title']}' with payload: {json.dumps(payload)}")
            else:
                logging.info(f"No update needed for movie '{radarr_movie['title']}', skipping API call.")

        # Trigger search if eligible and within limits
        if not dry_run and eligible_for_upgrade:
            # Get database record to check if this movie has EVER been searched before
            db_movie = None
            if imdb_id:
                db_movie = get_plex_metadata(imdb_id=imdb_id)
            elif tmdb_id:
                db_movie = get_plex_metadata(tmdb_id=tmdb_id)
            elif movie.get("ratingKey"):
                db_movie = get_plex_metadata(rating_key=movie.get("ratingKey"))
                
            # Only trigger search if movie has NEVER been searched before
            if db_movie and db_movie.get("last_radarr_search"):
                logging.info(f"Movie '{radarr_movie['title']}' has been searched before. Skipping search in main sync.")
            elif global_daily_search_limit is not None and daily_search_count >= global_daily_search_limit:
                logging.info(f"Global daily search limit reached ({global_daily_search_limit}). Search not triggered for '{radarr_movie['title']}'.")
            elif per_run_search_limit is not None and run_search_count >= per_run_search_limit:
                logging.info(f"Per-run search limit reached ({per_run_search_limit}). Search not triggered for '{radarr_movie['title']}'.")
            else:
                search_result = trigger_search(radarr_movie["id"])
                if search_result:
                    logging.info(f"Triggered search for movie '{radarr_movie['title']}' (ID: {radarr_movie['id']}).")
                    mark_movie_as_searched(movie)
                    run_search_count += 1
                    daily_search_count += 1
                    increment_daily_search_count(daily_search_file)
                else:
                    logging.error(f"Failed to trigger search for movie '{radarr_movie['title']}'.")
        elif dry_run and eligible_for_upgrade:
            # Check database for dry run logging as well
            db_movie = None
            if imdb_id:
                db_movie = get_plex_metadata(imdb_id=imdb_id)
            elif tmdb_id:
                db_movie = get_plex_metadata(tmdb_id=tmdb_id)
            elif movie.get("ratingKey"):
                db_movie = get_plex_metadata(rating_key=movie.get("ratingKey"))
                
            if db_movie and db_movie.get("last_radarr_search"):
                logging.info(f"[Dry Run] Movie '{radarr_movie['title']}' has been searched before. Would skip search in main sync.")
            else:
                logging.info(f"[Dry Run] Would trigger search for movie '{radarr_movie['title']}' (eligible for upgrade).")

    # Save final search count and log completion
    logging.info(f"Watchlist sync completed. Processed {len(combined_list)} movies, triggered {run_search_count} searches.")
    save_daily_search_count(daily_search_file, daily_search_count)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Plex watchlist sync process.")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode (do not update Radarr)")
    parser.add_argument("--scheduled", action="store_true", help="Run as part of a scheduled job")
    args = parser.parse_args()
    
    process_watchlist(dry_run=args.dry_run, scheduled_run=args.scheduled)