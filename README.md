# Foragerr

A Python application that automatically synchronizes your Plex watchlist with Radarr and manages movie quality upgrades.

## Features

- **Watchlist Sync** - Automatically adds movies from your Plex watchlist to Radarr
- **Friends' Watchlists** - Optionally sync movies from friends' watchlists via RSS feed
- **Friend Tagging** - Tag movies in Radarr with the friend's name who added them (jellyseerr integration used to grab friend names)
- **Quality Upgrades** - Automatically searches for better quality versions of undersized movies (or any movie you tag for uprading) on a scheduled interval.
- **Search Limits** - Configurable per-run and daily search limits to avoid hammering indexers
- **Dry Run Mode** - Preview changes without actually making them

## How It Works

Foragerr runs two scheduled jobs:

1. **Watchlist Sync Job**
   - Fetches your personal Plex watchlist
   - Optionally fetches friends' watchlists via RSS
   - Adds new movies to Radarr with appropriate tags
   - Triggers searches for newly added movies (within configured limits)

2. **Upgrade Job**
   - Scans Radarr for movies with the upgrade tag
   - Removes the tag from movies that meet the file size threshold
   - Triggers searches for undersized movies (with rate limiting)

## Quick Start

### Docker Compose (Recommended)

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/foragerr.git
   cd foragerr
   ```

2. Create your configuration:
   ```bash
   cp config.example.yaml config.yaml
   ```

3. Edit `config.yaml` with your settings (see [Configuration](#configuration))

4. Start the container:
   ```bash
   docker compose up -d
   ```

5. View logs:
   ```bash
   docker compose logs -f
   ```

### Manual Installation (more of a pain)

1. Clone and enter the directory:
   ```bash
   git clone https://github.com/yourusername/foragerr.git
   cd foragerr
   ```

2. Create a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create your configuration:
   ```bash
   cp config.example.yaml config.yaml
   ```

5. Edit `config.yaml` with your settings

6. Run the scheduler:
   ```bash
   python watchlist-scheduler.py
   ```

## Configuration

Copy `config.example.yaml` to `config.yaml` and configure:

### Required Settings

| Setting | Description |
|---------|-------------|
| `remotePlex.token` | Your Plex authentication token |
| `radarr.base_url` | Your Radarr server URL |
| `radarr.apikey` | Your Radarr API key |
| `radarr.root_folder` | Root folder path for new movies |

### Getting Your Plex Token

1. Sign into Plex Web App
2. Browse to any media item
3. Click the "..." menu and select "Get Info"
4. Click "View XML"
5. The token is in the URL: `X-Plex-Token=YOUR_TOKEN`

Or see: [Finding an Authentication Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)

### Getting Your Radarr API Key

1. Open Radarr
2. Go to Settings -> General
3. Copy the API Key

### Friends' Watchlist RSS

To sync friends' watchlists:

1. In Plex Web, go to your Watchlist
2. Click the Share button
3. Copy the RSS Feed URL
4. Add it to `remotePlex.friends_watchlist.feed_url`

### Tag Setup in Radarr

Before running Foragerr, create these tags in Radarr:

1. Go to Radarr -> Settings -> Tags
2. Create a tag for watchlist movies (e.g., "watchlist")
3. Create a tag for upgrade-eligible movies (e.g., "upgrade")
4. Note the tag IDs (visible in the URL when editing)
5. Add these IDs to your config

## Usage

### Run as Scheduler (Default)

Runs both jobs on configured intervals:
```bash
python watchlist-scheduler.py
```

### Run Jobs Once

Run watchlist sync once:
```bash
python watchlist-scheduler.py --run-watchlist
```

Run upgrade job once:
```bash
python watchlist-scheduler.py --run-upgrade
```

### Dry Run Mode

Preview changes without making them:
```bash
python watchlist-scheduler.py --dry-run
python main.py --dry-run
```

## Docker Network Modes

### Running with a VPN Container

If you route Radarr through a VPN container (like Gluetun), uncomment the network_mode in docker-compose.yaml:

```yaml
network_mode: "service:gluetun"
```

Then set `radarr.base_url` to `http://localhost:7878` since both containers share the same network namespace.

### Running on Host Network

If Radarr is running directly on the host:

```yaml
network_mode: host
```

## File Structure

```
foragerr/
├── watchlist-scheduler.py  # Entry point - schedules and runs jobs
├── main.py                 # Watchlist sync job logic
├── scheduled_upgrader.py   # Upgrade job logic
├── plex_api.py             # Plex API wrapper
├── radarr_api.py           # Radarr API wrapper
├── jellyseerr_api.py       # Jellyseerr user mapping
├── database.py             # SQLite database operations
├── search_conditions.py    # Rate limiting logic
├── config.py               # Config loader
├── config.yaml             # Your configuration (not in repo)
├── config.example.yaml     # Example configuration
├── requirements.txt        # Python dependencies
├── Dockerfile              # Docker build file
└── docker-compose.yaml     # Docker Compose configuration
```

## Data Files

These files are created at runtime in the working directory (or `/app/data` in Docker):

| File | Purpose |
|------|---------|
| `plex_watchlister.db` | SQLite database tracking processed movies |
| `daily_search_count.json` | Tracks daily search count |
| `watchlist_sync.log` | Application logs (rotates at 10MB) |
| `locks/*.lock` | Job lock files for concurrency control |

## Troubleshooting

### Connection Refused to Radarr

- Check that Radarr is running
- Verify the URL in config.yaml
- If using Docker, ensure correct network mode

### Movies Not Being Added

- Check the logs for error messages
- Verify your Plex token is valid
- Ensure the movie has a valid TMDB ID

### Search Limits

If searches aren't triggering:
- Check `max_daily_searches` hasn't been reached
- Check `searches_per_run` limit
- Movies are only searched once in the main sync job

### VPN Issues

If running behind a VPN container:
- Use `localhost` for Radarr URL
- Ensure the VPN container is running first
- Check that ports are mapped on the VPN container

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - see LICENSE file for details.
