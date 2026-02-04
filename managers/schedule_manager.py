"""
Schedule management for event schedule display and notifications.

Handles fetching and managing game event schedules.
"""

from config import SCHEDULE_URL, SCHEDULE_COLORS, load_schedule_config, save_schedule_channel

# Schedule system state
schedule_last_etag = None
schedule_cache = []  # In-memory cache

__all__ = [
    'SCHEDULE_URL',
    'SCHEDULE_COLORS',
    'schedule_last_etag',
    'schedule_cache',
    'load_schedule_config',
    'save_schedule_channel',
]
