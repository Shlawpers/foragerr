#!/usr/bin/env python3
"""
radarr_api.py

This module provides functions for interacting with Radarr's API.
It includes functionality to update movies, retrieve file information,
list all movies, trigger searches, and add new movies.
Configuration is loaded from config.yaml.
"""

import requests
import yaml
import logging
import json
import os  # Add this import
import sys  # Add this import

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

radarr_config = config.get("radarr", {})
# Use "url" if present, otherwise "base_url"
_raw_url = radarr_config.get("url") or radarr_config.get("base_url")
if not _raw_url or not str(_raw_url).strip():
    raise KeyError("Radarr URL not found or empty in config.")
RADARR_URL = str(_raw_url).strip().rstrip('/')

API_KEY = radarr_config.get("apikey")
if not API_KEY or not str(API_KEY).strip():
    raise KeyError("Radarr API key not found or empty in config.")
API_KEY = str(API_KEY).strip()

# Request timeout in seconds (configurable, default 30s)
REQUEST_TIMEOUT = config.get("radarr", {}).get("request_timeout", 30)

def get_headers():
    return {"Content-Type": "application/json"}

def _make_request(method, endpoint, payload=None):
    """
    Make an HTTP request to Radarr API with timeout and proper error handling.
    Returns the JSON response on success, None on failure.
    """
    url = f"{RADARR_URL}{endpoint}"
    headers = {
        "X-Api-Key": API_KEY,
        "Content-Type": "application/json"
    }
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        elif method == "PUT":
            response = requests.put(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, timeout=REQUEST_TIMEOUT)
        else:
            logging.error(f"Unsupported HTTP method: {method}")
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        logging.error(f"Request to {url} timed out after {REQUEST_TIMEOUT}s")
        return None
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Connection error during {method} request to {url}: {e}")
        return None
    except requests.exceptions.HTTPError as e:
        logging.error(f"HTTP error during {method} request to {url}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Request error during {method} request to {url}: {e}")
        return None
    except ValueError as e:
        logging.error(f"Invalid JSON response from {url}: {e}")
        return None

def test_connection():
    """
    Test the connection to Radarr by accessing the system status endpoint.
    Returns True if connection is successful, False otherwise.
    """
    endpoint = "/api/v3/system/status"
    result = _make_request("GET", endpoint)
    if result is not None:
        logging.info("Successfully connected to Radarr.")
        return True
    logging.error("Failed to connect to Radarr. Check that Radarr is running and your configuration is correct.")
    return False

def update_movie(movie_id, payload):
    """
    Update an existing movie in Radarr.
    The payload should include keys like "path", "rootFolderPath", etc.
    """
    endpoint = f"/api/v3/movie/{movie_id}"
    return _make_request("PUT", endpoint, payload)

def get_movie_file_info(movie_id):
    """
    Retrieve file information for a movie from Radarr.
    Returns a dict containing details extracted from the movie's 'movieFile' field.
    """
    endpoint = f"/api/v3/movie/{movie_id}"
    movie_data = _make_request("GET", endpoint)
    if movie_data:
        return movie_data.get("movieFile", {})
    else:
        logging.error(f"Error retrieving file info for movie {movie_id}.")
        return None

def get_all_movies():
    """
    Retrieve all movies from Radarr.
    """
    endpoint = "/api/v3/movie"
    movies = _make_request("GET", endpoint)
    if movies is not None:
        logging.info(f"Retrieved {len(movies)} movies from Radarr.")
        return movies
    else:
        logging.error("Error retrieving all movies from Radarr.")
        return []

def get_movie_by_imdb(imdb_id):
    """
    Retrieve a movie from Radarr by its IMDb ID.
    """
    imdb_id = imdb_id.strip().lower()
    movies = get_all_movies()
    for movie in movies:
        if movie.get("imdbId", "").strip().lower() == imdb_id:
            return movie
    logging.info(f"Movie with IMDb ID '{imdb_id}' not found in Radarr.")
    return None

def build_radarr_index():
    """
    Retrieves all movies from Radarr and builds a dictionary keyed by the normalized IMDb ID.
    This index allows quick lookup to determine if a movie is already in Radarr.
    """
    movies = get_all_movies()
    index = {}
    for movie in movies:
        imdb = movie.get("imdbId", "").strip().lower()
        if imdb:
            index[imdb] = movie
    return index

def trigger_search(movie_id):
    """
    Trigger a search for a movie in Radarr by issuing a POST to /api/v3/command.
    """
    endpoint = "/api/v3/command"
    payload = {
        "name": "MoviesSearch",
        "movieIds": [movie_id]
    }
    result = _make_request("POST", endpoint, payload)
    if result is not None:
        logging.info(f"Triggered search for movie {movie_id}: {result}")
        return result
    else:
        logging.error(f"Error triggering search for movie {movie_id}")
        return None

def add_movie(payload):
    """
    Add a new movie to Radarr using a POST request.
    """
    endpoint = "/api/v3/movie"
    result = _make_request("POST", endpoint, payload)
    if result is not None:
        logging.info(f"Successfully added movie '{payload.get('title')}' to Radarr.")
        return result
    else:
        logging.error(f"Error adding movie '{payload.get('title')}' to Radarr.")
        return None


# --- Tag Management Functions ---

# Cache for tags to avoid repeated API calls
_tag_cache = None


def get_all_tags():
    """
    Retrieve all tags from Radarr.
    Returns a list of tag objects: [{"id": 1, "label": "watchlist"}, ...]
    """
    global _tag_cache
    endpoint = "/api/v3/tag"
    result = _make_request("GET", endpoint)
    if result is not None:
        _tag_cache = {tag["label"].lower(): tag["id"] for tag in result}
        logging.debug(f"Fetched {len(result)} tags from Radarr")
        return result
    else:
        logging.error("Error retrieving tags from Radarr")
        return []


def create_tag(tag_name):
    """
    Create a new tag in Radarr.
    Returns the created tag object with ID, or None on failure.
    """
    global _tag_cache
    endpoint = "/api/v3/tag"
    payload = {"label": tag_name}
    result = _make_request("POST", endpoint, payload)
    if result is not None:
        logging.info(f"Created new Radarr tag: '{tag_name}' (ID: {result.get('id')})")
        # Update cache
        if _tag_cache is not None:
            _tag_cache[tag_name.lower()] = result["id"]
        return result
    else:
        logging.error(f"Error creating tag '{tag_name}' in Radarr")
        return None


def get_or_create_tag(tag_name):
    """
    Get existing tag ID or create a new tag if it doesn't exist.
    Uses caching to minimize API calls.

    Args:
        tag_name: The tag label (e.g., "friend-MattTurnip")

    Returns:
        int: The tag ID, or None if creation failed
    """
    global _tag_cache

    # Ensure cache is populated
    if _tag_cache is None:
        get_all_tags()

    # Check cache first (case-insensitive)
    tag_name_lower = tag_name.lower()
    if _tag_cache and tag_name_lower in _tag_cache:
        return _tag_cache[tag_name_lower]

    # Tag doesn't exist, create it
    result = create_tag(tag_name)
    if result:
        return result.get("id")

    return None


def invalidate_tag_cache():
    """Clear the tag cache to force a refresh on next call."""
    global _tag_cache
    _tag_cache = None