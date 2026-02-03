#!/usr/bin/env python3
"""
plex_api.py

This module handles remote Plex API calls to fetch the watchlist and detailed metadata from the remote Plex server.
It uses the central database.py module for all metadata storage and retrieval.
"""

import os
import requests
import logging
import yaml
import feedparser
import xml.etree.ElementTree as ET
import sys
from datetime import datetime

# Import database functions instead of using direct SQLite connections
from database import save_plex_metadata, get_plex_metadata

# Configure logging to print to terminal in real time.
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG for thorough logging
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s: %(message)s"
)

# Load Plex configuration from config.yaml with fallbacks
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
                logging.info(f"Loaded Plex configuration from {path}")
                break
    except (IOError, yaml.YAMLError) as e:
        logging.warning(f"Could not load config from {path}: {e}")
        continue

if not config:
    logging.error(f"Could not find config.yaml in any of: {config_paths}")
    sys.exit(1)

# Expected configuration keys:
#   remotePlex.base_url: Remote Plex server URL (e.g., "https://metadata.provider.plex.tv")
#   remotePlex.token: Plex token for remote fetching.
#   remotePlex.library_section (optional) used for watchlist queries.
plex_config = config.get("remotePlex", {})
REMOTE_PLEX_BASE_URL = plex_config.get("base_url", "").strip()
PLEX_TOKEN = plex_config.get("token", "").strip()
LIBRARY_SECTION = plex_config.get("library_section", 1)

# Request timeout in seconds (configurable, default 30s)
REQUEST_TIMEOUT = config.get("remotePlex", {}).get("request_timeout", 30)

if not REMOTE_PLEX_BASE_URL:
    logging.error("Plex base_url not configured in remotePlex section")
    sys.exit(1)
if not PLEX_TOKEN:
    logging.error("Plex token not configured in remotePlex section")
    sys.exit(1)

# --- Detailed Metadata Extraction ---
def extract_guids(video_element):
    """
    Extracts IMDb and TMDB IDs from <Guid> elements.
    """
    guids = {}
    for guid in video_element.findall(".//Guid"):
        guid_id = guid.attrib.get("id", "")
        if guid_id.startswith("imdb://"):
            guids["imdb"] = guid_id.replace("imdb://", "")
        elif guid_id.startswith("tmdb://"):
            guids["tmdb"] = guid_id.replace("tmdb://", "")
    logging.debug(f"Extracted GUIDs: {guids}")
    return guids

def get_detailed_metadata(ratingKey):
    """
    Fetches detailed metadata from the remote Plex server for a given ratingKey.
    """
    url = f"{REMOTE_PLEX_BASE_URL}/library/metadata/{ratingKey}"
    params = {"X-Plex-Token": PLEX_TOKEN}
    logging.debug(f"Fetching detailed metadata for remote ratingKey {ratingKey} from {url}")
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        detailed_video = root.find(".//Video")
        if detailed_video is None:
            logging.warning(f"No <Video> element found for remote ratingKey {ratingKey}.")
            return None
        return root
    except requests.exceptions.Timeout:
        logging.error(f"Request timed out fetching metadata for ratingKey {ratingKey}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching detailed metadata for remote ratingKey {ratingKey}: {str(e)}")
        return None
    except ET.ParseError as e:
        logging.error(f"Error parsing XML for ratingKey {ratingKey}: {str(e)}")
        return None

