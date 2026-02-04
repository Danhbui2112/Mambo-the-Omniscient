"""
Configuration module for Mambo Discord Bot

This module centralizes all configuration constants, environment variables,
and file paths used throughout the bot.
"""

import os
import json
import datetime
from dataclasses import dataclass
from typing import List
from dotenv import load_dotenv

# ============================================================================
# ENVIRONMENT SETUP
# ============================================================================

# Load environment variables from .env file (use absolute path for hosting)
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
_ENV_FILE = os.path.join(SCRIPT_DIR, '.env')

# DEBUG: Print where we're looking for .env
print(f"🔍 Looking for .env at: {_ENV_FILE}")
print(f"   File exists: {os.path.exists(_ENV_FILE)}")
if os.path.exists(_ENV_FILE):
    print(f"   File size: {os.path.getsize(_ENV_FILE)} bytes")
    # DEBUG: Read and show first few lines (hide actual values)
    try:
        with open(_ENV_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        print(f"   Total lines: {len(lines)}")
        for i, line in enumerate(lines[:5]):  # Show first 5 lines
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key = line.split('=')[0]
                print(f"   Line {i+1}: {key}=***")
            else:
                print(f"   Line {i+1}: {repr(line)}")
    except Exception as e:
        print(f"   Error reading file: {e}")

load_dotenv(_ENV_FILE)

# DEBUG: Check if DISCORD_TOKEN was loaded
_test_token = os.getenv('DISCORD_BOT_TOKEN')
print(f"   DISCORD_BOT_TOKEN loaded: {'Yes' if _test_token else 'No'}")

# ============================================================================
# FILE PATHS
# ============================================================================

RESTART_FILE_PATH = os.path.join(SCRIPT_DIR, "restart.json")
LAST_UPDATE_FILE_PATH = os.path.join(SCRIPT_DIR, "last_update.json")
CACHE_DIR = os.path.join(SCRIPT_DIR, "data_cache")
CONFIG_CACHE_FILE = os.path.join(CACHE_DIR, "config_cache.json")
MEMBER_CACHE_FILE = os.path.join(CACHE_DIR, "member_cache.json")
DATA_CACHE_DIR = os.path.join(CACHE_DIR, "data")
SMART_CACHE_DIR = os.path.join(CACHE_DIR, "smart_cache")

# Create cache directories
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DATA_CACHE_DIR, exist_ok=True)
os.makedirs(SMART_CACHE_DIR, exist_ok=True)

# Channel management file paths
ALLOWED_CHANNELS_CONFIG_FILE = os.path.join(SCRIPT_DIR, "allowed_channels_config.json")
ADMIN_LIST_FILE = os.path.join(SCRIPT_DIR, "admin_list.json")
CHANNEL_CHANGE_LOG_FILE = os.path.join(SCRIPT_DIR, "channel_change_log.json")

# Channel list display system
CHANNEL_LIST_CONFIG_FILE = os.path.join(SCRIPT_DIR, "channel_list_config.json")

# Server invite links storage
SERVER_INVITES_FILE = os.path.join(SCRIPT_DIR, "server_invites.json")

# Global leaderboard display system
GLOBAL_LEADERBOARD_CONFIG_FILE = os.path.join(SCRIPT_DIR, "global_leaderboard_config.json")

# Profile verification system
PROFILE_LINKS_FILE = os.path.join(SCRIPT_DIR, "profile_links.json")
EXAMPLE_PROFILE_IMAGE = os.path.join(SCRIPT_DIR, "assets", "example_profile.png")

# Schedule system
SCHEDULE_CACHE_FILE = os.path.join(SCRIPT_DIR, "schedule_cache.json")
SCHEDULE_CONFIG_FILE = os.path.join(SCRIPT_DIR, "schedule_config.json")

# Error logging
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def parse_int_list(env_var: str, default: list = None) -> list:
    """Parse comma-separated list of integers from environment variable"""
    value = os.getenv(env_var, "")
    if not value:
        return default or []
    try:
        return [int(x.strip()) for x in value.split(",") if x.strip()]
    except ValueError:
        return default or []


def load_schedule_config() -> dict:
    """Load saved schedule channel config"""
    if os.path.exists(SCHEDULE_CONFIG_FILE):
        try:
            with open(SCHEDULE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"channel_id": SCHEDULE_DEFAULT_CHANNEL_ID}


def save_schedule_channel(channel_id: int):
    """Save channel ID for schedule notifications"""
    with open(SCHEDULE_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            "channel_id": channel_id,
            "updated_at": datetime.datetime.now().isoformat()
        }, f, indent=2)


