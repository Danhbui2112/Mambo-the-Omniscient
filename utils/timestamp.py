"""
Timestamp management utilities for update tracking.
"""

import os
import json
import time
from config import LAST_UPDATE_FILE_PATH


def get_last_update_timestamp() -> int:
    """Get the last update timestamp from file
    
    Returns:
        Unix timestamp of last update, or current time if file not found
    """
    try:
        if os.path.exists(LAST_UPDATE_FILE_PATH):
            with open(LAST_UPDATE_FILE_PATH, "r") as f:
                data = json.load(f)
                return data.get("last_update_timestamp", int(time.time()))
    except Exception as e:
        print(f"Error reading last_update.json: {e}")
    return int(time.time())


def save_last_update_timestamp():
    """Save current timestamp as last update time"""
    try:
        current_timestamp = int(time.time())
        with open(LAST_UPDATE_FILE_PATH, "w") as f:
            json.dump({"last_update_timestamp": current_timestamp}, f)
        print(f"Update detected. Saved new timestamp {current_timestamp}")
    except Exception as e:
        print(f"CRITICAL: Failed to save timestamp file: {e}")
