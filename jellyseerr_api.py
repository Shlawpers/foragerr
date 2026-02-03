#!/usr/bin/env python3
"""
jellyseerr_api.py

This module handles Jellyseerr API calls to fetch user mappings.
It correlates Plex user IDs (from RSS feeds) with human-readable usernames.
"""

import logging
import re
import requests

# Request timeout in seconds
REQUEST_TIMEOUT = 30


def fetch_user_mapping(jellyseerr_url, api_key):
    """
    Fetch all users from Jellyseerr and build a plex_id -> username mapping.

    The Plex user ID is extracted from the avatar URL which contains the format:
    https://plex.tv/users/{plex_id}/avatar?c=...

    Args:
        jellyseerr_url: Base URL for Jellyseerr (e.g., "http://localhost:5055")
        api_key: Jellyseerr API key

    Returns:
        dict: Mapping of plex_id (str) -> username (str)
              e.g., {'707f3dfacb151965': 'clio576', ...}
    """
    if not jellyseerr_url or not api_key:
        logging.warning("Jellyseerr URL or API key not configured, skipping user mapping")
        return {}

    jellyseerr_url = jellyseerr_url.rstrip('/')
    user_mapping = {}

    try:
        # Fetch users with pagination (get up to 100 users)
        url = f"{jellyseerr_url}/api/v1/user"
        headers = {
            "X-Api-Key": api_key,
            "Accept": "application/json"
        }
        params = {"take": 100}

        response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        users = data.get("results", [])
        logging.info(f"Fetched {len(users)} users from Jellyseerr")

        for user in users:
            avatar_url = user.get("avatar", "")
            username = user.get("plexUsername") or user.get("displayName") or "unknown"

            # Extract plex_id from avatar URL
            # Format: https://plex.tv/users/{plex_id}/avatar?c=...
            match = re.search(r'/users/([a-f0-9]+)/avatar', avatar_url)
            if match:
                plex_id = match.group(1)
                user_mapping[plex_id] = username
                logging.debug(f"Mapped Plex ID {plex_id} -> {username}")

        logging.info(f"Built user mapping with {len(user_mapping)} entries")
        return user_mapping

    except requests.exceptions.Timeout:
        logging.error(f"Timeout fetching users from Jellyseerr at {jellyseerr_url}")
        return {}
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching users from Jellyseerr: {e}")
        return {}
    except (KeyError, ValueError) as e:
        logging.error(f"Error parsing Jellyseerr response: {e}")
        return {}


def get_username_for_plex_id(plex_id, user_mapping, manual_mappings=None, default_name="unknown"):
    """
    Look up username for a Plex user ID with fallbacks.

    Priority order:
    1. Manual mappings (user overrides in config)
    2. Jellyseerr user mapping
    3. Default name

    Args:
        plex_id: The Plex user ID from RSS feed author field
        user_mapping: Dict from fetch_user_mapping()
        manual_mappings: Optional dict of plex_id -> custom_name overrides
        default_name: Fallback name if no mapping found

    Returns:
        str: The username to use for tagging
    """
    if not plex_id:
        return default_name

    # Check manual mappings first (user overrides)
    if manual_mappings and plex_id in manual_mappings:
        return manual_mappings[plex_id]

    # Check Jellyseerr mapping
    if plex_id in user_mapping:
        return user_mapping[plex_id]

    # Log unknown user for discovery
    logging.info(f"Unknown Plex user ID: {plex_id} - add to manual_mappings in config to name them")
    return default_name