def get_personal_watchlist():
    """
    Fetches the personal Plex watchlist from the remote Plex server using offset-based pagination.
    Uses the database.py module for storing metadata.
    Returns a list of movie dictionaries with keys: title, ratingKey (remote), imdbId, tmdbId.
    """
    movies = []
    offset = 0
    totalSize = None
    page_size = 20
    seen_keys = set()

    while True:
        url = f"{REMOTE_PLEX_BASE_URL}/library/sections/watchlist/all"
        params = {
            "X-Plex-Token": PLEX_TOKEN,
            "X-Plex-Container-Start": offset,
            "X-Plex-Container-Size": page_size
        }
        logging.debug(f"Fetching watchlist summary with params: {params}")
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except requests.exceptions.Timeout:
            logging.error(f"Request timed out fetching watchlist at offset {offset}")
            break
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching watchlist summary at offset {offset}: {str(e)}")
            break
        except ET.ParseError as e:
            logging.error(f"Error parsing watchlist XML at offset {offset}: {str(e)}")
            break

        if totalSize is None:
            totalSize = int(root.attrib.get("totalSize", "0"))
            logging.info(f"Total items in watchlist: {totalSize}")

        videos = root.findall("Video")
        if not videos:
            logging.info("No more watchlist items found; ending pagination.")
            break

        for video in videos:
            ratingKey = video.attrib.get("ratingKey")
            title = video.attrib.get("title")
            year = int(video.attrib.get("year", 0))
            
            if not ratingKey or ratingKey in seen_keys:
                continue
            seen_keys.add(ratingKey)
            
            logging.debug(f"Processing video '{title}' with remote ratingKey: {ratingKey}")

            # Check if we have this movie in our database
            db_movie = get_plex_metadata(rating_key=ratingKey)
            if db_movie:
                movies.append({
                    "title": title,
                    "ratingKey": ratingKey,
                    "imdbId": db_movie.get("imdb_id"),
                    "tmdbId": db_movie.get("tmdb_id"),
                    "year": year
                })
                continue

            # If not in database, fetch detailed metadata (this is the slow part)
            logging.info(f"Fetching metadata for '{title}' ({len(seen_keys)}/{totalSize})...")
            detailed_xml = get_detailed_metadata(ratingKey)
            if detailed_xml is None:
                continue

            detailed_video = detailed_xml.find(".//Video")
            if detailed_video is None:
                continue

            guids = extract_guids(detailed_video)
            imdb_id = guids.get("imdb")
            tmdb_id = guids.get("tmdb")
            
            # Create movie object for database
            movie_data = {
                "title": title,
                "ratingKey": ratingKey,
                "imdbId": imdb_id, 
                "tmdbId": tmdb_id,
                "year": year
            }
            
            # Save to database
            save_plex_metadata(movie_data)
            movies.append(movie_data)

        logging.info(f"Processed {len(seen_keys)}/{totalSize} watchlist items...")
        offset += page_size
        if offset >= totalSize:
            break

    logging.info(f"Fetched {len(seen_keys)} unique items from personal Plex watchlist.")
    return movies

def get_friends_watchlist(feed_url):
    """
    Fetches the friends' Plex watchlist from an RSS feed.
    Returns a list of movie dictionaries with author (Plex user ID) for tagging.

    Each entry includes:
        - title: Movie/show title
        - pubDate: When added to watchlist
        - guid: Contains IMDB/TVDB ID
        - imdbId: Extracted IMDB ID
        - plex_author_id: Plex user ID of the friend who added it
        - category: "movie" or "show"
    """
    try:
        feed = feedparser.parse(feed_url)
        movies = []
        for entry in feed.entries:
            # Extract author (Plex user ID) - feedparser exposes this as 'author'
            plex_author_id = getattr(entry, 'author', None) or entry.get('author', '')

            # Extract category (movie vs show)
            category = ''
            if hasattr(entry, 'tags') and entry.tags:
                category = entry.tags[0].get('term', '') if isinstance(entry.tags[0], dict) else ''
            if not category:
                # Fallback: check the category element
                category = getattr(entry, 'category', '')

            movies.append({
                "title": entry.title,
                "pubDate": entry.get("pubDate"),
                "guid": entry.get("guid"),
                "imdbId": extract_imdb_id(entry),
                "plex_author_id": plex_author_id,
                "category": category
            })

        # Log summary of unique authors found
        unique_authors = set(m.get("plex_author_id") for m in movies if m.get("plex_author_id"))
        logging.info(f"Fetched {len(movies)} items from {len(unique_authors)} unique friends in RSS feed")

        return movies
    except Exception as e:
        logging.error(f"Error fetching friends' Plex watchlist: {str(e)}")
        return []