# ============================================================================
# BOT CONFIGURATION
# ============================================================================

@dataclass
class BotConfig:
    """Bot configuration constants"""
    SERVICE_ACCOUNT_FILE: str = 'credentials.json'
    GOOGLE_SHEET_ID: str = None
    CONFIG_SHEET_NAME: str = 'Clubs_Config'
    ADMIN_ROLE_IDS: List[int] = None
    GOD_MODE_USER_IDS: List[int] = None
    ALLOWED_CHANNEL_IDS: List[int] = None
    CACHE_UPDATE_COOLDOWN: int = 60  # seconds
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 1  # seconds
    
    def __post_init__(self):
        # Load from environment variables for security
        self.GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID', '')  # Must be set in .env
        self.ADMIN_ROLE_IDS = parse_int_list('ADMIN_ROLE_IDS')
        
        # Support both singular and plural format for GOD_MODE_USER_ID(S)
        god_mode_ids = parse_int_list('GOD_MODE_USER_IDS')
        if not god_mode_ids:
            # Fallback to singular format
            single_id = os.getenv('GOD_MODE_USER_ID', '')
            if single_id:
                try:
                    god_mode_ids = [int(single_id)]
                except ValueError:
                    god_mode_ids = []
        self.GOD_MODE_USER_IDS = god_mode_ids
        
        self.ALLOWED_CHANNEL_IDS = parse_int_list('ALLOWED_CHANNEL_IDS')


config = BotConfig()

# ============================================================================
# CHANNEL IDs - loaded from environment variables
# ============================================================================

# Channel to send successful command logs
LOGGING_CHANNEL_ID = int(os.getenv('LOGGING_CHANNEL_ID', '0'))

# Channel for club data requests
REQUEST_CHANNEL_ID = int(os.getenv('REQUEST_CHANNEL_ID', '0'))

# Channel for failed commands / debug logs
DEBUG_LOG_CHANNEL_ID = int(os.getenv('DEBUG_LOG_CHANNEL_ID', '0'))

# Channel for permanent channel list message
CHANNEL_LIST_DISPLAY_CHANNEL_ID = int(os.getenv('CHANNEL_LIST_DISPLAY_CHANNEL_ID', '0'))

# ============================================================================
# SUPPORT & SOCIAL LINKS
# ============================================================================

SUPPORT_SERVER_URL = "https://discord.com/invite/touchclub"
SUPPORT_MESSAGE = "Looking for clubs? Join our discord server"
SUPPORT_HELP_MESSAGE = "Need help? Join our support server"

# Donation & Vote Prompts
DONATION_URL = "https://ko-fi.com/senchouxflare_7b7m"
VOTE_URL = "https://top.gg/bot/1312444816071720980/vote"
DONATION_MESSAGE = "Support us on Ko-fi"
VOTE_MESSAGE = "Vote for the bot on Top.gg"

# ============================================================================
# PROMO SYSTEM
# ============================================================================

PROMO_CHANCE = 0.25  # 25% chance to show promo message
PROMO_COOLDOWN = 3600  # 3600 seconds (1 hour) cooldown per user

# ============================================================================
# PERFORMANCE MONITORING
# ============================================================================

SLOW_COMMAND_THRESHOLD = 2.0  # Commands slower than 2 seconds will be logged

# ============================================================================
# SCHEDULE SYSTEM
# ============================================================================

SCHEDULE_URL = "https://raw.githubusercontent.com/JustWastingTime/TazunaDiscordBot/main/assets/schedule.json"
SCHEDULE_NOTIFY_USER_ID = int(os.getenv('SCHEDULE_NOTIFY_USER_ID', '0'))  # User to ping on updates
SCHEDULE_DEFAULT_CHANNEL_ID = int(os.getenv('SCHEDULE_DEFAULT_CHANNEL_ID', '0'))  # Fallback channel

SCHEDULE_COLORS = {
    "Anniversary": 0xFFD700,
    "Scenario": 0x00BFFF,
    "Banner": 0xFF69B4,
    "Legend Races": 0xFFA500,
    "Champions Meeting": 0xADFF2F,
    "Story Event": 0x9370DB,
    "Misc": 0x808080,
    "Default": 0x808080
}

# ============================================================================
# PROFILE VERIFICATION SYSTEM
# ============================================================================

OCR_SERVICE_URL = os.getenv("OCR_SERVICE_URL", "http://2.56.246.119:30404")
