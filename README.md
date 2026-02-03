# Foragerr

Automatically sync your Plex watchlist to Radarr and keep your movies upgraded to better quality.

## What It Does

Foragerr runs two independent jobs on a schedule:

### Job 1: Watchlist Sync

**Problem:** You add movies to your Plex watchlist, but they don't automatically appear in Radarr.

**Solution:** Foragerr checks your Plex watchlist and adds any missing movies to Radarr.

- Syncs your personal Plex watchlist to Radarr
- Optionally syncs friends' watchlists too (via RSS feed)
- Tags each movie so you know where it came from
- Triggers a search for newly added movies (with rate limiting)

### Job 2: Persistent Upgrader

**Problem:** You add a movie to Radarr, it searches once, finds nothing (or grabs a low-quality version), and then... nothing. It just sits there. You have to manually search again and again.

**Solution:** Foragerr keeps searching for your movies until they reach your desired quality.

- **Retries failed downloads** — Movies that didn't grab anything on first search get searched again automatically
- **Upgrades low-quality grabs** — Movies below your size threshold (e.g., < 4GB) keep getting searched for better versions
- **Stops when satisfied** — Once a movie meets your quality threshold, it's removed from the upgrade queue
- **Works on any tagged movie** — Tag any movie in Radarr for upgrade and Foragerr will hunt for it
- **Rate-limited** — Won't hammer your indexers (configurable searches per run and per day)

---

## Quick Start

### Using Pre-built Image (Easiest)

1. Create a `docker-compose.yaml`:
```yaml
services:
  foragerr:
    image: ghcr.io/shlawpers/foragerr:latest
    container_name: foragerr
    restart: unless-stopped
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./data:/app/data
    # Uncomment if Radarr is behind a VPN container:
    # network_mode: "service:gluetun"
```

2. Download the example config:
```bash
curl -O https://raw.githubusercontent.com/Shlawpers/foragerr/main/config.example.yaml
mv config.example.yaml config.yaml
```

3. Edit `config.yaml` with your Plex token and Radarr API key (see [Configuration](#configuration))

4. Start it:
```bash
docker compose up -d
```

### Building Locally

```bash
git clone https://github.com/Shlawpers/foragerr.git
cd foragerr
cp config.example.yaml config.yaml
# Edit config.yaml with your settings
docker compose up -d
```

---

## Configuration

Edit `config.yaml` with these required values:

| Setting | Where to Find It |
|---------|------------------|
| `remotePlex.token` | [Plex Support Article](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) |
| `radarr.base_url` | Your Radarr URL (e.g., `http://localhost:7878`) |
| `radarr.apikey` | Radarr → Settings → General → API Key |
| `radarr.root_folder` | Path where Radarr stores movies |

### Setting Up Tags in Radarr

Foragerr uses tags to track movies. Create these in Radarr first:

1. Go to **Radarr → Settings → Tags**
2. Create a tag called `watchlist` (or any name you prefer)
3. Create a tag called `upgrade`
4. Note the tag IDs and add them to your config

### Optional: Friends' Watchlists

To sync movies your friends add to their Plex watchlists:

1. In Plex Web, go to **Watchlist → Share → Copy RSS Feed URL**
2. Add the URL to `remotePlex.friends_watchlist.feed_url`
3. Set `enabled: true`

Foragerr can tag movies by friend name if you connect Jellyseerr (it pulls usernames from there).

---

## Key Settings

```yaml
schedule:
  check_interval_minutes: 120    # How often to sync watchlist
  max_daily_searches: 200        # Total searches per day (shared by both jobs)
  searches_per_run: 3            # Max searches per job run

upgrade:
  check_interval_minutes: 100    # How often to retry upgrades
  min_file_size_gb: 4            # Target quality threshold
                                 # Movies missing or below this size = keep searching
                                 # Movies at or above this size = done, remove from queue
```

---

## Running Behind a VPN

If Radarr runs behind a VPN container (like Gluetun), Foragerr needs to share that network:

```yaml
services:
  foragerr:
    image: ghcr.io/shlawpers/foragerr:latest
    network_mode: "service:gluetun"
    # ...
```

Then set `radarr.base_url` to `http://localhost:7878` (they share the same network).

---

## CLI Options

```bash
# Run both jobs on schedule (default)
python watchlist-scheduler.py

# Run watchlist sync once
python watchlist-scheduler.py --run-watchlist

# Run upgrader once
python watchlist-scheduler.py --run-upgrade

# Preview without making changes
python watchlist-scheduler.py --dry-run
```

---

## Troubleshooting

**"Connection refused to Radarr"**
- Is Radarr running?
- Check the URL in config.yaml
- If using Docker, check your network_mode setting

**Movies not being added**
- Check logs: `docker compose logs -f`
- Verify your Plex token is valid
- Movies need a TMDB ID to be added

**Searches not triggering**
- Check if `max_daily_searches` limit was reached
- Movies are only auto-searched once when first added (the upgrader handles retries)
- The upgrader only searches movies that are missing or below `min_file_size_gb`

---

## License

MIT