def extract_imdb_id(item):
    """
    Extracts the IMDb ID from the guid of a Plex RSS item.
    """
    guid = ""
    if isinstance(item, dict):
        guid = item.get("guid", "")
    else:
        guid = getattr(item, "guid", "")
    if guid and "imdb://" in guid:
        return guid.split("imdb://")[-1].split("?")[0]
    return None

def merge_watchlists(personal_list, friends_list):
    """
    Merges personal and friends' watchlists, keyed by IMDb ID if available, otherwise by remote ratingKey.
    """
    merged = {}
    for item in personal_list:
        imdb = (item.get("imdbId") or "").strip().lower()
        key = imdb if imdb else item.get("ratingKey")
        if key:
            merged[key] = item
    for item in friends_list:
        imdb = (item.get("imdbId") or "").strip().lower()
        if imdb and imdb not in merged:
            merged[imdb] = item
    merged_list = list(merged.values())
    logging.info(f"Merged watchlist contains {len(merged_list)} unique movies.")
    return merged_list

# Add this function to enhance friends' watchlist items with TMDB IDs
def enhance_friends_watchlist_metadata(friends_list):
    """
    Enhance friends' watchlist items with TMDB IDs by querying Radarr's lookup API.
    """
    enhanced_list = []
    
    for movie in friends_list:
        imdb_id = movie.get("imdbId")
        if not imdb_id:
            logging.debug(f"Skipping movie '{movie.get('title')}' - no IMDB ID available")
            enhanced_list.append(movie)
            continue
            
        # Check if we already have this movie in our database
        db_movie = get_plex_metadata(imdb_id=imdb_id)
        if db_movie and db_movie.get("tmdb_id"):
            movie["tmdbId"] = db_movie.get("tmdb_id")
            enhanced_list.append(movie)
            logging.debug(f"Found cached TMDB ID {movie.get('tmdbId')} for '{movie.get('title')}' in database")
            continue
            
        # If not in database, query Radarr's lookup endpoint
        try:
            from radarr_api import _make_request
            
            endpoint = f"/api/v3/movie/lookup/imdb?imdbId={imdb_id}"
            result = _make_request("GET", endpoint)
            
            if result:
                # Handle both single result (dict) and list results
                if isinstance(result, dict) and "tmdbId" in result:
                    movie["tmdbId"] = result["tmdbId"]
                    logging.debug(f"Found TMDB ID {movie['tmdbId']} for '{movie.get('title')}' via Radarr lookup")
                elif isinstance(result, list) and len(result) > 0 and "tmdbId" in result[0]:
                    movie["tmdbId"] = result[0]["tmdbId"]
                    logging.debug(f"Found TMDB ID {movie['tmdbId']} for '{movie.get('title')}' via Radarr lookup (list result)")
                else:
                    logging.warning(f"Radarr lookup for '{movie.get('title')}' (IMDB: {imdb_id}) returned no usable TMDB ID")
                
                # Save to database for future reference if we got a TMDB ID
                if movie.get("tmdbId"):
                    save_plex_metadata(movie)
            else:
                logging.warning(f"Radarr lookup for '{movie.get('title')}' (IMDB: {imdb_id}) failed")
                
        except Exception as e:
            logging.error(f"Error enhancing metadata for '{movie.get('title')}': {str(e)}")
        
        enhanced_list.append(movie)
    
    # Log summary
    with_tmdb = sum(1 for m in enhanced_list if m.get("tmdbId"))
    logging.info(f"Enhanced {with_tmdb} out of {len(enhanced_list)} friends' watchlist items with TMDB IDs")
    
    return enhanced_list