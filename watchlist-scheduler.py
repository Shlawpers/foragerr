#!/usr/bin/env python3
"""
watchlist-scheduler.py

This script manages the scheduling and execution of the two main components:
1. Main Watchlist Sync - Synchronizes Plex watchlist items to Radarr
2. Scheduled Upgrader - Periodically processes movies with the upgrade tag

It uses the schedule library to run these jobs at configured intervals
and handles proper initialization, error handling, and logging.
"""

import argparse
import fcntl
import logging
import os
import schedule
import signal
import sys
import time
import yaml
from datetime import datetime

# Set up logging to file and console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('watchlist_sync.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Load configuration from config.yaml
try:
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
        
    # Set debug mode if configured
    if config.get("execution", {}).get("debug_mode", False):
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Debug mode enabled")
        
except Exception as e:
    logging.error(f"Failed to load configuration: {e}")
    sys.exit(1)

# Create directory for lock files
LOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locks')
os.makedirs(LOCK_DIR, exist_ok=True)

# Track open lock file handles for proper cleanup
_active_locks = {}

# Configurable lock timeout (default 2 hours)
LOCK_TIMEOUT_SECONDS = config.get("schedule", {}).get("lock_timeout_seconds", 7200)

def with_job_lock(func_name):
    """
    Acquire an exclusive lock using fcntl for proper concurrency control.
    Returns True if lock acquired, False if job is already running.
    """
    lock_file = os.path.join(LOCK_DIR, f"{func_name}.lock")

    try:
        # Open file (create if doesn't exist)
        fd = open(lock_file, 'w')

        # Try to acquire exclusive lock (non-blocking)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            # Lock is held by another process
            fd.close()
            # Check if lock is stale based on file modification time
            if os.path.exists(lock_file):
                lock_age = time.time() - os.path.getmtime(lock_file)
                if lock_age > LOCK_TIMEOUT_SECONDS:
                    logging.warning(f"Lock for {func_name} appears stale (age: {lock_age/60:.1f} minutes)")
                else:
                    logging.info(f"Job {func_name} is already running (started {lock_age/60:.1f} minutes ago)")
            return False

        # Write timestamp to lock file
        fd.write(str(time.time()))
        fd.flush()

        # Store the file descriptor for later release
        _active_locks[func_name] = fd
        logging.debug(f"Lock acquired for {func_name}")
        return True

    except Exception as e:
        logging.error(f"Error acquiring lock for {func_name}: {e}")
        return False

def release_job_lock(func_name):
    """Release the job lock and close the file descriptor."""
    if func_name in _active_locks:
        try:
            fd = _active_locks[func_name]
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
            del _active_locks[func_name]
            logging.debug(f"Lock released for {func_name}")
        except Exception as e:
            logging.error(f"Error releasing lock for {func_name}: {e}")

    # Also try to remove the lock file
    lock_file = os.path.join(LOCK_DIR, f"{func_name}.lock")
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
    except Exception as e:
        logging.warning(f"Could not remove lock file for {func_name}: {e}")

def release_all_locks():
    """Release all active locks - called during shutdown."""
    for func_name in list(_active_locks.keys()):
        release_job_lock(func_name)

def job_watchlist(dry_run=False):
    """Run the watchlist synchronization job with concurrency protection"""
    job_name = "watchlist_sync"
    
    # Check if job is already running
    if not with_job_lock(job_name):
        logging.info("Skipping watchlist sync job - already running")
        return False
    
    try:
        logging.info("Watchlist sync job started.")
        from main import process_watchlist
        process_watchlist(dry_run=dry_run, scheduled_run=True)
        logging.info("Watchlist sync job completed.")
        
        # Print next scheduled times for both jobs after job completes
        for job in schedule.jobs:
            logging.info(f"Next {job.job_func.__name__} scheduled at: {job.next_run.strftime('%Y-%m-%d %H:%M:%S')}")
                
        return True
    except Exception as e:
        logging.exception(f"Exception in watchlist sync job: {e}")
        return False
    finally:
        # Always release the lock when done
        release_job_lock(job_name)

def job_upgrade(dry_run=False):
    """Run the scheduled upgrader job with concurrency protection"""
    job_name = "upgrade_job"
    
    # Check if job is already running
    if not with_job_lock(job_name):
        logging.info("Skipping upgrade job - already running")
        return False
    
    try:
        logging.info("Upgrade job started.")
        from scheduled_upgrader import job_upgrade as run_upgrade_job
        run_upgrade_job(dry_run=dry_run)
        logging.info("Upgrade job completed.")
        
        # Print next scheduled times for both jobs after job completes
        for job in schedule.jobs:
            logging.info(f"Next {job.job_func.__name__} scheduled at: {job.next_run.strftime('%Y-%m-%d %H:%M:%S')}")
                
        return True
    except Exception as e:
        logging.exception(f"Exception in upgrade job: {e}")
        return False
    finally:
        # Always release the lock when done
        release_job_lock(job_name)

def setup_schedule():
    """
    Set up the scheduler based on the configuration.
    Returns a list of scheduled jobs.
    """
    # Schedule the watchlist sync job
    watchlist_interval = config.get("schedule", {}).get("check_interval_minutes", 60)
    schedule.every(watchlist_interval).minutes.do(job_watchlist)
    logging.info(f"Watchlist sync job scheduled every {watchlist_interval} minutes.")
    
    # Schedule the upgrader job
    upgrader_interval = config.get("upgrade", {}).get("check_interval_minutes", 120)
    schedule.every(upgrader_interval).minutes.do(job_upgrade)
    logging.info(f"Upgrade job scheduled every {upgrader_interval} minutes.")
    
    # Print next run time for all scheduled jobs
    jobs = schedule.jobs
    for job in jobs:
        logging.info(f"Next scheduled task: {job.job_func.__name__} at {job.next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        
    return jobs

# Global flag for graceful shutdown
_shutdown_requested = False

def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    global _shutdown_requested
    sig_name = signal.Signals(sig).name
    logging.info(f"Received {sig_name} signal, initiating graceful shutdown...")
    _shutdown_requested = True
    release_all_locks()

def main():
    """
    Main function to parse arguments and run the scheduler.
    """
    global _shutdown_requested

    parser = argparse.ArgumentParser(description="Run and schedule Plex watchlist synchronization and upgrader jobs")
    parser.add_argument("--run-watchlist", action="store_true", help="Run watchlist sync once and exit")
    parser.add_argument("--run-upgrade", action="store_true", help="Run upgrade job once and exit")
    parser.add_argument("--schedule", action="store_true", help="Run as a scheduler (default)")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode")
    args = parser.parse_args()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # If no arguments are provided, default to scheduler mode
    if not (args.run_watchlist or args.run_upgrade or args.schedule):
        args.schedule = True

    # Run specific jobs once if requested
    if args.run_watchlist:
        job_watchlist(dry_run=args.dry_run)
        return

    if args.run_upgrade:
        job_upgrade(dry_run=args.dry_run)
        return

    # Setup and run the scheduler
    if args.schedule:
        logging.info("Starting Plex Watchlister scheduler...")
        jobs = setup_schedule()
        logging.info("Scheduler started")

        while not _shutdown_requested:
            try:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
            except Exception as e:
                if _shutdown_requested:
                    break
                logging.exception(f"Error in scheduler loop: {e}")
                time.sleep(120)  # Wait a bit longer after an error

        logging.info("Scheduler stopped gracefully")

if __name__ == "__main__":
    main()