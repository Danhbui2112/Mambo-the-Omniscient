import discord
from discord import app_commands
from discord.ext import tasks
from datetime import time as dt_time
import gspread
import pandas as pd
import os
import datetime
from gspread.exceptions import WorksheetNotFound, APIError
import data_updater

# Auto-sync helpers (only for /search_club command)
from auto_sync_helpers import (
    fetch_circle_data,
    sync_club_from_api  # Keep for manual /sync_club command
)

# ============================================================================
# PHASE 2 MODULAR IMPORTS
# ============================================================================
# Core modules extracted from monolithic bot code
from config import config, BotConfig, SCRIPT_DIR
from models import SmartCache, CROSS_CLUB_CACHE, ProxyManager, gs_manager
from utils import log_error, format_fans, get_last_update_timestamp, save_last_update_timestamp
from managers import add_support_footer, SCHEDULE_COLORS

import aiohttp
import json
import time
import pytz
import sys
import subprocess
import random
import asyncio
from typing import Tuple, Optional, List
from dataclasses import dataclass
from collections import Counter
from dotenv import load_dotenv
from wcwidth import wcswidth
from io import StringIO
import logging
from logging.handlers import RotatingFileHandler



# Load environment variables from .env file (use absolute path for hosting)
_SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
_ENV_FILE = os.path.join(_SCRIPT_DIR, '.env')

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
# CONFIGURATION
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
# FILE PATHS
# ============================================================================

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
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

# Initialize smart cache with 24-hour TTL (data updates daily)
smart_cache = SmartCache(SMART_CACHE_DIR, ttl_seconds=86400)

# Channel management file paths
ALLOWED_CHANNELS_CONFIG_FILE = os.path.join(SCRIPT_DIR, "allowed_channels_config.json")
ADMIN_LIST_FILE = os.path.join(SCRIPT_DIR, "admin_list.json")
CHANNEL_CHANGE_LOG_FILE = os.path.join(SCRIPT_DIR, "channel_change_log.json")

# Channel IDs - loaded from environment variables with fallback defaults
LOGGING_CHANNEL_ID = int(os.getenv('LOGGING_CHANNEL_ID', '0'))  # Channel to send successful command logs
REQUEST_CHANNEL_ID = int(os.getenv('REQUEST_CHANNEL_ID', '0'))  # Channel for club data requests
DEBUG_LOG_CHANNEL_ID = int(os.getenv('DEBUG_LOG_CHANNEL_ID', '0'))  # Channel for failed commands / debug logs

# Channel list display system
CHANNEL_LIST_DISPLAY_CHANNEL_ID = int(os.getenv('CHANNEL_LIST_DISPLAY_CHANNEL_ID', '0'))  # Channel for permanent channel list message
CHANNEL_LIST_CONFIG_FILE = os.path.join(SCRIPT_DIR, "channel_list_config.json")

# Server invite links storage
SERVER_INVITES_FILE = os.path.join(SCRIPT_DIR, "server_invites.json")

# Global leaderboard display system
GLOBAL_LEADERBOARD_CONFIG_FILE = os.path.join(SCRIPT_DIR, "global_leaderboard_config.json")

# Global state for pending club requests
pending_requests = {}

# Support server
SUPPORT_SERVER_URL = "https://discord.com/invite/touchclub"
SUPPORT_MESSAGE = "Looking for clubs? Join our discord server"
SUPPORT_HELP_MESSAGE = "Need help? Join our support server"

# Donation & Vote Prompts
DONATION_URL = "https://ko-fi.com/senchouxflare_7b7m"
VOTE_URL = "https://top.gg/bot/1312444816071720980/vote"
PROMO_CHANCE = 0.25  # 25% chance to show promo message
PROMO_COOLDOWN = 3600  # 3600 seconds (1 hour) cooldown per user
DONATION_MESSAGE = "Support us on Ko-fi"
VOTE_MESSAGE = "Vote for the bot on Top.gg"

# Track last promo time per user
promo_cooldowns = {}  # {user_id: last_promo_timestamp}

# Track command execution start times for performance monitoring
command_start_times = {}  # {interaction_id: start_time}
SLOW_COMMAND_THRESHOLD = 2.0  # Commands slower than 2 seconds will be logged

# ============================================================================
# PROXY MANAGER - Rotate through Webshare proxies for API calls
# ============================================================================

# Initialize Phase 2 singletons
proxy_manager = ProxyManager()

# ============================================================================
# SCHEDULE SYSTEM - Auto-fetch from TazunaDiscordBot GitHub
# ============================================================================
SCHEDULE_URL = "https://raw.githubusercontent.com/JustWastingTime/TazunaDiscordBot/main/assets/schedule.json"
SCHEDULE_CACHE_FILE = os.path.join(SCRIPT_DIR, "schedule_cache.json")
SCHEDULE_CONFIG_FILE = os.path.join(SCRIPT_DIR, "schedule_config.json")
SCHEDULE_NOTIFY_USER_ID = int(os.getenv('SCHEDULE_NOTIFY_USER_ID', '0'))  # User to ping on updates
SCHEDULE_DEFAULT_CHANNEL_ID = int(os.getenv('SCHEDULE_DEFAULT_CHANNEL_ID', '0'))  # Fallback channel
schedule_last_etag = None
schedule_cache = []  # In-memory cache

SCHEDULE_COLORS = {
    "Anniversary": 0xFFD700, "Scenario": 0x00BFFF, "Banner": 0xFF69B4,
    "Legend Races": 0xFFA500, "Champions Meeting": 0xADFF2F,
    "Story Event": 0x9370DB, "Misc": 0x808080, "Default": 0x808080
}

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
# PROFILE VERIFICATION SYSTEM
# ============================================================================
OCR_SERVICE_URL = os.getenv("OCR_SERVICE_URL", "http://2.56.246.119:30404")
PROFILE_LINKS_FILE = os.path.join(SCRIPT_DIR, "profile_links.json")
EXAMPLE_PROFILE_IMAGE = os.path.join(SCRIPT_DIR, "assets", "example_profile.png")

# Pending verification requests: {user_id: {"member_name": str, "club_name": str, "expires": datetime}}
pending_verifications = {}




def add_support_footer(embed: discord.Embed, extra_text: str = "") -> discord.Embed:
    """
    Add support server link to embed footer with embedded link
    
    Args:
        embed: Discord embed to add footer to
        extra_text: Optional additional text before support message
    
    Returns:
        Modified embed with support footer containing embedded link
    """
    # Use Discord markdown format for embedded link: [text](url)
    footer_link = f"[{SUPPORT_MESSAGE}]({SUPPORT_SERVER_URL})"
    
    if extra_text:
        footer_text = f"{extra_text}\n{footer_link}"
    else:
        footer_text = footer_link
    
    embed.set_footer(text=footer_text)
    return embed


async def maybe_send_promo_message(interaction: discord.Interaction):
    """
    Maybe send a promotional message with donation & vote links.
    Based on random chance (25%) and user cooldown (1 minute).
    Sends as PUBLIC message (not ephemeral).
    """
    import random
    import time as time_module
    
    user_id = interaction.user.id
    current_time = time_module.time()
    
    # Check cooldown
    if user_id in promo_cooldowns:
        time_since_last = current_time - promo_cooldowns[user_id]
        if time_since_last < PROMO_COOLDOWN:
            return  # Still in cooldown
    
    # Random chance check (25%)
    if random.random() > PROMO_CHANCE:
        return  # Not this time
    
    # Update cooldown
    promo_cooldowns[user_id] = current_time
    
    # Create promo embed
    embed = discord.Embed(
        title="💝 Support the Bot!",
        description="If you find this bot helpful, consider support the bot!",
        color=discord.Color.from_str("#FF69B4")  # Pink color
    )
    
    embed.add_field(
        name="☕ Donation",
        value=f"[{DONATION_MESSAGE}]({DONATION_URL})",
        inline=True
    )
    
    embed.add_field(
        name="⭐ Vote",
        value=f"[{VOTE_MESSAGE}]({VOTE_URL})",
        inline=True
    )
    
    embed.set_footer(text="Thank you for your support! 💕")
    
    try:
        # Send as PUBLIC message (not ephemeral)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"Error sending promo message: {e}")


# ============================================================================
# PROFILE VERIFICATION HELPERS
# ============================================================================

def load_profile_links() -> dict:
    """Load Discord ID -> Trainer ID mappings from file"""
    if os.path.exists(PROFILE_LINKS_FILE):
        try:
            with open(PROFILE_LINKS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_profile_link(discord_id: int, trainer_id: str, member_name: str, club_name: str, viewer_id: str = None):
    """Save a verified profile link
    
    Args:
        discord_id: Discord user ID
        trainer_id: Trainer ID from OCR (12-digit number)
        member_name: In-game trainer name
        club_name: Club name at time of linking
        viewer_id: Player ID from uma.moe API (never changes even if user changes club/name)
    """
    links = load_profile_links()
    links[str(discord_id)] = {
        "viewer_id": viewer_id,  # Primary identifier - never changes
        "trainer_id": trainer_id,
        "member_name": member_name,
        "club_name": club_name,
        "linked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    with open(PROFILE_LINKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(links, f, indent=2)

async def call_ocr_service(image_data: bytes) -> dict:
    """Call Node.js OCR service to extract trainer data from image"""
    try:
        import base64
        base64_image = base64.b64encode(image_data).decode('utf-8')
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OCR_SERVICE_URL}/api/extract",
                json={"base64Image": f"data:image/png;base64,{base64_image}"},
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('success'):
                        return result.get('data', {})
        return {}
    except Exception as e:
        print(f"OCR service error: {e}")
        return {}

def get_trainer_id_from_sheets(member_name: str, club_name: str) -> str:
    """Get Trainer ID for a member from Google Sheets (Members sheet)"""
    try:
        club_config = client.config_cache.get(club_name, {})
        members_sheet = club_config.get('Members_Sheet_Name')
        if not members_sheet:
            return None
        
        ws = gs_manager.sh.worksheet(members_sheet)
        records = ws.get_all_records()
        
        for record in records:
            name = record.get('Name', '')
            if name.casefold() == member_name.casefold():
                # Try different column names for trainer ID
                trainer_id = record.get('Trainer_ID') or record.get('TrainerID') or record.get('ID') or ''
                return str(trainer_id).replace(' ', '')
        return None
    except Exception as e:
        print(f"Error getting trainer ID from sheets: {e}")
        return None

def get_viewer_id_from_sheets(member_name: str, club_name: str) -> str:
    """Get Viewer ID (Player ID) for a member from Google Sheets (Members sheet)
    
    The Members sheet should have 'Trainer ID' column with viewer_id from uma.moe API.
    Uses get_all_values() to avoid duplicate header issues.
    """
    try:
        club_config = client.config_cache.get(club_name, {})
        members_sheet = club_config.get('Members_Sheet_Name')
        if not members_sheet:
            return None
        
        ws = gs_manager.sh.worksheet(members_sheet)
        all_values = ws.get_all_values()
        
        if len(all_values) < 2:
            return None
        
        # Find header row (skip === CURRENT === if present)
        header_idx = 0
        if all_values[0] and '=== CURRENT' in str(all_values[0][0]):
            header_idx = 1
        
        if len(all_values) <= header_idx:
            return None
        
        header = all_values[header_idx]
        
        # Find Name column
        name_col = None
        for i, col in enumerate(header):
            if str(col).lower() == 'name':
                name_col = i
                break
        
        if name_col is None:
            return None
        
        # Find Trainer ID column
        trainer_id_col = None
        for i, col in enumerate(header):
            col_lower = str(col).lower().replace(' ', '').replace('_', '')
            if col_lower in ['trainerid', 'viewerid', 'id']:
                trainer_id_col = i
                break
        
        if trainer_id_col is None:
            return None
        
        # Search for member by name
        for row in all_values[header_idx + 1:]:
            if len(row) > name_col:
                row_name = str(row[name_col]).strip()
                if row_name.casefold() == member_name.casefold():
                    if len(row) > trainer_id_col:
                        viewer_id = str(row[trainer_id_col]).replace(' ', '')
                        return viewer_id if viewer_id else None
        return None
    except Exception as e:
        print(f"Error getting viewer ID from sheets: {e}")
        return None

async def find_member_by_viewer_id_in_club(viewer_id: str, club_name: str) -> dict:
    """Find member by viewer_id in a specific club's Members sheet
    
    Returns:
        dict with member data if found, None otherwise
        {"name": str, "club": str, "row_data": list}
    """
    try:
        club_config = client.config_cache.get(club_name, {})
        members_sheet = club_config.get('Members_Sheet_Name')
        if not members_sheet:
            return None
        
        ws = await asyncio.to_thread(gs_manager.sh.worksheet, members_sheet)
        all_values = await asyncio.to_thread(ws.get_all_values)
        
        if len(all_values) < 2:
            return None
        
        # Find header row (skip === CURRENT === if present)
        header_idx = 0
        if all_values[0] and '=== CURRENT' in str(all_values[0][0]):
            header_idx = 1
        
        header = all_values[header_idx]
        
        # Find Trainer ID column
        trainer_id_col = None
        for i, col in enumerate(header):
            col_lower = str(col).lower().replace(' ', '')
            if col_lower in ['trainerid', 'trainer_id', 'viewerid', 'viewer_id', 'id']:
                trainer_id_col = i
                break
        
        if trainer_id_col is None:
            return None
        
        # Find Name column
        name_col = None
        for i, col in enumerate(header):
            if str(col).lower() == 'name':
                name_col = i
                break
        
        if name_col is None:
            return None
        
        # Search for viewer_id
        for row in all_values[header_idx + 1:]:
            if len(row) > trainer_id_col:
                row_viewer_id = str(row[trainer_id_col]).replace(' ', '')
                if row_viewer_id == str(viewer_id):
                    member_name = row[name_col] if len(row) > name_col else 'Unknown'
                    return {
                        "name": member_name,
                        "club": club_name,
                        "viewer_id": viewer_id,
                        "row_data": row
                    }
        
        return None
    except Exception as e:
        print(f"Error finding member by viewer_id in {club_name}: {e}")
        return None

async def find_member_across_all_clubs(viewer_id: str) -> dict:
    """Search all tracked clubs for a member with given viewer_id (from sheets - may be outdated)
    
    Used when member's stored club doesn't have them anymore (they changed clubs).
    
    Returns:
        dict with member data if found, None otherwise
    """
    for club_name in client.config_cache.keys():
        result = await find_member_by_viewer_id_in_club(viewer_id, club_name)
        if result:
            return result
        # No delay needed - using proxy rotation
    return None

async def find_viewer_in_clubs_via_api(viewer_id: str) -> dict:
    """Search all tracked clubs for viewer_id using uma.moe API (real-time data)
    
    Uses Yui logic with CONCURRENT requests for fast lookup:
    - Fetch all clubs in parallel (batch of 5 at a time to avoid rate limiting)
    - If found with active_days >= current_day - 1, this is definitely the current club
    - Otherwise collect all matches and select one with most active days
    
    Args:
        viewer_id: The player's viewer_id (player ID)
        
    Returns:
        dict with {club_name, member_name, viewer_id, active_days} if found, None otherwise
    """
    from auto_sync_helpers import fetch_circle_data
    import datetime
    
    # Get current day of month for early exit check
    current_day = datetime.datetime.now(datetime.timezone.utc).day
    print(f"[API Search] Searching for viewer_id {viewer_id} (current day: {current_day})...")
    
    # Helper function to search one club
    async def search_one_club(club_name: str, circle_id: str):
        try:
            api_data = await fetch_circle_data(str(circle_id), timeout=10, proxy_url=proxy_manager.get_next_proxy())
            if not api_data:
                return None
            
            members = api_data.get('members', [])
            for member in members:
                member_viewer_id = str(member.get('viewer_id', ''))
                if member_viewer_id == str(viewer_id):
                    member_name = member.get('trainer_name', 'Unknown')
                    daily_fans = member.get('daily_fans', [])
                    active_days = sum(1 for fans in daily_fans if fans and fans > 0)
                    
                    return {
                        "club_name": club_name,
                        "member_name": member_name,
                        "viewer_id": viewer_id,
                        "active_days": active_days
                    }
            return None
        except Exception as e:
            print(f"[API Search] Error checking {club_name}: {e}")
            return None
    
    # Build list of clubs to search
    club_tasks = []
    for club_name, club_config in client.config_cache.items():
        circle_id = club_config.get('Club_ID')
        if circle_id:
            club_tasks.append((club_name, circle_id))
    
    print(f"[API Search] Searching {len(club_tasks)} clubs concurrently...")
    
    # Process in batches of 10 (proxies help avoid rate limiting)
    batch_size = 10
    matches = []
    all_results = []  # Track all results for failure statistics
    
    for i in range(0, len(club_tasks), batch_size):
        batch = club_tasks[i:i+batch_size]
        
        # Create concurrent tasks for this batch
        tasks = [search_one_club(club_name, circle_id) for club_name, circle_id in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_results.extend(zip(batch, results))  # Store (club_info, result) pairs
        
        # Process results - Fix 1: Log exceptions instead of silent skip
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                # Log exception with club name
                club_name_failed = batch[idx][0] if idx < len(batch) else "Unknown"
                print(f"[API Search] ❌ Failed to check '{club_name_failed}': {type(result).__name__}: {result}")
                continue
            
            if isinstance(result, dict) and result:
                print(f"[API Search] Found in '{result['club_name']}' as '{result['member_name']}' with {result['active_days']} active days")
                
                # Early exit if this is clearly the current club
                if result['active_days'] >= current_day - 1:
                    print(f"[API Search] ✅ Early exit! '{result['club_name']}' is current club ({result['active_days']}/{current_day} days)")
                    return result
                
                # Fix 4: Special case for Day 1 new joiner with no data yet
                elif current_day == 1 and result['active_days'] == 0:
                    print(f"[API Search] ✅ Early exit! Day 1 new joiner in '{result['club_name']}'")
                    return result
                
                matches.append(result)
            elif result is None:
                # API returned None (not found in this club) - normal behavior
                pass
            else:
                # Unexpected result type - log for debugging
                print(f"[API Search] ⚠️ Unexpected result type: {type(result)}")
        
        # Minimal delay between batches (proxies rotate)
    
    # Fix 2: No matches found - log failure statistics
    if not matches:
        total_clubs = len(club_tasks)
        failed_count = sum(1 for _, r in all_results if isinstance(r, Exception))
        
        print(f"[API Search] viewer_id {viewer_id} not found")
        print(f"[API Search] Checked {total_clubs} clubs, {failed_count} failed with errors")
        
        if failed_count > total_clubs * 0.5:
            print(f"[API Search] ⚠️ High failure rate ({failed_count}/{total_clubs}) - may be API/network issue")
        
        return None
    
    # If only one match, return it
    if len(matches) == 1:
        print(f"[API Search] Only one match found: {matches[0]['club_name']}")
        return matches[0]
    
    # Multiple matches - use Yui logic: select club with most active days
    print(f"[API Search] Found {len(matches)} clubs with viewer_id, applying Yui logic...")
    best_match = max(matches, key=lambda x: x['active_days'])
    print(f"[API Search] Selected '{best_match['club_name']}' with {best_match['active_days']} active days (most recent)")
    
    return best_match


async def find_all_clubs_for_viewer(viewer_id: str) -> list:
    """Find ALL clubs where viewer_id exists (for transfer detection)
    
    Unlike find_viewer_in_clubs_via_api which returns only best match,
    this returns ALL clubs sorted by active_days DESC.
    
    Returns:
        List of {club_name, member_name, viewer_id, active_days}
        First item = current club (most active_days)
        Remaining items = old clubs
    """
    from auto_sync_helpers import fetch_circle_data
    import datetime
    
    current_day = datetime.datetime.now(datetime.timezone.utc).day
    print(f"[Transfer Check] Searching ALL clubs for viewer_id {viewer_id}...")
    
    async def search_one_club(club_name: str, circle_id: str):
        try:
            api_data = await fetch_circle_data(str(circle_id), timeout=10, proxy_url=proxy_manager.get_next_proxy())
            if not api_data:
                return None
            
            members = api_data.get('members', [])
            for member in members:
                member_viewer_id = str(member.get('viewer_id', ''))
                if member_viewer_id == str(viewer_id):
                    member_name = member.get('trainer_name', 'Unknown')
                    daily_fans = member.get('daily_fans', [])
                    active_days = sum(1 for fans in daily_fans if fans and fans > 0)
                    
                    return {
                        "club_name": club_name,
                        "member_name": member_name,
                        "viewer_id": viewer_id,
                        "active_days": active_days,
                        "daily_fans": daily_fans  # Include for CarryOver calculation
                    }
            return None
        except Exception as e:
            print(f"[Transfer Check] Error checking {club_name}: {e}")
            return None
    
    # Build list of clubs to search
    club_tasks = []
    for club_name, club_config in client.config_cache.items():
        circle_id = club_config.get('Club_ID')
        if circle_id:
            club_tasks.append((club_name, circle_id))
    
    # Process all clubs concurrently
    batch_size = 10
    all_matches = []
    
    for i in range(0, len(club_tasks), batch_size):
        batch = club_tasks[i:i+batch_size]
        tasks = [search_one_club(club_name, circle_id) for club_name, circle_id in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, dict) and result:
                all_matches.append(result)
    
    # Sort by active_days DESC (current club first)
    all_matches.sort(key=lambda x: x['active_days'], reverse=True)
    
    print(f"[Transfer Check] Found {len(all_matches)} clubs for viewer_id {viewer_id}")
    for match in all_matches:
        print(f"  - {match['club_name']}: {match['active_days']} active days")
    
    return all_matches

async def send_log_to_web(
    log_type: str,
    command: str = None,
    user: str = None,
    user_id: int = None,
    server: str = None,
    server_id: int = None,
    channel: str = None,
    params: str = None,
    status: str = "success",
    error: str = None
):
    """Send log entry to web dashboard"""
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{OCR_SERVICE_URL}/api/logs",
                json={
                    "type": log_type,
                    "command": command,
                    "user": user,
                    "user_id": user_id,
                    "server": server,
                    "server_id": server_id,
                    "channel": channel,
                    "params": params,
                    "status": status,
                    "error": error
                },
                timeout=aiohttp.ClientTimeout(total=5)
            )
    except Exception as e:
        print(f"Log to web error (non-critical): {e}")


async def sync_channels_to_web():
    """Sync channel list to web dashboard"""
    try:
        channels = load_channels_config()
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{OCR_SERVICE_URL}/api/channels",
                json={"channels": channels},
                timeout=aiohttp.ClientTimeout(total=5)
            )
    except Exception as e:
        print(f"Sync channels error (non-critical): {e}")


async def sync_stats_to_web():
    """Sync bot stats to web dashboard"""
    try:
        servers = len(client.guilds) if client.guilds else 0
        clubs = len(client.config_cache) if hasattr(client, 'config_cache') else 0
        members = sum(len(m) for m in client.member_cache.values()) if hasattr(client, 'member_cache') else 0
        
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{OCR_SERVICE_URL}/api/stats",
                json={
                    "servers": servers,
                    "clubs": clubs,
                    "members": members,
                    "uptime": "99.9%"
                },
                timeout=aiohttp.ClientTimeout(total=5)
            )
    except Exception as e:
        print(f"Sync stats error (non-critical): {e}")


class ProfileOwnershipView(discord.ui.View):
    """View asking if user owns the profile they're viewing"""
    
    def __init__(self, member_name: str, club_name: str):
        super().__init__(timeout=60)
        self.member_name = member_name
        self.club_name = club_name
    
    @discord.ui.button(label="Yes, this is me", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm_ownership(self, interaction: discord.Interaction, button: discord.ui.Button):
        """User claims ownership - send DM with instructions"""
        # Disable all buttons first
        for child in self.children:
            child.disabled = True
        self.stop()
        
        # Update the ephemeral message
        await interaction.response.edit_message(
            content="✅ Check your DMs! I've sent you instructions for verification.",
            view=self
        )
        
        # Try to send DM to user
        try:
            dm_channel = await interaction.user.create_dm()
            
            # Store pending verification with DM channel
            pending_verifications[interaction.user.id] = {
                "member_name": self.member_name,
                "club_name": self.club_name,
                "channel_id": dm_channel.id,  # Use DM channel
                "original_channel_id": interaction.channel_id,
                "expires": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
            }
            
            # Send DM with example image
            message_content = (
                f"📸 **Profile Verification for {self.member_name}**\n\n"
                f"Please send a screenshot of your trainer profile.\n"
                f"Make sure it clearly shows your **Trainer ID** (12-digit number like `237 076 837 318`).\n\n"
                f"**How to get it:**\n"
                f"1. Open Uma Musume\n"
                f"2. Tap your profile icon (top left)\n"
                f"3. Screenshot the profile page\n"
                f"4. Send the screenshot here\n\n"
                f"⏰ You have **5 minutes** to respond.\n\n"
                f"💡 If you don't want to link profile, type **cancel** to stop this operation."
            )
            
            if os.path.exists(EXAMPLE_PROFILE_IMAGE):
                await dm_channel.send(
                    content=message_content,
                    file=discord.File(EXAMPLE_PROFILE_IMAGE, filename="example_profile.png")
                )
            else:
                await dm_channel.send(content=message_content)
                
        except discord.Forbidden:
            # User has DMs disabled
            await interaction.followup.send(
                "❌ I couldn't send you a DM. Please enable DMs from server members and try again.",
                ephemeral=True
            )
            if interaction.user.id in pending_verifications:
                del pending_verifications[interaction.user.id]
        except Exception as e:
            print(f"Error sending DM for verification: {e}")
            await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True
            )
    
    @discord.ui.button(label="No", style=discord.ButtonStyle.grey)
    async def deny_ownership(self, interaction: discord.Interaction, button: discord.ui.Button):
        """User doesn't own this profile"""
        for child in self.children:
            child.disabled = True
        self.stop()
        
        await interaction.response.edit_message(
            content="👍 Got it! You can link your profile anytime by using `/stats` on your own profile.",
            view=self
        )


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def is_retryable_error(e: Exception) -> bool:
    """Check if an error is network-related and can be retried"""
    error_str = str(e).lower()
    retryable_keywords = [
        "remotedisconnected", "connection aborted", "service unavailable",
        "429", "failed to resolve", "name resolution",
        # Server errors (typically transient)
        "500", "502", "503", "504",
        "server error", "bad gateway", "gateway timeout",
        "internal server error", "temporarily unavailable",
        # Google Sheets API specific patterns
        "apierror: [-1]", "error 502", "that's an error"
    ]
    return any(keyword in error_str for keyword in retryable_keywords)


def invalidate_cache_for_club(club_name: str, data_sheet_name: str = None):
    """Invalidate cache for a specific club after data update
    
    Call this function after updating data via API or manual upload
    to ensure next request fetches fresh data.
    
    Args:
        club_name: Name of the club
        data_sheet_name: Optional sheet name, will auto-detect if not provided
    
    Example:
        # After updating data for TouchGold
        invalidate_cache_for_club("TouchGold")
        # Or with explicit sheet name
        invalidate_cache_for_club("TouchGold", "TouchGold_Data")
    """
    try:
        # Auto-detect sheet name if not provided
        if data_sheet_name is None:
            # Try to get from config cache
            if hasattr(client, 'config_cache') and club_name in client.config_cache:
                club_config = client.config_cache[club_name]
                data_sheet_name = club_config.get('Data_Sheet_Name')
            
            # Fallback to convention: {club_name}_Data
            if not data_sheet_name:
                data_sheet_name = f"{club_name}_Data"
        
        cache_key = f"{club_name}_{data_sheet_name}"
        smart_cache.invalidate(cache_key)
        print(f"✅ Cache invalidated for {club_name}")
        
        return True
    except Exception as e:
        print(f"⚠️ Failed to invalidate cache for {club_name}: {e}")
        return False


def format_fans(n) -> str:
    """Format fan count with K/M suffix"""
    try:
        n_int = int(float(str(n).replace(',', '')))
    except ValueError:
        return str(n)
    
    if n_int == 0:
        return "0"
    
    sign = "+" if n_int > 0 else "-" if n_int < 0 else ""
    n_abs = abs(n_int)
    
    if n_abs >= 1_000_000:
        # Nếu >= 100M: chỉ hiện số nguyên (ví dụ: +139M)
        # Nếu < 100M: hiện 1 chữ số thập phân (ví dụ: +13.6M)
        if n_abs >= 100_000_000:
            return f"{sign}{n_abs // 1_000_000}M"
        else:
            return f"{sign}{n_abs / 1_000_000:.1f}M"
    if n_abs >= 1_000:
        # Giữ nguyên làm tròn cho K
        return f"{sign}{n_abs // 1_000}K"
    
    return f"{sign}{n_abs}"


def format_fans_full(n) -> str:
    """Format fan count with full number and sign"""
    try:
        n_int = int(float(str(n).replace(',', '')))
    except ValueError:
        return str(n)
    return f"{n_int:+,}"

def format_fans_billion(n) -> str:
    """Format fan count to Billion unit"""
    try:
        n_int = int(float(str(n).replace(',', '')))
    except ValueError:
        return str(n)
    
    if n_int == 0:
        return "0B"
    
    # Convert to billion
    n_billion = n_int / 1_000_000_000
    
    if n_billion >= 10:
        return f"{n_billion:.1f}B"
    else:
        return f"{n_billion:.2f}B"


def calculate_daily_from_cumulative(cumulative: List[int]) -> List[int]:
    """
    Convert cumulative fan totals to daily differences
    
    Args:
        cumulative: List of cumulative fan totals (one per day)
    
    Returns:
        List of daily fan gains
        
    Example:
        Input:  [0, 0, 238644810, 242678516, 245877460]
        Output: [0, 0, 238644810, 4033706, 3198944]
                            ^first    ^diff    ^diff
    """
    daily = []
    for i, total in enumerate(cumulative):
        if i == 0:
            # First day: use total as daily (first non-zero is starting point)
            daily.append(total if total > 0 else 0)
        else:
            if total > 0 and cumulative[i-1] >= 0:
                # Calculate difference from previous day
                diff = total - cumulative[i-1]
                daily.append(max(0, diff))  # Prevent negative values
            else:
                # No data or invalid
                daily.append(0)
    
    return daily


def center_text_exact(text: str, total_width: int = 56) -> str:
    """Center text exactly, accounting for emoji width"""
    # Calculate actual display width
    display_width = wcswidth(text) if wcswidth(text) != -1 else len(text)
    
    if display_width >= total_width:
        return text[:total_width]
    
    padding_total = total_width - display_width
    padding_left = padding_total // 2
    padding_right = padding_total - padding_left
    
    result = (' ' * padding_left) + text + (' ' * padding_right)
    return result


def format_stat_line_compact(label: str, value: str, label_width: int = 30) -> str:
    """
    Format stat line with LEFT-ALIGNED value, accounting for emoji
    """
    if not label.endswith(':'):
        label += ':'
    
    # Calculate actual display width of label
    label_display_width = wcswidth(label) if wcswidth(label) != -1 else len(label)
    
    # Add spaces to reach target width
    spaces_needed = label_width - label_display_width
    left = label + (' ' * max(0, spaces_needed))
    
    line = left + value
    
    # Truncate if too long (based on display width)
    if wcswidth(line) > 56:
        return line[:56]
    return line
def get_last_update_timestamp() -> int:
    """Get the last update timestamp from file"""
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


def get_kick_note(player: pd.Series, max_day: int) -> Optional[str]:
    """Determine if a player should be kicked"""
    try:
        if (max_day > 10) and (player['Total Fans'] == 0):
            return "   (Should be kicked)"
    except Exception as e:
        print(f"Error in get_kick_note for {player['Name']}: {e}")
    return None

# ============================================================================
# DATABASE MANAGERS - Intelligent Hybrid System
# ============================================================================

# Try to initialize Supabase (fast, primary for config/members)
try:
    from supabase_manager import SupabaseManager
    supabase_db = SupabaseManager()
    USE_SUPABASE = True
    print("✅ Supabase connected")
except Exception as e:
    supabase_db = None
    USE_SUPABASE = False
    print(f"⚠️ Supabase unavailable: {e}")

# Phase 2: Google Sheets Manager and Hybrid DB already imported
# (gs_manager and hybrid_db initialized in models package)

# ============================================================================
# PERMISSION DECORATORS
# ============================================================================

def is_admin_or_has_role():
    """Check if user is admin or has required role"""
    async def predicate(interaction: discord.Interaction) -> bool:
        user_id = interaction.user.id
        
        # God mode always has access
        if user_id in config.GOD_MODE_USER_IDS:
            print(f"✅ Permission: {interaction.user.name} passed (God Mode)")
            return True
        
        # Must be in a guild context
        if not interaction.guild:
            print(f"❌ Permission: {interaction.user.name} failed (No guild context)")
            return False
        
        # Check if user is server owner
        if user_id == interaction.guild.owner_id:
            print(f"✅ Permission: {interaction.user.name} passed (Server Owner)")
            return True
        
        # Check administrator permission (with safety checks)
        try:
            if interaction.user.guild_permissions.administrator:
                print(f"✅ Permission: {interaction.user.name} passed (Administrator)")
                return True
        except (AttributeError, TypeError) as e:
            print(f"⚠️ Permission check error for {interaction.user.name}: {e}")
        
        # Check specific admin roles
        try:
            user_role_ids = {role.id for role in interaction.user.roles}
            if user_role_ids & set(config.ADMIN_ROLE_IDS):
                print(f"✅ Permission: {interaction.user.name} passed (Admin Role)")
                return True
        except (AttributeError, TypeError) as e:
            print(f"⚠️ Role check error for {interaction.user.name}: {e}")
        
        # Debug info when permission denied
        print(f"❌ Permission DENIED for {interaction.user.name}:")
        print(f"   - User ID: {user_id}")
        print(f"   - Guild: {interaction.guild.name}")
        print(f"   - Owner ID: {interaction.guild.owner_id}")
        print(f"   - Is Owner: {user_id == interaction.guild.owner_id}")
        try:
            print(f"   - Has Admin perm: {interaction.user.guild_permissions.administrator}")
        except:
            print(f"   - Has Admin perm: ERROR checking")
        print(f"   - GOD_MODE_USER_IDS: {config.GOD_MODE_USER_IDS}")
        print(f"   - ADMIN_ROLE_IDS: {config.ADMIN_ROLE_IDS}")
        
        return False
    return app_commands.check(predicate)


def is_primary_admin():
    """Check if user is primary admin"""
    async def predicate(interaction: discord.Interaction) -> bool:
        result = interaction.user.id in config.GOD_MODE_USER_IDS
        if result:
            print(f"✅ Permission [is_primary_admin]: {interaction.user.name} passed")
        else:
            print(f"❌ Permission [is_primary_admin]: {interaction.user.name} DENIED")
            print(f"   - User ID: {interaction.user.id}")
            print(f"   - GOD_MODE_USER_IDS: {config.GOD_MODE_USER_IDS}")
        return result
    return app_commands.check(predicate)


def is_leader_or_admin():
    """Check if user is Leader, Server Admin, or God Mode (Officers cannot use)"""
    async def predicate(interaction: discord.Interaction) -> bool:
        user_id = interaction.user.id
        
        # God mode always has access
        if user_id in config.GOD_MODE_USER_IDS:
            print(f"✅ Permission [is_leader_or_admin]: {interaction.user.name} passed (God Mode)")
            return True
        
        # Must be in a guild context
        if not interaction.guild:
            print(f"❌ Permission [is_leader_or_admin]: {interaction.user.name} failed (No guild)")
            return False
        
        # Check if user is server owner
        if user_id == interaction.guild.owner_id:
            print(f"✅ Permission [is_leader_or_admin]: {interaction.user.name} passed (Server Owner)")
            return True
        
        # Check administrator permission
        try:
            if interaction.user.guild_permissions.administrator:
                print(f"✅ Permission [is_leader_or_admin]: {interaction.user.name} passed (Administrator)")
                return True
        except (AttributeError, TypeError) as e:
            print(f"⚠️ Permission [is_leader_or_admin] check error: {e}")
        
        # Check if user is a Leader (NOT Officer)
        for club_name, club_config in client.config_cache.items():
            leaders = club_config.get('Leaders', [])
            if user_id in leaders:
                print(f"✅ Permission [is_leader_or_admin]: {interaction.user.name} passed (Leader of {club_name})")
                return True
        
        # Debug info when permission denied
        print(f"❌ Permission [is_leader_or_admin] DENIED for {interaction.user.name}:")
        print(f"   - User ID: {user_id}")
        print(f"   - Guild: {interaction.guild.name}")
        print(f"   - Owner ID: {interaction.guild.owner_id}")
        print(f"   - Is Owner: {user_id == interaction.guild.owner_id}")
        try:
            print(f"   - Has Admin perm: {interaction.user.guild_permissions.administrator}")
        except:
            print(f"   - Has Admin perm: ERROR checking")
        
        return False
    return app_commands.check(predicate)



def is_god_mode_only():
    """Check if user is God mode - error handled by global error handler"""
    async def predicate(interaction: discord.Interaction) -> bool:
        result = interaction.user.id in config.GOD_MODE_USER_IDS
        if result:
            print(f"✅ Permission [is_god_mode_only]: {interaction.user.name} passed")
        else:
            print(f"❌ Permission [is_god_mode_only]: {interaction.user.name} DENIED")
            print(f"   - User ID: {interaction.user.id}")
            print(f"   - GOD_MODE_USER_IDS: {config.GOD_MODE_USER_IDS}")
        return result
    return app_commands.check(predicate)


# ============================================================================
# CHANNEL MANAGEMENT HELPER FUNCTIONS
# ============================================================================

def load_channels_config() -> List[dict]:
    """Load list of allowed channels from file"""
    try:
        if os.path.exists(ALLOWED_CHANNELS_CONFIG_FILE):
            with open(ALLOWED_CHANNELS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            channels = data.get('channels', [])
            print(f"✅ Loaded {len(channels)} allowed channel(s)")
            return channels
    except Exception as e:
        print(f"⚠️ Error loading channels config: {e}")
    return []



def add_channel_to_config(interaction: discord.Interaction) -> dict:
    """Add a channel to the allowed channels list"""
    
    # Load existing channels
    existing_channels = load_channels_config()
    
    # Check if channel already exists
    channel_exists = any(ch['channel_id'] == interaction.channel_id for ch in existing_channels)
    if channel_exists:
        raise ValueError(f"Channel already in allowed list")
    
    # Create new channel entry
    new_channel = {
        "channel_id": interaction.channel_id,
        "channel_name": interaction.channel.name if hasattr(interaction.channel, 'name') else "Unknown",
        "server_id": interaction.guild_id,
        "server_name": interaction.guild.name if interaction.guild else "Unknown",
        "added_by": interaction.user.id,
        "added_by_name": str(interaction.user),
        "added_at": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # Add to list
    existing_channels.append(new_channel)
    
    # Save to file
    config_data = {
        "channels": existing_channels,
        "last_updated": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
    }
    
    with open(ALLOWED_CHANNELS_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)
    
    print(f"💾 Added channel: {new_channel['channel_name']} (ID: {new_channel['channel_id']})")
    return new_channel


def remove_channel_from_config(channel_id: int) -> bool:
    """Remove a channel from allowed list"""
    
    # Load existing channels
    existing_channels = load_channels_config()
    
    # Find and remove channel
    original_count = len(existing_channels)
    existing_channels = [ch for ch in existing_channels if ch['channel_id'] != channel_id]
    
    if len(existing_channels) == original_count:
        return False  # Channel not found
    
    # Save updated list
    config_data = {
        "channels": existing_channels,
        "last_updated": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
    }
    
    with open(ALLOWED_CHANNELS_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)
    
    print(f"🗑️ Removed channel ID: {channel_id}")
    return True



def log_channel_change(action: str, interaction: discord.Interaction, note: str = None):
    """Log channel configuration changes to history file"""
    try:
        # Load existing history
        history = {"history": []}
        if os.path.exists(CHANNEL_CHANGE_LOG_FILE):
            with open(CHANNEL_CHANGE_LOG_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        
        # Create log entry
        log_entry = {
            "action": action,
            "changed_by": interaction.user.id,
            "changed_by_name": str(interaction.user),
            "timestamp": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
        }
        
        if action == "set_channel":
            log_entry.update({
                "channel_id": interaction.channel_id,
                "channel_name": interaction.channel.name if hasattr(interaction.channel, 'name') else "Unknown",
                "server_id": interaction.guild_id,
                "server_name": interaction.guild.name if interaction.guild else "Unknown"
            })
        
        if note:
            log_entry["note"] = note
        
        # Append and save
        history["history"].insert(0, log_entry)  # Most recent first
        
        with open(CHANNEL_CHANGE_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        
        print(f"📝 Logged action: {action}")
    
    except Exception as e:
        print(f"⚠️ Error logging channel change: {e}")


async def send_log_to_channel(embed: discord.Embed):
    """Send notification embed to logging channel"""
    try:
        log_channel = client.get_channel(LOGGING_CHANNEL_ID)
        
        if not log_channel:
            print(f"⚠️ Warning: Logging channel {LOGGING_CHANNEL_ID} not found")
            return
        
        await log_channel.send(embed=embed)
        print(f"✅ Sent log to channel {LOGGING_CHANNEL_ID}")
    
    except discord.errors.Forbidden:
        print(f"❌ No permission to send to logging channel {LOGGING_CHANNEL_ID}")
    except Exception as e:
        print(f"❌ Error sending log to channel: {e}")


async def send_debug_log(embed: discord.Embed):
    """Send error/debug logs to dedicated debug channel (for failed commands)"""
    try:
        debug_channel = client.get_channel(DEBUG_LOG_CHANNEL_ID)
        
        if not debug_channel:
            print(f"⚠️ Warning: Debug log channel {DEBUG_LOG_CHANNEL_ID} not found")
            return
        
        await debug_channel.send(embed=embed)
        print(f"📝 Sent debug log to channel {DEBUG_LOG_CHANNEL_ID}")
    
    except discord.errors.Forbidden:
        print(f"❌ No permission to send to debug channel {DEBUG_LOG_CHANNEL_ID}")
    except Exception as e:
        print(f"❌ Error sending debug log: {e}")


def load_admin_list() -> List[int]:
    """Load admin user IDs from file"""
    try:
        if os.path.exists(ADMIN_LIST_FILE):
            with open(ADMIN_LIST_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            admin_ids = data.get("admin_user_ids", [])
            print(f"✅ Loaded {len(admin_ids)} dynamic admins")
            return admin_ids
    except Exception as e:
        print(f"⚠️ Error loading admin list: {e}")
    return []


def save_admin_list(admin_ids: List[int], updated_by: int):
    """Save admin user IDs to file"""
    try:
        admin_data = {
            "admin_user_ids": admin_ids,
            "last_updated": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S"),
            "updated_by": updated_by
        }
        
        with open(ADMIN_LIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(admin_data, f, indent=2, ensure_ascii=False)
        
        print(f"💾 Saved admin list: {len(admin_ids)} admins")
    
    except Exception as e:
        print(f"❌ Error saving admin list: {e}")
        raise


# ============================================================================
# CHANNEL LIST DISPLAY SYSTEM
# ============================================================================

def load_channel_list_message_id() -> int:
    """Load the permanent channel list message ID from file"""
    try:
        if os.path.exists(CHANNEL_LIST_CONFIG_FILE):
            with open(CHANNEL_LIST_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get("channel_list_message_id")
    except Exception as e:
        print(f"⚠️ Error loading channel list message ID: {e}")
    return None


def save_channel_list_message_id(message_id: int):
    """Save the permanent channel list message ID to file"""
    try:
        config_data = {
            "channel_list_message_id": message_id,
            "channel_list_channel_id": CHANNEL_LIST_DISPLAY_CHANNEL_ID,
            "last_updated": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(CHANNEL_LIST_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        
        print(f"💾 Saved channel list message ID: {message_id}")
    except Exception as e:
        print(f"❌ Error saving channel list message ID: {e}")


# ============================================================================
# SERVER INVITE LINKS MANAGEMENT
# ============================================================================

def load_server_invites() -> dict:
    """Load all server invite links from file"""
    try:
        if os.path.exists(SERVER_INVITES_FILE):
            with open(SERVER_INVITES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('invites', {})
    except Exception as e:
        print(f"⚠️ Error loading server invites: {e}")
    return {}


def save_server_invite(server_id: int, server_name: str, invite_url: str, member_count: int = 0):
    """Save a server invite link to file"""
    try:
        invites = load_server_invites()
        
        invites[str(server_id)] = {
            "server_name": server_name,
            "invite_url": invite_url,
            "member_count": member_count,
            "created_at": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
        }
        
        config_data = {
            "invites": invites,
            "last_updated": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(SERVER_INVITES_FILE, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        
        print(f"💾 Saved invite for {server_name}: {invite_url}")
    except Exception as e:
        print(f"❌ Error saving server invite: {e}")


def remove_server_invite(server_id: int):
    """Remove a server invite from file when bot leaves"""
    try:
        invites = load_server_invites()
        server_id_str = str(server_id)
        
        if server_id_str in invites:
            server_name = invites[server_id_str].get('server_name', 'Unknown')
            del invites[server_id_str]
            
            config_data = {
                "invites": invites,
                "last_updated": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
            }
            
            with open(SERVER_INVITES_FILE, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            
            print(f"🗑️ Removed invite for {server_name}")
    except Exception as e:
        print(f"❌ Error removing server invite: {e}")


def get_server_invite(server_id: int) -> str:
    """Get invite URL for a specific server"""
    invites = load_server_invites()
    invite_data = invites.get(str(server_id), {})
    return invite_data.get('invite_url')


# ============================================================================
# FILTER MODAL FOR GLOBAL LEADERBOARD
# ============================================================================

class FilterModal(discord.ui.Modal, title="Filter by Daily Average"):
    """Modal for filtering members by daily average"""
    
    min_daily = discord.ui.TextInput(
        label="Minimum Daily Average",
        placeholder="e.g., 10000",
        required=True,
        style=discord.TextStyle.short,
        min_length=1,
        max_length=10
    )
    
    max_daily = discord.ui.TextInput(
        label="Maximum Daily Average (Optional)",
        placeholder="e.g., 50000 (leave empty for no max)",
        required=False,
        style=discord.TextStyle.short,
        min_length=0,
        max_length=10
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """Process filter and update leaderboard"""
        try:
            # Parse inputs
            min_val = int(self.min_daily.value.replace(',', '').replace(' ', ''))
            max_val = None
            
            if self.max_daily.value.strip():
                max_val = int(self.max_daily.value.replace(',', '').replace(' ', ''))
            
            # Validate
            if max_val and max_val < min_val:
                await interaction.response.send_message(
                    "❌ Max must be greater than Min!",
                    ephemeral=True
                )
                return
            
            # Apply filter through view
            await self.view.apply_filter_and_update(interaction, min_val, max_val)
            
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter valid numbers!",
                ephemeral=True
            )


class OldClubModal(discord.ui.Modal, title="Add Old Club Data"):
    """Modal for entering old club ID to fetch data from uma.moe"""
    
    club_id = discord.ui.TextInput(
        label="Old Club ID (from uma.moe URL)",
        placeholder="e.g., 123456789",
        required=True,
        style=discord.TextStyle.short,
        min_length=1,
        max_length=20
    )
    
    def __init__(self, viewer_id: str, current_club: str, member_name: str, original_interaction=None):
        super().__init__()
        self.viewer_id = viewer_id
        self.current_club = current_club
        self.member_name = member_name
        self.original_interaction = original_interaction
    
    async def on_submit(self, interaction: discord.Interaction):
        """Fetch old club data and save transfer info"""
        from auto_sync_helpers import fetch_circle_data
        
        await interaction.response.defer(ephemeral=True)
        
        old_club_id = self.club_id.value.strip()
        
        try:
            # 1. Fetch old club data from uma.moe
            print(f"[Old Club] Fetching data for club ID: {old_club_id}")
            api_data = await fetch_circle_data(old_club_id, timeout=15)
            
            if not api_data:
                await interaction.followup.send(
                    f"❌ Could not fetch data for Club ID `{old_club_id}`.\n"
                    "Please verify the ID from uma.moe URL.",
                    ephemeral=True
                )
                return
            
            old_club_name = api_data.get('name', 'Unknown Club')
            members = api_data.get('members', [])
            
            # 2. Find member in old club
            old_member_data = None
            for member in members:
                if str(member.get('viewer_id', '')) == str(self.viewer_id):
                    old_member_data = member
                    break
            
            if not old_member_data:
                await interaction.followup.send(
                    f"❌ You were not found in **{old_club_name}**.\n"
                    f"Please verify this is your previous club.",
                    ephemeral=True
                )
                return
            
            # 3. Calculate old club stats
            daily_fans = old_member_data.get('daily_fans', [])
            old_total = sum(fans for fans in daily_fans if fans and fans > 0)
            old_active_days = sum(1 for fans in daily_fans if fans and fans > 0)
            
            # 4. Save to transfer_requests.json
            transfer_file = os.path.join(SCRIPT_DIR, 'transfer_requests.json')
            transfers = []
            if os.path.exists(transfer_file):
                try:
                    with open(transfer_file, 'r', encoding='utf-8') as f:
                        transfers = json.load(f)
                except:
                    transfers = []
            
            # Remove old entry if exists
            transfers = [t for t in transfers if t.get('viewer_id') != self.viewer_id]
            
            # Add new entry
            transfers.append({
                'viewer_id': self.viewer_id,
                'member_name': self.member_name,
                'old_club_name': old_club_name,
                'old_club_id': old_club_id,
                'new_club_name': self.current_club,
                'old_total_fans': old_total,
                'old_active_days': old_active_days,
                'added_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'added_by': interaction.user.id
            })
            
            with open(transfer_file, 'w', encoding='utf-8') as f:
                json.dump(transfers, f, indent=2, ensure_ascii=False)
            
            # 5. Update CROSS_CLUB_CACHE
            update_cross_club_cache(
                trainer_id=self.viewer_id,
                club_name=old_club_name,
                day31_cumulative=old_total,
                month=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
            )
            
            # 6. Confirm to user
            await interaction.followup.send(
                f"✅ **Old club data added!**\n\n"
                f"**Previous Club:** {old_club_name}\n"
                f"**Total Fans:** {old_total:,}\n"
                f"**Active Days:** {old_active_days}\n\n"
                f"Use `/profile` again to see combined stats.",
                ephemeral=True
            )
            
            print(f"[Old Club] Saved transfer: {self.member_name} from {old_club_name} to {self.current_club}")
            
        except Exception as e:
            print(f"[Old Club] Error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                f"❌ Error fetching old club data: {e}",
                ephemeral=True
            )


class OldClubPromptView(discord.ui.View):
    """View with button to add old club data for transferred members"""
    
    def __init__(self, viewer_id: str, current_club: str, member_name: str):
        super().__init__(timeout=300)
        self.viewer_id = viewer_id
        self.current_club = current_club
        self.member_name = member_name
    
    @discord.ui.button(label="📦 Add Old Club", style=discord.ButtonStyle.primary)
    async def add_old_club_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open modal to input old club ID"""
        modal = OldClubModal(
            viewer_id=self.viewer_id,
            current_club=self.current_club,
            member_name=self.member_name
        )
        await interaction.response.send_modal(modal)
        
        # Disable button after clicked
        button.disabled = True
        button.label = "📦 Old Club Added"
        await interaction.message.edit(view=self)
    
    @discord.ui.button(label="❌ Skip", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Skip adding old club data"""
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="Skipped. Use `/profile` anytime to see your stats.",
            view=self
        )

class GlobalLeaderboardView(discord.ui.View):
    """Pagination view with filtering for global leaderboard"""
    
    def __init__(self, all_members: list, members_per_page: int = 10, original_view=None, original_embed=None):
        super().__init__(timeout=300)  # 5 min timeout
        self.all_members_original = all_members.copy()  # Keep original
        self.all_members = all_members  # Working copy
        self.members_per_page = members_per_page
        self.current_page = 0
        self.filter_min = None
        self.filter_max = None
        self.original_view = original_view  # LeaderboardView to return to
        self.original_embed = original_embed  # Original leaderboard embed
        
        self._update_pagination()
        self.update_buttons()
    
    def _update_pagination(self):
        """Recalculate pagination after filter"""
        total_members = len(self.all_members)
        self.total_pages = max(1, (total_members + self.members_per_page - 1) // self.members_per_page)
    
    async def apply_filter_and_update(self, interaction: discord.Interaction, min_daily: int, max_daily: int = None):
        """Filter members and update message"""
        self.filter_min = min_daily
        self.filter_max = max_daily
        
        # Filter from original list
        self.all_members = [
            m for m in self.all_members_original
            if min_daily <= m['daily'] and (max_daily is None or m['daily'] <= max_daily)
        ]
        
        # Reset to page 1
        self.current_page = 0
        self._update_pagination()
        self.update_buttons()
        
        # Update message with embed
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    def clear_filter(self):
        """Remove filter and show all members"""
        self.filter_min = None
        self.filter_max = None
        self.all_members = self.all_members_original.copy()
        self.current_page = 0
        self._update_pagination()
        self.update_buttons()
    
    def update_buttons(self):
        """Enable/disable buttons based on current page"""
        # First page buttons
        self.first_button.disabled = (self.current_page == 0)
        self.previous_button.disabled = (self.current_page == 0)
        
        # Last page buttons
        self.next_button.disabled = (self.current_page >= self.total_pages - 1)
        self.last_button.disabled = (self.current_page >= self.total_pages - 1)
        
        # Clear filter button
        self.clear_filter_button.disabled = (self.filter_min is None)
    
    def get_page_embed(self) -> discord.Embed:
        """Generate Discord Embed for global leaderboard"""
        start_idx = self.current_page * self.members_per_page
        end_idx = min(start_idx + self.members_per_page, len(self.all_members))
        page_members = self.all_members[start_idx:end_idx]
        
        # Title
        title = "🌍 Global Leaderboard - All Members"
        
        # Description (filter status)
        description_parts = []
        if self.filter_min is not None:
            filter_text = f"Daily Average: {self.filter_min:,}"
            if self.filter_max:
                filter_text += f" - {self.filter_max:,}"
            else:
                filter_text += "+"
            description_parts.append(f"🔍 **Filter:** {filter_text}")
        else:
            description_parts.append("Rankings across all clubs")
        
        description = "\n".join(description_parts)
        
        # Create embed
        embed = discord.Embed(
            title=title,
            description=description,
            color=0x00D9FF  # Cyan color
        )
        
        # Summary section
        total_members_shown = len(self.all_members)
        total_members_all = len(self.all_members_original)
        total_clubs = len(set(m['club'] for m in self.all_members))
        
        summary_text = f"**Total Members:** {total_members_all}\n"
        summary_text += f"**Showing:** {total_members_shown} ✅\n"
        summary_text += f"**Total Clubs:** {total_clubs}"
        
        embed.add_field(name="📊 Summary", value=summary_text, inline=False)
        
        # Rankings section
        if page_members:
            rankings_lines = []
            for i, member in enumerate(page_members, start=start_idx + 1):
                # Medal for top 3
                if i <= 3:
                    medal = ["🥇", "🥈", "🥉"][i-1]
                else:
                    medal = f"**{i}.**"
                
                # Format numbers
                monthly_formatted = f"{member['fans']:,}"
                daily_formatted = f"{member['daily']:,}/day"
                
                # Build member entry with tree structure
                rankings_lines.append(f"{medal} {member['name']}")
                rankings_lines.append(f"├ **Monthly Growth:** {monthly_formatted} fans")
                rankings_lines.append(f"├ **Daily Average:** {daily_formatted}")
                rankings_lines.append(f"└ **Club:** {member['club']}\n")
            
            rankings_text = "\n".join(rankings_lines)
        else:
            rankings_text = "⚠️ No members match filter criteria"
        
        embed.add_field(
            name=f"📋 Rankings (Page {self.current_page + 1}/{self.total_pages})",
            value=rankings_text,
            inline=False
        )
        
        # Footer
        timestamp = datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime('%Y-%m-%d %H:%M:%S')
        embed.set_footer(text=f"Last updated: {timestamp}")
        
        return embed
    
    # ===== NAVIGATION BUTTONS (Row 0) =====
    
    @discord.ui.button(label="⏮ First", style=discord.ButtonStyle.secondary, custom_id="global_lb_first", row=0)
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Jump to first page"""
        if self.current_page != 0:
            self.current_page = 0
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="global_lb_prev", row=0)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to previous page"""
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="global_lb_next", row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to next page"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(label="Last ⏭", style=discord.ButtonStyle.secondary, custom_id="global_lb_last", row=0)
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Jump to last page"""
        last_page = self.total_pages - 1
        if self.current_page != last_page:
            self.current_page = last_page
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        else:
            await interaction.response.defer()
    
    # ===== FILTER BUTTONS (Row 1) =====
    
    @discord.ui.button(label="🔍 Filter", style=discord.ButtonStyle.primary, custom_id="global_lb_filter", row=1)
    async def filter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open filter modal"""
        modal = FilterModal()
        modal.view = self  # Pass view reference
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🔄 Clear Filter", style=discord.ButtonStyle.danger, custom_id="global_lb_clear", row=1)
    async def clear_filter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Remove filter"""
        if self.filter_min is None:
            await interaction.response.defer()
            return
        
        self.clear_filter()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    @discord.ui.button(label="⬅️ Return", style=discord.ButtonStyle.secondary, custom_id="global_lb_return", row=1)
    async def return_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Return to club leaderboard"""
        if self.original_view and self.original_embed:
            # Reset original view buttons
            self.original_view.clear_items()
            self.original_view.add_item(self.original_view.summary_button)
            self.original_view.add_item(self.original_view.global_lb_button)
            await interaction.response.edit_message(embed=self.original_embed, view=self.original_view)
        else:
            # No original view, just dismiss
            await interaction.response.defer()


# ============================================================================
# HELPER FUNCTION FOR GLOBAL LEADERBOARD
# ============================================================================

async def get_all_members_global() -> list:
    """Get all members from all clubs for global leaderboard - FAST (uses cache)"""
    all_members = []
    
    # CRITICAL FIX: Ensure ALL club caches are loaded to prevent inconsistent results
    # Without this, global leaderboard shows different data depending on which club was viewed first
    clubs_to_load = []
    for club_name, club_config in client.config_cache.items():
        data_sheet_name = club_config.get('Data_Sheet_Name')
        if data_sheet_name:
            cache_key = f"{club_name}_{data_sheet_name}"
            # Check if cache exists and is fresh
            cached_result = smart_cache.get(cache_key)
            if cached_result is None:
                clubs_to_load.append((club_name, data_sheet_name))
    
    # Warm missing caches (fast - concurrent)
    if clubs_to_load:
        import asyncio
        tasks = []
        for club_name, sheet_name in clubs_to_load:
            task = asyncio.create_task(_load_data_for_command(club_name, sheet_name))
            tasks.append(task)
        
        # Wait for all loads (with timeout)
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10.0)
        except asyncio.TimeoutError:
            print("⚠️ Some club data loading timed out for global leaderboard")
    
    # Now load data from cache (all should be available)
    for club_name, club_config in client.config_cache.items():
        data_sheet_name = club_config.get('Data_Sheet_Name')
        if not data_sheet_name:
            continue
        
        try:
            # FAST: Read from cache (use global smart_cache, not client.smart_cache)
            cache_key = f"{club_name}_{data_sheet_name}"
            
            # Try SmartCache (in-memory + disk)
            cached_result = smart_cache.get(cache_key)
            df = None
            
            if cached_result is not None:
                df, cache_timestamp = cached_result
            
            if df is None or df.empty:
                continue
            
            # Calculate monthly stats
            min_day = df['Day'].min()
            max_day = df['Day'].max()
            days_count = max_day - min_day + 1
            
            # Group by member name
            for member_name in df['Name'].unique():
                member_df = df[df['Name'] == member_name]
                
                # Get first and last record
                first_record = member_df[member_df['Day'] == min_day]
                last_record = member_df[member_df['Day'] == max_day]
                
                if first_record.empty or last_record.empty:
                    continue
                
                # Calculate monthly growth
                first_fans = int(first_record.iloc[0].get('Total Fans', 0))
                last_fans = int(last_record.iloc[0].get('Total Fans', 0))
                monthly_growth = last_fans - first_fans
                
                # Calculate daily average
                daily_average = monthly_growth // days_count if days_count > 0 else 0
                
                all_members.append({
                    'name': member_name,
                    'fans': monthly_growth,  # Total gained this month
                    'daily': daily_average,  # Average per day
                    'club': club_name
                })
        except Exception as e:
            print(f"⚠️ Error loading {club_name} for global leaderboard: {e}")
            continue
    
    # Sort by monthly growth (descending)
    all_members.sort(key=lambda x: x['fans'], reverse=True)
    return all_members




async def get_channel_status(channel_id: int, server_id: int, server_name_cached: str, channel_name_cached: str) -> tuple:
    try:
        if os.path.exists(GLOBAL_LEADERBOARD_CONFIG_FILE):
            with open(GLOBAL_LEADERBOARD_CONFIG_FILE, 'r') as f:
                data = json.load(f)
                return data.get("global_leaderboard_message_id")
    except Exception as e:
        print(f"⚠️ Error loading global leaderboard message ID: {e}")
    return None

def save_global_leaderboard_message_id(message_id: int):
    """Save global leaderboard message ID to file"""
    try:
        with open(GLOBAL_LEADERBOARD_CONFIG_FILE, 'w') as f:
            json.dump({"global_leaderboard_message_id": message_id}, f)
        print(f"✅ Saved global leaderboard message ID: {message_id}")
    except Exception as e:
        print(f"❌ Error saving global leaderboard message ID: {e}")


async def update_global_leaderboard_message():
    """Update or create global leaderboard message with pagination"""
    try:
        # Get display channel
        display_channel = client.get_channel(CHANNEL_LIST_DISPLAY_CHANNEL_ID)
        if not display_channel:
            print("❌ Global leaderboard display channel not found")
            return
        
        print("🔄 Updating global leaderboard...")
        
        # Load message ID
        message_id = load_global_leaderboard_message_id()
        
        # Aggregate all members from all clubs
        all_members = []
        
        for club_name, club_config in client.config_cache.items():
            data_sheet_name = club_config.get('Data_Sheet_Name')
            if not data_sheet_name:
                continue
            
            try:
                # Get latest data from Google Sheets
                df, _ = await _load_data_for_command(club_name, data_sheet_name)
                
                if df is None or df.empty:
                    continue
                
                # Calculate monthly stats
                # Get first and last day of current data
                min_day = df['Day'].min()
                max_day = df['Day'].max()
                days_count = max_day - min_day + 1
                
                # Group by member name
                for member_name in df['Name'].unique():
                    member_df = df[df['Name'] == member_name]
                    
                    # Get first and last record
                    first_record = member_df[member_df['Day'] == min_day]
                    last_record = member_df[member_df['Day'] == max_day]
                    
                    if first_record.empty or last_record.empty:
                        continue
                    
                    # Calculate monthly growth
                    first_fans = int(first_record.iloc[0].get('Total Fans', 0))
                    last_fans = int(last_record.iloc[0].get('Total Fans', 0))
                    monthly_growth = last_fans - first_fans
                    
                    # Calculate daily average
                    daily_average = monthly_growth // days_count if days_count > 0 else 0
                    
                    all_members.append({
                        'name': member_name,
                        'fans': monthly_growth,  # Total gained this month
                        'daily': daily_average,  # Average per day
                        'club': club_name,
                        'days': days_count
                    })
            except Exception as e:
                print(f"⚠️ Error loading {club_name} for leaderboard: {e}")
                continue
        
        # Sort by monthly growth
        all_members.sort(key=lambda x: x['fans'], reverse=True)
        
        # Create view with pagination
        if all_members:
            view = GlobalLeaderboardView(all_members, members_per_page=10)
            content = view.get_page_content()
        else:
            view = None
            content = (
                "🏆 **Global Leaderboard**\n\n"
                "⚠️ No member data available yet.\n"
                "Data will appear after clubs are set up and synced."
            )
        
        # Update or create message
        if message_id:
            try:
                message = await display_channel.fetch_message(message_id)
                await message.edit(content=content, view=view)
                print(f"✅ Updated global leaderboard ({len(all_members)} members)")
            except discord.NotFound:
                # Message deleted, create new
                message = await display_channel.send(content=content, view=view)
                save_global_leaderboard_message_id(message.id)
                print(f"✅ Created new global leaderboard: {message.id}")
        else:
            # Create new message
            message = await display_channel.send(content=content, view=view)
            save_global_leaderboard_message_id(message.id)
            print(f"✅ Created global leaderboard: {message.id}")
    
    except Exception as e:
        print(f"❌ Error updating global leaderboard: {e}")
        import traceback
        traceback.print_exc()


async def get_channel_status(channel_id: int, server_id: int, server_name_cached: str, channel_name_cached: str) -> tuple:
    """Get channel status and info - NEVER auto-delete from list
    
    Returns:
        tuple: (status_emoji, channel_mention_or_name, status_text, server_name, channel_type)
    """
    try:
        channel = client.get_channel(channel_id)
        
        if channel:
            # Channel found and accessible
            server_name = channel.guild.name if channel.guild else "Direct Message"
            
            # Determine channel type
            if isinstance(channel, discord.TextChannel):
                channel_type = "Text"
            elif isinstance(channel, discord.VoiceChannel):
                channel_type = "Voice"
            elif isinstance(channel, discord.StageChannel):
                channel_type = "Stage"
            elif isinstance(channel, discord.ForumChannel):
                channel_type = "Forum"
            elif isinstance(channel, discord.CategoryChannel):
                channel_type = "Category"
            else:
                channel_type = "Unknown"
            
            return ("✅", channel.mention, "Accessible", server_name, channel_type)
        else:
            # Bot not in server or channel deleted - USE CACHED DATA
            # DO NOT auto-delete from list
            return ("⚠️", f"#{channel_name_cached}", 
                "Bot not in server (cached)", 
                server_name_cached, "Unknown")
                
    except discord.errors.Forbidden:
        return ("❌", f"Channel {channel_id}", 
            "No permission to view", 
            "Unknown", "Unknown")
    except Exception as e:
        return ("❌", f"Channel {channel_id}", 
            f"Error: {str(e)[:50]}", 
            "Unknown", "Unknown")


# ============================================================================
# CHANNEL LIST VIEW WITH PAGINATION
# ============================================================================

class ChannelListView(discord.ui.View):
    """Pagination view for channel list with invite links"""
    
    def __init__(self, channels_data: list, invites: dict, channels_per_page: int = 10):
        super().__init__(timeout=None)  # Persistent view
        self.channels_data = channels_data  # List of (channel_info_dict, status_tuple)
        self.invites = invites
        self.channels_per_page = channels_per_page
        self.current_page = 0
        self.total_pages = max(1, (len(channels_data) + channels_per_page - 1) // channels_per_page)
        self._update_buttons()
    
    def _update_buttons(self):
        """Update button states based on current page"""
        self.first_btn.disabled = self.current_page == 0
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page >= self.total_pages - 1
        self.last_btn.disabled = self.current_page >= self.total_pages - 1
    
    def get_page_embed(self) -> discord.Embed:
        """Generate embed for current page"""
        current_time = datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
        
        embed = discord.Embed(
            title="📋 Bot Allowed Channels Configuration",
            description=f"**Channels where the bot commands can be used**\n\n"
                    f"📄 Page {self.current_page + 1}/{self.total_pages} | Total: {len(self.channels_data)} channels",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        
        if not self.channels_data:
            embed.add_field(
                name="🌐 Current Status",
                value="**No channel restrictions active**\n\n"
                    "The bot can be used in **all channels** across all servers.\n"
                    "Use `/set_channel` to add restrictions.",
                inline=False
            )
        else:
            # Get channels for current page
            start_idx = self.current_page * self.channels_per_page
            end_idx = min(start_idx + self.channels_per_page, len(self.channels_data))
            page_channels = self.channels_data[start_idx:end_idx]
            
            # Build channel list for this page
            channel_lines = []
            for i, (ch_data, status_tuple) in enumerate(page_channels, start=start_idx + 1):
                status_emoji, channel_info, status_text, server_name, channel_type = status_tuple
                server_id = ch_data.get('server_id')
                
                line = (
                    f"**{i}.** {status_emoji} {channel_info}\n"
                    f"   └ **Server:** {server_name}\n"
                    f"   └ **ID:** `{ch_data.get('channel_id')}`"
                )
                channel_lines.append(line)
            
            # Add to embed (split if needed)
            content = "\n\n".join(channel_lines)
            if len(content) > 1024:
                # Split into multiple fields
                mid = len(channel_lines) // 2
                embed.add_field(
                    name=f"📝 Channels ({start_idx + 1}-{start_idx + mid})",
                    value="\n\n".join(channel_lines[:mid])[:1024],
                    inline=False
                )
                embed.add_field(
                    name=f"📝 Channels ({start_idx + mid + 1}-{end_idx})",
                    value="\n\n".join(channel_lines[mid:])[:1024],
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"📝 Channels ({start_idx + 1}-{end_idx})",
                    value=content,
                    inline=False
                )
        
        embed.add_field(
            name="⚙️ Management",
            value="**Set channel:** `/set_channel`\n"
                "*Only administrators can manage channels*",
            inline=False
        )
        
        embed.set_footer(text=f"Last updated: {current_time} (Vietnam)")
        return embed
    
    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary, custom_id="channel_list_first")
    async def first_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary, custom_id="channel_list_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary, custom_id="channel_list_next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary, custom_id="channel_list_last")
    async def last_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.total_pages - 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)


async def update_channel_list_message():
    """Update or create the permanent channel list message with pagination"""
    try:
        # Load channels from file
        channels_config = load_channels_config()
        
        # Load server invites
        invites = load_server_invites()
        
        # Get the display channel
        display_channel = client.get_channel(CHANNEL_LIST_DISPLAY_CHANNEL_ID)
        if not display_channel:
            print(f"⚠️ Cannot find channel list display channel: {CHANNEL_LIST_DISPLAY_CHANNEL_ID}")
            return
        
        # Build channel data with status
        channels_data = []
        channels_to_remove = []
        
        for ch_data in channels_config:
            channel_id = ch_data['channel_id']
            server_id = ch_data.get('server_id', 'Unknown')
            server_name_cached = ch_data.get('server_name', 'Unknown Server')
            channel_name_cached = ch_data.get('channel_name', 'Unknown Channel')
            
            try:
                status_tuple = await get_channel_status(
                    channel_id, 
                    server_id, 
                    server_name_cached, 
                    channel_name_cached
                )
                
                if status_tuple[0] == "✅":
                    channels_data.append((ch_data, status_tuple))
                else:
                    # Bot not in server - auto remove
                    channels_to_remove.append(ch_data)
                    print(f"🗑️ Auto-removing channel: {channel_name_cached} - Bot not in server")
            except Exception as e:
                # Keep with error status
                error_tuple = ("❌", f"#{channel_name_cached}", f"Error", server_name_cached, "Unknown")
                channels_data.append((ch_data, error_tuple))
        
        # Auto-remove inaccessible channels
        if channels_to_remove:
            print(f"🗑️ Removing {len(channels_to_remove)} inaccessible channel(s)")
            for ch_to_remove in channels_to_remove:
                channels_config = [ch for ch in channels_config if ch.get('channel_id') != ch_to_remove.get('channel_id')]
            
            config.ALLOWED_CHANNEL_IDS = [ch['channel_id'] for ch in channels_config]
            
            config_data = {
                "channels": channels_config,
                "last_updated": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(ALLOWED_CHANNELS_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
        
        # Create view with pagination
        view = ChannelListView(channels_data, invites, channels_per_page=10)
        embed = view.get_page_embed()
        
        # Try to delete old message and create new one (to ensure view works)
        message_id = load_channel_list_message_id()
        
        if message_id:
            try:
                old_message = await display_channel.fetch_message(message_id)
                await old_message.delete()
                print(f"🗑️ Deleted old channel list message {message_id}")
            except:
                pass
        
        # Create new message with view
        message = await display_channel.send(embed=embed, view=view)
        save_channel_list_message_id(message.id)
        print(f"✅ Created new channel list message {message.id} with pagination")
        
    except Exception as e:
        print(f"❌ Error updating channel list message: {e}")
        import traceback
        traceback.print_exc()


# ============================================================================
# SERVER LIST VIEW WITH PAGINATION
# ============================================================================

# Server list config file
SERVER_LIST_CONFIG_FILE = os.path.join(SCRIPT_DIR, "server_list_config.json")

def load_server_list_message_id() -> int:
    """Load server list message ID from file"""
    try:
        if os.path.exists(SERVER_LIST_CONFIG_FILE):
            with open(SERVER_LIST_CONFIG_FILE, 'r') as f:
                data = json.load(f)
                return data.get('message_id')
    except Exception as e:
        print(f"⚠️ Error loading server list message ID: {e}")
    return None

def save_server_list_message_id(message_id: int):
    """Save server list message ID to file"""
    try:
        with open(SERVER_LIST_CONFIG_FILE, 'w') as f:
            json.dump({
                "message_id": message_id,
                "updated_at": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).isoformat()
            }, f)
        print(f"✅ Saved server list message ID: {message_id}")
    except Exception as e:
        print(f"❌ Error saving server list message ID: {e}")


class ServerListView(discord.ui.View):
    """Pagination view for server list"""
    
    def __init__(self, servers_data: list, invites: dict = None, servers_per_page: int = 10):
        super().__init__(timeout=None)  # Persistent view
        self.servers_data = servers_data
        self.invites = invites or {}  # {server_id: {'invite_url': url}}
        self.servers_per_page = servers_per_page
        self.current_page = 0
        self.total_pages = max(1, (len(servers_data) + servers_per_page - 1) // servers_per_page)
        self._update_buttons()
    
    def _update_buttons(self):
        """Update button states based on current page"""
        self.first_btn.disabled = self.current_page == 0
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page >= self.total_pages - 1
        self.last_btn.disabled = self.current_page >= self.total_pages - 1
    
    def get_page_embed(self) -> discord.Embed:
        """Generate embed for current page"""
        current_time = datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
        
        total_members = sum(s['member_count'] for s in self.servers_data)
        
        embed = discord.Embed(
            title="🌐 Bot Server List",
            description=f"**All servers where the bot is present**\n\n"
                    f"📄 Page {self.current_page + 1}/{self.total_pages} | "
                    f"Total: {len(self.servers_data)} servers | "
                    f"👥 {total_members:,} members",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        
        if not self.servers_data:
            embed.add_field(
                name="📭 No Servers",
                value="Bot is not in any servers yet.",
                inline=False
            )
        else:
            # Get servers for current page
            start_idx = self.current_page * self.servers_per_page
            end_idx = min(start_idx + self.servers_per_page, len(self.servers_data))
            page_servers = self.servers_data[start_idx:end_idx]
            
            # Build server list for this page
            server_lines = []
            for i, server in enumerate(page_servers, start=start_idx + 1):
                # Format join date
                joined_at = server.get('joined_at', 'Unknown')
                if isinstance(joined_at, datetime.datetime):
                    joined_str = joined_at.strftime("%Y-%m-%d")
                else:
                    joined_str = str(joined_at)[:10]
                
                # Get invite link for this server
                invite_url = self.invites.get(str(server['id']), {}).get('invite_url', '')
                invite_text = f" | [Join]({invite_url})" if invite_url else ""
                
                line = (
                    f"**{i}.** 🏠 **{server['name']}**\n"
                    f"   └ 👥 {server['member_count']:,} members | 📅 Joined: {joined_str}{invite_text}\n"
                    f"   └ 🆔 `{server['id']}`"
                )
                server_lines.append(line)
            
            # Add to embed (split if needed)
            content = "\n\n".join(server_lines)
            if len(content) > 1024:
                # Split into multiple fields
                mid = len(server_lines) // 2
                embed.add_field(
                    name=f"🏠 Servers ({start_idx + 1}-{start_idx + mid})",
                    value="\n\n".join(server_lines[:mid])[:1024],
                    inline=False
                )
                embed.add_field(
                    name=f"🏠 Servers ({start_idx + mid + 1}-{end_idx})",
                    value="\n\n".join(server_lines[mid:])[:1024],
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"🏠 Servers ({start_idx + 1}-{end_idx})",
                    value=content,
                    inline=False
                )
        
        embed.set_footer(text=f"Last updated: {current_time} (Vietnam)")
        return embed
    
    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary, custom_id="server_list_first")
    async def first_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            self.current_page = 0
            self._update_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        except discord.NotFound:
            pass  # Interaction expired
    
    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary, custom_id="server_list_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            self.current_page = max(0, self.current_page - 1)
            self._update_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        except discord.NotFound:
            pass  # Interaction expired
    
    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary, custom_id="server_list_next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            self.current_page = min(self.total_pages - 1, self.current_page + 1)
            self._update_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        except discord.NotFound:
            pass  # Interaction expired
    
    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary, custom_id="server_list_last")
    async def last_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            self.current_page = self.total_pages - 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        except discord.NotFound:
            pass  # Interaction expired


async def update_server_list_message():
    """Update or create the permanent server list message with pagination"""
    try:
        # Get the display channel (same as channel list)
        display_channel = client.get_channel(CHANNEL_LIST_DISPLAY_CHANNEL_ID)
        if not display_channel:
            print(f"⚠️ Cannot find server list display channel: {CHANNEL_LIST_DISPLAY_CHANNEL_ID}")
            return
        
        # Build server data
        servers_data = []
        for guild in client.guilds:
            servers_data.append({
                'id': guild.id,
                'name': guild.name,
                'member_count': guild.member_count or 0,
                'joined_at': guild.me.joined_at if guild.me else None
            })
        
        # Sort by member count (descending)
        servers_data.sort(key=lambda x: x['member_count'], reverse=True)
        
        # Load server invites
        invites = load_server_invites()
        
        # Create view with pagination
        view = ServerListView(servers_data, invites=invites, servers_per_page=10)
        embed = view.get_page_embed()
        
        # Try to update existing message or create new one
        message_id = load_server_list_message_id()
        
        if message_id:
            try:
                old_message = await display_channel.fetch_message(message_id)
                await old_message.delete()
                print(f"🗑️ Deleted old server list message {message_id}")
            except:
                pass
        
        # Create new message with view
        message = await display_channel.send(embed=embed, view=view)
        save_server_list_message_id(message.id)
        print(f"✅ Created new server list message {message.id} with pagination ({len(servers_data)} servers)")
        
    except Exception as e:
        print(f"❌ Error updating server list message: {e}")
        import traceback
        traceback.print_exc()


# ============================================================================
# ORIGINAL CHANNEL MANAGEMENT HELPER FUNCTIONS
# ============================================================================




# ============================================================================
# DISCORD BOT CLIENT
# ============================================================================

class ClubManagementBot(discord.Client):
    """Custom Discord client for club management"""
    
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.tree.interaction_check = self.global_channel_check
        self.config_cache = {}
        self.member_cache = {}
        self.last_cache_update_time = 0
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
    
    async def setup_hook(self):
        """Setup hook called when bot is ready"""
        # Register persistent views for God Mode panel
        try:
            from god_mode_panel import GodModeControlPanel
            self.add_view(GodModeControlPanel())
            print("✅ Registered GodModeControlPanel persistent view")
        except Exception as e:
            print(f"⚠️ Error registering GodModeControlPanel: {e}")
        
        # Sync commands to Discord
        await self.tree.sync()
        print("✅ Commands synced to Discord")
        # Note: Scheduled tasks start themselves via @tasks.loop decorators

    
    async def global_channel_check(self, interaction: discord.Interaction) -> bool:
        """Check if command is used in allowed channel"""
        # Track command start time for performance monitoring
        if interaction.type == discord.InteractionType.application_command:
            command_start_times[interaction.id] = time.time()
        
        # God mode users bypass all checks
        if interaction.user.id in config.GOD_MODE_USER_IDS:
            print(f"✅ Channel check bypass: {interaction.user.name} (God Mode)")
            if interaction.type == discord.InteractionType.application_command:
                asyncio.create_task(self.log_command(interaction))
            return True
        
        # Check lockdown mode - only God mode users can use bot during lockdown
        try:
            from god_mode_panel import is_lockdown_active, get_lockdown_state
            if is_lockdown_active():
                state = get_lockdown_state()
                reason = state.get('reason', 'Maintenance in progress')
                
                # For autocomplete, fail silently
                if interaction.type == discord.InteractionType.autocomplete:
                    return False
                
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            f"🔒 **Bot is in Maintenance Mode**\n\n"
                            f"**Reason:** {reason}\n\n"
                            f"The bot is temporarily unavailable while we perform updates or maintenance.\n"
                            f"Please try again later.",
                            ephemeral=True
                        )
                except (discord.errors.InteractionResponded, discord.errors.HTTPException):
                    pass
                return False
        except ImportError:
            pass  # If import fails, continue normally
        
        # Server owner bypasses channel restrictions
        if interaction.guild and interaction.user.id == interaction.guild.owner_id:
            print(f"✅ Channel check bypass: {interaction.user.name} (Server Owner)")
            if interaction.type == discord.InteractionType.application_command:
                asyncio.create_task(self.log_command(interaction))
            return True
        
        # Server administrators bypass channel restrictions
        try:
            if interaction.guild and interaction.user.guild_permissions.administrator:
                print(f"✅ Channel check bypass: {interaction.user.name} (Administrator)")
                if interaction.type == discord.InteractionType.application_command:
                    asyncio.create_task(self.log_command(interaction))
                return True
        except (AttributeError, TypeError) as e:
            print(f"⚠️ Could not check admin permissions: {e}")
        
        # ✅ Load channels config from JSON file
        channels_config = load_channels_config()
        
        # Build list of allowed channel IDs from:
        # 1. File-based config (from set_channel command)
        # 2. Default hardcoded channels (from config.ALLOWED_CHANNEL_IDS)
        allowed_channel_ids = set(config.ALLOWED_CHANNEL_IDS)  # Start with defaults
        for ch in channels_config:
            allowed_channel_ids.add(ch.get('channel_id'))
        
        # If no restrictions (empty file + empty defaults), allow all
        if not allowed_channel_ids:
            # Log command
            if interaction.type == discord.InteractionType.application_command:
                asyncio.create_task(self.log_command(interaction))
            return True
        
        # For autocomplete, fail silently if not in allowed channels
        if interaction.type == discord.InteractionType.autocomplete:
            return interaction.channel_id in allowed_channel_ids
        
        # For commands, check channel and send message if wrong
        if interaction.channel_id not in allowed_channel_ids:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "⚠️ **Bot Not Configured for This Channel**\n\n"
                        "Please use `/help` or ask the **server owner** or someone with **admin privileges** to use `/set_channel` to enable the bot here.\n\n"
                        f"[Need help?]({SUPPORT_SERVER_URL})",
                        ephemeral=False
                    )
            except (discord.errors.InteractionResponded, discord.errors.HTTPException):
                # Silently fail if we can't respond
                pass
            return False
        
        # Log command (only for actual commands, not autocomplete)
        if interaction.type == discord.InteractionType.application_command:
            asyncio.create_task(self.log_command(interaction))
        
        return True
        
    async def log_command(self, interaction: discord.Interaction):
        """Log all commands to logging channel AND console for debugging"""
        try:
            # Get command name and parameters
            command_name = interaction.command.name if interaction.command else "Unknown"
            
            # Build parameters string
            params = []
            if interaction.namespace:
                for key, value in interaction.namespace.__dict__.items():
                    if not key.startswith('_'):
                        # Format user mentions
                        if isinstance(value, discord.Member) or isinstance(value, discord.User):
                            params.append(f"{key}=@{value.name}")
                        else:
                            params.append(f"{key}={value}")
            
            params_str = ", ".join(params) if params else "No parameters"
            
            # ========== CONSOLE DEBUG LOGGING ==========
            timestamp = datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
            server_name = interaction.guild.name if interaction.guild else "DM"
            server_id = interaction.guild_id if interaction.guild_id else "N/A"
            channel_name = interaction.channel.name if hasattr(interaction.channel, 'name') else "Unknown"
            
            print(f"\n{'='*60}")
            print(f"📝 COMMAND EXECUTED: /{command_name}")
            print(f"{'='*60}")
            print(f"⏰ Time: {timestamp}")
            print(f"👤 User: {interaction.user.name} (ID: {interaction.user.id})")
            print(f"🏠 Server: {server_name} (ID: {server_id})")
            print(f"📺 Channel: #{channel_name} (ID: {interaction.channel_id})")
            print(f"📋 Parameters: {params_str}")
            print(f"{'='*60}\n")
            # ========== END CONSOLE DEBUG LOGGING ==========
            
            # Create embed for Discord logging channel
            embed = discord.Embed(
                title=f"📝 Command Executed: /{command_name}",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            
            embed.add_field(
                name="User",
                value=f"{interaction.user.mention} (`{interaction.user.name}` - ID: {interaction.user.id})",
                inline=False
            )
            
            embed.add_field(
                name="Channel",
                value=f"<#{interaction.channel_id}> (ID: {interaction.channel_id})",
                inline=True
            )
            
            if interaction.guild:
                embed.add_field(
                    name="Server",
                    value=f"{interaction.guild.name} (ID: {interaction.guild_id})",
                    inline=True
                )
            
            embed.add_field(
                name="Parameters",
                value=f"```{params_str}```",
                inline=False
            )
            
            # Send to logging channel
            await send_log_to_channel(embed)
        
        except Exception as e:
            print(f"⚠️ Error logging command: {e}")
    
    async def update_single_club_config(self, club_name: str, field_updates: dict):
        """
        Update config cache for a single club without reloading all clubs.
        This is much faster than update_caches() which reloads ALL clubs.
        
        Args:
            club_name: Name of the club to update
            field_updates: Dictionary of field names and their new values
                        e.g., {'Target_Per_Day': 50000}
        """
        try:
            if club_name not in self.config_cache:
                print(f"Warning: Club '{club_name}' not in cache, cannot update")
                return False
            
            # Update in-memory cache
            for field, value in field_updates.items():
                self.config_cache[club_name][field] = value
            
            # Also update the serializable cache file
            config_cache_file = os.path.join(SCRIPT_DIR, "cache", "config_cache.json")
            if os.path.exists(config_cache_file):
                try:
                    with open(config_cache_file, 'r', encoding='utf-8') as f:
                        saved_cache = json.load(f)
                    
                    if club_name in saved_cache:
                        for field, value in field_updates.items():
                            saved_cache[club_name][field] = value
                        
                        with open(config_cache_file, 'w', encoding='utf-8') as f:
                            json.dump(saved_cache, f, ensure_ascii=False, indent=2)
                        
                        print(f"Cache file updated for {club_name}: {field_updates}")
                except Exception as e:
                    print(f"Warning: Could not update cache file: {e}")
            
            print(f"Config cache updated for {club_name}: {field_updates}")
            return True
            
        except Exception as e:
            print(f"Error updating single club config: {e}")
            return False
    
    async def update_caches(self):
        """Update config and member caches from Google Sheets"""
        current_time = time.time()
        
        # Check cooldown
        if current_time - self.last_cache_update_time < config.CACHE_UPDATE_COOLDOWN:
            print("Bot: Cache update skipped (cooldown active).")
            return
        
        try:
            print("Bot: Attempting to update cache from Google Sheets...")
            config_ws = await asyncio.to_thread(gs_manager.sh.worksheet, config.CONFIG_SHEET_NAME)
            club_configs = await asyncio.to_thread(config_ws.get_all_records)
            
            new_config_cache = {}
            new_member_cache = {}
            serializable_config = {}
            total_members = 0
            
            for club_config in club_configs:
                club_name = club_config.get('Club_Name')
                data_sheet_name = club_config.get('Data_Sheet_Name')
                
                if not club_name or not data_sheet_name:
                    continue
                
                # Find config row
                config_cell = await asyncio.to_thread(config_ws.find, club_name, in_column=1)
                if not config_cell:
                    print(f"Warning: Could not find row for {club_name}")
                    continue
                
                club_config['row'] = config_cell.row
                club_config['config_sheet'] = config_ws
                
                new_config_cache[club_name] = club_config
                serializable_config[club_name] = {
                    k: v for k, v in club_config.items() if k != 'config_sheet'
                }
                
                # Load members from DATA SHEET (has actual data with all members)
                # instead of Members sheet which may be outdated
                member_names = await self._load_members_from_data_sheet(data_sheet_name)
                new_member_cache[club_name] = member_names
                total_members += len(member_names)
                
                # Rate limiting: wait 3s between each sheet read to avoid quota errors
                # Google Sheets API has 60 requests/minute limit, so 3s = 20 requests/minute max
                await asyncio.sleep(3)
            
            # Update caches
            self.config_cache = new_config_cache
            self.member_cache = new_member_cache
            
            # Save to files
            self._save_cache_files(serializable_config, new_member_cache)
            
            print(f"Cache updated: {len(self.config_cache)} clubs, {total_members} members.")
            self.last_cache_update_time = time.time()
        
        except Exception as e:
            await self._handle_cache_error(e)
    
    async def _load_members(self, members_sheet_name: str) -> List[str]:
        """Load member names from a sheet with retry logic for rate limiting"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                members_ws = await asyncio.to_thread(gs_manager.sh.worksheet, members_sheet_name)
                all_values = await asyncio.to_thread(members_ws.get_all_values)
                
                if not all_values or len(all_values) < 1:
                    print(f"Warning: Sheet '{members_sheet_name}' is empty")
                    return []
                
                # Check if first row is the === CURRENT === header and skip it
                header_row_idx = 0
                if all_values[0] and all_values[0][0] and '=== CURRENT' in all_values[0][0]:
                    header_row_idx = 1  # Skip CURRENT header row
                
                if len(all_values) <= header_row_idx:
                    print(f"Warning: Sheet '{members_sheet_name}' has no data rows")
                    return []
                
                header = all_values[header_row_idx]
                data_rows = all_values[header_row_idx + 1:]  # Data starts after header
                
                # Try to find "Name" column - support multiple variations
                name_col = None
                possible_name_columns = ["Name", "Trainer Name", "Member Name", "name", "trainer_name"]
                
                for col_name in possible_name_columns:
                    try:
                        name_col = header.index(col_name)
                        break
                    except ValueError:
                        continue
                
                if name_col is None:
                    print(f"❌ ERROR: Sheet '{members_sheet_name}' has no 'Name' column!")
                    print(f"   Available columns: {header}")
                    print(f"   Expected one of: {possible_name_columns}")
                    return []
                
                members = [
                    row[name_col].strip()
                    for row in data_rows
                    if len(row) > name_col and row[name_col] and row[name_col].strip()
                ]
                
                print(f"✅ Loaded {len(members)} members from '{members_sheet_name}'")
                return members
                
            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "quota" in error_str
                
                if is_rate_limit and attempt < max_retries - 1:
                    # Longer exponential backoff: 5s, 15s, 30s
                    wait_times = [5, 15, 30]
                    wait_time = wait_times[attempt]
                    print(f"⚠️ Rate limit hit for {members_sheet_name}, waiting {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    print(f"❌ Error loading members from {members_sheet_name}: {e}")
                    import traceback
                    traceback.print_exc()
                    return []
        
        return []
    
    async def _load_members_from_data_sheet(self, data_sheet_name: str) -> List[str]:
        """Load unique member names from Data Sheet (has actual stats data)"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                data_ws = await asyncio.to_thread(gs_manager.sh.worksheet, data_sheet_name)
                all_values = await asyncio.to_thread(data_ws.get_all_values)
                
                if not all_values or len(all_values) < 1:
                    print(f"Warning: Data Sheet '{data_sheet_name}' is empty")
                    return []
                
                # Check if first row is the === CURRENT === header and skip it
                header_row_idx = 0
                if all_values[0] and all_values[0][0] and '=== CURRENT' in all_values[0][0]:
                    header_row_idx = 1  # Skip CURRENT header row
                
                if len(all_values) <= header_row_idx:
                    print(f"Warning: Data Sheet '{data_sheet_name}' has no data rows")
                    return []
                
                header = all_values[header_row_idx]
                data_rows = all_values[header_row_idx + 1:]  # Data starts after header
                
                # Find "Name" column
                name_col = None
                possible_name_columns = ["Name", "Trainer Name", "Member Name", "name", "trainer_name"]
                
                for col_name in possible_name_columns:
                    try:
                        name_col = header.index(col_name)
                        break
                    except ValueError:
                        continue
                
                if name_col is None:
                    print(f"❌ ERROR: Data Sheet '{data_sheet_name}' has no 'Name' column!")
                    print(f"   Available columns: {header}")
                    return []
                
                # Get UNIQUE member names from data sheet
                unique_members = set()
                for row in data_rows:
                    if len(row) > name_col and row[name_col] and row[name_col].strip():
                        unique_members.add(row[name_col].strip())
                
                members = sorted(list(unique_members))  # Sort alphabetically
                print(f"✅ Loaded {len(members)} unique members from '{data_sheet_name}'")
                return members
                
            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "quota" in error_str
                
                if is_rate_limit and attempt < max_retries - 1:
                    wait_times = [5, 15, 30]
                    wait_time = wait_times[attempt]
                    print(f"⚠️ Rate limit hit for {data_sheet_name}, waiting {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    print(f"❌ Error loading members from data sheet {data_sheet_name}: {e}")
                    import traceback
                    traceback.print_exc()
                    return []
        
        return []
    
    def _save_cache_files(self, config_data: dict, member_data: dict):
        """Save cache data to files"""
        print("Writing to local cache files...")
        with open(CONFIG_CACHE_FILE, "w") as f:
            json.dump(config_data, f)
        with open(MEMBER_CACHE_FILE, "w") as f:
            json.dump(member_data, f)
        print("Local cache files updated.")
    
    async def _handle_cache_error(self, e: Exception):
        """Handle cache update errors by loading from files"""
        if is_retryable_error(e):
            print(f"CRITICAL: GSheets connection failed: {e}")
            print("--- Loading from local cache files instead... ---")
            
            try:
                with open(CONFIG_CACHE_FILE, 'r') as f:
                    config_from_cache = json.load(f)
                with open(MEMBER_CACHE_FILE, 'r') as f:
                    self.member_cache = json.load(f)
                
                # Re-attach config_sheet if possible
                try:
                    config_ws = await asyncio.to_thread(gs_manager.sh.worksheet, config.CONFIG_SHEET_NAME)
                    for name, club_config in config_from_cache.items():
                        club_config['config_sheet'] = config_ws
                        self.config_cache[name] = club_config
                except Exception:
                    self.config_cache = config_from_cache
                
                print(f"Loaded {len(self.config_cache)} clubs from cache.")
            
            except FileNotFoundError:
                print("CRITICAL: GSheets failed AND cache files not found.")
            except Exception as cache_e:
                print(f"CRITICAL: Failed to read cache: {cache_e}")
        else:
            print(f"Bot: Failed to update cache (non-network error): {e}")

# ============================================================================
# BOT INITIALIZATION
# ============================================================================

intents = discord.Intents.default()
client = ClubManagementBot(intents=intents)


# ============================================================================
# TOURNAMENT SETUP COMMAND
# ============================================================================

@client.tree.command(name="tournament_setup", description="Setup Tournament system (category, channels, role)")
@app_commands.checks.has_permissions(administrator=True)
async def tournament_setup(interaction: discord.Interaction):
    """One command setup - create category, channels, roles, dropdown panel"""
    await interaction.response.defer(ephemeral=True)
    
    guild = interaction.guild
    
    # Check if already setup
    existing_category = discord.utils.get(guild.categories, name="Tournament")
    if existing_category:
        await interaction.followup.send(
            "⚠️ Tournament category already exists! Delete it before setting up again.",
            ephemeral=True
        )
        return
    
    try:
        # Import tournament modules
        from cogs.tournament import AdminPanelView
        from tournament_manager import create_tournament
        
        # 1. Create Category
        category = await guild.create_category(
            name="Tournament",
            reason="Tournament Setup"
        )
        
        # 2. Create Admin Channel (private - admins only)
        admin_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        for role in guild.roles:
            if role.permissions.administrator:
                admin_overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        
        admin_channel = await guild.create_text_channel(
            name="tournament-admin",
            category=category,
            overwrites=admin_overwrites,
            reason="Tournament Admin Channel"
        )
        print(f"✅ Created: tournament-admin ({admin_channel.id})")
        
        # 3. Create Announcement Channel (public - read only for normal users)
        announce_overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True
            )
        }
        announce_channel = await guild.create_text_channel(
            name="tournament-announce",
            category=category,
            overwrites=announce_overwrites,
            reason="Tournament Announcements"
        )
        print(f"✅ Created: tournament-announce ({announce_channel.id})")
        
        # 4. Create Tournament Role (permanent - for pings)
        tournament_role = await guild.create_role(
            name="Tournament",
            color=discord.Color.orange(),
            mentionable=True,
            reason="Tournament Participant Role"
        )
        print(f"✅ Created role: Tournament ({tournament_role.id})")
        
        # 5. Create Referee Role (permanent - for result submission)
        referee_role = await guild.create_role(
            name="Referee",
            color=discord.Color.blue(),
            mentionable=True,
            reason="Tournament Referee Role"
        )
        print(f"✅ Created role: Referee ({referee_role.id})")
        
        # 6. Create Chat Channel (locked by default - opens during tournament)
        chat_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            tournament_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False  # Locked by default
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True
            )
        }
        chat_channel = await guild.create_text_channel(
            name="tournament-chat",
            category=category,
            overwrites=chat_overwrites,
            reason="Tournament Chat (opens during tournament)"
        )
        print(f"✅ Created: tournament-chat ({chat_channel.id})")
        
        # 7. Create Forum Channel (only bot/admin can create posts)
        # In forums: send_messages = create new post, send_messages_in_threads = reply in post
        forum_overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
                send_messages=False,              # Can't create posts
                send_messages_in_threads=False    # Can't reply
            ),
            tournament_role: discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=False,              # Can't create posts (IMPORTANT!)
                send_messages_in_threads=False    # Can't reply in posts
            ),
            referee_role: discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=False,              # Can't create posts
                send_messages_in_threads=True     # Can reply in posts only
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,               # Bot CAN create posts
                send_messages_in_threads=True
            )
        }
        # Add admin permission to forum
        for role in guild.roles:
            if role.permissions.administrator:
                forum_overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    send_messages_in_threads=True,
                    create_public_threads=True
                )
        
        forum_channel = await guild.create_forum(
            name="tournament-matches",
            category=category,
            overwrites=forum_overwrites,
            reason="Tournament Matches Forum"
        )
        print(f"✅ Created: tournament-matches forum ({forum_channel.id})")
        
        # 8. Send Admin Panel to admin channel
        embed = discord.Embed(
            title="🎮 TOURNAMENT CONTROL PANEL",
            description="Use the dropdowns below to manage tournaments.",
            color=discord.Color.dark_gold()
        )
        embed.add_field(
            name="📌 Instructions",
            value="1️⃣ **Create Tournament** - Create & open registration\n"
                "2️⃣ **Close Registration** - Close & generate bracket\n"
                "3️⃣ **End Tournament** - Cleanup threads & roles",
            inline=False
        )
        
        panel_view = AdminPanelView()
        await admin_channel.send(embed=embed, view=panel_view)
        
        # Save channel/role IDs for future use
        tournament = create_tournament(
            guild_id=guild.id,
            name="Pending",
            created_by=interaction.user.id
        )
        tournament.category_id = category.id
        tournament.admin_channel_id = admin_channel.id
        tournament.public_channel_id = announce_channel.id
        tournament.chat_channel_id = chat_channel.id
        tournament.forum_channel_id = forum_channel.id
        tournament.tournament_role_id = tournament_role.id
        tournament.referee_role_id = referee_role.id
        tournament.save()
        
        await interaction.followup.send(
            f"✅ **Tournament Setup Complete!**\n\n"
            f"📁 Category: {category.mention}\n"
            f"🔒 Admin: {admin_channel.mention}\n"
            f"📢 Announce: {announce_channel.mention}\n"
            f"💬 Chat: {chat_channel.mention}\n"
            f"📋 Forum: {forum_channel.mention}\n"
            f"🏷️ Tournament Role: {tournament_role.mention}\n"
            f"⚖️ Referee Role: {referee_role.mention}",
            ephemeral=True
        )
        
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Bot lacks Manage Channels/Roles permission!",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# ============================================================================
# SCHEDULED TASK - AUTO REFRESH DATA CACHE (member_cache + config_cache)
# ============================================================================

@tasks.loop(time=dt_time(hour=23, minute=0, tzinfo=pytz.UTC))  # 6:00 AM Vietnam (UTC+7)
async def auto_refresh_data_cache():
    """Auto-refresh member_cache and config_cache daily at 6:00 AM Vietnam time
    
    This ensures new members appear in autocomplete for /stats command.
    Runs before the 7AM data update so cache is fresh.
    """
    try:
        print("🔄 Starting daily data_cache refresh...")
        
        # Call update_caches to refresh both config_cache and member_cache
        await client.update_caches()
        
        # Log success
        log_channel = client.get_channel(LOGGING_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(
                title="🔄 Daily Cache Refresh Complete",
                description=f"✅ Refreshed `member_cache` and `config_cache`\n"
                        f"📊 **Clubs:** {len(client.config_cache)}\n"
                        f"👥 **Members:** {sum(len(m) for m in client.member_cache.values())}",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            await log_channel.send(embed=embed)
        
        print(f"✅ Cache refresh completed: {len(client.config_cache)} clubs, "
            f"{sum(len(m) for m in client.member_cache.values())} members")
            
    except Exception as e:
        print(f"❌ Cache refresh failed: {e}")
        import traceback
        traceback.print_exc()
        
        log_channel = client.get_channel(LOGGING_CHANNEL_ID)
        if log_channel:
            error_embed = discord.Embed(
                title="❌ Daily Cache Refresh Failed",
                description=f"```{str(e)[:500]}```",
                color=discord.Color.red(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            await log_channel.send(embed=error_embed)

@auto_refresh_data_cache.before_loop
async def before_cache_refresh():
    """Wait for bot to be ready before starting cache refresh task"""
    await client.wait_until_ready()
    print("✅ Cache refresh task ready (will run daily at 6:00 AM Vietnam)")


# ============================================================================
# SCHEDULED TASK - AUTO BACKUP CONFIG FILES
# ============================================================================

@tasks.loop(hours=6)  # Every 6 hours
async def auto_backup_configs():
    """Auto-backup critical config files every 6 hours
    
    Runs 4 times per day to ensure backups are created regularly.
    """
    try:
        from auto_backup import daily_backup
        
        print("📦 Starting scheduled config backup...")
        results = daily_backup()
        
        print(f"📦 Backup complete: {results['success']} files backed up")
        
        # Log to channel
        log_channel = client.get_channel(LOGGING_CHANNEL_ID)
        if log_channel and results['success'] > 0:
            embed = discord.Embed(
                title="📦 Config Backup Complete",
                description=(
                    f"✅ **Backed up:** {results['success']} files\n"
                    f"⏭️ **Skipped:** {results['skipped']} (not found)\n"
                    f"❌ **Failed:** {results['failed']}"
                ),
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            if results['files']:
                embed.add_field(
                    name="Files",
                    value="\n".join(f"• `{f}`" for f in results['files'][:5]),
                    inline=False
                )
            await log_channel.send(embed=embed)
                
    except Exception as e:
        print(f"❌ Backup failed: {e}")
        import traceback
        traceback.print_exc()

@auto_backup_configs.before_loop
async def before_backup():
    """Wait for bot to be ready"""
    await client.wait_until_ready()
    print("✅ Backup task ready (will run every 6 hours)")


# ============================================================================
# CHANNEL CONFIG MIGRATION
# ============================================================================

def migrate_old_channel_config():
    """Migrate old single-channel config to new multi-channel format"""
    old_file = os.path.join(SCRIPT_DIR, "allowed_channel_config.json")
    new_file = ALLOWED_CHANNELS_CONFIG_FILE
    
    # Check if old file exists and new file doesn't
    if os.path.exists(old_file) and not os.path.exists(new_file):
        try:
            with open(old_file, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
            
            # Convert to new format
            new_data = {
                "channels": [{
                    "channel_id": old_data.get('channel_id'),
                    "channel_name": old_data.get('channel_name', 'Unknown'),
                    "server_id": old_data.get('server_id'),
                    "server_name": old_data.get('server_name', 'Unknown'),
                    "added_by": old_data.get('set_by'),
                    "added_by_name": old_data.get('set_by_name', 'Unknown'),
                    "added_at": old_data.get('set_at', datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S"))
                }],
                "last_updated": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # Save new format
            with open(new_file, 'w', encoding='utf-8') as f:
                json.dump(new_data, f, indent=2, ensure_ascii=False)
            
            # Rename old file as backup
            backup_file = old_file + '.backup'
            os.rename(old_file, backup_file)
            
            print(f"✅ Migrated channel config from old format to new format")
            print(f"   Old file backed up to: {backup_file}")
            
            return True
        except Exception as e:
            print(f"⚠️ Error migrating channel config: {e}")
            return False
    
    return False

# ============================================================================
# EVENT HANDLERS
# ============================================================================

@client.event
async def on_ready():
    """Called when bot is ready"""    
    # Start background cleanup task
    if not cleanup_expired_requests.is_running():
        cleanup_expired_requests.start()
        print("✅ Started cleanup_expired_requests background task")
    
    # Start daily cache refresh task
    if not auto_refresh_data_cache.is_running():
        auto_refresh_data_cache.start()
        print("✅ Started auto_refresh_data_cache task (runs daily at 6:00 AM Vietnam)")
    
    # Start club data sync task (rank update + member data) if not already running
    if not update_club_data_task.is_running():
        update_club_data_task.start()
        print("✅ Started club data sync task (7:30 AM & PM Vietnam time)")
    
    
    # NOTE: Auto-sync and cache warming tasks have been REMOVED
    # - auto_sync_all_clubs: No longer auto-syncing from uma.moe API
    # - warm_cache: No longer pre-warming cache
    # Data is now fetched on-demand from Google Sheets with cache as fallback only
    
    print("=" * 50)
    print(f"✅ Bot ready! Logged in as {client.user}")
    print("=" * 50)
    
    # Migrate old channel config if needed
    migrate_old_channel_config()
    
    # Load channel configuration
    channels_config = load_channels_config()
    if channels_config:
        config.ALLOWED_CHANNEL_IDS = [ch['channel_id'] for ch in channels_config]
        print(f"✅ Channel restrictions active: {len(config.ALLOWED_CHANNEL_IDS)} channel(s)")
        for ch in channels_config:
            print(f"   - {ch['channel_name']} in {ch['server_name']} (ID: {ch['channel_id']})")
    else:
        print(f"ℹ️ Using default allowed channels: {config.ALLOWED_CHANNEL_IDS}")

    
    # Load admin list and merge with hard-coded admins
    admin_list = load_admin_list()
    if admin_list:
        # Merge dynamic admins with hard-coded role IDs
        original_admins = set(config.ADMIN_ROLE_IDS)
        config.ADMIN_ROLE_IDS = list(original_admins | set(admin_list))
        print(f"✅ Merged {len(admin_list)} dynamic admins with {len(original_admins)} hard-coded admins")
    
    await client.update_caches()
    
    # Initialize or update the permanent channel list message
    print("🔄 Initializing channel list message...")
    await update_channel_list_message()
    
    # Initialize or update the permanent server list message
    print("🔄 Initializing server list message...")
    await update_server_list_message()

    # Initialize God Mode control panel
    try:
        from god_mode_panel import update_god_mode_panel
        await update_god_mode_panel(client)
        print("✅ God Mode control panel initialized")
    except Exception as e:
        print(f"⚠️ Error initializing God Mode panel: {e}")
    
    await handle_restart_message()



async def handle_restart_message():
    """Handle restart message after bot restart"""
    if not os.path.exists(RESTART_FILE_PATH):
        return
    
    try:
        with open(RESTART_FILE_PATH, "r") as f:
            restart_data = json.load(f)
        
        channel_id = restart_data.get("channel_id")
        start_time = restart_data.get("start_time")
        message_id = restart_data.get("message_id")
        
        if not all([channel_id, start_time, message_id]):
            return
        
        time_taken = round(time.time() - start_time, 2)
        
        try:
            channel = await client.fetch_channel(channel_id)
            
            # Delete old message
            try:
                old_message = await channel.fetch_message(message_id)
                await old_message.delete()
                print("Deleted old restart message.")
            except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                print(f"Could not delete old message: {e}")
            
            # Send new message
            await channel.send(
                f"✅ **Restart complete!** (Time taken: {time_taken} seconds)"
            )
        
        except Exception as e:
            print(f"Error handling restart message: {e}")
    
    except Exception as e:
        print(f"Error reading restart file: {e}")
    
    finally:
        os.remove(RESTART_FILE_PATH)

# ============================================================================
# AUTOCOMPLETE FUNCTIONS
# ============================================================================

async def club_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> List[app_commands.Choice[str]]:
    """Autocomplete for club names - SERVER FILTERED (must complete in <3s)"""
    try:
        # CRITICAL: Only use cached data - NEVER query sheets
        # Also add timeout protection to prevent Unknown interaction errors
        if not client.config_cache:
            return []
        
        # Filter clubs by current server - fast path
        server_id = interaction.guild_id
        current_lower = current.lower() if current else ""
        
        # Fast path: If no server ID (DM), show all clubs
        if not server_id:
            choices = [
                app_commands.Choice(name=name, value=name)
                for name in list(client.config_cache.keys())[:25]
                if not current_lower or current_lower in name.lower()
            ]
            return choices[:25]
        
        # Filter by server - use pre-built list for speed
        server_clubs = [
            name for name, config in client.config_cache.items()
            if config.get('Server_ID') == str(server_id) or config.get('Server_ID') == server_id
        ]
        
        # Fallback: If no clubs for this server yet, show all (for setup phase)
        if not server_clubs:
            server_clubs = list(client.config_cache.keys())
        
        # Filter by user input - limit iterations
        choices = [
            app_commands.Choice(name=name, value=name)
            for name in server_clubs[:50]  # Limit source list
            if not current_lower or current_lower in name.lower()
        ]
        return choices[:25]
        
    except asyncio.CancelledError:
        # Interaction was cancelled (timeout) - return empty silently
        return []
    except discord.errors.NotFound:
        # Interaction expired due to network latency - ignore silently
        return []
    except Exception as e:
        # Log but don't raise - prevents Unknown interaction errors
        print(f"Error in club_autocomplete: {e}")
        return []


async def member_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> List[app_commands.Choice[str]]:
    """Autocomplete for member names - must complete in <3s to avoid Unknown interaction"""
    try:
        club_name = getattr(interaction.namespace, 'club_name', None)
        
        if not club_name or not client.member_cache:
            return []
        
        # Find club (case-insensitive) using cached data only
        member_list = client.member_cache.get(club_name, [])
        
        if not member_list:
            # Fast case-insensitive lookup
            club_name_lower = club_name.casefold()
            for cached_club, members in client.member_cache.items():
                if cached_club.casefold() == club_name_lower:
                    member_list = members
                    break
        
        if not member_list:
            return []
        
        # Filter and return choices
        # IMPORTANT: Filter ALL members first, then limit to 25
        # This ensures users can find members even in large clubs (30+ members)
        current_lower = current.lower() if current else ""
        
        if current_lower:
            # User is typing - filter ALL members to find matches
            choices = [
                app_commands.Choice(name=name, value=name)
                for name in member_list
                if current_lower in name.lower()
            ]
        else:
            # No filter - sort to show non-ASCII names (Chinese/Japanese) FIRST
            # This ensures names like 王牌 appear in the dropdown without typing
            def sort_key(name):
                # Non-ASCII characters get priority (sort first)
                has_non_ascii = any(ord(c) > 127 for c in name)
                return (0 if has_non_ascii else 1, name.lower())
            
            sorted_members = sorted(member_list, key=sort_key)
            choices = [
                app_commands.Choice(name=name, value=name)
                for name in sorted_members[:25]
            ]
        
        return choices[:25]
    
    except asyncio.CancelledError:
        # Interaction was cancelled (timeout) - return empty silently
        return []
    except discord.errors.NotFound:
        # Interaction expired due to network latency - ignore silently
        return []
    except asyncio.TimeoutError:
        # Timeout - return empty silently
        return []
    except Exception as e:
        # Log but don't raise - prevents Unknown interaction errors
        # Only log non-network errors
        error_str = str(e).lower()
        if '10062' not in error_str and 'unknown interaction' not in error_str:
            print(f"Error in member_autocomplete: {e}")
        return []


# ============================================================================
# EVENT HANDLER: ON_MESSAGE (FOR DM CUSTOM NAME HANDLING)
# ============================================================================

@client.event
async def on_message(message):
    """Handle DM replies for custom name requests AND profile verification screenshots"""
    # Skip bot messages
    if message.author.bot:
        return
    
    # Only process DMs
    if not isinstance(message.channel, discord.DMChannel):
        return
    
    user_id = message.author.id
    
    # ========== PROFILE VERIFICATION HANDLER ==========
    if user_id in pending_verifications:
        verification = pending_verifications[user_id]
        
        # Check if expired
        if datetime.datetime.now(datetime.timezone.utc) > verification['expires']:
            del pending_verifications[user_id]
            await message.reply("⏰ Verification expired. Please use `/stats` again to start a new verification.")
            return
        
        # Check if user wants to cancel
        if message.content.strip().lower() == 'cancel':
            del pending_verifications[user_id]
            await message.reply("❌ **Profile verification cancelled.**\n\nYou can link your profile anytime by using `/stats` on your own profile.")
            return
        
        # Check if message has image attachment
        if not message.attachments:
            await message.reply("📸 Please send an **image/screenshot** of your trainer profile.\n\n💡 Type **cancel** to stop this operation.")
            return
        
        # Check if it's an image
        attachment = message.attachments[0]
        if not attachment.content_type or not attachment.content_type.startswith('image/'):
            await message.reply("📸 Please send an **image** file (PNG, JPG, etc.).")
            return
        
        # Process the image
        try:
            processing_msg = await message.reply("⏳ Processing your screenshot...")
            image_data = await attachment.read()
            ocr_result = await call_ocr_service(image_data)
            
            if not ocr_result or not ocr_result.get('trainer_id'):
                await processing_msg.edit(content="❌ **Could not read Trainer ID from image.**\n\nPlease make sure the screenshot clearly shows your Trainer ID (12-digit number).")
                del pending_verifications[user_id]
                return
            
            extracted_id = ocr_result.get('trainer_id', '').replace(' ', '')
            extracted_club = ocr_result.get('club', 'Unknown')
            
            # Get viewer_id from sheets (primary identifier that never changes)
            viewer_id = get_viewer_id_from_sheets(
                verification['member_name'], 
                verification['club_name']
            )
            
            save_profile_link(
                discord_id=user_id,
                trainer_id=extracted_id,
                member_name=verification['member_name'],
                club_name=verification['club_name'],
                viewer_id=viewer_id  # Store viewer_id for future lookups
            )
            
            await processing_msg.edit(
                content=(
                    f"✅ **Verification successful!**\n\n"
                    f"Your Discord account has been linked to:\n"
                    f"**Trainer ID:** `{extracted_id}`\n"
                    f"**Player ID:** `{viewer_id or 'Unknown'}`\n"
                    f"**Club:** {extracted_club}\n"
                    f"**Member Name:** {verification['member_name']}\n\n"
                    f"When you use `/profile` in the future, we'll know this is your profile!"
                )
            )
            del pending_verifications[user_id]
            
        except Exception as e:
            print(f"Profile verification error: {e}")
            if user_id in pending_verifications:
                del pending_verifications[user_id]
            try:
                await message.reply(f"❌ Error processing verification: {e}")
            except:
                pass
        return
    
    # ========== CUSTOM NAME REQUEST HANDLER ==========
    # Check if user has pending custom name request
    if message.author.id in pending_requests:
        request_data = pending_requests[message.author.id]
        
        if request_data.get('awaiting_custom_name'):
            custom_name = message.content.strip()
            
            # Validate custom name
            if not custom_name:
                await message.reply("❌ Name cannot be empty. Please try again.")
                return
            
            if custom_name in client.config_cache:
                await message.reply(
                    f"❌ The name \"{custom_name}\" is also taken. "
                    f"Please choose a different name."
                )
                return
            
            # Valid! Create club with custom name
            await message.reply(
                f"✅ Creating club with name: **{custom_name}**\n"
                f"Please wait..."
            )
            
            try:
                await auto_create_club_from_api(
                    custom_name,
                    request_data['api_data'],
                    request_data['club_data'],
                    None
                )
                
                # Notify requester
                await message.reply(
                    f"✅ **Club created successfully!**\n\n"
                    f"**Original Name:** {request_data['original_name']}\n"
                    f"**Custom Name:** {custom_name}\n\n"
                    f"You can now use `/leaderboard {custom_name}` and other commands."
                )
                
                # Notify admin channel
                channel = await client.fetch_channel(request_data['admin_channel_id'])
                await channel.send(
                    f"✅ **Club created with custom name**\n\n"
                    f"**Original Name:** {request_data['original_name']}\n"
                    f"**Custom Name:** {custom_name}\n"
                    f"**Requester:** <@{message.author.id}>"
                )
                
            except Exception as e:
                await message.reply(f"❌ Error creating club: {e}")
            
            finally:
                # Clean up pending request
                del pending_requests[message.author.id]


# ============================================================================
# BACKGROUND TASK: CLEANUP EXPIRED REQUESTS
# ============================================================================

from discord.ext import tasks

@tasks.loop(minutes=1)
async def cleanup_expired_requests():
    """Clean up pending requests after 5 minutes"""
    current_time = time.time()
    expired = []
    
    for user_id, data in pending_requests.items():
        if current_time - data['timestamp'] > 300:  # 5 minutes
            expired.append(user_id)
    
    for user_id in expired:
        data = pending_requests.pop(user_id)
        
        # Notify requester
        try:
            user = await client.fetch_user(user_id)
            await user.send(
                "⏱️ **Request timed out**\n\n"
                "Your custom name request has expired. "
                "Please use `/search_club` again if you still want to add this club."
            )
        except:
            pass
        
        # Notify admin channel
        try:
            channel = await client.fetch_channel(data['admin_channel_id'])
            await channel.send(
                f"⏱️ **Request timed out**\n\n"
                f"Club: {data['original_name']}\n"
                f"Requester: <@{user_id}> did not respond within 5 minutes."
            )
        except:
            pass

# Note: cleanup_expired_requests.start() should be called in on_ready event


# ============================================================================
# CLUB SETUP MODALS
# ============================================================================

def extract_club_id_from_url(url: str) -> str:
    """
    Extract Club ID (9-digit number) from club URL.
    Only accepts URLs with numeric IDs (not text names like 'Chibi%20Club').
    
    Examples:
        https://chronogenesis.net/club_profile?circle_id=525713827 -> "525713827"
        https://uma.moe/club/123456789 -> "123456789"
        https://chronogenesis.net/club_profile?circle_id=Chibi%20Club -> "" (INVALID)
    
    Returns:
        Club ID string (9 digits) or empty string if not found/invalid
    """
    import re
    # Strictly find 9-digit number at the end of URL or after = sign
    # This rejects URLs with text names like "Chibi%20Club"
    match = re.search(r'[=\/](\d{9})(?:\D*$|$)', url)
    if match:
        return match.group(1)
    return ""


async def validate_club_url_and_id(url: str) -> tuple:
    """
    Validate club URL format and verify club exists via API.
    
    Args:
        url: Club profile URL
        
    Returns:
        (is_valid, club_id, error_message)
        - is_valid: True if URL is valid and club exists
        - club_id: Extracted 9-digit club ID, or empty string if invalid
        - error_message: Error description if invalid, None if valid
    """
    import re
    
    # Check if URL contains non-numeric ID (like "Chibi%20Club")
    # Pattern: circle_id= followed by non-digit characters
    if re.search(r'circle_id=[^0-9\d]', url) or re.search(r'circle_id=[A-Za-z%]', url):
        return (False, "", "URL contains club name instead of numeric ID. Please use URL with 9-digit Club ID.")
    
    # Extract club ID (must be 9 digits)
    club_id = extract_club_id_from_url(url)
    
    if not club_id:
        return (False, "", "Could not find Club ID (9 digits) in URL.")
    
    if len(club_id) != 9:
        return (False, "", f"Club ID must be 9 digits, received: {club_id}")
    
    # Verify club exists via API
    try:
        api_data = await fetch_club_data_full(club_id, max_retries=2)
        
        if not api_data:
            return (False, club_id, f"Club with ID `{club_id}` does not exist on uma.moe.")
        
        # Check if we got valid circle data
        circle_data = api_data.get('circle', {})
        if not circle_data:
            return (False, club_id, f"Could not find club info for ID `{club_id}`.")
        
        # Club exists!
        return (True, club_id, None)
        
    except Exception as e:
        print(f"API validation error for club {club_id}: {e}")
        # On API error, allow creation but warn user
        return (True, club_id, None)  # Allow if API fails (avoid blocking)

class CompetitiveClubSetupModal(discord.ui.Modal, title="Setup Competitive Club"):
    """Modal for setting up a competitive club with quota"""
    
    club_name_input = discord.ui.TextInput(
        label="Club Name",
        placeholder="My Club Name",
        required=True,
        max_length=50
    )
    
    club_url_input = discord.ui.TextInput(
        label="Club Profile URL",
        placeholder="https://chronogenesis.net/club_profile?circle_id=XXXXXX",
        required=True,
        max_length=200
    )
    
    quota_input = discord.ui.TextInput(
        label="Daily Quota (fans/day)",
        placeholder="5000",
        required=True,
        min_length=1,
        max_length=10
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle competitive club setup submission"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Extract values
            club_name = self.club_name_input.value.strip()
            club_url = self.club_url_input.value.strip()
            quota_str = self.quota_input.value.strip()
            
            # ===== VALIDATE CLUB NAME =====
            if not club_name:
                await interaction.followup.send(
                    "❌ Error: Club name cannot be empty.",
                    ephemeral=True
                )
                return
            
            if len(club_name) > 50:
                await interaction.followup.send(
                    "❌ Error: Club name is too long (max 50 characters).",
                    ephemeral=True
                )
                return
            
            # Check for dangerous characters
            invalid_chars = ['/', '\\', '?', '*', ':', '[', ']']
            found_invalid = [char for char in invalid_chars if char in club_name]
            
            if found_invalid:
                invalid_list = ', '.join(f"'{char}'" for char in found_invalid)
                await interaction.followup.send(
                    f"❌ Error: Club name contains invalid characters: {invalid_list}\n"
                    f"These characters cannot be used in Google Sheets tab names.\n"
                    f"Please avoid: / \\ ? * : [ ]",
                    ephemeral=True
                )
                return
            
            # Check if club already exists (case-insensitive) using existing cache
            # Skip cache reload to speed up response - cache is updated after creation
            for existing_club in client.config_cache.keys():
                if existing_club.lower() == club_name.lower():
                    await interaction.followup.send(
                        f"❌ Error: Club '{existing_club}' already exists.\n"
                        f"(Club names are case-insensitive)",
                        ephemeral=True
                    )
                    return
            
            # ===== VALIDATE QUOTA =====
            try:
                target_quota = int(quota_str)
                if target_quota <= 0:
                    await interaction.followup.send(
                        "❌ Error: Daily quota must be a positive number!",
                        ephemeral=True
                    )
                    return
            except ValueError:
                await interaction.followup.send(
                    "❌ Error: Daily quota must be a valid number!",
                    ephemeral=True
                )
                return
            
            # ===== VALIDATE CLUB URL =====
            if not club_url:
                await interaction.followup.send(
                    "❌ Error: Club URL cannot be empty.",
                    ephemeral=True
                )
                return
            
            # Basic URL validation
            if not club_url.startswith(('http://', 'https://')):
                # Show error with image guide
                embed = discord.Embed(
                    title="❌ Invalid Club URL",
                    description=(
                        "Club URL must start with `http://` or `https://`\n\n"
                        "**Expected format:**\n"
                        "`https://chronogenesis.net/club_profile?circle_id=XXXXXXXXX`\n\n"
                        "**Your URL:**\n"
                        f"`{club_url}`\n\n"
                        "📌 **Club ID usually found here:**"
                    ),
                    color=discord.Color.red()
                )
                embed.set_image(url="attachment://club_id_guide.png")
                
                try:
                    file = discord.File(os.path.join(SCRIPT_DIR, "assets", "club_id_guide.png"), filename="club_id_guide.png")
                    await interaction.followup.send(embed=embed, file=file, ephemeral=True)
                except FileNotFoundError:
                    await interaction.followup.send(
                        f"❌ Error: Club URL must start with http:// or https://\n"
                        f"Expected: https://chronogenesis.net/club_profile?circle_id=XXXXXXXXX",
                        ephemeral=True
                    )
                return
            
            # ===== VALIDATE CLUB ID FROM URL =====
            is_valid, club_id, error_msg = await validate_club_url_and_id(club_url)
            
            if not is_valid:
                # Show error with guide image
                embed = discord.Embed(
                    title="❌ Invalid Club URL",
                    description=(
                        f"**Error:** {error_msg}\n\n"
                        "**Correct URL format:**\n"
                        "`https://chronogenesis.net/club_profile?circle_id=XXXXXXXXX`\n\n"
                        "⚠️ **Note:** URL must contain **9-digit Club ID**, not club name.\n\n"
                        "**WRONG examples:**\n"
                        f"❌ `...circle_id=Chibi%20Club`\n"
                        f"❌ `...circle_id=MyClubName`\n\n"
                        "**CORRECT examples:**\n"
                        f"✅ `...circle_id=525713827`\n\n"
                        "**Your URL:**\n"
                        f"`{club_url}`\n\n"
                        "📌 **How to get Club ID:**"
                    ),
                    color=discord.Color.red()
                )
                embed.set_image(url="attachment://club_id_guide.png")
                
                try:
                    file = discord.File(os.path.join(SCRIPT_DIR, "assets", "club_id_guide.png"), filename="club_id_guide.png")
                    await interaction.followup.send(embed=embed, file=file, ephemeral=True)
                except FileNotFoundError:
                    await interaction.followup.send(
                        f"❌ **Invalid Club URL**\n\n"
                        f"**Error:** {error_msg}\n\n"
                        f"URL must contain 9-digit Club ID, not club name.\n"
                        f"Example: `https://chronogenesis.net/club_profile?circle_id=525713827`",
                        ephemeral=True
                    )
                return
            
            # ===== CREATE CLUB =====
            # Generate sheet names
            data_sheet = f"{club_name}_Data"
            members_sheet = f"{club_name}_Members"
            
            # Check sheet name length
            if len(data_sheet) > 100 or len(members_sheet) > 100:
                await interaction.followup.send(
                    f"❌ Error: Club name is too long for sheet naming.\n"
                    f"Sheet names would be:\n"
                    f"- {data_sheet} ({len(data_sheet)} chars)\n"
                    f"- {members_sheet} ({len(members_sheet)} chars)\n"
                    f"Maximum: 100 characters per sheet name.",
                    ephemeral=True
                )
                return
            
            # Create Data sheet (run in thread to avoid blocking)
            data_ws = await asyncio.to_thread(
                gs_manager.sh.add_worksheet, 
                title=data_sheet, rows=100, cols=6
            )
            await asyncio.to_thread(
                data_ws.update, 'A1:F1', 
                [['Name', 'Day', 'Total Fans', 'Daily', 'Target', 'CarryOver']]
            )
            
            print(f"✅ Created sheet: {data_sheet}")
            
            # Create Members sheet (run in thread to avoid blocking)
            members_ws = await asyncio.to_thread(
                gs_manager.sh.add_worksheet, 
                title=members_sheet, rows=50, cols=2
            )
            await asyncio.to_thread(
                members_ws.update, 'A1:B1', 
                [['Trainer ID', 'Name']]
            )
            
            print(f"✅ Created sheet: {members_sheet}")
            
            # club_id already validated and extracted by validate_club_url_and_id()
            
            # Get Server ID from interaction
            server_id = str(interaction.guild_id) if interaction.guild_id else ""
            
            # Add to config with competitive type (run in thread)
            config_ws = await asyncio.to_thread(
                gs_manager.sh.worksheet, config.CONFIG_SHEET_NAME
            )
            await asyncio.to_thread(
                config_ws.append_row,
                [
                    club_name,           # Column A: Club_Name
                    data_sheet,          # Column B: Data_Sheet_Name
                    members_sheet,       # Column C: Members_Sheet_Name
                    target_quota,        # Column D: Target_Per_Day
                    club_url,            # Column E: Club_URL
                    "competitive",       # Column F: Club_Type
                    club_id,             # Column G: Club_ID (auto-extracted)
                    "",                  # Column H: Leaders
                    "",                  # Column I: Officers
                    server_id            # Column J: Server_ID (auto from guild)
                ]
            )
            
            print(f"✅ Added {club_name} to config with URL: {club_url}, ID: {club_id}, Server: {server_id}")
            
            # Add new club directly to cache (no need to reload all clubs)
            client.config_cache[club_name] = {
                'Club_Name': club_name,
                'Data_Sheet_Name': data_sheet,
                'Members_Sheet_Name': members_sheet,
                'Target_Per_Day': target_quota,
                'Club_URL': club_url,
                'Club_Type': 'competitive',
                'Club_ID': club_id,
                'Server_ID': server_id
            }
            # Also initialize member_cache for this club (empty for now, will populate on first sync)
            client.member_cache[club_name] = []
            print(f"📝 Added {club_name} to config_cache and member_cache (skipped full reload)")
            
            await interaction.followup.send(
                f"✅ **Successfully created Competitive Club '{club_name}'!**\n\n"
                f"📊 **Created sheets:**\n"
                f"- Data: `{data_sheet}`\n"
                f"- Members: `{members_sheet}`\n\n"
                f"🎯 **Daily Quota:** {format_fans(target_quota).replace('+', '')} fans/day\n"
                f"🔗 **Club URL:** {club_url}\n\n"
                f"**Next steps:**\n"
                f"1. Use `/add_member` to add members\n"
                f"2. Use `/club_set_webhook` to set notification channel",
                ephemeral=True
            )
        
        except Exception as e:
            error_msg = str(e)
            
            # Handle duplicate sheet name error
            if "already exists" in error_msg.lower():
                await interaction.followup.send(
                    f"❌ Error: A sheet with this name already exists.\n"
                    f"Sheet names tried:\n"
                    f"- {data_sheet}\n"
                    f"- {members_sheet}\n\n"
                    f"Please choose a different club name.",
                    ephemeral=True
                )
            else:
                print(f"Error in CompetitiveClubSetupModal: {e}")
                await interaction.followup.send(
                    f"❌ An error occurred while creating the club:\n```{e}```",
                    ephemeral=True
                )


class CasualClubSetupModal(discord.ui.Modal, title="Setup Casual Club"):
    """Modal for setting up a casual club (no quota)"""
    
    club_name_input = discord.ui.TextInput(
        label="Club Name",
        placeholder="My Club Name",
        required=True,
        max_length=50
    )
    
    club_url_input = discord.ui.TextInput(
        label="Club Profile URL",
        placeholder="https://chronogenesis.net/club_profile?circle_id=XXXXXX",
        required=True,
        max_length=200
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle casual club setup submission"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Extract values
            club_name = self.club_name_input.value.strip()
            club_url = self.club_url_input.value.strip()
            target_quota = 0  # Casual clubs have no quota
            
            # ===== VALIDATE CLUB NAME =====
            if not club_name:
                await interaction.followup.send(
                    "❌ Error: Club name cannot be empty.",
                    ephemeral=True
                )
                return
            
            if len(club_name) > 50:
                await interaction.followup.send(
                    "❌ Error: Club name is too long (max 50 characters).",
                    ephemeral=True
                )
                return
            
            # Check for dangerous characters
            invalid_chars = ['/', '\\', '?', '*', ':', '[', ']']
            found_invalid = [char for char in invalid_chars if char in club_name]
            
            if found_invalid:
                invalid_list = ', '.join(f"'{char}'" for char in found_invalid)
                await interaction.followup.send(
                    f"❌ Error: Club name contains invalid characters: {invalid_list}\n"
                    f"These characters cannot be used in Google Sheets tab names.\n"
                    f"Please avoid: / \\ ? * : [ ]",
                    ephemeral=True
                )
                return
            
            # Check if club already exists (case-insensitive) using existing cache
            # Skip cache reload to speed up response - cache is updated after creation
            for existing_club in client.config_cache.keys():
                if existing_club.lower() == club_name.lower():
                    await interaction.followup.send(
                        f"❌ Error: Club '{existing_club}' already exists.\n"
                        f"(Club names are case-insensitive)",
                        ephemeral=True
                    )
                    return
            
            # ===== VALIDATE CLUB URL =====
            if not club_url:
                await interaction.followup.send(
                    "❌ Error: Club URL cannot be empty.",
                    ephemeral=True
                )
                return
            
            # Basic URL validation
            if not club_url.startswith(('http://', 'https://')):
                # Show error with image guide
                embed = discord.Embed(
                    title="❌ Invalid Club URL",
                    description=(
                        "Club URL must start with `http://` or `https://`\n\n"
                        "**Expected format:**\n"
                        "`https://chronogenesis.net/club_profile?circle_id=XXXXXXXXX`\n\n"
                        "**Your URL:**\n"
                        f"`{club_url}`\n\n"
                        "📌 **Club ID usually found here:**"
                    ),
                    color=discord.Color.red()
                )
                embed.set_image(url="attachment://club_id_guide.png")
                
                try:
                    file = discord.File(os.path.join(SCRIPT_DIR, "assets", "club_id_guide.png"), filename="club_id_guide.png")
                    await interaction.followup.send(embed=embed, file=file, ephemeral=True)
                except FileNotFoundError:
                    await interaction.followup.send(
                        f"❌ Error: Club URL must start with http:// or https://\n"
                        f"Expected: https://chronogenesis.net/club_profile?circle_id=XXXXXXXXX",
                        ephemeral=True
                    )
                return
            
            # ===== VALIDATE CLUB ID FROM URL =====
            is_valid, club_id, error_msg = await validate_club_url_and_id(club_url)
            
            if not is_valid:
                # Show error with guide image
                embed = discord.Embed(
                    title="❌ Invalid Club URL",
                    description=(
                        f"**Error:** {error_msg}\n\n"
                        "**Correct URL format:**\n"
                        "`https://chronogenesis.net/club_profile?circle_id=XXXXXXXXX`\n\n"
                        "⚠️ **Note:** URL must contain **9-digit Club ID**, not club name.\n\n"
                        "**WRONG examples:**\n"
                        f"❌ `...circle_id=Chibi%20Club`\n"
                        f"❌ `...circle_id=MyClubName`\n\n"
                        "**CORRECT examples:**\n"
                        f"✅ `...circle_id=525713827`\n\n"
                        "**Your URL:**\n"
                        f"`{club_url}`\n\n"
                        "📌 **How to get Club ID:**"
                    ),
                    color=discord.Color.red()
                )
                embed.set_image(url="attachment://club_id_guide.png")
                
                try:
                    file = discord.File(os.path.join(SCRIPT_DIR, "assets", "club_id_guide.png"), filename="club_id_guide.png")
                    await interaction.followup.send(embed=embed, file=file, ephemeral=True)
                except FileNotFoundError:
                    await interaction.followup.send(
                        f"❌ **Invalid Club URL**\n\n"
                        f"**Error:** {error_msg}\n\n"
                        f"URL must contain 9-digit Club ID, not club name.\n"
                        f"Example: `https://chronogenesis.net/club_profile?circle_id=525713827`",
                        ephemeral=True
                    )
                return
            
            # ===== CREATE CLUB =====
            # Generate sheet names
            data_sheet = f"{club_name}_Data"
            members_sheet = f"{club_name}_Members"
            
            # Check sheet name length
            if len(data_sheet) > 100 or len(members_sheet) > 100:
                await interaction.followup.send(
                    f"❌ Error: Club name is too long for sheet naming.\n"
                    f"Sheet names would be:\n"
                    f"- {data_sheet} ({len(data_sheet)} chars)\n"
                    f"- {members_sheet} ({len(members_sheet)} chars)\n"
                    f"Maximum: 100 characters per sheet name.",
                    ephemeral=True
                )
                return
            
            # Create Data sheet (run in thread to avoid blocking)
            data_ws = await asyncio.to_thread(
                gs_manager.sh.add_worksheet, 
                title=data_sheet, rows=100, cols=6
            )
            await asyncio.to_thread(
                data_ws.update, 'A1:F1', 
                [['Name', 'Day', 'Total Fans', 'Daily', 'Target', 'CarryOver']]
            )
            
            print(f"✅ Created sheet: {data_sheet}")
            
            # Create Members sheet (run in thread to avoid blocking)
            members_ws = await asyncio.to_thread(
                gs_manager.sh.add_worksheet, 
                title=members_sheet, rows=50, cols=2
            )
            await asyncio.to_thread(
                members_ws.update, 'A1:B1', 
                [['Trainer ID', 'Name']]
            )
            
            print(f"✅ Created sheet: {members_sheet}")
            
            # club_id already validated and extracted by validate_club_url_and_id()
            
            # Get Server ID from interaction
            server_id = str(interaction.guild_id) if interaction.guild_id else ""
            
            # Add to config with casual type (run in thread)
            config_ws = await asyncio.to_thread(
                gs_manager.sh.worksheet, config.CONFIG_SHEET_NAME
            )
            await asyncio.to_thread(
                config_ws.append_row,
                [
                    club_name,           # Column A: Club_Name
                    data_sheet,          # Column B: Data_Sheet_Name
                    members_sheet,       # Column C: Members_Sheet_Name
                    target_quota,        # Column D: Target_Per_Day (0 for casual)
                    club_url,            # Column E: Club_URL
                    "casual",            # Column F: Club_Type
                    club_id,             # Column G: Club_ID (auto-extracted)
                    "",                  # Column H: Leaders
                    "",                  # Column I: Officers
                    server_id            # Column J: Server_ID (auto from guild)
                ]
            )
            
            print(f"✅ Added {club_name} to config with URL: {club_url}, ID: {club_id}, Server: {server_id}")
            
            # Add new club directly to cache (no need to reload all clubs)
            client.config_cache[club_name] = {
                'Club_Name': club_name,
                'Data_Sheet_Name': data_sheet,
                'Members_Sheet_Name': members_sheet,
                'Target_Per_Day': target_quota,
                'Club_URL': club_url,
                'Club_Type': 'casual',
                'Club_ID': club_id,
                'Server_ID': server_id
            }
            # Also initialize member_cache for this club (empty for now, will populate on first sync)
            client.member_cache[club_name] = []
            print(f"📝 Added {club_name} to config_cache and member_cache (skipped full reload)")
            
            await interaction.followup.send(
                f"✅ **Successfully created Casual Club '{club_name}'!**\n\n"
                f"📊 **Created sheets:**\n"
                f"- Data: `{data_sheet}`\n"
                f"- Members: `{members_sheet}`\n\n"
                f"😊 **Club Type:** Casual (no daily quota)\n"
                f"🔗 **Club URL:** {club_url}\n\n"
                f"**Next steps:**\n"
                f"1. Use `/add_member` to add members\n"
                f"2. Use `/club_set_webhook` to set notification channel",
                ephemeral=True
            )
        
        except Exception as e:
            error_msg = str(e)
            
            # Handle duplicate sheet name error
            if "already exists" in error_msg.lower():
                await interaction.followup.send(
                    f"❌ Error: A sheet with this name already exists.\n"
                    f"Sheet names tried:\n"
                    f"- {data_sheet}\n"
                    f"- {members_sheet}\n\n"
                    f"Please choose a different club name.",
                    ephemeral=True
                )
            else:
                print(f"Error in CasualClubSetupModal: {e}")
                await interaction.followup.send(
                    f"❌ An error occurred while creating the club:\n```{e}```",
                    ephemeral=True
                )


# ============================================================================
# ADMIN COMMANDS
# ============================================================================

# ============================================================================
# UPDATED CONFIG STRUCTURE
# ============================================================================
# Clubs_Config sheet now has columns:
# Club_Name | Data_Sheet_Name | Members_Sheet_Name | Target_Per_Day | Club_URL

@client.tree.command(
    name="club_setup",
    description="Leader/Admin: Initialize a new club and its related sheets."
)
@app_commands.describe(
    club_type="Club competitive level"
)
@app_commands.choices(club_type=[
    app_commands.Choice(name="🔥 Competitive", value="competitive"),
    app_commands.Choice(name="😊 Casual", value="casual")
])
@app_commands.checks.cooldown(10, 60.0, key=lambda i: i.guild_id)  # 10 per minute per server
@is_leader_or_admin()
async def club_setup(
    interaction: discord.Interaction,
    club_type: str
):
    """Create a new club with data and member sheets - Shows modal based on club type"""
    
    # Show the appropriate modal based on club type
    if club_type == "competitive":
        modal = CompetitiveClubSetupModal()
    else:  # casual
        modal = CasualClubSetupModal()
    
    await interaction.response.send_modal(modal)



# ============================================================================
# NEW COMMAND: UPDATE CLUB URL
# ============================================================================

@client.tree.command(
    name="club_set_url",
    description="Admin: Sets or updates the club profile URL."
)
@app_commands.autocomplete(club_name=club_autocomplete)
@app_commands.describe(
    club_name="The club to update",
    club_url="Club profile URL (e.g., https://chronogenesis.net/club_profile?circle_id=620261816)"
)
@is_admin_or_has_role()
async def club_set_url(
    interaction: discord.Interaction,
    club_name: str,
    club_url: str
):
    """Set or update club profile URL"""
    await interaction.response.defer(ephemeral=True)
    
    # Validate URL
    club_url = club_url.strip()
    
    if not club_url.startswith(('http://', 'https://')):
        await interaction.followup.send(
            "❌ Error: Club URL must start with http:// or https://",
            ephemeral=True
        )
        return
    
    club_config = client.config_cache.get(club_name)
    if not club_config:
        await interaction.followup.send(
            f"❌ Error: Club '{club_name}' not found.",
            ephemeral=True
        )
        return
    
    if 'config_sheet' not in club_config:
        await interaction.followup.send(
            "❌ Error: Bot is in cached mode. Cannot execute write command.",
            ephemeral=True
        )
        return
    
    try:
        config_sheet = club_config['config_sheet']
        row_index = club_config['row']
        
        # Update column E (Club_URL) - column 5
        await asyncio.to_thread(config_sheet.update_cell, row_index, 5, club_url)
        
        # Update only this club's config cache (FAST - no full reload)
        await client.update_single_club_config(club_name, {'Club_URL': club_url})
        await interaction.followup.send(
            f"✅ Successfully updated club URL for '{club_name}'.\n"
            f"🔗 New URL: {club_url}",
            ephemeral=True
        )
    
    except Exception as e:
        await interaction.followup.send(f"❌ An error occurred: {e}", ephemeral=True)





# ============================================================================
# CLUB ROLE MANAGEMENT COMMANDS (Server Owner Only)
# ============================================================================

@client.tree.command(
    name="club_assign_leader",
    description="Admin/Owner: Assign a user as club Leader"
)
@app_commands.autocomplete(club_name=club_autocomplete)
@app_commands.describe(
    club_name="The club name",
    user="User to assign as Leader"
)
@is_admin_or_has_role()
async def club_assign_leader(
    interaction: discord.Interaction,
    club_name: str,
    user: discord.Member
):
    """Assign a user as club Leader"""
    await interaction.response.defer(ephemeral=False)
    
    # Check club exists
    club_config = client.config_cache.get(club_name)
    if not club_config:
        await interaction.followup.send(
            f"❌ Club '{club_name}' not found!",
            ephemeral=False
        )
        return
    
    if 'config_sheet' not in club_config:
        await interaction.followup.send(
            "❌ Bot is in cached mode. Cannot execute write command.",
            ephemeral=False
        )
        return
    
    try:
        # Get current leaders
        leaders = club_config.get('Leaders', [])
        if isinstance(leaders, str):
            # Parse JSON string
            leaders = json.loads(leaders) if leaders else []
        
        # Check if already a leader
        if user.id in leaders:
            await interaction.followup.send(
                f"⚠️ {user.mention} is already a Leader of `{club_name}`!",
                ephemeral=False
            )
            return
        
        # Add to leaders list
        leaders.append(user.id)
        
        # Update in Google Sheets (column 8 - Leaders)
        config_sheet = club_config['config_sheet']
        row = club_config['row']
        await asyncio.to_thread(config_sheet.update_cell, row, 8, json.dumps(leaders))
        
        # Update only this club's config cache (FAST - no full reload)
        await client.update_single_club_config(club_name, {'Leaders': json.dumps(leaders)})
        
        await interaction.followup.send(
            f"✅ **Role Assigned**\n\n"
            f"User: {user.mention} (`{user.name}`)\n"
            f"Role: **Leader**\n"
            f"Club: `{club_name}`\n"
            f"Total Leaders: {len(leaders)}",
            ephemeral=False
        )
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=False)


@client.tree.command(
    name="club_assign_officer",
    description="Leader/Server Owner: Assign a user as club Officer"
)
@app_commands.autocomplete(club_name=club_autocomplete)
@app_commands.describe(
    club_name="The club name",
    user="User to assign as Officer"
)
async def club_assign_officer(
    interaction: discord.Interaction,
    club_name: str,
    user: discord.Member
):
    """Assign a user as club Officer (Leaders can do this)"""
    await interaction.response.defer(ephemeral=False)
    
    club_config = client.config_cache.get(club_name)
    if not club_config:
        await interaction.followup.send(
            f"❌ Club '{club_name}' not found!",
            ephemeral=False
        )
        return
    
    if 'config_sheet' not in club_config:
        await interaction.followup.send(
            "❌ Bot is in cached mode. Cannot execute write command.",
            ephemeral=False
        )
        return
    
    # Check permissions: God Mode, Server Owner, or Leader of this club
    is_god_mode = interaction.user.id in config.GOD_MODE_USER_IDS
    is_server_owner = interaction.guild and interaction.guild.owner_id == interaction.user.id
    
    # Check if user is a Leader of this club
    leaders = club_config.get('Leaders', [])
    if isinstance(leaders, str):
        leaders = json.loads(leaders) if leaders else []
    is_leader = interaction.user.id in leaders
    
    if not (is_god_mode or is_server_owner or is_leader):
        await interaction.followup.send(
            f"❌ **Permission Denied**\n\n"
            f"Only **Server Owners** or **Leaders** of `{club_name}` can assign Officers.",
            ephemeral=True
        )
        return
    
    try:
        # Get current officers
        officers = club_config.get('Officers', [])
        if isinstance(officers, str):
            officers = json.loads(officers) if officers else []
        
        if user.id in officers:
            await interaction.followup.send(
                f"⚠️ {user.mention} is already an Officer of `{club_name}`!",
                ephemeral=False
            )
            return
        
        officers.append(user.id)
        
        # Update in Google Sheets (column 10 - Officers)
        config_sheet = club_config['config_sheet']
        row = club_config['row']
        await asyncio.to_thread(config_sheet.update_cell, row, 10, json.dumps(officers))
        
        # Update only this club's config cache (FAST - no full reload)
        await client.update_single_club_config(club_name, {'Officers': json.dumps(officers)})
        
        await interaction.followup.send(
            f"✅ **Role Assigned**\n\n"
            f"User: {user.mention} (`{user.name}`)\n"
            f"Role: **Officer**\n"
            f"Club: `{club_name}`\n"
            f"Total Officers: {len(officers)}",
            ephemeral=False
        )
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=False)


@client.tree.command(
    name="club_remove_leader",
    description="Admin/Owner: Remove Leader role from user"
)
@app_commands.autocomplete(club_name=club_autocomplete)
@app_commands.describe(
    club_name="The club name",
    user="User to remove from Leaders"
)
@is_admin_or_has_role()
async def club_remove_leader(
    interaction: discord.Interaction,
    club_name: str,
    user: discord.Member
):
    """Remove Leader role from user"""
    await interaction.response.defer(ephemeral=False)
    
    club_config = client.config_cache.get(club_name)
    if not club_config:
        await interaction.followup.send(
            f"❌ Club '{club_name}' not found!",
            ephemeral=False
        )
        return
    
    if 'config_sheet' not in club_config:
        await interaction.followup.send(
            "❌ Bot is in cached mode. Cannot execute write command.",
            ephemeral=False
        )
        return
    
    try:
        leaders = club_config.get('Leaders', [])
        if isinstance(leaders, str):
            leaders = json.loads(leaders) if leaders else []
        
        if user.id not in leaders:
            await interaction.followup.send(
                f"⚠️ {user.mention} is not a Leader of `{club_name}`!",
                ephemeral=False
            )
            return
        
        leaders.remove(user.id)
        
        config_sheet = club_config['config_sheet']
        row = club_config['row']
        await asyncio.to_thread(config_sheet.update_cell, row, 8, json.dumps(leaders))  # Column 8 = Leaders
        
        # Update only this club's config cache (FAST - no full reload)
        await client.update_single_club_config(club_name, {'Leaders': json.dumps(leaders)})
        
        await interaction.followup.send(
            f"✅ **Role Removed**\n\n"
            f"User: {user.mention}\n"
            f"Role: **Leader**\n"
            f"Club: `{club_name}`",
            ephemeral=False
        )
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=False)


@client.tree.command(
    name="club_remove_officer",
    description="Server Owner: Remove Officer role from user"
)
@app_commands.autocomplete(club_name=club_autocomplete)
@app_commands.describe(
    club_name="The club name",
    user="User to remove from Officers"
)
@is_primary_admin()
async def club_remove_officer(
    interaction: discord.Interaction,
    club_name: str,
    user: discord.Member
):
    """Remove Officer role from user"""
    await interaction.response.defer(ephemeral=False)
    
    club_config = client.config_cache.get(club_name)
    if not club_config:
        await interaction.followup.send(
            f"❌ Club '{club_name}' not found!",
            ephemeral=False
        )
        return
    
    if 'config_sheet' not in club_config:
        await interaction.followup.send(
            "❌ Bot is in cached mode. Cannot execute write command.",
            ephemeral=False
        )
        return
    
    try:
        officers = club_config.get('Officers', [])
        if isinstance(officers, str):
            officers = json.loads(officers) if officers else []
        
        if user.id not in officers:
            await interaction.followup.send(
                f"⚠️ {user.mention} is not an Officer of `{club_name}`!",
                ephemeral=False
            )
            return
        
        officers.remove(user.id)
        
        config_sheet = club_config['config_sheet']
        row = club_config['row']
        await asyncio.to_thread(config_sheet.update_cell, row, 10, json.dumps(officers))
        
        # Update only this club's config cache (FAST - no full reload)
        await client.update_single_club_config(club_name, {'Officers': json.dumps(officers)})
        
        await interaction.followup.send(
            f"✅ **Role Removed**\n\n"
            f"User: {user.mention}\n"
            f"Role: **Officer**\n"
            f"Club: `{club_name}`",
            ephemeral=False
        )
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=False)


@client.tree.command(
    name="club_show_roles",
    description="Show all role assignments for a club"
)
@app_commands.autocomplete(club_name=club_autocomplete)
@app_commands.describe(club_name="The club name")
async def club_show_roles(
    interaction: discord.Interaction,
    club_name: str
):
    """Show all role assignments for a club"""
    await interaction.response.defer(ephemeral=True)
    
    club_config = client.config_cache.get(club_name)
    if not club_config:
        await interaction.followup.send(
            f"❌ Club '{club_name}' not found!",
            ephemeral=True
        )
        return
    
    embed = discord.Embed(
        title=f"🎖️ Club Roles: {club_name}",
        color=discord.Color.blue()
    )
    
    # Leaders
    leaders = club_config.get('Leaders', [])
    if isinstance(leaders, str):
        leaders = json.loads(leaders) if leaders else []
    
    if leaders:
        leader_list = []
        for lid in leaders:
            try:
                leader = await client.fetch_user(lid)
                leader_list.append(f"• {leader.mention} (`{leader.name}`)")
            except:
                leader_list.append(f"• User ID: {lid}")
        
        embed.add_field(
            name=f"⭐ Leaders ({len(leaders)})",
            value="\n".join(leader_list),
            inline=False
        )
    else:
        embed.add_field(
            name="⭐ Leaders",
            value="No leaders assigned",
            inline=False
        )
    
    # Officers  
    officers = club_config.get('Officers', [])
    if isinstance(officers, str):
        officers = json.loads(officers) if officers else []
    
    if officers:
        officer_list = []
        for oid in officers:
            try:
                officer = await client.fetch_user(oid)
                officer_list.append(f"• {officer.mention} (`{officer.name}`)")
            except:
                officer_list.append(f"• User ID: {oid}")
        
        embed.add_field(
            name=f"🛡️ Officers ({len(officers)})",
            value="\n".join(officer_list),
            inline=False
        )
    else:
        embed.add_field(
            name="🛡️ Officers",
            value="No officers assigned",
            inline=False
        )
    
    embed.set_footer(text=SUPPORT_MESSAGE)
    
    await interaction.followup.send(embed=embed, ephemeral=True)


@client.tree.command(
    name="club_set_quota",
    description="Leader/Server Owner: Update club daily target (KPI)"
)
@app_commands.autocomplete(club_name=club_autocomplete)
@app_commands.describe(
    club_name="The club name",
    daily_target="New daily target/KPI for club members"
)
async def club_set_quota(
    interaction: discord.Interaction,
    club_name: str,
    daily_target: int
):
    """Update club daily target/quota (Leaders can do this)"""
    await interaction.response.defer(ephemeral=False)
    
    club_config = client.config_cache.get(club_name)
    if not club_config:
        await interaction.followup.send(
            f"❌ Club '{club_name}' not found!",
            ephemeral=False
        )
        return
    
    if 'config_sheet' not in club_config:
        await interaction.followup.send(
            "❌ Bot is in cached mode. Cannot execute write command.",
            ephemeral=False
        )
        return
    
    # Check permissions: God Mode, Server Owner, or Leader of this club
    is_god_mode = interaction.user.id in config.GOD_MODE_USER_IDS
    is_server_owner = interaction.guild and interaction.guild.owner_id == interaction.user.id
    
    # Check if user is a Leader of this club
    leaders = club_config.get('Leaders', [])
    if isinstance(leaders, str):
        leaders = json.loads(leaders) if leaders else []
    is_leader = interaction.user.id in leaders
    
    if not (is_god_mode or is_server_owner or is_leader):
        await interaction.followup.send(
            f"❌ **Permission Denied**\n\n"
            f"Only **Server Owners** or **Leaders** of `{club_name}` can update quota.",
            ephemeral=True
        )
        return
    
    # Validate daily target
    if daily_target < 0:
        await interaction.followup.send(
            "❌ Daily target must be a positive number!",
            ephemeral=False
        )
        return
    
    try:
        # Get current quota
        old_quota = club_config.get('Target_Per_Day', 0)
        
        # Update in Google Sheets (column 4 - Target_Per_Day)
        config_sheet = club_config['config_sheet']
        row = club_config['row']
        await asyncio.to_thread(config_sheet.update_cell, row, 4, daily_target)
        
        # Update only this club's config cache (FAST - no full reload)
        await client.update_single_club_config(club_name, {'Target_Per_Day': daily_target})
        
        await interaction.followup.send(
            f"✅ **Quota Updated**\n\n"
            f"Club: `{club_name}`\n"
            f"Old Target: **{old_quota:,}** fans/day\n"
            f"New Target: **{daily_target:,}** fans/day\n"
            f"Updated by: {interaction.user.mention}",
            ephemeral=False
        )
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=False)


# ============================================================================
# HELP COMMAND WITH INTERACTIVE BUTTONS
# ============================================================================

class HelpView(discord.ui.View):
    """Interactive help menu with buttons for different command categories"""
    
    def __init__(self):
        super().__init__(timeout=180)  # 3 minutes timeout
    
    @discord.ui.button(label="👤 User Commands", style=discord.ButtonStyle.primary, custom_id="user_help")
    async def user_commands_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show user commands with detailed explanations"""
        embed = discord.Embed(
            title="👤 User Commands",
            description="Commands available to all users",
            color=discord.Color.blue()
        )
        
        # Stats Commands
        embed.add_field(
            name="━━━━━━ 📊 Stats & Rankings ━━━━━━",
            value="‎",  # Zero-width space
            inline=False
        )
        
        embed.add_field(
            name="📊 /leaderboard",
            value=(
                "View club member rankings and fan counts\n"
                "`/leaderboard club_name:[select]`\n\n"
                "Shows rankings, daily gains, targets, and surplus/deficit"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📈 /stats",
            value=(
                "View detailed stats for a specific member\n"
                "`/stats club_name:[select] member_name:[select]`\n\n"
                "Shows fan count, daily growth, rank, and performance"
            ),
            inline=False
        )
        
        embed.add_field(
            name="👤 /profile",
            value=(
                "View stats for your linked profile\n"
                "`/profile`\n\n"
                "Quick access after linking via `/stats` ➜ 'Yes, this is me'"
            ),
            inline=False
        )
        
        # Discovery Commands
        embed.add_field(
            name="━━━━━━ 🔍 Discovery ━━━━━━",
            value="‎",
            inline=False
        )
        
        embed.add_field(
            name="🔍 /search_club",
            value=(
                "Search and request tracking for new clubs\n"
                "`/search_club club_name:[type exact name]`\n\n"
                "⚠️ Cooldown: 30 seconds per user"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📋 /club_list",
            value=(
                "Browse all clubs in this server\n"
                "`/club_list`\n\n"
                "Shows all clubs, types, member counts, and quotas"
            ),
            inline=False
        )
        
        embed.add_field(
            name="👥 /club_show_roles",
            value=(
                "View Leaders and Officers of a club\n"
                "`/club_show_roles club_name:[select]`"
            ),
            inline=False
        )
        
        # Info Commands
        embed.add_field(
            name="━━━━━━ ℹ️ System Info ━━━━━━",
            value="‎",
            inline=False
        )
        
        embed.add_field(
            name="ℹ️ /status & /uptime",
            value=(
                "`/status` - Check bot health and latency\n"
                "`/uptime` - See how long bot has been online"
            ),
            inline=False
        )
        
        # Add support server link
        embed.add_field(
            name="━━━━━━ 💬 Support ━━━━━━",
            value=(
                f"❓ {SUPPORT_HELP_MESSAGE}: [Join Here]({SUPPORT_SERVER_URL})\n"
                f"☕ Donation: [{DONATION_MESSAGE}]({DONATION_URL})"
            ),
            inline=False
        )
        
        embed.set_footer(text="💡 Tip: Use autocomplete by typing commands")
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="🛡️ Manager Commands", style=discord.ButtonStyle.success, custom_id="manager_help")
    async def manager_commands_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show manager/admin commands"""
        embed = discord.Embed(
            title="🛡️ Manager Commands",
            description=(
                "**Permissions:**\n"
                "👑 Admin - Full access | ⭐ Leader - Club management | 🛡️ Officer - Display role"
            ),
            color=discord.Color.green()
        )
        
        # Admin Commands
        embed.add_field(
            name="━━━━━━ 👑 Admin Commands ━━━━━━",
            value="‎",
            inline=False
        )
        
        embed.add_field(
            name="Club Management",
            value=(
                "`/club_setup` - Create new club\n"
                "• Setup club name, type (casual/competitive), and daily quota\n\n"
                
                "`/club_assign_leader` - Assign Leader role\n"
                "`/club_remove_leader` - Remove Leader role\n"
                "• Only Server Owner/Admin can manage Leaders"
            ),
            inline=False
        )
        
        embed.add_field(
            name="Role Management",
            value=(
                "`/club_assign_officer` - Assign Officer role\n"
                "`/club_remove_officer` - Remove Officer role"
            ),
            inline=False
        )
        
        embed.add_field(
            name="Channel Management",
            value=(
                "`/set_channel` - Set current channel as allowed\n"
                "• Run this command in the channel you want to use\n"
                "• Automatically replaces previous channel"
            ),
            inline=False
        )
        
        # Leader Commands
        embed.add_field(
            name="━━━━━━ ⭐ Leader Commands ━━━━━━",
            value="‎",
            inline=False
        )
        
        embed.add_field(
            name="Club Configuration",
            value=(
                "`/club_setup` - Leaders can also create clubs\n\n"
                
                "`/club_set_quota` - Update daily target\n"
                "• Set daily fan quota for competitive clubs\n\n"
                
                "`/club_set_url` - Update club URL\n"
                "• Paste club profile URL from uma.moe\n\n"
                
                "`/club_set_type` - Change club type\n"
                "• Switch between casual and competitive"
            ),
            inline=False
        )
        
        # Officer Note
        embed.add_field(
            name="━━━━━━ 🛡️ Officer Role ━━━━━━",
            value="‎",
            inline=False
        )
        
        embed.add_field(
            name="ℹ️ Note",
            value=(
                "Officers have same permissions as regular members\n"
                "This is an organizational role for club hierarchy display\n\n"
                "**Members are auto-synced daily** - no manual commands needed"
            ),
            inline=False
        )
        
        # Add support server link
        embed.add_field(
            name="━━━━━━ 💬 Support ━━━━━━",
            value=(
                f"❓ {SUPPORT_HELP_MESSAGE}: [Join Here]({SUPPORT_SERVER_URL})\n"
                f"☕ Donation: [{DONATION_MESSAGE}]({DONATION_URL})"
            ),
            inline=False
        )
        
        embed.set_footer(text="💡 Use autocomplete when typing commands")
        
        await interaction.response.edit_message(embed=embed, view=self)


@client.tree.command(
    name="help",
    description="Show bot commands and features"
)
async def help_command(interaction: discord.Interaction):
    """Interactive help command with buttons"""
    
    embed = discord.Embed(
        title="🤖 Bot Help Menu",
        description=(
            "Welcome to the Club Management Bot!\n\n"
            "**Click a button below** to see available commands:\n\n"
            "👤 **User Commands** - Commands for all users\n"
            "🛡️ **Manager Commands** - Commands for Leaders, Officers, and Admins\n\n"
            f"💬 **Need Help?** [Join our support server]({SUPPORT_SERVER_URL})\n"
            f"☕ **Donation:** [{DONATION_MESSAGE}]({DONATION_URL})"
        ),
        color=discord.Color.gold()
    )
    
    embed.add_field(
        name="📊 Features",
        value=(
            "• Real-time club leaderboards\n"
            "• Member stats tracking\n"
            "• Automated data syncing\n"
            "• Role-based permissions"
        ),
        inline=False
    )
    
    view = HelpView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)


# ============================================================================
# CLUB LIST PAGINATION VIEW
# ============================================================================

class ClubQuotaFilterModal(discord.ui.Modal, title="🔍 Filter by Quota"):
    """Modal for filtering clubs by quota range"""
    
    min_quota = discord.ui.TextInput(
        label="Minimum Quota (fans/day)",
        placeholder="e.g. 1000000 for 1M",
        required=True,
        max_length=15
    )
    
    max_quota = discord.ui.TextInput(
        label="Maximum Quota (fans/day) - Optional",
        placeholder="Leave empty for no max limit",
        required=False,
        max_length=15
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parse min value
            min_val = self.min_quota.value.replace(",", "").replace(".", "").strip()
            min_quota = int(min_val)
            
            # Parse max value (optional)
            max_quota = None
            if self.max_quota.value.strip():
                max_val = self.max_quota.value.replace(",", "").replace(".", "").strip()
                max_quota = int(max_val)
            
            # Apply filter via view
            view: ClubListView = self.view
            await view.apply_quota_filter(interaction, min_quota, max_quota)
            
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid number format. Use numbers like `1000000` for 1M.",
                ephemeral=True
            )

class ClubListView(discord.ui.View):
    """Pagination view for club list with quota filter"""
    
    def __init__(self, all_clubs: list, clubs_per_page: int = 5):
        super().__init__(timeout=300)  # 5 min timeout
        self.all_clubs_original = all_clubs.copy()  # Keep original
        self.all_clubs = all_clubs  # Working copy
        self.clubs_per_page = clubs_per_page
        self.current_page = 0
        
        # Quota filter only
        self.quota_min = None
        self.quota_max = None
        
        self._update_pagination()
        self.update_buttons()
    
    def _update_pagination(self):
        """Recalculate pagination after filter"""
        self.total_pages = max(1, (len(self.all_clubs) + self.clubs_per_page - 1) // self.clubs_per_page)
    
    def _apply_quota_filter(self):
        """Apply quota filter"""
        # Start from original
        filtered = self.all_clubs_original.copy()
        
        # Apply quota filter
        if self.quota_min is not None:
            def get_quota(config):
                try:
                    return int(config.get('Target_Per_Day', 0) or 0)
                except:
                    return 0
            
            filtered = [
                (name, config) for name, config in filtered
                if get_quota(config) >= self.quota_min and
                (self.quota_max is None or get_quota(config) <= self.quota_max)
            ]
        
        self.all_clubs = filtered
    
    async def apply_quota_filter(self, interaction: discord.Interaction, min_quota: int, max_quota: int = None):
        """Filter clubs by quota range"""
        self.quota_min = min_quota
        self.quota_max = max_quota
        self._apply_quota_filter()
        
        # Reset to page 1
        self.current_page = 0
        self._update_pagination()
        self.update_buttons()
        
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    def update_buttons(self):
        """Enable/disable buttons based on current page"""
        self.first_button.disabled = (self.current_page == 0)
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= self.total_pages - 1)
        self.last_button.disabled = (self.current_page >= self.total_pages - 1)
        # Clear filter enabled if quota filter is active
        self.clear_filter_button.disabled = (self.quota_min is None)
    
    def get_page_embed(self) -> discord.Embed:
        """Generate embed for current page"""
        start_idx = self.current_page * self.clubs_per_page
        end_idx = min(start_idx + self.clubs_per_page, len(self.all_clubs))
        page_clubs = self.all_clubs[start_idx:end_idx]
        
        # Title 
        title = "📋 All Clubs in the System"
        
        # Description with filter status
        desc_parts = [f"Found **{len(self.all_clubs)}** club(s)"]
        
        # Quota filter
        if self.quota_min is not None:
            quota_text = f"{self.quota_min:,}"
            if self.quota_max:
                quota_text += f" - {self.quota_max:,}"
            else:
                quota_text += "+"
            desc_parts.append(f"💰 Quota: **{quota_text}**")
        
        desc_parts.append(f"Page {self.current_page + 1}/{self.total_pages}")
        
        embed = discord.Embed(
            title=title,
            description=" • ".join(desc_parts),
            color=discord.Color.blue()
        )
        
        if not page_clubs:
            embed.add_field(
                name="No clubs found",
                value="No clubs match the current filter.",
                inline=False
            )
        else:
            for name, config in page_clubs:
                club_type = config.get('Club_Type', 'Unknown')
                quota = config.get('Target_Per_Day', 'N/A')
                
                # Get member count
                member_list = client.member_cache.get(name, [])
                member_count = len(member_list) if member_list else "N/A"
                
                # Type emoji
                type_emoji = "🔥" if club_type == "competitive" else "😊"
                
                # Format quota
                try:
                    quota_value = int(quota) if quota and quota != 'N/A' else 0
                    quota_str = f"{quota_value:,}" if quota_value > 0 else "None"
                except:
                    quota_str = str(quota)
                
                # Get rank
                rank = config.get('Rank', '')
                rank_str = f"🏆 Rank: **#{rank}**\n" if rank else ""
                
                embed.add_field(
                    name=f"{type_emoji} {name}",
                    value=(
                        f"{rank_str}"
                        f"Type: **{club_type.title() if club_type else 'Unknown'}**\n"
                        f"Members: **{member_count}**\n"
                        f"Daily Quota: **{quota_str}** fans/day"
                    ),
                    inline=True
                )
        
        # Support message
        embed.add_field(
            name="━━━━━━━━━━━━━━━━━━━━━━",
            value=f"**{SUPPORT_MESSAGE}**: [Join Here]({SUPPORT_SERVER_URL})",
            inline=False
        )
        
        embed.set_footer(text=f"Page {self.current_page + 1} of {self.total_pages} • Total: {len(self.all_clubs)} club(s)")
        
        return embed
    
    @discord.ui.button(label="⏮ First", style=discord.ButtonStyle.secondary, custom_id="club_first", row=1)
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.primary, custom_id="club_prev", row=1)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="club_next", row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    @discord.ui.button(label="Last ⏭", style=discord.ButtonStyle.secondary, custom_id="club_last", row=1)
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.total_pages - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
    
    @discord.ui.button(label="💰 Quota Filter", style=discord.ButtonStyle.primary, custom_id="club_quota_filter", row=2)
    async def quota_filter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open quota filter modal"""
        modal = ClubQuotaFilterModal()
        modal.view = self  # Pass view reference
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🗑️ Clear", style=discord.ButtonStyle.danger, custom_id="club_clear", row=2)
    async def clear_filter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Reset quota filter
        self.quota_min = None
        self.quota_max = None
        self.all_clubs = self.all_clubs_original.copy()
        self.current_page = 0
        self._update_pagination()
        self.update_buttons()
        
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)


# ============================================================================
# CHANNEL MANAGEMENT COMMANDS
# ============================================================================

@client.tree.command(
    name="club_list",
    description="View all clubs in the system"
)
async def club_list(interaction: discord.Interaction):
    """List all clubs in the system with pagination"""
    await interaction.response.defer(ephemeral=False)
    
    try:
        # Get ALL clubs in the system
        all_clubs = list(client.config_cache.items())
        
        if not all_clubs:
            embed = discord.Embed(
                title="📋 **No Clubs Found**",
                description=(
                    "There are no clubs in the system yet.\n"
                    f"Admins can use `/club_setup` to create a club.\n\n"
                    f"**{SUPPORT_MESSAGE}**: [Join Here]({SUPPORT_SERVER_URL})"
                ),
                color=discord.Color.orange()
            )
            await interaction.followup.send(embed=embed, ephemeral=False)
            return
        
        # Sort clubs by name by default
        all_clubs.sort(key=lambda x: x[0].lower())
        
        # Create view with pagination
        view = ClubListView(all_clubs, clubs_per_page=5)
        embed = view.get_page_embed()
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=False)
    
    except Exception as e:
        await interaction.followup.send(
            f"❌ Error loading club list: {e}",
            ephemeral=True
        )


# ============================================================================
# SYSTEM COMMANDS
# ============================================================================

# ============================================================================
# CLUB REQUEST VIEW (FOR NON-TRACKED CLUBS)
# ============================================================================


class ClubRequestView(discord.ui.View):
    """View for requesting data - user selects club type"""
    
    def __init__(self, club_data: dict, api_data: dict, requester_info: dict):
        super().__init__(timeout=300)
        self.club_data = club_data
        self.api_data = api_data
        self.requester_info = requester_info
    
    @discord.ui.button(
        label="🔥 Competitive Club",
        style=discord.ButtonStyle.primary,
        custom_id="request_competitive"
    )
    async def request_competitive(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Request competitive club - ask user for quota"""
        modal = UserQuotaModal(
            self.club_data,
            self.api_data,
            self.requester_info,
            club_type="competitive"
        )
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(
        label="😊 Casual Club",
        style=discord.ButtonStyle.secondary,
        custom_id="request_casual"
    )
    async def request_casual(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Request casual club - no quota needed"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Send request with casual type
            await send_club_request_to_admin(
                self.club_data,
                self.api_data,
                self.requester_info,
                club_type="casual",
                target_quota=0
            )
            
            await interaction.followup.send(
                "✅ **Casual club request submitted!**\n\n"
                f"Club '{self.club_data['name']}' has been submitted for approval.\n"
                "An admin will review your request shortly.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True
            )


class UserQuotaModal(discord.ui.Modal, title="Set Daily Target"):
    """Modal for requester to input daily quota for competitive club"""
    
    def __init__(self, club_data: dict, api_data: dict, requester_info: dict, club_type: str):
        super().__init__()
        self.club_data = club_data
        self.api_data = api_data
        self.requester_info = requester_info
        self.club_type = club_type
    
    quota_input = discord.ui.TextInput(
        label="Daily Target (fans/day)",
        placeholder="5000",
        required=True,
        min_length=1,
        max_length=10
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle quota submission"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            quota = int(self.quota_input.value)
            if quota <= 0:
                await interaction.followup.send(
                    "❌ Quota must be a positive number!",
                    ephemeral=True
                )
                return
            
            # Send request with competitive type and quota
            await send_club_request_to_admin(
                self.club_data,
                self.api_data,
                self.requester_info,
                club_type=self.club_type,
                target_quota=quota
            )
            
            await interaction.followup.send(
                "✅ **Competitive club request submitted!**\n\n"
                f"Club '{self.club_data['name']}' has been submitted for approval.\n"
                f"Type: Competitive\n"
                f"Daily Target: {quota:,} fans/day\n\n"
                "An admin will review your request shortly.",
                ephemeral=True
            )
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid quota! Please enter a number.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True
            )


class AdminApprovalView(discord.ui.View):
    """View for admin to approve/reject club requests"""
    
    def __init__(self, club_data: dict, api_data: dict, requester_info: dict, club_type: str, target_quota: int):
        super().__init__(timeout=None)  # No timeout for admin actions
        self.club_data = club_data
        self.api_data = api_data
        self.requester_info = requester_info
        self.club_type = club_type
        self.target_quota = target_quota
    
    @discord.ui.button(
        label="✅ Approve",
        style=discord.ButtonStyle.success,
        custom_id="approve_club_request"
    )
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Approve club request - create club with requester's settings"""
        # CRITICAL: Defer FIRST to avoid timeout (3s window)
        await interaction.response.defer()
        
        club_name = self.club_data['name']
        
        # Check for duplicate
        if club_name in client.config_cache:
            await self._handle_duplicate_deferred(interaction)
            return
        
        # No duplicate - create club with requester's settings
        try:
            await auto_create_club_from_api(
                club_name,
                self.api_data,
                self.club_data,
                interaction,
                club_type=self.club_type,
                target_quota=self.target_quota,
                requester_info=self.requester_info
            )
            
            quota_text = f"{self.target_quota:,} fans/day" if self.club_type == "competitive" else "N/A (Casual)"
            
            await interaction.followup.send(
                f"✅ **Club '{club_name}' created successfully!**\n\n"
                f"**Type:** {self.club_type.capitalize()}\n"
                f"**Quota:** {quota_text}\n"
                f"**Members imported:** {len(self.api_data.get('members', []))}"
            )
            
            # Notify requester
            try:
                requester = await client.fetch_user(self.requester_info['user_id'])
                await requester.send(
                    f"✅ **Your club request was approved!**\n\n"
                    f"**Club Name:** {club_name}\n"
                    f"**Type:** {self.club_type.capitalize()}\n"
                    f"**Daily Quota:** {quota_text}\n"
                    f"**Status:** Now tracked by the bot\n\n"
                    f"You can use:\n"
                    f"• `/leaderboard {club_name}`\n"
                    f"• `/stats {club_name} <member>`"
                )
            except:
                pass
            
        except Exception as e:
            await interaction.followup.send(
                f"❌ **Error creating club:**\n```\n{e}\n```"
            )
    
    @discord.ui.button(
        label="❌ Reject",
        style=discord.ButtonStyle.danger,
        custom_id="reject_club_request"
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Reject club request"""
        await interaction.response.defer()
        
        try:
            # Notify requester
            requester = await client.fetch_user(self.requester_info['user_id'])
            await requester.send(
                f"❌ **Your club request was rejected**\n\n"
                f"**Club Name:** {self.club_data['name']}\n"
                f"**Reason:** Admin declined the request\n\n"
                f"If you believe this was a mistake, please contact an admin."
            )
            
            await interaction.followup.send(
                f"✅ Request rejected. Requester <@{self.requester_info['user_id']}> has been notified."
            )
            
        except discord.Forbidden:
            await interaction.followup.send(
                f"✅ Request rejected (but couldn't DM requester)."
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error: {e}"
            )
    
    async def _handle_duplicate_deferred(self, interaction: discord.Interaction):
        """Handle duplicate name when already deferred"""
        await interaction.followup.send(
            f"❌ **Duplicate club name**\n\n"
            f"Club '{self.club_data['name']}' already exists in the system.\n"
            f"Please ask requester to choose a different name or use a custom identifier.",
            ephemeral=True
        )


class ClubTypeSelectionView(discord.ui.View):
    """View for admin to select club type (competitive/casual)"""
    
    def __init__(self, club_name: str, api_data: dict, club_data: dict, requester_info: dict):
        super().__init__(timeout=300)
        self.club_name = club_name
        self.api_data = api_data
        self.club_data = club_data
        self.requester_info = requester_info
    
    @discord.ui.button(
        label="🔥 Competitive",
        style=discord.ButtonStyle.primary,
        custom_id="select_competitive"
    )
    async def competitive(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Select competitive mode - check duplicate then ask for quota"""
        # Check for duplicate FIRST
        if self.club_name in client.config_cache:
            await self._handle_duplicate(interaction, club_type="competitive")
            return
        
        # No duplicate - show modal to input quota
        modal = QuotaInputModal(
            self.club_name,
            self.api_data,
            self.club_data,
            self.requester_info,
            club_type="competitive"
        )
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(
        label="😊 Casual",
        style=discord.ButtonStyle.secondary,
        custom_id="select_casual"
    )
    async def casual(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Select casual mode - check duplicate then create with quota=0"""
        await interaction.response.defer()
        
        # Check for duplicate FIRST
        if self.club_name in client.config_cache:
            await self._handle_duplicate_sync(interaction, club_type="casual", target_quota=0)
            return
        
        # No duplicate - create club
        try:
            await auto_create_club_from_api(
                self.club_name,
                self.api_data,
                self.club_data,
                interaction,
                club_type="casual",
                target_quota=0,
                requester_info=self.requester_info
            )
            
            await interaction.followup.send(
                f"✅ **Casual club '{self.club_name}' created!**\n\n"
                f"Members imported: {len(self.api_data.get('members', []))}\n"
                f"Quota: Not applicable (casual mode)"
            )
            
            # Notify requester
            try:
                requester = await client.fetch_user(self.requester_info['user_id'])
                await requester.send(
                    f"✅ **Your club request was approved!**\n\n"
                    f"**Club Name:** {self.club_name}\n"
                    f"**Type:** Casual\n"
                    f"**Status:** Now tracked by the bot\n\n"
                    f"You can use:\n"
                    f"• `/leaderboard {self.club_name}`\n"
                    f"• `/stats {self.club_name} <member>`"
                )
            except:
                pass
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}")


    async def _handle_duplicate(self, interaction: discord.Interaction, club_type: str):
        """Handle duplicate name - will ask for quota in modal callback"""
        # For competitive, we need to get quota from modal first
        # So we'll pass a flag to the modal
        modal = QuotaInputModal(
            self.club_name,
            self.api_data,
            self.club_data,
            self.requester_info,
            club_type=club_type,
            is_duplicate=True
        )
        await interaction.response.send_modal(modal)
    
    async def _handle_duplicate_sync(self, interaction: discord.Interaction, club_type: str, target_quota: int):
        """Handle duplicate name for casual (already deferred)"""
        try:
            requester = await client.fetch_user(self.requester_info['user_id'])
            await requester.send(
                f"⚠️ **Your club request has a duplicate name!**\n\n"
                f"**Club Name:** {self.club_name}\n"
                f"**Type:** {club_type.capitalize()}\n"
                f"**Quota:** {target_quota if club_type == 'competitive' else 'N/A (Casual)'}\n\n"
                f"**Conflict:** Another club named \"{self.club_name}\" already exists\n\n"
                f"**Please reply to this message** with a custom name.\n"
                f"Examples: \"{self.club_name} EU\", \"{self.club_name} 2025\"\n\n"
                f"⏱️ You have 5 minutes to reply."
            )
            
            # Store pending request with club_type and quota
            pending_requests[self.requester_info['user_id']] = {
                'club_data': self.club_data,
                'api_data': self.api_data,
                'requester_info': self.requester_info,
                'original_name': self.club_name,
                'club_type': club_type,
                'target_quota': target_quota,
                'awaiting_custom_name': True,
                'admin_channel_id': interaction.channel_id,
                'timestamp': time.time()
            }
            
            await interaction.followup.send(
                f"⏸️ **Duplicate detected**\n\n"
                f"**Club:** {self.club_name}\n"
                f"**Type:** {club_type.capitalize()}\n"
                f"**Requester:** <@{self.requester_info['user_id']}> has been asked for custom name\n\n"
                f"Waiting for response..."
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ **Cannot DM requester**\n"
                f"Ask <@{self.requester_info['user_id']}> to enable DMs."
            )


class QuotaInputModal(discord.ui.Modal, title="Set Daily Quota"):
    """Modal for admin to input daily quota for competitive clubs"""
    
    def __init__(self, club_name: str, api_data: dict, club_data: dict, requester_info: dict, club_type: str, is_duplicate: bool = False):
        super().__init__()
        self.club_name = club_name
        self.api_data = api_data
        self.club_data = club_data
        self.requester_info = requester_info
        self.club_type = club_type
        self.is_duplicate = is_duplicate
    
    quota_input = discord.ui.TextInput(
        label="Daily Quota (fans/day)",
        placeholder="5000",
        required=True,
        min_length=1,
        max_length=10
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle quota submission"""
        await interaction.response.defer()
        
        try:
            # Validate quota
            quota = int(self.quota_input.value)
            if quota <= 0:
                await interaction.followup.send(
                    "❌ Quota must be a positive number!",
                    ephemeral=True
                )
                return
            
            # If this is a duplicate case, DM requester and store settings
            if self.is_duplicate:
                try:
                    requester = await client.fetch_user(self.requester_info['user_id'])
                    await requester.send(
                        f"⚠️ **Your club request has a duplicate name!**\n\n"
                        f"**Club Name:** {self.club_name}\n"
                        f"**Type:** Competitive\n"
                        f"**Quota:** {quota:,} fans/day\n\n"
                        f"**Conflict:** Another club named \"{self.club_name}\" already exists\n\n"
                        f"**Please reply to this message** with a custom name.\n"
                        f"Examples: \"{self.club_name} EU\", \"{self.club_name} 2025\"\n\n"
                        f"⏱️ You have 5 minutes to reply."
                    )
                    
                    # Store pending request with club_type and quota
                    pending_requests[self.requester_info['user_id']] = {
                        'club_data': self.club_data,
                        'api_data': self.api_data,
                        'requester_info': self.requester_info,
                        'original_name': self.club_name,
                        'club_type': self.club_type,
                        'target_quota': quota,
                        'awaiting_custom_name': True,
                        'admin_channel_id': interaction.channel_id,
                        'timestamp': time.time()
                    }
                    
                    await interaction.followup.send(
                        f"⏸️ **Duplicate detected**\n\n"
                        f"**Club:** {self.club_name}\n"
                        f"**Type:** Competitive\n"
                        f"**Quota:** {quota:,} fans/day\n"
                        f"**Requester:** <@{self.requester_info['user_id']}> has been asked for custom name\n\n"
                        f"Waiting for response..."
                    )
                except discord.Forbidden:
                    await interaction.followup.send(
                        f"❌ **Cannot DM requester**\n"
                        f"Ask <@{self.requester_info['user_id']}> to enable DMs."
                    )
                return
            
        # Not duplicate - create club with specified quota
            await auto_create_club_from_api(
                self.club_name,
                self.api_data,
                self.club_data,
                interaction,
                club_type=self.club_type,
                target_quota=quota,
                requester_info=self.requester_info
            )
            
            await interaction.followup.send(
                f"✅ **Competitive club '{self.club_name}' created!**\n\n"
                f"Members imported: {len(self.api_data.get('members', []))}\n"
                f"Daily Quota: {quota:,} fans/day"
            )
            
            # Notify requester
            try:
                requester = await client.fetch_user(self.requester_info['user_id'])
                await requester.send(
                    f"✅ **Your club request was approved!**\n\n"
                    f"**Club Name:** {self.club_name}\n"
                    f"**Type:** Competitive\n"
                    f"**Daily Quota:** {quota:,} fans/day\n"
                    f"**Status:** Now tracked by the bot\n\n"
                    f"You can use:\n"
                    f"• `/leaderboard {self.club_name}`\n"
                    f"• `/stats {self.club_name} <member>`"
                )
            except:
                pass
            
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid quota! Please enter a valid number.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error creating club: {e}",
                ephemeral=True
            )


# ============================================================================
# HELPER FUNCTIONS FOR CLUB REQUESTS
# ============================================================================

async def send_club_request_to_admin(club_data: dict, api_data: dict, requester_info: dict, club_type: str, target_quota: int):
    """Send club request to admin channel for approval"""
    channel = await client.fetch_channel(REQUEST_CHANNEL_ID)
    
    embed = discord.Embed(
        title="🆕 New Club Data Request",
        description=f"A user has requested to add a club to the tracking system.",
        color=0x00D9FF
    )
    
    embed.add_field(
        name="📋 Club Information",
        value=(
            f"**Name:** {club_data['name']}\n"
            f"**Circle ID:** {club_data.get('circle_id', 'N/A')}\n"
            f"**Members:** {club_data.get('member_count', 'N/A')}/30\n"
            f"**Type:** {club_type.capitalize()}\n"
            f"**Daily Quota:** {target_quota:,} fans/day" if club_type == 'competitive' else f"**Type:** Casual (no quota)"
        ),
        inline=False
    )
    
    embed.add_field(
        name="👤 Requester",
        value=(
            f"**User:** {requester_info['user_name']}\n"
            f"**User ID:** `{requester_info['user_id']}`\n"
            f"**Server:** {requester_info.get('guild_name', 'DM')}"
        ),
        inline=False
    )
    
    # Preview member list (first 5)
    members = api_data.get('members', [])[:5]
    if members:
        member_list = "\n".join([
            f"• {m.get('trainer_name', 'Unknown')} (ID: `{m.get('viewer_id', 'N/A')}`)"
            for m in members
        ])
        embed.add_field(
            name="👥 Member Preview (First 5)",
            value=member_list,
            inline=False
        )
    
    view = AdminApprovalView(club_data, api_data, requester_info, club_type, target_quota)
    await channel.send(embed=embed, view=view)


async def auto_create_club_from_api(
    club_name: str, 
    api_data: dict, 
    club_data: dict, 
    interaction,
    club_type: str = "competitive",
    target_quota: int = 5000,
    requester_info: dict = None
):
    """Automatically create club from API data with Server_ID tracking"""
    # Generate sheet names
    data_sheet = f"{club_name}_Data"
    members_sheet = f"{club_name}_Members"
    
    # Get server_id from requester_info or interaction
    server_id = ""
    if requester_info and requester_info.get('guild_id'):
        server_id = str(requester_info['guild_id'])
    elif interaction and hasattr(interaction, 'guild') and interaction.guild:
        server_id = str(interaction.guild.id)
    
    # Create Members sheet (non-blocking)
    members_ws = await asyncio.to_thread(
        gs_manager.sh.add_worksheet,
        title=members_sheet,
        rows=100,
        cols=5
    )
    await asyncio.to_thread(members_ws.update, 'A1:B1', [['Trainer ID', 'Name']])
    
    # Extract and write members
    members = api_data.get('members', [])
    member_rows = [
        [str(m.get('viewer_id', '')), m.get('trainer_name', 'Unknown')]
        for m in members
    ]
    
    if member_rows:
        await asyncio.to_thread(
            members_ws.update,
            f'A2:B{len(member_rows)+1}',
            member_rows
        )
    
    # Create Data sheet (non-blocking)
    data_ws = await asyncio.to_thread(
        gs_manager.sh.add_worksheet,
        title=data_sheet,
        rows=1000,
        cols=10
    )
    await asyncio.to_thread(data_ws.update, 'A1:F1', [[
        'Name', 'Day', 'Total Fans', 'Daily', 'Target', 'CarryOver'
    ]])
    
    # Get club URL from circle_id
    circle_id = club_data.get('circle_id', '')
    club_url = f"https://chronogenesis.net/club_profile?circle_id={circle_id}"
    
    # Add to Clubs_Config with provided settings + Club_ID + Server_ID for auto-sync (non-blocking)
    config_ws = await asyncio.to_thread(gs_manager.sh.worksheet, config.CONFIG_SHEET_NAME)
    await asyncio.to_thread(config_ws.append_row, [
        club_name,           # Column A: Club_Name
        data_sheet,          # Column B: Data_Sheet_Name
        members_sheet,       # Column C: Members_Sheet_Name
        target_quota,        # Column D: Target_Per_Day
        club_url,            # Column E: Club_URL
        club_type,           # Column F: Club_Type
        circle_id,           # Column G: Club_ID (for auto-sync)
        "",                  # Column H: Leaders
        "",                  # Column I: Officers
        server_id            # Column J: Server_ID (from requester)
    ])
    
    # Update caches
    await client.update_caches()
    
    print(f"✅ Auto-created club: {club_name} ({club_type}, quota: {target_quota}) with {len(member_rows)} members, Server: {server_id}")


# ============================================================================
# SYSTEM COMMANDS
# ============================================================================

@client.tree.command(name="status", description="Checks the bot's status and latency.")
async def status(interaction: discord.Interaction):
    """Show bot status with database connections"""
    latency = round(client.latency * 1000)
    
    cache_status = "✅ Active" if client.config_cache and client.member_cache else "⚠️ Empty"
    
    # Check Supabase status
    supabase_status = "❌ Disabled"
    if USE_SUPABASE and supabase_db:
        try:
            # Quick test query
            clubs = supabase_db.get_all_clubs()
            supabase_status = f"✅ Connected ({len(clubs)} clubs)"
        except:
            supabase_status = "⚠️ Error"
    
    # Check Google Sheets status
    gsheets_status = "✅ Connected"
    try:
        gs_manager.sh.worksheet(config.CONFIG_SHEET_NAME)
    except:
        gsheets_status = "⚠️ Disconnected (Using Cache)"
    
    # Check hybrid failover state
    hybrid_info = ""
    if hybrid_db:
        if hybrid_db.sheets_available:
            hybrid_info = "\n**Failover:** ✅ Sheets Active"
        else:
            retry_in = int(hybrid_db.retry_interval - 
                        (datetime.now() - hybrid_db.last_sheets_failure).total_seconds())
            if retry_in > 0:
                hybrid_info = f"\n**Failover:** 🔄 Using Supabase (retry in {retry_in}s)"
            else:
                hybrid_info = "\n**Failover:** 🔄 Using Supabase (retrying...)"
    
    # Determine primary database
    db_mode = "🔄 Hybrid (Auto-Failover)" if hybrid_db else ("🚀 Supabase" if USE_SUPABASE else "📊 Google Sheets")
    
    status_message = (
        f"🏓 **Pong!**\n"
        f"**Latency:** {latency}ms\n"
        f"**Database Mode:** {db_mode}{hybrid_info}\n"
        f"**Supabase:** {supabase_status}\n"
        f"**GSheets:** {gsheets_status}\n"
        f"**Cache Status:** {cache_status}\n"
        f"**Clubs Loaded:** {len(client.config_cache)}"
    )
    
    await interaction.response.send_message(status_message, ephemeral=True)


@client.tree.command(name="uptime", description="Shows how long the bot has been online.")
async def uptime(interaction: discord.Interaction):
    """Show bot uptime"""
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = now - client.start_time
    total_seconds = int(delta.total_seconds())
    
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    
    uptime_string = (
        f"**{days}** days, **{hours}** hours, "
        f"**{minutes}** minutes, **{seconds}** seconds"
    )
    
    await interaction.response.send_message(
        f"Bot has been online for:\n{uptime_string}",
        ephemeral=True
    )

# ============================================================================
# DATA LOADING HELPER
# ============================================================================

async def _load_data_for_command(club_name: str, data_sheet_name: str) -> Tuple[pd.DataFrame, Optional[str]]:
    """Load data from Google Sheets FIRST with enhanced retry, cache as fallback only
    
    NEW BEHAVIOR (Google Sheets-First):
    1. Try Google Sheets with 5 retries + exponential backoff
    2. Only use cache if ALL Google Sheets attempts fail
    3. Clear error messages for troubleshooting
    """
    cache_warning = None
    cache_key = f"{club_name}_{data_sheet_name}"
    
    # ===== TRY GOOGLE SHEETS FIRST WITH ENHANCED RETRY =====
    max_retries = 5  # Increased from 3 to 5
    retry_delay = 1  # Base delay in seconds
    last_error = None
    df = None
    
    print(f"📡 Attempting to load {club_name} from Google Sheets...")
    
    for attempt in range(max_retries):
        try:
            # Run blocking gspread calls in thread pool to avoid blocking event loop
            ws = await asyncio.to_thread(gs_manager.sh.worksheet, data_sheet_name)
            data = await asyncio.to_thread(ws.get_all_values)
            
            # Check if first row is the === CURRENT === header and skip it
            header_row_idx = 0
            if data and data[0] and data[0][0] and '=== CURRENT' in data[0][0]:
                header_row_idx = 1  # Skip CURRENT header row
            
            headers = data[header_row_idx] if len(data) > header_row_idx else []
            rows = data[header_row_idx + 1:] if len(data) > header_row_idx + 1 else []
            
            # Check if sheet is empty AFTER successful fetch
            if not data or len(rows) < 1:
                raise ValueError(
                    f"Sheet '{data_sheet_name}' is empty.\n"
                    f"This club has no data yet. Please wait for the bot to update the data (usually at 7AM UTC or 7PM UTC)."
                )
            
            df = pd.DataFrame(rows, columns=headers)
            print(f"✅ Successfully loaded {club_name} from Google Sheets (attempt {attempt + 1})")
            break  # Success - exit retry loop
            
        except ValueError as ve:
            # Empty sheet - re-raise ValueError (not a connection issue)
            raise ve
        
        except Exception as e:
            last_error = e
            # Check if retryable error
            if is_retryable_error(e):
                if attempt < max_retries - 1:
                    # Exponential backoff with jitter to avoid thundering herd
                    base_wait = retry_delay * (2 ** attempt)
                    jitter = random.uniform(0, 1)  # Add 0-1 second randomness
                    wait_time = base_wait + jitter
                    
                    print(f"⚠️ GSheets connection error for {club_name} (attempt {attempt + 1}/{max_retries})")
                    print(f"   Error: {str(e)[:100]}")
                    print(f"   ⏳ Retrying in {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # All retries exhausted
                    print(f"❌ All {max_retries} Google Sheets retries failed for {club_name}")
            else:
                # Non-retryable error - raise immediately
                print(f"❌ Non-retryable error for {club_name}: {e}")
                raise e
    
    # ===== FALLBACK TO CACHE IF ALL GSHEETS RETRIES FAILED =====
    if df is None and last_error and is_retryable_error(last_error):
        print(f"🔄 Attempting cache fallback for {club_name}...")
        
        # Try SmartCache first (in-memory + disk)
        cached_result = smart_cache.get(cache_key)
        if cached_result is not None:
            df, cache_timestamp = cached_result
            cache_age_hours = (time.time() - cache_timestamp) / 3600
            cache_warning = (
                f"⚠️ **Google Sheets không khả dụng**\n"
                f"Hiển thị dữ liệu từ cache (cập nhật {cache_age_hours:.1f} giờ trước)\n\n"
            )
            print(f"✅ Using SmartCache for {club_name} (age: {cache_age_hours:.1f} hours)")
        else:
            # Try old legacy cache as last resort
            cache_file_path = os.path.join(DATA_CACHE_DIR, f"{club_name}_Data.json")
            try:
                with open(cache_file_path, "r") as f:
                    cache_data = json.load(f)
                
                df = pd.read_json(StringIO(cache_data["dataframe_json"]), orient="records")
                cache_timestamp = cache_data.get("timestamp", 0)
                cache_warning = f"⚠️ **Google Sheets không khả dụng.** Hiển thị dữ liệu cache cũ từ <t:{cache_timestamp}:R>.\n\n"
                print(f"✅ Using legacy cache for {club_name}")
            
            except FileNotFoundError:
                raise Exception(
                    f"❌ Không thể kết nối Google Sheets sau {max_retries} lần thử VÀ không tìm thấy cache cho {club_name}.\n\n"
                    f"**Nguyên nhân có thể:**\n"
                    f"• Mất kết nối internet\n"
                    f"• Google Sheets API đang bảo trì\n"
                    f"• Chưa có dữ liệu nào được tải về\n\n"
                    f"**Giải pháp:**\n"
                    f"• Kiểm tra kết nối mạng\n"
                    f"• Thử lại sau vài phút"
                )
            except Exception as cache_e:
                raise Exception(
                    f"❌ Google Sheets failed sau {max_retries} retries VÀ cache bị lỗi cho {club_name}: {cache_e}"
                )
    
    # If still no data, raise error
    if df is None:
        raise Exception(f"❌ Không thể tải dữ liệu cho {club_name} từ bất kỳ nguồn nào.")
    
    # ===== PROCESS DATA =====
    try:
        # Only process fresh data from Google Sheets (not cached data)
        if cache_warning is None:
            cols_to_numeric = ['Day', 'Total Fans', 'Daily', 'Target', 'CarryOver', 'Name']
            for col in cols_to_numeric:
                if col not in df.columns:
                    raise Exception(f"Missing required column '{col}' in sheet '{data_sheet_name}'.")
                if col != 'Name':
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df = df.dropna(subset=['Day', 'Total Fans', 'Daily', 'Target', 'CarryOver'])
            df['Day'] = df['Day'].astype(int)
            df['Total Fans'] = df['Total Fans'].astype(int)
            df['Daily'] = df['Daily'].astype(int)
            df['Target'] = df['Target'].astype(int)
            df['CarryOver'] = df['CarryOver'].astype(int)
        
        # Calculate behind status (for both fresh and cached data)
        df.sort_values(by=['Name', 'Day'], inplace=True)
        df['is_slightly_behind'] = (df['CarryOver'] < 0) & (df['CarryOver'] >= -700_000)
        
        s = df['is_slightly_behind']
        consecutive_groups = (df['Name'] != df['Name'].shift()) | (s != s.shift())
        group_ids = consecutive_groups.cumsum()
        consecutive_count = s.groupby(group_ids).cumsum()
        df['consecutive_slight_behind_days'] = consecutive_count.where(s, 0)
        
        is_severely_behind = (df['CarryOver'] < -700_000)
        is_chronically_behind = (df['is_slightly_behind'] == True) & (df['consecutive_slight_behind_days'] > 5)
        df['is_behind'] = is_severely_behind | is_chronically_behind
        
        if df.empty:
            raise pd.errors.EmptyDataError(f"No valid numeric data found in '{data_sheet_name}'.")
        
        # ===== STORE IN CACHE (only if data is from Google Sheets, not from cache) =====
        if cache_warning is None:
            smart_cache.set(cache_key, df)
            print(f"💾 Cached fresh data for {club_name}")
        
        return df, cache_warning
    
    except Exception as process_e:
        print(f"❌ Error processing DataFrame for {club_name}: {process_e}")
        raise process_e


# ============================================================================
# HELPER FUNCTION: FETCH CLUB DATA FROM API
# ============================================================================

async def fetch_club_data_from_api(trainer_id: str, max_retries: int = 3, use_proxy: bool = True) -> dict:
    """
    Fetch club data from uma.moe API with retry logic and proxy rotation
    
    Args:
        trainer_id: The club/circle ID to fetch
        max_retries: Number of retry attempts for transient errors
        use_proxy: Whether to use rotating proxies (default: True)
    
    Returns: dict with club data or None if not found
    """
    url = f"https://uma.moe/api/v4/circles?circle_id={trainer_id}"
    
    for attempt in range(max_retries):
        try:
            # Get next proxy from rotation
            proxy_url = proxy_manager.get_next_proxy() if use_proxy else None
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, 
                    timeout=aiohttp.ClientTimeout(total=15),
                    proxy=proxy_url
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data
                    elif response.status == 404:
                        return None
                    # Check for retryable HTTP errors (502, 503, 504)
                    elif response.status in [502, 503, 504]:
                        if attempt + 1 < max_retries:
                            wait_time = 2 * (2 ** attempt)  # 2s, 4s, 8s (faster with proxies)
                            print(f"⚠️ API returned {response.status}. Retrying with different proxy in {wait_time}s... ({attempt + 1}/{max_retries})")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise Exception(f"API returned status {response.status} after {max_retries} attempts")
                    else:
                        raise Exception(f"API returned status {response.status}")
                        
        except asyncio.TimeoutError:
            if attempt + 1 < max_retries:
                wait_time = 2 * (2 ** attempt)
                print(f"⚠️ Timeout. Retrying with different proxy in {wait_time}s... ({attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                continue
            else:
                raise Exception(f"Request timeout after {max_retries} attempts")
                
        except aiohttp.ClientError as e:
            error_str = str(e).lower()
            if any(kw in error_str for kw in ['502', '503', '504', 'server error', 'bad gateway', 'proxy']):
                if attempt + 1 < max_retries:
                    wait_time = 2 * (2 ** attempt)
                    print(f"⚠️ Network error: {e}. Retrying with different proxy in {wait_time}s... ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                    continue
            raise Exception(f"Network error: {e}")
            
        except Exception as e:
            if "API returned status" in str(e):
                raise  # Don't wrap API errors
            raise Exception(f"Failed to fetch data: {e}")
    
    return None


# ============================================================================
# SEARCH CLUB VIEW (WITH BUTTONS)
# ============================================================================

class SearchClubView(discord.ui.View):
    """View with buttons for search club results"""
    
    def __init__(self, club_name: str, member_name: str, timeout=300):
        super().__init__(timeout=timeout)
        self.club_name = club_name
        self.member_name = member_name
        
        # Setup buttons
        self.leaderboard_btn = discord.ui.Button(
            label="📊 View Leaderboard",
            style=discord.ButtonStyle.primary,
            custom_id="view_leaderboard"
        )
        self.stats_btn = discord.ui.Button(
            label="📈 Member Stats",
            style=discord.ButtonStyle.primary,
            custom_id="view_stats"
        )
        
        self.leaderboard_btn.callback = self.show_leaderboard
        self.stats_btn.callback = self.show_stats
        
        self.add_item(self.leaderboard_btn)
        self.add_item(self.stats_btn)
    
    async def show_leaderboard(self, interaction: discord.Interaction):
        """Show leaderboard for the club"""
        await interaction.response.defer()
        
        # Check if club exists in our system
        club_config = client.config_cache.get(self.club_name)
        if not club_config:
            await interaction.followup.send(
                f"⚠️ Club '{self.club_name}' is not configured in this bot.\n"
                f"Contact an admin to add this club using `/club_setup`.",
                ephemeral=True
            )
            return
        
        # Call leaderboard command logic
        try:
            data_sheet_name = club_config.get('Data_Sheet_Name')
            df, cache_warning = await _load_data_for_command(self.club_name, data_sheet_name)
            
            max_day = df['Day'].max()
            df_latest = df[df['Day'] == max_day].copy()
            
            df_behind_quota = df_latest[df_latest['is_behind'] == True].copy()
            df_above_quota = df_latest[df_latest['is_behind'] == False].copy()
            
            df_above_quota.sort_values(by='Total Fans', ascending=False, inplace=True)
            df_behind_quota.sort_values(by='Total Fans', ascending=False, inplace=True)
            
            # Build leaderboard display (same as leaderboard command)
            YELLOW = '\u001b[33m'
            RESET = '\u001b[0m'
            VERT = '|'
            HORZ = '-'
            CROSS = '+'
            
            # ===== DYNAMIC NAME PADDING =====
            # Calculate the longest name in the player list for proper alignment
            all_players = pd.concat([df_above_quota, df_behind_quota])
            max_name_length = max(len(str(player['Name'])) for _, player in all_players.iterrows()) if not all_players.empty else 10
            max_name_length = min(max_name_length + 2, 25)  # Add 2 spaces padding, cap at 25
            name_col_width = max_name_length + 2  # Account for spaces around name
            
            header_display = f" # {VERT} {'Name':<{max_name_length}} {VERT} Daily {VERT}  Carry  {VERT} Target {VERT} Total"
            separator = f"{HORZ*3}{CROSS}{HORZ*name_col_width}{CROSS}{HORZ*7}{CROSS}{HORZ*9}{CROSS}{HORZ*8}{CROSS}{HORZ*6}"
            
            divider_text = "(Players Behind Quota)"
            padding = (len(separator) - len(divider_text)) // 2
            divider_line = f"{HORZ*padding}{divider_text}{HORZ*(len(separator) - len(divider_text) - padding)}"
            
            body = []
            
            def format_player_line(player, rank, kick_note):
                is_behind = player.get('is_behind', False)
                color = YELLOW if is_behind else ""
                reset = RESET if is_behind else ""
                
                player_name = player['Name'][:max_name_length].ljust(max_name_length)
                rank_str = f"{rank:>2}."
                
                daily = format_fans(player['Daily']).rjust(6)
                carry = format_fans(player['CarryOver']).rjust(8)
                target = format_fans(player['Target']).replace('+', '').rjust(7)
                total = format_fans(player['Total Fans']).replace('+', '').rjust(6)
                
                line = f"{color}{rank_str}{VERT} {player_name} {VERT}{daily} {VERT}{carry} {VERT}{target} {VERT}{total}{reset}"
                
                if kick_note:
                    line += f"\n{color}   {kick_note}{reset}"
                
                return line
            
            rank_counter = 1
            
            for _, player in df_above_quota.head(30).iterrows():
                kick_note = get_kick_note(player, max_day)
                body.append(format_player_line(player, rank_counter, kick_note))
                rank_counter += 1
            
            if not df_behind_quota.empty:
                body.append(divider_line)
            
            remaining_slots = 30 - len(df_above_quota)
            if remaining_slots > 0:
                for _, player in df_behind_quota.head(remaining_slots).iterrows():
                    if rank_counter > 30:
                        break
                    kick_note = get_kick_note(player, max_day)
                    body.append(format_player_line(player, rank_counter, kick_note))
                    rank_counter += 1
            
            current_timestamp = get_last_update_timestamp()
            message_content = f"Data retrieved from Chronogenesis <t:{current_timestamp}:f>\n"
            if cache_warning:
                message_content = cache_warning + message_content
            message_content += "```ansi\n" + f"{header_display}\n{separator}\n" + "\n".join(body) + "\n```"
            
            embed = discord.Embed(
                title=f"🏆 Leaderboard (Club: {self.club_name} - Day {max_day})",
                description=message_content,
                color=discord.Color.purple()
            )
            
            club_daily_quota = club_config.get('Target_Per_Day', 0)
            footer_text = f"Daily Quota: {format_fans(club_daily_quota).replace('+', '')}"
            embed.set_footer(text=footer_text)
            
            view = LeaderboardView(original_embed=embed, club_name=self.club_name, full_df=df, max_day=max_day)
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error loading leaderboard: {e}",
                ephemeral=True
            )
    
    async def show_stats(self, interaction: discord.Interaction):
        """Show stats for the member"""
        await interaction.response.defer()
        
        # Check if club exists in our system
        club_config = client.config_cache.get(self.club_name)
        if not club_config:
            await interaction.followup.send(
                f"⚠️ Club '{self.club_name}' is not configured in this bot.",
                ephemeral=True
            )
            return
        
        # Check if member exists in our system
        member_list = client.member_cache.get(self.club_name, [])
        found_member = None
        
        for m_name in member_list:
            if m_name.casefold() == self.member_name.casefold():
                found_member = m_name
                break
        
        if not found_member:
            await interaction.followup.send(
                f"⚠️ Member '{self.member_name}' is not tracked in our system.\n"
                f"They may be in the club but not added to our database yet.",
                ephemeral=True
            )
            return
        
        # Call stats command logic
        try:
            data_sheet_name = club_config.get('Data_Sheet_Name')
            df, cache_warning = await _load_data_for_command(self.club_name, data_sheet_name)
            
            # Strip whitespace for matching (handles trailing spaces like "王牌 " vs "王牌")
            df['Name_stripped'] = df['Name'].str.strip()
            df_member = df[df['Name_stripped'] == found_member.strip()].copy()
            
            if df_member.empty:
                # Try case-insensitive match as fallback
                df_member = df[df['Name_stripped'].str.lower() == found_member.strip().lower()].copy()
            
            if df_member.empty:
                await interaction.followup.send(
                    f"No data found for '{found_member}'.",
                    ephemeral=True
                )
                return
            
            df_member.sort_values(by='Day', inplace=True)
            
            view = StatsView(
                member_name=found_member,
                club_name=self.club_name,
                df_member=df_member,
                club_config=club_config
            )
            
            embed = view._create_overview_embed()
            view._update_buttons()
            
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error loading stats: {e}",
                ephemeral=True
            )


# ============================================================================
# REDESIGNED SEARCH CLUB COMMAND - USING UMA.MOE API
# ============================================================================

# NOTE: fetch_club_data_from_api is defined above with proxy support

@client.tree.command(
    name="search_club",
    description="Search for club information by member's Trainer ID"
)
@app_commands.describe(
    trainer_id="The Trainer ID to search for"
)
async def search_club(interaction: discord.Interaction, trainer_id: str):
    """Search club by trainer ID using uma.moe API - REDESIGNED"""
    await interaction.response.defer()
    
    # Validate trainer_id format
    trainer_id = trainer_id.strip()
    if not trainer_id.isdigit():
        await interaction.followup.send(
            "❌ Invalid Trainer ID format. Please enter numbers only.",
            ephemeral=True
        )
        return
    
    try:
        # Fetch data from API
        api_data = await fetch_club_data_from_api(trainer_id)
        
        if not api_data or 'circle' not in api_data:
            # Not found
            embed = discord.Embed(
                title="🔍 Club Search Results",
                description=(
                    f"**Trainer ID:** `{trainer_id}`\n\n"
                    f"❌ **NO CLUB FOUND**\n\n"
                    f"This trainer is not currently a member of any club.\n\n"
                    f"**Possible reasons:**\n"
                    f"• Trainer ID does not exist\n"
                    f"• Trainer is not in a club\n"
                    f"• API connection error\n\n"
                    f"💡 **TIP:** Double-check the Trainer ID and try again."
                ),
                color=0xFF6B6B  # Red
            )
            
            await interaction.followup.send(embed=embed)
            return
        
        # ============================================================
        # PARSE API DATA
        # ============================================================
        
        circle_data = api_data['circle']
        members_data = api_data.get('members', [])
        
        # Basic Club Info
        club_name = circle_data['name']
        club_id = circle_data['circle_id']
        leader_name = circle_data['leader_name']
        member_count = circle_data['member_count']
        
        # Join & Policy Settings
        join_style = circle_data['join_style']
        policy = circle_data['policy']
        created_at = circle_data['created_at']
        
        # Rankings - use 'or' to handle None values from API
        monthly_rank = circle_data.get('monthly_rank') or 'N/A'
        monthly_point = circle_data.get('monthly_point', 0) or 0
        last_month_rank = circle_data.get('last_month_rank') or 'N/A'
        last_month_point = circle_data.get('last_month_point', 0) or 0
        
        # Find member data
        member_info = None
        member_name = "Unknown"
        
        for member in members_data:
            if str(member['viewer_id']) == trainer_id:
                member_info = member
                member_name = member['trainer_name']
                break
        
        # ============================================================
        # BUILD EMBED
        # ============================================================
        
        embed = discord.Embed(
            title=f"🏆 {club_name}",
            description=f"Club ID: `{club_id}`",
            color=0x00D9FF  # Cyan
        )
        
        # Set thumbnail (if available)
        # Note: uma.moe API might return club icon URL - adjust if available
        
        # ===== SECTION 1: BASIC INFO =====
        basic_info = (
            f"**👤 Searched Member:** {member_name}\n"
            f"**🆔 Trainer ID:** `{trainer_id}`\n"
            f"**👑 Leader:** {leader_name}\n"
            f"**👥 Members:** {member_count}/30"
        )
        embed.add_field(name="📋 Basic Information", value=basic_info, inline=False)
        
        # ===== SECTION 2: CLUB SETTINGS =====
        join_style_map = {
            1: "🌐 Open to All",
            2: "📝 Application Required",
            3: "🔒 Invite Only"
        }
        join_style_text = join_style_map.get(join_style, "❓ Unknown")
        
        policy_map = {
            1: "😊 Casual",
            2: "⚖️ Moderate",
            3: "📢 Active Recruitment",
            4: "🔥 Competitive"
        }
        policy_text = policy_map.get(policy, "❓ Unknown")
        
        # Format created date
        try:
            from datetime import datetime
            created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            created_str = created_date.strftime("%B %d, %Y")
        except:
            created_str = "Unknown"
        
        settings_info = (
            f"**🚪 Join Style:** {join_style_text}\n"
            f"**🎯 Policy:** {policy_text}\n"
            f"**📅 Created:** {created_str}"
        )
        embed.add_field(name="⚙️ Club Settings", value=settings_info, inline=False)
        
        # ===== SECTION 3: RANKINGS =====
        current_rank_str = f"#{monthly_rank:,}" if monthly_rank != 'N/A' else "Unranked"
        current_points_str = format_fans_billion(monthly_point) if monthly_point else "0B"
        
        last_rank_str = f"#{last_month_rank:,}" if last_month_rank != 'N/A' else "Unranked"
        last_points_str = format_fans_billion(last_month_point) if last_month_point else "0B"
        
        # Calculate rank change
        rank_change_text = ""
        if monthly_rank != 'N/A' and last_month_rank != 'N/A':
            rank_diff = last_month_rank - monthly_rank
            if rank_diff > 0:
                rank_change_text = f"**📈 Trend:** +{rank_diff:,} (Improving)"
            elif rank_diff < 0:
                rank_change_text = f"**📉 Trend:** {rank_diff:,} (Declining)"
            else:
                rank_change_text = f"**➡️ Trend:** No change"
        
        rankings_info = (
            f"**🏅 Current Month:** {current_rank_str}\n"
            f"**💎 Points:** {current_points_str}\n"
            f"**📊 Last Month:** {last_rank_str} ({last_points_str})\n"
            f"{rank_change_text}"
        )
        embed.add_field(name="🏆 Club Rankings", value=rankings_info, inline=False)
        
        # ===== SECTION 4: MEMBER PERFORMANCE =====
        if member_info:
            cumulative_fans = member_info.get('daily_fans', [])
            month = member_info.get('month', 'N/A')
            
            # Calculate ACTUAL daily fans (cumulative → daily differences)
            daily_fans = calculate_daily_from_cumulative(cumulative_fans)
            
            # Get non-zero days with both cumulative and daily
            non_zero_days = [
                (i+1, cumulative_fans[i], daily_fans[i]) 
                for i in range(len(daily_fans)) 
                if daily_fans[i] > 0
            ]
            
            # Calculate totals
            total_daily_fans = sum(daily_fans)
            active_days = len(non_zero_days)
            avg_daily = total_daily_fans / active_days if active_days > 0 else 0
            
            # Get last 7 days with data (or all if less than 7)
            recent_days = non_zero_days[-7:] if len(non_zero_days) >= 7 else non_zero_days
            
            # Build compact performance text (max 56 chars/line)
            lines = []
            lines.append("━" * 48)
            lines.append("📊  MEMBER PERFORMANCE")
            lines.append("━" * 48)
            lines.append("")
            
            lines.append(f"Month:         {month}")
            lines.append(f"Total Fans:    {format_fans(total_daily_fans).replace('+', '')}")
            lines.append(f"Active Days:   {active_days}")
            lines.append(f"Daily Avg:     {format_fans(avg_daily).replace('+', '')}")
            
            if recent_days:
                lines.append("")
                lines.append("━" * 48)
                lines.append(f"📈  RECENT ACTIVITY (Last {len(recent_days)} Days)")
                lines.append("━" * 48)
                
                for day, cumulative, daily in recent_days:
                    day_str = f"Day {day:02d}"
                    
                    # Format daily fans
                    if daily >= cumulative and cumulative > 0:
                        # This is the first non-zero day
                        daily_display = f"{format_fans(cumulative).replace('+', '')} (first)"
                    else:
                        daily_display = format_fans(daily).replace('+', '')
                    
                    # Compact format: "Day 16 ▸ 238.6M (first)"
                    lines.append(f"{day_str} ▸ {daily_display}")
            
            performance_text = "\n".join(lines)
            embed.add_field(name="👤 Member Performance", value=f"```\n{performance_text}\n```", inline=False)
        
        # ===== FOOTER & LINKS =====
        club_url = f"https://chronogenesis.net/club_profile?circle_id={club_id}"
        embed.add_field(
            name="🔗 Quick Links",
            value=f"[📱 View Club Profile]({club_url})",
            inline=False
        )
        
        # Timestamp
        import pytz
        from datetime import datetime
        vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
        now = datetime.now(vietnam_tz)
        timestamp_display = now.strftime("%b %d, %Y • %I:%M %p %Z")
        
        embed.set_footer(
            text=f"🕐 {timestamp_display}",
            icon_url="https://uma.moe/favicon.ico"
        )
        
        # ============================================================
        # CHECK IF CLUB IS TRACKED & ADD BUTTONS
        # ============================================================
        
        if club_name in client.config_cache:
            view = SearchClubView(club_name=club_name, member_name=member_name)
            embed.description += "\n\n✅ **This club is tracked in our system!**"
            await interaction.followup.send(embed=embed, view=view)
        else:
            # Club not tracked - show request button
            embed.description += "\n\n⚠️ **This club is not tracked**\n\nClick below to request adding this club to the bot."
            
            view = ClubRequestView(
                club_data={
                    'name': club_name,
                    'circle_id': api_data.get('circle', {}).get('circle_id', ''),
                    'member_count': len(api_data.get('members', []))
                },
                api_data=api_data,
                requester_info={
                    'user_id': interaction.user.id,
                    'user_name': str(interaction.user),
                    'guild_id': interaction.guild.id if interaction.guild else None,
                    'guild_name': interaction.guild.name if interaction.guild else 'DM'
                }
            )
            await interaction.followup.send(embed=embed, view=view)
    
    except Exception as e:
        print(f"Error in /search_club: {e}")
        import traceback
        traceback.print_exc()
        
        # Friendly error messages
        error_message = str(e)
        if "timeout" in error_message.lower():
            error_msg = "⏱️ **API Timeout**\n\nThe uma.moe API took too long to respond. Please try again in a moment."
        elif "network" in error_message.lower() or "connection" in error_message.lower():
            error_msg = "🌐 **Network Error**\n\nCouldn't connect to uma.moe API. Check your internet connection and try again."
        elif "404" in error_message:
            error_msg = f"❌ **Not Found**\n\nTrainer ID `{trainer_id}` was not found in the database."
        else:
            error_msg = f"❌ **Error Occurred**\n\n```{error_message}```"
        
        await interaction.followup.send(error_msg, ephemeral=True)

# ============================================================================
# LEADERBOARD COMMAND & VIEW
# ============================================================================

class LeaderboardView(discord.ui.View):
    """View with buttons for leaderboard"""
    
    def __init__(self, original_embed: discord.Embed, club_name: str, full_df: pd.DataFrame, max_day: int, timeout=300):
        super().__init__(timeout=timeout)
        self.original_embed = original_embed
        self.club_name = club_name
        self.full_df = full_df
        self.max_day = max_day
        
        self.summary_button = discord.ui.Button(
            label="📊 Show Summary",
            style=discord.ButtonStyle.primary,
            custom_id="show_summary"
        )
        self.global_lb_button = discord.ui.Button(
            label="🌍 Global Leaderboard",
            style=discord.ButtonStyle.success,
            custom_id="show_global_lb"
        )
        self.back_button = discord.ui.Button(
            label="⬅️ Back to Leaderboard",
            style=discord.ButtonStyle.secondary,
            custom_id="show_leaderboard"
        )
        
        self.summary_button.callback = self.show_summary
        self.global_lb_button.callback = self.show_global_leaderboard
        self.back_button.callback = self.show_leaderboard
        self.add_item(self.summary_button)
        self.add_item(self.global_lb_button)
    
    async def show_leaderboard(self, interaction: discord.Interaction):
        """Show leaderboard embed"""
        await interaction.response.defer()
        self.clear_items()
        self.add_item(self.summary_button)
        await interaction.edit_original_response(embed=self.original_embed, view=self)
    
    async def show_summary(self, interaction: discord.Interaction):
        """Show summary embed - COMPACT DESIGN"""
        await interaction.response.defer()
        
        try:
            df_latest = self.full_df[self.full_df['Day'] == self.max_day].copy()
            
            if df_latest.empty:
                await interaction.followup.send(
                    f"Error: No data found for Day {self.max_day}.",
                    ephemeral=True
                )
                return
            
            # ============== Calculate Statistics ==============
            
            above_target_count = (df_latest['CarryOver'] >= 0).sum()
            below_target_count = (df_latest['CarryOver'] < 0).sum()
            total_members = len(df_latest)
            above_pct = int((above_target_count / total_members * 100)) if total_members > 0 else 0
            below_pct = 100 - above_pct
            
            total_daily_fans = df_latest['Daily'].sum()
            average_daily_fans = df_latest['Daily'].mean()
            
            df_top_daily = df_latest.sort_values(by='Daily', ascending=False)
            top_3 = df_top_daily.head(3)
            
            overall_total_fans = df_latest['Total Fans'].sum()
            
            at_risk_count = 0
            try:
                if self.max_day > 10:
                    at_risk_count = ((df_latest['Total Fans'] == 0)).sum()
            except:
                pass
            
            club_config = client.config_cache.get(self.club_name, {})
            club_daily_quota = club_config.get('Target_Per_Day', 1)
            if club_daily_quota <= 0:
                club_daily_quota = 1
            
            quota_pct = int((total_daily_fans / (club_daily_quota * total_members) * 100)) if total_members > 0 else 0
            
            # ===== CALCULATE PROJECTED PROPERLY =====
            projected = club_daily_quota * total_members * 30
            
            # ============== Build Display ==============
            
            lines = []
            
            # Header
            lines.append("=" * 56)
            lines.append(center_text_exact("📊 PERFORMANCE SUMMARY", 56))
            lines.append(center_text_exact(f"Club: {self.club_name} • Day {self.max_day}", 56))
            
            # Timestamp with timezone
            current_timestamp = get_last_update_timestamp()
            from datetime import datetime
            import pytz
            
            utc_time = datetime.fromtimestamp(current_timestamp, tz=pytz.UTC)
            vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
            local_time = utc_time.astimezone(vietnam_tz)
            timestamp_display = local_time.strftime("%b %d, %Y %I:%M %p %Z")
            
            lines.append(center_text_exact(f"🕐 Updated: {timestamp_display}", 56))
            lines.append("=" * 56)
            
            # ===== TODAY'S RESULTS =====
            lines.append("")
            lines.append("📈 TODAY'S RESULTS")
            lines.append("─" * 56)
            
            lines.append(format_stat_line_compact("Total Gained:", format_fans(total_daily_fans).replace('+', ''), label_width=25))
            lines.append(format_stat_line_compact("Average/Member:", format_fans(average_daily_fans).replace('+', ''), label_width=25))
            
            if not top_3.empty:
                top_player = top_3.iloc[0]
                top_name = top_player['Name'][:20]
                top_gain = format_fans(top_player['Daily'])
                lines.append(format_stat_line_compact("Top Performer:", f"{top_name} ({top_gain})", label_width=25))
            
            lines.append(format_stat_line_compact("vs Quota:", f"+{quota_pct}%", label_width=25))
            lines.append("─" * 56)
            
            # ===== TOP PERFORMERS =====
            lines.append("")
            lines.append("🏆 TOP PERFORMERS")
            lines.append("─" * 56)

            # Header - định nghĩa vị trí cột Gain
            header = "Rank  Name               Gain"
            lines.append(header)
            lines.append("─" * 56)

            # Tính vị trí bắt đầu của cột Gain trong header
            gain_start_position = header.rfind("Gain")  # Tìm vị trí "Gain" trong header

            medals = ["🥇", "🥈", "🥉"]
            for i, (_, player) in enumerate(top_3.iterrows()):
                if i >= 3:
                    break
                
                medal = medals[i]
                name = player['Name'][:25]  # Giới hạn name
                gain = format_fans(player['Daily'])
                
                # Build phần bên trái (emoji + name)
                left_part = f" {medal}   {name}"
                
                # Tính display width thực tế của left_part
                left_width = wcswidth(left_part)
                if left_width == -1:  # Fallback nếu wcwidth fail
                    left_width = len(left_part)
                
                # Tính số spaces cần thiết để Gain bắt đầu đúng vị trí
                spaces_needed = gain_start_position - left_width - 3
                
                # Đảm bảo ít nhất 2 spaces
                if spaces_needed < 2:
                    spaces_needed = 2
                
                # Build line với Gain căn phải (width = 8)
                line = left_part + (' ' * spaces_needed) + f"{gain:>8}"
                lines.append(line)

            lines.append("─" * 56)
            
            # ===== PERFORMANCE BREAKDOWN =====
            lines.append("")
            lines.append("📊 PERFORMANCE BREAKDOWN")
            lines.append("─" * 56)
            
            lines.append(format_stat_line_compact("Above Target:", f"{above_target_count} members ({above_pct}%)", label_width=25))
            lines.append(format_stat_line_compact("Behind Target:", f"{below_target_count} members ({below_pct}%)", label_width=25))
            lines.append("")
            lines.append(format_stat_line_compact("Performance Ratio:", f"{above_pct}:{below_pct}", label_width=25))
            
            if at_risk_count > 0:
                lines.append(format_stat_line_compact("At-Risk:", f"{at_risk_count} need attention", label_width=25))
            
            lines.append("─" * 56)
            
            # ===== OVERALL PROGRESS =====
            lines.append("")
            lines.append("🎯 OVERALL PROGRESS")
            lines.append("─" * 56)

            lines.append(format_stat_line_compact("Total Fans:", format_fans_billion(overall_total_fans), label_width=25))

            progress_pct = int((overall_total_fans / projected * 100)) if projected > 0 else 0
            progress_text = f"{progress_pct}% • Day {self.max_day}/30"
            lines.append(format_stat_line_compact("Progress:", progress_text, label_width=25))

            proj_text = f"{format_fans_billion(projected)} (target)"
            lines.append(format_stat_line_compact("Projected:", proj_text, label_width=25))

            if progress_pct >= 100:
                exceeded_pct = progress_pct - 100
                remaining_text = f"Target exceeded! +{exceeded_pct}%"
            else:
                fans_remaining = projected - overall_total_fans
                remaining_pct = 100 - progress_pct
                remaining_text = f"{format_fans_billion(fans_remaining)} ({remaining_pct}%)"

            lines.append(format_stat_line_compact("Remaining:", remaining_text, label_width=25))

            lines.append("─" * 56)
            
            # ===== KEY INSIGHTS =====
            lines.append("")
            lines.append("💡 KEY INSIGHTS")
            lines.append("=" * 56)
            
            performance_vs_quota = quota_pct - 100
            if performance_vs_quota > 0:
                lines.append(f"• Performance {performance_vs_quota}% above quota")
            else:
                lines.append(f"• Performance {abs(performance_vs_quota)}% below quota")
            
            if at_risk_count > 0:
                lines.append(f"• {at_risk_count} members need intervention")
            
            if len(df_top_daily) >= 10:
                top_10_total = df_top_daily.head(10)['Daily'].sum()
                top_10_pct = int((top_10_total / total_daily_fans * 100)) if total_daily_fans > 0 else 0
                lines.append(f"• Top 10 contribute {top_10_pct}% of gains")
            
            current_pace = overall_total_fans
            expected_pace = (club_daily_quota * total_members * self.max_day)
            pace_pct = int((current_pace / expected_pace * 100)) if expected_pace > 0 else 0
            
            if pace_pct >= 95:
                lines.append(f"• On track to meet {format_fans_billion(projected)} target")
            elif pace_pct >= 80:
                lines.append(f"• Slightly behind pace ({pace_pct}% of target)")
            else:
                lines.append(f"• Behind pace ({pace_pct}% of target)")
            
            lines.append("=" * 56)
            
            # ============== Create Embed ==============
            
            message_content = "```\n" + "\n".join(lines) + "\n```"
            
            summary_embed = discord.Embed(
                title=f"📊 Performance Dashboard",
                description=message_content,
                color=0x00A9FF
            )
            
            footer_text = f"Daily Quota: {format_fans(club_daily_quota).replace('+', '')}/member"
            summary_embed.set_footer(text=footer_text)
            
            self.clear_items()
            self.add_item(self.back_button)
            await interaction.edit_original_response(embed=summary_embed, view=self)
        
        except Exception as e:
            print(f"Error in show_summary: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)
    
    async def show_global_leaderboard(self, interaction: discord.Interaction):
        """Show global leaderboard across all clubs (EMBED FORMAT)"""
        await interaction.response.defer()
        
        try:
            # Get all members (FAST - from cache)
            all_members = await get_all_members_global()
            
            if all_members:
                # Create global leaderboard view (5 members per page)
                # Pass self and original_embed for Return button functionality
                global_view = GlobalLeaderboardView(
                    all_members, 
                    members_per_page=5,
                    original_view=self,
                    original_embed=self.original_embed
                )
                embed = global_view.get_page_embed()
                
                # EDIT original message with embed
                await interaction.edit_original_response(content=None, embed=embed, view=global_view)
            else:
                await interaction.followup.send("⚠️ No member data available.", ephemeral=True)
        
        except Exception as e:
            print(f"Error in show_global_leaderboard: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


@client.tree.command(name="leaderboard", description="Shows the club leaderboard (latest day).")
@app_commands.autocomplete(club_name=club_autocomplete)
@app_commands.describe(club_name="The club you want to see")
async def leaderboard(interaction: discord.Interaction, club_name: str):
    """Show club leaderboard"""
    try:
        await interaction.response.defer()
    except discord.errors.NotFound:
        # Interaction already expired - silently return
        print(f"[Leaderboard] Interaction expired for {interaction.user.name}")
        return
    
    # Case-insensitive club name matching
    club_config = client.config_cache.get(club_name)
    actual_club_name = club_name  # Keep track of the actual club name
    
    if not club_config:
        # Try case-insensitive search
        for cached_club in client.config_cache.keys():
            if cached_club.casefold() == club_name.casefold():
                club_config = client.config_cache[cached_club]
                actual_club_name = cached_club
                break
    
    if not club_config:
        await interaction.followup.send(f"Error: Club '{club_name}' not found.")
        return
    
    # Use actual_club_name for cache keys and display
    club_name = actual_club_name
    
    data_sheet_name = club_config.get('Data_Sheet_Name')
    if not data_sheet_name:
        await interaction.followup.send(f"Error: Config for {club_name} is invalid.")
        return
    
    try:
        df, cache_warning = await _load_data_for_command(club_name, data_sheet_name)
        
        max_day = df['Day'].max()
        df_latest = df[df['Day'] == max_day].copy()
        
        df_behind_quota = df_latest[df_latest['is_behind'] == True].copy()
        df_above_quota = df_latest[df_latest['is_behind'] == False].copy()
        
        df_above_quota.sort_values(by='Total Fans', ascending=False, inplace=True)
        df_behind_quota.sort_values(by='Total Fans', ascending=False, inplace=True)
        
        # ANSI Colors
        YELLOW = '\u001b[33m'
        RESET = '\u001b[0m'
        
        # Border characters - DÙNG ASCII CHUẨN
        VERT = '|'
        HORZ = '-'
        CROSS = '+'
        
        # Check club type
        club_type = club_config.get('Club_Type', 'competitive').lower()
        is_casual = club_type == 'casual'
        
        # ===== DYNAMIC NAME PADDING =====
        # Discord code block has ~57 character limit per line
        # Competitive layout: " # | Name | Daily | Surplus | Target | Total"
        #                      3  + name + 7     + 9       + 8      + 6 + separators
        # Fixed chars (competitive): 3 + 7 + 9 + 8 + 6 + 5 (separators) = 38
        # Max name width for 57 chars: 57 - 38 = 19 chars (including padding)
        # Casual layout: " # | Name | Daily | Total" = 3 + 7 + 6 + 3 = 19 fixed
        # Max name width for casual: 57 - 19 = 38 chars
        
        MAX_NAME_COMPETITIVE = 15  # 15 chars for name in competitive mode
        MAX_NAME_CASUAL = 22       # 22 chars for name in casual mode
        
        all_players = pd.concat([df_above_quota, df_behind_quota]) if not is_casual else df_latest
        
        if is_casual:
            max_name_cap = MAX_NAME_CASUAL
        else:
            max_name_cap = MAX_NAME_COMPETITIVE
        
        # Find longest name but cap at max allowed
        if not all_players.empty:
            max_name_length = max(len(str(player['Name'])) for _, player in all_players.iterrows())
            max_name_length = min(max_name_length, max_name_cap)
        else:
            max_name_length = 10
        
        # Format header và separator theo club type với dynamic name width
        name_col_width = max_name_length + 2  # Account for spaces around name
        if is_casual:
            # Casual: Simpler design - # | Name | Daily | Total
            header_display = f" # {VERT} {'Name':<{max_name_length}} {VERT} Daily {VERT} Total"
            separator = f"{HORZ*3}{CROSS}{HORZ*name_col_width}{CROSS}{HORZ*7}{CROSS}{HORZ*6}"
        else:
            # Competitive: # | Name | Daily | Surplus | Target | Total
            header_display = f" # {VERT} {'Name':<{max_name_length}} {VERT} Daily {VERT} Surplus {VERT} Target {VERT} Total"
            separator = f"{HORZ*3}{CROSS}{HORZ*name_col_width}{CROSS}{HORZ*7}{CROSS}{HORZ*9}{CROSS}{HORZ*8}{CROSS}{HORZ*6}"
        
        divider_text = "(Players Behind Quota)" if not is_casual else ""
        if divider_text:
            padding = (len(separator) - len(divider_text)) // 2
            divider_line = f"{HORZ*padding}{divider_text}{HORZ*(len(separator) - len(divider_text) - padding)}"
        else:
            divider_line = separator
        
        body = []
        
        def format_player_line(player, rank, kick_note):
            # Kiểm tra nếu player dưới quota
            is_behind = player.get('is_behind', False)
            
            # Apply màu vàng nếu behind (chỉ cho competitive)
            color = YELLOW if is_behind and not is_casual else ""
            reset = RESET if is_behind and not is_casual else ""
            
            # Format các trường chung - sử dụng max_name_length động
            player_name = player['Name'][:max_name_length].ljust(max_name_length)
            rank_str = f"{rank:>2}."
            
            # Tạo line theo club type
            if is_casual:
                # Casual: Simple format
                daily = format_fans(player['Daily']).rjust(6)
                total = format_fans(player['Total Fans']).replace('+', '').rjust(6)
                line = f"{rank_str}{VERT} {player_name} {VERT}{daily} {VERT}{total}"
            else:
                # Competitive: Original format
                daily = format_fans(player['Daily']).rjust(6)
                carry = format_fans(player['CarryOver']).rjust(8)
                target = format_fans(player['Target']).replace('+', '').rjust(7)
                total = format_fans(player['Total Fans']).replace('+', '').rjust(6)
                line = f"{color}{rank_str}{VERT} {player_name} {VERT}{daily} {VERT}{carry} {VERT}{target} {VERT}{total}{reset}"
            
            if kick_note and not is_casual:
                line += f"\n{color}   {kick_note}{reset}"
            
            return line
        
        rank_counter = 1
        
        # Add above quota players (or all players for casual)
        if is_casual:
            # Casual: Just rank by total, no quota concept
            df_sorted = df_latest.sort_values(by='Total Fans', ascending=False)
            for _, player in df_sorted.head(30).iterrows():
                body.append(format_player_line(player, rank_counter, None))
                rank_counter += 1
        else:
            # Competitive: Above quota first
            for _, player in df_above_quota.head(30).iterrows():
                kick_note = get_kick_note(player, max_day)
                body.append(format_player_line(player, rank_counter, kick_note))
                rank_counter += 1
            
            # Add divider
            if not df_behind_quota.empty:
                body.append(divider_line)
            
            # Add behind quota players
            remaining_slots = 30 - len(df_above_quota)
            if remaining_slots > 0:
                for _, player in df_behind_quota.head(remaining_slots).iterrows():
                    if rank_counter > 30:
                        break
                    kick_note = get_kick_note(player, max_day)
                    body.append(format_player_line(player, rank_counter, kick_note))
                    rank_counter += 1
        
        # Create embed
        current_timestamp = get_last_update_timestamp()
        message_content = f"Data retrieved from Chronogenesis <t:{current_timestamp}:f>\n"
        if cache_warning:
            message_content = cache_warning + message_content
        message_content += "```ansi\n" + f"{header_display}\n{separator}\n" + "\n".join(body) + "\n```"
        
        # Get rank from config
        club_rank = club_config.get('Rank', '')
        rank_display = f" - Global Ranking #{club_rank}" if club_rank else ""
        
        embed = discord.Embed(
            title=f"🏆 Leaderboard (Club: {club_name} - Day {max_day}{rank_display})",
            description=message_content,
            color=discord.Color.purple()
        )
        
        club_daily_quota = club_config.get('Target_Per_Day', 0)
        footer_text = f"Daily Quota: {format_fans(club_daily_quota).replace('+', '')}"
        embed.set_footer(text=footer_text)
        
        view = LeaderboardView(original_embed=embed, club_name=club_name, full_df=df, max_day=max_day)
        await interaction.followup.send(embed=embed, view=view)
    
    except Exception as e:
        print(f"Error in /leaderboard command: {e}")
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


# ============================================================================
# REDESIGNED STATS COMMAND - COMPACT VERSION
# ============================================================================

class StatsView(discord.ui.View):
    """View with buttons for stats navigation"""
    
    def __init__(self, member_name: str, club_name: str, df_member: pd.DataFrame, 
                club_config: dict, timeout=300):
        super().__init__(timeout=timeout)
        self.member_name = member_name
        self.club_name = club_name
        self.df_member = df_member
        self.club_config = club_config
        self.current_page = 0
        self.mode = "overview"  # overview, summary, history
        
        # Prepare data
        self.latest_data = df_member.iloc[-1]
        self.max_day = int(self.latest_data['Day'])
        self.total_days = len(df_member)
        
        # ===== YUI LOGIC: Detect new member =====
        # Find the FIRST day this member has data (their join day)
        self.join_day = int(df_member['Day'].min())
        self.actual_days_in_club = self.max_day - self.join_day + 1
        self.is_new_member = self.join_day > 1  # Joined after Day 1
        
        # Setup buttons
        self._setup_buttons()
    
    def _setup_buttons(self):
        """Setup navigation buttons"""
        self.summary_btn = discord.ui.Button(
            label="📊 Summary", 
            style=discord.ButtonStyle.primary,
            custom_id="summary"
        )
        self.history_btn = discord.ui.Button(
            label="📅 History",
            style=discord.ButtonStyle.primary, 
            custom_id="history"
        )
        self.back_btn = discord.ui.Button(
            label="⬅️ Back",
            style=discord.ButtonStyle.secondary,
            custom_id="back"
        )
        self.prev_btn = discord.ui.Button(
            label="◀️ Previous",
            style=discord.ButtonStyle.secondary,
            custom_id="prev"
        )
        self.next_btn = discord.ui.Button(
            label="▶️ Next",
            style=discord.ButtonStyle.secondary,
            custom_id="next"
        )
        
        self.summary_btn.callback = self.show_summary
        self.history_btn.callback = self.show_history
        self.back_btn.callback = self.show_overview
        self.prev_btn.callback = self.prev_page
        self.next_btn.callback = self.next_page
    
    def _update_buttons(self):
        """Update button visibility based on mode"""
        self.clear_items()
        
        if self.mode == "overview":
            self.add_item(self.summary_btn)
            self.add_item(self.history_btn)
        elif self.mode == "summary":
            self.add_item(self.back_btn)
        elif self.mode == "history":
            self.add_item(self.back_btn)
            self.add_item(self.prev_btn)
            self.add_item(self.next_btn)
            
            # Update pagination buttons
            total_pages = (self.total_days + 9) // 10
            self.prev_btn.disabled = (self.current_page == 0)
            self.next_btn.disabled = (self.current_page >= total_pages - 1)
    
    def _create_overview_embed(self) -> discord.Embed:
        """Create overview embed - main stats page"""
        lines = []
        
        # Header
        lines.append("=" * 56)
        lines.append(center_text_exact(f"📊 MEMBER STATS: {self.member_name}", 56))
        lines.append("=" * 56)
        
        # Club name with rank
        club_rank = self.club_config.get('Rank', '')
        rank_display = f" - Global Ranking: #{club_rank}" if club_rank else ""
        lines.append(f"Club: {self.club_name}{rank_display}")
        
        # ===== YUI LOGIC: Show new member indicator =====
        if self.is_new_member:
            lines.append(f"🆕 New Member (Joined Day {self.join_day})")
            lines.append(f"Active Days: {self.actual_days_in_club} day(s)")
        
        # Progress bar
        progress_pct = int((self.max_day / 30) * 100)
        lines.append(f"Day: {self.max_day}/30 ({progress_pct}% complete)")
        
        # Timestamp with timezone
        timestamp = get_last_update_timestamp()
        from datetime import datetime
        import pytz
        utc_time = datetime.fromtimestamp(timestamp, tz=pytz.UTC)
        vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
        local_time = utc_time.astimezone(vietnam_tz)
        timestamp_display = local_time.strftime("%b %d, %Y %I:%M %p %Z")
        lines.append(f"Updated: {timestamp_display}")
        lines.append("=" * 56)
        
        # Current Snapshot
        lines.append("")
        lines.append("🎯 CURRENT SNAPSHOT")
        lines.append("─" * 56)
        
        total_fans = format_fans(self.latest_data['Total Fans'])
        daily_gain = format_fans(self.latest_data['Daily'])
        carry_over = format_fans(self.latest_data['CarryOver'])
        
        lines.append(format_stat_line_compact("Total Fans:", total_fans.replace('+', '')))
        lines.append(format_stat_line_compact("Today's Gain:", daily_gain))
        lines.append(format_stat_line_compact("Carry Over:", carry_over))
        
        # Status - adjusted for new members
        if self.is_new_member and self.actual_days_in_club <= 3:
            # New member with 3 days or less - don't judge harshly
            status = "🆕 New - Building up"
        elif self.latest_data['CarryOver'] >= 0:
            status = "✅ Above Target"
        elif self.latest_data['CarryOver'] >= -700_000:
            status = "⚠️ Behind Target"
        else:
            status = "🔴 Critical"
        lines.append(format_stat_line_compact("Status:", status))
        lines.append("─" * 56)
        
        # Recent Performance (Last 7 days)
        lines.append("")
        lines.append("📈 RECENT PERFORMANCE (Last 7 Days)")
        lines.append("─" * 56)
        
        recent_7 = self.df_member.tail(7) if len(self.df_member) >= 7 else self.df_member
        avg_daily_7 = recent_7['Daily'].mean()
        best_day = recent_7.loc[recent_7['Daily'].idxmax()]
        worst_day = recent_7.loc[recent_7['Daily'].idxmin()]
        
        lines.append(format_stat_line_compact("Average Daily:", format_fans(avg_daily_7).replace('+', '')))
        lines.append(format_stat_line_compact("Best Day:", f"Day {int(best_day['Day'])} ({format_fans(best_day['Daily'])}) 🏆"))
        lines.append(format_stat_line_compact("Worst Day:", f"Day {int(worst_day['Day'])} ({format_fans(worst_day['Daily'])}) ⬇️"))
        
        # Trend calculation
        if len(recent_7) >= 4:
            first_half = recent_7.head(3)['Daily'].mean()
            second_half = recent_7.tail(4)['Daily'].mean()
            # Handle NaN values
            import math
            if first_half > 0 and not math.isnan(first_half) and not math.isnan(second_half):
                trend_pct = ((second_half - first_half) / first_half * 100)
            else:
                trend_pct = 0
            
            if not math.isnan(trend_pct) and trend_pct > 5:
                trend = f"📈 Rising (+{int(trend_pct)}%)"
            elif not math.isnan(trend_pct) and trend_pct < -5:
                trend = f"📉 Declining ({int(trend_pct)}%)"
            else:
                trend = "➡️ Stable"
            lines.append(format_stat_line_compact("Trend:", trend))
        
        lines.append("─" * 56)
        
        # Quick Insights
        lines.append("")
        lines.append("💡 QUICK INSIGHTS")
        lines.append("=" * 56)
        
        insights = []
        
        # ===== YUI LOGIC: Handle new members differently =====
        if self.is_new_member and self.actual_days_in_club <= 3:
            # New member welcome message instead of harsh comparisons
            insights.append(f"👋 Welcome! You joined on Day {self.join_day}")
            insights.append(f"📊 {self.actual_days_in_club} day(s) of data - keep farming!")
            
            # Give encouraging feedback
            if avg_daily_7 > 0:
                insights.append(f"💪 Current pace: {format_fans(avg_daily_7).replace('+', '')}/day")
        else:
            # Club average comparison (only for established members)
            club_quota = self.club_config.get('Target_Per_Day', 1)
            # Handle NaN from mean()
            import math
            if club_quota > 0 and not math.isnan(avg_daily_7):
                diff_pct = int(((avg_daily_7 - club_quota) / club_quota * 100))
            else:
                diff_pct = 0
            if diff_pct > 15:
                insights.append(f"✓ Above club average by {diff_pct}%")
            elif diff_pct < -15:
                insights.append(f"⚠ Below club average by {abs(diff_pct)}%")
            
            # Streak check (only if enough data)
            if self.actual_days_in_club >= 5:
                behind_days = (self.df_member.tail(7)['CarryOver'] < 0).sum()
                if behind_days >= 5:
                    insights.append(f"⚠ Behind target for {behind_days} days straight")
            
            # Performance trend (only if enough data)
            if len(recent_7) >= 4 and self.actual_days_in_club >= 7:
                first_half = recent_7.head(3)['Daily'].mean()
                second_half = recent_7.tail(4)['Daily'].mean()
                # Handle NaN
                import math
                if first_half > 0 and not math.isnan(first_half) and not math.isnan(second_half):
                    trend_pct = ((second_half - first_half) / first_half * 100)
                else:
                    trend_pct = 0
                if not math.isnan(trend_pct) and abs(trend_pct) >= 25:
                    insights.append(f"⚠ Performance dropped {abs(int(trend_pct))}% from peak")
            
            # Catch-up calculation
            if self.latest_data['CarryOver'] < 0:
                deficit = abs(self.latest_data['CarryOver'])
                days_left = max(1, (30 - self.max_day))
                needed_daily = deficit / days_left
                insights.append(f"💡 Action: Need {format_fans(needed_daily).replace('+', '')}/day to catch up")
        
        # Display insights
        if insights:
            for insight in insights:
                lines.append(insight)
        else:
            lines.append("✓ Performance is stable and on track")
        
        lines.append("=" * 56)
        lines.append("")
        lines.append("🔍 Want more details?")
        lines.append("Use buttons below:")
        
        content = "```\n" + "\n".join(lines) + "\n```"
        
        embed = discord.Embed(
            description=content,
            color=discord.Color.blue()
        )
        
        return embed
    
    def _create_summary_embed(self) -> discord.Embed:
        """Create summary embed - detailed analytics"""
        lines = []
        
        # Header
        lines.append("=" * 56)
        lines.append(center_text_exact("📊 PERFORMANCE SUMMARY", 56))
        lines.append(center_text_exact(f"{self.member_name}", 56))
        lines.append("=" * 56)
        lines.append(f"Analysis Period: {self.total_days} days")
        lines.append(f"Club: {self.club_name}")
        lines.append("─" * 56)
        
        # Overall Performance
        lines.append("")
        lines.append("📈 OVERALL PERFORMANCE")
        lines.append("─" * 56)
        
        total_fans = self.latest_data['Total Fans']
        avg_daily = total_fans / self.total_days if self.total_days > 0 else 0
        best_day = self.df_member.loc[self.df_member['Daily'].idxmax()]
        worst_day = self.df_member.loc[self.df_member['Daily'].idxmin()]
        
        lines.append(format_stat_line_compact("Total Fans Earned", format_fans(total_fans).replace('+', '')))
        lines.append(format_stat_line_compact("Average Daily", format_fans(avg_daily).replace('+', '')))
        lines.append(format_stat_line_compact("Best Single Day", f"Day {int(best_day['Day'])} ({format_fans(best_day['Daily'])})"))
        lines.append(format_stat_line_compact("Worst Single Day", f"Day {int(worst_day['Day'])} ({format_fans(worst_day['Daily'])})"))
        lines.append("─" * 56)
        
        # Consistency Analysis
        lines.append("")
        lines.append("📊 CONSISTENCY ANALYSIS")
        lines.append("─" * 56)
        
        days_above_avg = (self.df_member['Daily'] >= avg_daily).sum()
        days_below_avg = self.total_days - days_above_avg
        above_pct = int(days_above_avg / self.total_days * 100) if self.total_days > 0 else 0
        
        lines.append(format_stat_line_compact("Days Above Average", f"{days_above_avg} days ({above_pct}%)"))
        lines.append(format_stat_line_compact("Days Below Average", f"{days_below_avg} days ({100-above_pct}%)"))
        
        # Calculate consistency score (handle NaN from std_dev)
        std_dev = self.df_member['Daily'].std()
        import math
        if avg_daily > 0 and not math.isnan(std_dev):
            consistency_score = max(0, min(100, int(100 - (std_dev / avg_daily * 50))))
        else:
            consistency_score = 0
        lines.append(format_stat_line_compact("Consistency Score", f"{consistency_score}/100"))
        lines.append("─" * 56)
        
        # Target Tracking
        lines.append("")
        lines.append("🎯 TARGET TRACKING")
        lines.append("─" * 56)
        
        days_above_target = (self.df_member['CarryOver'] >= 0).sum()
        days_below_target = self.total_days - days_above_target
        target_pct = int(days_above_target / self.total_days * 100) if self.total_days > 0 else 0
        
        lines.append(format_stat_line_compact("Days Above Target", f"{days_above_target} days ({target_pct}%)"))
        lines.append(format_stat_line_compact("Days Below Target", f"{days_below_target} days ({100-target_pct}%)"))
        lines.append(format_stat_line_compact("Current Status", format_fans(self.latest_data['CarryOver'])))
        
        if self.latest_data['CarryOver'] < 0:
            club_quota = self.club_config.get('Target_Per_Day', 1)
            days_remaining = max(1, 30 - self.max_day)
            needed_per_day = abs(self.latest_data['CarryOver']) / days_remaining
            catchup_days = abs(self.latest_data['CarryOver']) / club_quota if club_quota > 0 else 0
            lines.append(format_stat_line_compact("Est. Days to Catch Up", f"{catchup_days:.1f} days"))
        
        lines.append("─" * 56)
        
        # Trend Analysis (weekly)
        lines.append("")
        lines.append("📉 TREND ANALYSIS")
        lines.append("─" * 56)
        
        weeks = []
        for week_num in range(1, 5):
            week_data = self.df_member[
                (self.df_member['Day'] >= (week_num-1)*7 + 1) & 
                (self.df_member['Day'] <= week_num*7)
            ]
            if not week_data.empty:
                weeks.append((f"Week {week_num}", week_data['Daily'].mean()))
        
        for week_name, week_avg in weeks:
            lines.append(format_stat_line_compact(f"{week_name} Avg", format_fans(week_avg).replace('+', '')))
        
        if len(weeks) >= 2:
            first_week_avg = weeks[0][1]
            last_week_avg = weeks[-1][1]
            trend_pct = ((last_week_avg - first_week_avg) / first_week_avg * 100) if first_week_avg > 0 else 0
            
            if trend_pct > 0:
                trend = f"📈 Rising (+{int(trend_pct)}%)"
            else:
                trend = f"📉 Declining ({int(trend_pct)}%)"
            lines.append(format_stat_line_compact("Overall Trend", trend))
        
        lines.append("=" * 56)
        
        content = "```ansi\n" + "\n".join(lines) + "\n```"
        
        embed = discord.Embed(
            title=f"📊 Summary: {self.member_name}",
            description=content,
            color=discord.Color.green()
        )
        
        return embed
    
    def _create_history_embed(self) -> discord.Embed:
        """Create history embed - paginated daily records (10 days per page)"""
        lines = []
        
        # Calculate pagination - FIXED: 10 items per page
        items_per_page = 10
        total_pages = (self.total_days + items_per_page - 1) // items_per_page
        start_idx = self.current_page * items_per_page
        end_idx = min(start_idx + items_per_page, self.total_days)
        
        # Get data for current page (reversed to show latest first)
        df_reversed = self.df_member.iloc[::-1]
        page_data = df_reversed.iloc[start_idx:end_idx]
        
        # Calculate display range
        first_day = int(page_data.iloc[0]['Day'])
        last_day = int(page_data.iloc[-1]['Day'])
        
        # Header
        lines.append("=" * 56)
        lines.append(center_text_exact(f"DAILY HISTORY: {self.member_name}", 56))
        lines.append("=" * 56)
        lines.append(f"Club: {self.club_name}")
        lines.append(f"Showing: Days {first_day}-{last_day} (Page {self.current_page + 1}/{total_pages})")
        lines.append("─" * 56)
        
        # Column headers
        header = "Day    Daily Gain              Carry Over      Status"
        lines.append(header)
        lines.append("─" * 56)
        
        # Display format - show exactly 10 days
        for _, row in page_data.iterrows():
            day_num = int(row['Day'])
            daily = format_fans_full(row['Daily'])
            carry = format_fans(row['CarryOver'])
            
            # Status icon
            if row['CarryOver'] >= 0:
                icon = "✅"
            elif row['CarryOver'] >= -700_000:
                icon = "⚠️"
            else:
                icon = "🔴"
            
            # Mark best/worst
            badge = ""
            if row['Daily'] == self.df_member['Daily'].max():
                badge = "🏆"
            elif row['Daily'] == self.df_member['Daily'].min():
                badge = "⬇️"
            
            # Format columns with proper spacing
            day_col = f"{day_num:>2}"          # Width: 2
            daily_col = f"{daily:>11}"         # Width: 11
            carry_col = f"{carry:>8}"          # Width: 8
            
            # Build line
            line = f"{day_col}    {daily_col}              {carry_col}      "
            
            # Add icons at the end
            icons = icon + badge
            line += icons
            
            lines.append(line)
        
        lines.append("")
        lines.append("=" * 56)
        footer_line = f"Page {self.current_page + 1}/{total_pages} • Total: {self.total_days} days"
        lines.append(center_text_exact(footer_line, 56))
        lines.append("")
        lines.append("Legend: ✅=On Track  ⚠️=Behind  🔴=Critical")
        lines.append("        🏆=Best Day  ⬇️=Worst Day")
        
        content = "```\n" + "\n".join(lines) + "\n```"
        
        embed = discord.Embed(
            description=content,
            color=discord.Color.orange()
        )
        
        return embed
    
    async def show_overview(self, interaction: discord.Interaction):
        """Show overview page"""
        await interaction.response.defer()
        self.mode = "overview"
        self._update_buttons()
        embed = self._create_overview_embed()
        await interaction.edit_original_response(embed=embed, view=self)
    
    async def show_summary(self, interaction: discord.Interaction):
        """Show summary page"""
        await interaction.response.defer()
        self.mode = "summary"
        self._update_buttons()
        embed = self._create_summary_embed()
        await interaction.edit_original_response(embed=embed, view=self)
    
    async def show_history(self, interaction: discord.Interaction):
        """Show history page"""
        await interaction.response.defer()
        self.mode = "history"
        self.current_page = 0
        self._update_buttons()
        embed = self._create_history_embed()
        await interaction.edit_original_response(embed=embed, view=self)
    
    async def prev_page(self, interaction: discord.Interaction):
        """Previous page in history"""
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
        self._update_buttons()
        embed = self._create_history_embed()
        await interaction.edit_original_response(embed=embed, view=self)
    
    async def next_page(self, interaction: discord.Interaction):
        """Next page in history"""
        await interaction.response.defer()
        total_pages = (self.total_days + 9) // 10
        if self.current_page < total_pages - 1:
            self.current_page += 1
        self._update_buttons()
        embed = self._create_history_embed()
        await interaction.edit_original_response(embed=embed, view=self)


# ============================================================================
# TRANSFER WARNING VIEW (FOR POSSIBLE CLUB TRANSFERS)
# ============================================================================

class OldClubIDModal(discord.ui.Modal, title="Provide Old Club ID"):
    """Modal for entering old club ID for transfer members"""
    
    def __init__(self, member_name: str, club_name: str, trainer_id: str):
        super().__init__()
        self.member_name = member_name
        self.club_name = club_name
        self.trainer_id = trainer_id
    
    old_club_id = discord.ui.TextInput(
        label="Old Club ID (9 digits)",
        placeholder="e.g., 525713827",
        required=True,
        min_length=9,
        max_length=9
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        old_id = self.old_club_id.value
        
        # Validate it's a number
        if not old_id.isdigit():
            await interaction.response.send_message(
                "❌ Club ID must be 9 digits only.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        # Fetch old club data from uma.moe API
        old_club_data = None
        old_club_name = "Unknown"
        old_club_members = 0
        
        try:
            old_club_data = await fetch_club_data_from_api(old_id)
            if old_club_data:
                old_club_name = old_club_data.get('circle_name', 'Unknown')
                old_club_members = len(old_club_data.get('members', []))
        except Exception as e:
            print(f"Error fetching old club data: {e}")
        
        # Save to transfer_requests.json
        transfer_file = os.path.join(SCRIPT_DIR, "transfer_requests.json")
        try:
            if os.path.exists(transfer_file):
                with open(transfer_file, 'r', encoding='utf-8') as f:
                    transfer_data = json.load(f)
            else:
                transfer_data = []
            
            # Add new request
            import datetime
            vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
            now = datetime.datetime.now(vietnam_tz)
            
            transfer_request = {
                'member_name': self.member_name,
                'current_club': self.club_name,
                'trainer_id': self.trainer_id,
                'old_club_id': old_id,
                'old_club_name': old_club_name,
                'old_club_members': old_club_members,
                'submitted_by': str(interaction.user.id),
                'submitted_at': now.strftime('%Y-%m-%d %H:%M:%S'),
                'status': 'pending'  # pending, processed, rejected
            }
            transfer_data.append(transfer_request)
            
            with open(transfer_file, 'w', encoding='utf-8') as f:
                json.dump(transfer_data, f, ensure_ascii=False, indent=2)
            
            print(f"📋 Saved transfer request: {self.member_name} -> Old Club: {old_club_name} ({old_id})")
            
        except Exception as e:
            print(f"Error saving transfer request: {e}")
        
        # Log to debug channel for admin review
        try:
            debug_channel = client.get_channel(DEBUG_LOG_CHANNEL_ID)
            if debug_channel:
                embed = discord.Embed(
                    title="📋 Transfer Request Submitted",
                    color=0xf39c12
                )
                embed.add_field(name="Member", value=self.member_name, inline=True)
                embed.add_field(name="Trainer ID", value=f"`{self.trainer_id}`", inline=True)
                embed.add_field(name="Current Club", value=self.club_name, inline=True)
                embed.add_field(name="Old Club ID", value=f"`{old_id}`", inline=True)
                embed.add_field(name="Old Club Name", value=old_club_name, inline=True)
                embed.add_field(name="Old Club Members", value=str(old_club_members), inline=True)
                embed.add_field(name="Submitted By", value=interaction.user.mention, inline=False)
                embed.set_footer(text=f"Use /transfer_review to process this request")
                
                await debug_channel.send(embed=embed)
        except Exception as e:
            print(f"Error logging old club ID: {e}")
        
        # Send confirmation to user
        if old_club_data:
            await interaction.followup.send(
                f"✅ **Thank you!**\n\n"
                f"**Your old club:**\n"
                f"• **Name:** {old_club_name}\n"
                f"• **ID:** `{old_id}`\n"
                f"• **Members:** {old_club_members}\n\n"
                f"An admin will review and adjust your Day 1 data based on your previous club's records.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"✅ Your old club ID `{old_id}` has been recorded.\n"
                f"⚠️ Could not fetch club data - the club may no longer exist or ID is invalid.\n"
                f"An admin will review this manually.",
                ephemeral=True
            )


class TransferWarningView(discord.ui.View):
    """View with button to provide old club ID for transfer members"""
    
    def __init__(self, member_name: str, club_name: str, trainer_id: str, timeout=300):
        super().__init__(timeout=timeout)
        self.member_name = member_name
        self.club_name = club_name
        self.trainer_id = trainer_id
    
    @discord.ui.button(label="📋 Provide Old Club ID", style=discord.ButtonStyle.primary)
    async def provide_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open modal to enter old club ID"""
        # First, send the guide image
        image_path = os.path.join(SCRIPT_DIR, "assets", "club_id_guide.png")
        
        if os.path.exists(image_path):
            file = discord.File(image_path, filename="club_id_guide.png")
            embed = discord.Embed(
                title="📖 How to Find Your Old Club ID",
                description=(
                    "**Option 1: Chronogenesis Website**\n"
                    "1. Go to [chronogenesis.net](https://chronogenesis.net)\n"
                    "2. Search for your old club name\n"
                    "3. The Club ID is in the URL: `circle_id=XXXXXXXXX`\n\n"
                    "**Option 2: From the image below**\n"
                    "The 9-digit number in the URL is your Club ID."
                ),
                color=0x3498db
            )
            embed.set_image(url="attachment://club_id_guide.png")
            
            await interaction.response.send_message(
                embed=embed,
                file=file,
                ephemeral=True
            )
            
            # Then send the modal in a followup
            await interaction.followup.send(
                "Now please enter your old Club ID below:",
                ephemeral=True,
                view=TransferSubmitView(self.member_name, self.club_name, self.trainer_id)
            )
        else:
            # If image not found, just show modal directly
            modal = OldClubIDModal(self.member_name, self.club_name, self.trainer_id)
            await interaction.response.send_modal(modal)


class TransferSubmitView(discord.ui.View):
    """Secondary view with button to open the modal"""
    
    def __init__(self, member_name: str, club_name: str, trainer_id: str, timeout=300):
        super().__init__(timeout=timeout)
        self.member_name = member_name
        self.club_name = club_name
        self.trainer_id = trainer_id
    
    @discord.ui.button(label="📝 Enter Club ID", style=discord.ButtonStyle.success)
    async def enter_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = OldClubIDModal(self.member_name, self.club_name, self.trainer_id)
        await interaction.response.send_modal(modal)


# ============================================================================
# UPDATED STATS COMMAND
# ============================================================================

@client.tree.command(name="stats", description="View detailed stats for a member.")
@app_commands.autocomplete(club_name=club_autocomplete, member_name=member_autocomplete)
@app_commands.describe(club_name="The member's club", member_name="The member's name")
async def stats(interaction: discord.Interaction, club_name: str, member_name: str):
    """Show detailed member stats with navigation"""
    try:
        await interaction.response.defer()
    except discord.errors.NotFound:
        print(f"[Stats] Interaction expired for {interaction.user.name}")
        return
    
    # Check if user has linked profile - suggest /profile command
    profile_links = load_profile_links()
    user_link = profile_links.get(str(interaction.user.id))
    show_profile_tip = user_link is not None
    
    # Case-insensitive club name matching
    club_config = client.config_cache.get(club_name)
    actual_club_name = club_name
    
    if not club_config:
        # Try case-insensitive search
        for cached_club in client.config_cache.keys():
            if cached_club.casefold() == club_name.casefold():
                club_config = client.config_cache[cached_club]
                actual_club_name = cached_club
                break
    
    if not club_config:
        await interaction.followup.send(f"Error: Club '{club_name}' not found.")
        return
    
    club_name = actual_club_name
    
    data_sheet_name = club_config.get('Data_Sheet_Name')
    if not data_sheet_name:
        await interaction.followup.send(f"Error: Config for {club_name} is invalid.")
        return
    
    # Find member (already case-insensitive)
    member_list = client.member_cache.get(club_name, [])
    found_member = None
    
    if member_name in member_list:
        found_member = member_name
    else:
        for m_name in member_list:
            if m_name.casefold() == member_name.casefold():
                found_member = m_name
                break
    
    if not found_member:
        await interaction.followup.send(
            f"Error: Member '{member_name}' not found in club '{club_name}'."
        )
        return
    
    try:
        df, cache_warning = await _load_data_for_command(club_name, data_sheet_name)
        
        # Strip whitespace from Name column for matching
        # This handles cases where API/Sheets have trailing spaces (e.g., "王牌 " vs "王牌")
        df['Name_stripped'] = df['Name'].str.strip()
        df_member = df[df['Name_stripped'] == found_member.strip()].copy()
        
        if df_member.empty:
            # Try case-insensitive match as fallback
            df_member = df[df['Name_stripped'].str.lower() == found_member.strip().lower()].copy()
        
        if df_member.empty:
            await interaction.followup.send(f"No data found for '{found_member}'.")
            return
        
        df_member.sort_values(by='Day', inplace=True)
        
        # Create view with navigation
        view = StatsView(
            member_name=found_member,
            club_name=club_name,
            df_member=df_member,
            club_config=club_config
        )
        
        # Show initial overview
        embed = view._create_overview_embed()
        view._update_buttons()
        
        await interaction.followup.send(embed=embed, view=view)
        
        # ================================================================
        # MID-MONTH JOINER DETECTION (auto-detect old club)
        # ================================================================
        first_day_with_data = df_member['Day'].min() if not df_member.empty else 1
        is_mid_month_joiner = first_day_with_data > 1
        
        # Try to get viewer_id from profile_links if user is viewing their own profile
        profile_links = load_profile_links()
        user_link = profile_links.get(str(interaction.user.id))
        viewer_id = None
        if user_link and user_link.get('member_name', '').casefold() == found_member.casefold():
            viewer_id = user_link.get('viewer_id')
        
        if is_mid_month_joiner and viewer_id:
            # Show prompt for old club data
            prompt_view = OldClubPromptView(
                viewer_id=str(viewer_id),
                current_club=club_name,
                member_name=found_member
            )
            await interaction.followup.send(
                f"📦 **Detected mid-month join!**\n"
                f"You joined **{club_name}** on Day {first_day_with_data}.\n\n"
                f"Want to add data from your previous club?",
                view=prompt_view,
                ephemeral=True
            )
        
        # ===== TRANSFER WARNING CHECK =====
        # Check if this member has a transfer warning (from member sheet)
        try:
            members_sheet_name = club_config.get('Members_Sheet_Name')
            if members_sheet_name:
                member_ws = await asyncio.to_thread(
                    gs_manager.sh.worksheet, members_sheet_name
                )
                all_members_data = await asyncio.to_thread(member_ws.get_all_values)
                
                if len(all_members_data) > 1:
                    # Find header row
                    header = all_members_data[0] if all_members_data else []
                    transfer_warning_col = -1
                    name_col = -1
                    trainer_id_col = -1
                    
                    for i, col in enumerate(header):
                        if col == '_TransferWarning':
                            transfer_warning_col = i
                        elif col == 'Name':
                            name_col = i
                        elif col == 'Trainer ID':
                            trainer_id_col = i
                    
                    # Search for member in data
                    if transfer_warning_col >= 0 and name_col >= 0:
                        for row in all_members_data[1:]:
                            if len(row) > name_col and row[name_col].strip().casefold() == found_member.strip().casefold():
                                # Found member - check transfer warning
                                if len(row) > transfer_warning_col and row[transfer_warning_col]:
                                    # Has transfer warning
                                    trainer_id = row[trainer_id_col] if trainer_id_col >= 0 and len(row) > trainer_id_col else ""
                                    transfer_view = TransferWarningView(
                                        member_name=found_member,
                                        club_name=club_name,
                                        trainer_id=trainer_id
                                    )
                                    await interaction.followup.send(
                                        f"⚠️ **Club Transfer Detected**\n"
                                        f"We noticed you recently joined **{club_name}** and may have transferred from another club.\n\n"
                                        f"To calculate your Day 1 data accurately, please provide your **old club ID**.",
                                        view=transfer_view,
                                        ephemeral=True
                                    )
                                break
        except Exception as e:
            print(f"Error checking transfer warning: {e}")

        
        # Check if user already linked this profile
        profile_links = load_profile_links()
        user_link = profile_links.get(str(interaction.user.id))
        
        if user_link:
            # Check if user is viewing their OWN linked profile
            is_own_profile = (
                user_link.get('member_name', '').casefold() == found_member.casefold() and
                user_link.get('club_name', '').casefold() == club_name.casefold()
            )
            
            if is_own_profile:
                # User is viewing their own profile - suggest /profile command
                try:
                    await interaction.followup.send(
                        "💡 **Tip:** Try `/profile` to look for your data faster!",
                        ephemeral=True
                    )
                except:
                    pass
            # If viewing someone else's profile, don't show anything
        else:
            # User hasn't linked - ask ownership if viewing their own profile
            ownership_view = ProfileOwnershipView(member_name=found_member, club_name=club_name)
            try:
                await interaction.followup.send(
                    f"🔗 **Are you the owner of this profile?**\n"
                    f"Trainer: **{found_member}** | Club: **{club_name}**",
                    view=ownership_view,
                    ephemeral=True
                )
            except Exception as e:
                print(f"Could not send ownership prompt: {e}")
    
    except Exception as e:
        print(f"Error in /stats command: {e}")
        import traceback
        traceback.print_exc()
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)



# ============================================================================
# PROFILE COMMAND (FOR LINKED USERS)
# ============================================================================

@client.tree.command(name="profile", description="View your linked profile stats (must link first via /stats).")
async def profile(interaction: discord.Interaction):
    """Show stats for the user's linked profile
    
    Uses viewer_id (player_id) for robust lookup that handles:
    - User changing clubs
    - User changing their in-game name
    """
    try:
        await interaction.response.defer()
    except discord.errors.NotFound:
        # Interaction already expired - silently return
        print(f"[Profile] Interaction expired for {interaction.user.name}")
        return
    
    # Check if user has linked profile
    profile_links = load_profile_links()
    user_link = profile_links.get(str(interaction.user.id))
    
    if not user_link:
        await interaction.followup.send(
            "❌ **No linked profile found!**\n\n"
            "To link your profile:\n"
            "1. Use `/stats <club_name> <member_name>` to view a profile\n"
            "2. Click 'Yes, this is me' button\n"
            "3. Send a screenshot of your trainer profile in DM\n\n"
            "After linking, you can use `/profile` to quickly view your stats!",
            ephemeral=True
        )
        return
    
    club_name = user_link.get('club_name')
    member_name = user_link.get('member_name')
    viewer_id = user_link.get('viewer_id')  # Player ID that never changes
    
    found_club = None
    found_member = None
    profile_updated = False
    club_config = None
    
    # ===== STEP 1: Try to find member in stored club first (fast path) =====
    # This avoids API calls for users who haven't changed clubs
    
    club_config = client.config_cache.get(club_name)
    if not club_config:
        # Try case-insensitive match
        for cached_club in client.config_cache.keys():
            if cached_club.casefold() == club_name.casefold():
                club_config = client.config_cache[cached_club]
                club_name = cached_club
                break
    
    if club_config:
        # Try to find member by name in stored club
        member_list = client.member_cache.get(club_name, [])
        
        if member_name in member_list:
            found_member = member_name
            found_club = club_name
        else:
            for m_name in member_list:
                if m_name.casefold() == member_name.casefold():
                    found_member = m_name
                    found_club = club_name
                    break
    
    # ===== STEP 2: If member NOT found in stored club and has viewer_id, search via API =====
    # This handles users who changed clubs - use Yui logic to find current club
    if not found_member and viewer_id:
        await interaction.edit_original_response(
            content="🔍 Searching for your profile via API (real-time)..."
        )
        
        # Use API to find real-time club membership
        result = await find_viewer_in_clubs_via_api(viewer_id)
        if result:
            found_member = result['member_name']
            found_club = result['club_name']
            profile_updated = True
            
            # Update profile link with new club/name
            save_profile_link(
                discord_id=interaction.user.id,
                trainer_id=user_link.get('trainer_id', ''),
                member_name=found_member,
                club_name=found_club,
                viewer_id=viewer_id
            )
            
            # Get club config for new club
            club_config = client.config_cache.get(found_club)
    
    # Step 3: If still not found, show error (or try fallback for legacy profiles)
    if not found_member or not found_club:
        # Fallback for legacy profile links: try to get viewer_id from sheets
        if not viewer_id and club_config:
            print(f"[Profile] Legacy profile detected, trying to get viewer_id from sheets...")
            viewer_id = get_viewer_id_from_sheets(member_name, club_name)
            
            if viewer_id:
                print(f"[Profile] Found viewer_id {viewer_id} for {member_name}, searching via API...")
                # Use API to find real-time club membership
                result = await find_viewer_in_clubs_via_api(viewer_id)
                if result:  
                    found_member = result['member_name']
                    found_club = result['club_name']
                    profile_updated = True
                    
                    # Update profile link with new data AND viewer_id
                    save_profile_link(
                        discord_id=interaction.user.id,
                        trainer_id=user_link.get('trainer_id', ''),
                        member_name=found_member,
                        club_name=found_club,
                        viewer_id=viewer_id
                    )
                    club_config = client.config_cache.get(found_club)
    
    # Final check - still not found
    if not found_member or not found_club:
        if viewer_id:
            await interaction.followup.send(
                f"❌ **Profile not found**\n\n"
                f"Your Player ID `{viewer_id}` was not found in any tracked club.\n\n"
                f"**Possible reasons:**\n"
                f"• You may have left all tracked clubs\n"
                f"• The club data sync is still in progress (try again in a few minutes)\n"
                f"• API connection issues (check bot logs)\n\n"
                f"To link a new profile, use `/stats <club_name> <member_name>`.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ Member '{member_name}' not found in '{club_name}'.\n\n"
                "Your linked profile may be outdated. Please link again using `/stats`.",
                ephemeral=True
            )
        return
    
    if not club_config:
        await interaction.followup.send(
            f"❌ Could not find config for '{found_club}'.",
            ephemeral=True
        )
        return
    
    data_sheet_name = club_config.get('Data_Sheet_Name')
    if not data_sheet_name:
        await interaction.followup.send(f"Error: Config for {found_club} is invalid.")
        return
    
    try:
        # Load data
        df, cache_warning = await _load_data_for_command(found_club, data_sheet_name)
        
        # Strip whitespace for matching (handles trailing spaces like "王牌 " vs "王牌")
        df['Name_stripped'] = df['Name'].str.strip()
        df_member = df[df['Name_stripped'] == found_member.strip()].copy()
        
        if df_member.empty:
            # Try case-insensitive match as fallback
            df_member = df[df['Name_stripped'].str.lower() == found_member.strip().lower()].copy()
        
        if df_member.empty:
            await interaction.followup.send(f"No data found for '{found_member}'.")
            return
        
        df_member.sort_values(by='Day', inplace=True)
        
        # ================================================================
        # OLD CLUB DATA LOOKUP (for transferred members)
        # Check if member is a new joiner and fetch old club data
        # ================================================================
        old_club_info = None
        old_club_df = None
        
        # Check if this member has old club data in CROSS_CLUB_CACHE
        if viewer_id:
            cross_club_data = get_cross_club_data(str(viewer_id))
            if cross_club_data and cross_club_data.get('club_name') != found_club:
                old_club_info = {
                    'club_name': cross_club_data.get('club_name'),
                    'day31_cumulative': cross_club_data.get('day31_cumulative'),
                    'month': cross_club_data.get('month')
                }
                print(f"[Profile] Found old club data in cache: {old_club_info['club_name']}")
        
        # Also check transfer_requests.json for manually submitted old club
        if not old_club_info:
            try:
                transfer_file = 'transfer_requests.json'
                if os.path.exists(transfer_file):
                    with open(transfer_file, 'r', encoding='utf-8') as f:
                        transfers = json.load(f)
                    
                    # Find matching transfer by viewer_id or member_name
                    for req in transfers:
                        if (req.get('viewer_id') == viewer_id or 
                            req.get('member_name', '').lower() == found_member.lower()):
                            if req.get('old_club_name') and req.get('old_club_name') != found_club:
                                old_club_info = {
                                    'club_name': req.get('old_club_name'),
                                    'old_club_id': req.get('old_club_id'),
                                    'month': req.get('month', 'Previous')
                                }
                                print(f"[Profile] Found old club in transfer_requests: {old_club_info['club_name']}")
                                break
            except Exception as e:
                print(f"[Profile] Error reading transfer_requests.json: {e}")
        
        # ================================================================
        # AUTO-DETECT OLD CLUB (if mid-month joiner but no old club found)
        # ================================================================
        first_day_with_data = df_member['Day'].min() if not df_member.empty else 1
        is_mid_month_joiner = first_day_with_data > 1
        
        if not old_club_info and is_mid_month_joiner and viewer_id:
            print(f"[Profile] Mid-month joiner detected (first day: {first_day_with_data}), searching for old club...")
            
            try:
                # Search ALL tracked clubs for this viewer_id
                all_clubs = await find_all_clubs_for_viewer(str(viewer_id))
                
                if len(all_clubs) > 1:
                    # Found multiple clubs - the one that's not current is old club
                    for club_data in all_clubs:
                        if club_data['club_name'] != found_club:
                            old_club_info = {
                                'club_name': club_data['club_name'],
                                'active_days': club_data['active_days'],
                                'daily_fans': club_data.get('daily_fans', []),
                                'auto_detected': True
                            }
                            
                            # Update CROSS_CLUB_CACHE and transfer_requests.json
                            old_total = sum(f for f in club_data.get('daily_fans', []) if f and f > 0)
                            update_cross_club_cache(
                                trainer_id=str(viewer_id),
                                club_name=club_data['club_name'],
                                day31_cumulative=old_total,
                                month=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
                            )
                            
                            print(f"[Profile] ✅ Auto-detected old club: {club_data['club_name']} ({club_data['active_days']} active days)")
                            break
                            
                elif len(all_clubs) == 1 and is_mid_month_joiner:
                    # Only one club found but mid-month joiner - old club NOT in system
                    # Show prompt for user to add old club ID
                    print(f"[Profile] Mid-month joiner but old club not in system, will show prompt")
                    
            except Exception as e:
                print(f"[Profile] Error auto-detecting old club: {e}")
        
        # If old club found and is tracked, try to load old club data
        if old_club_info and old_club_info['club_name'] in client.config_cache:
            old_club_config = client.config_cache.get(old_club_info['club_name'])
            if old_club_config:
                old_data_sheet = old_club_config.get('Data_Sheet_Name')
                if old_data_sheet:
                    try:
                        old_df, _ = await _load_data_for_command(old_club_info['club_name'], old_data_sheet)
                        old_df['Name_stripped'] = old_df['Name'].str.strip()
                        old_df_member = old_df[old_df['Name_stripped'] == found_member.strip()].copy()
                        
                        if not old_df_member.empty:
                            old_df_member.sort_values(by='Day', inplace=True)
                            old_club_df = old_df_member
                            print(f"[Profile] Loaded {len(old_club_df)} days of data from old club {old_club_info['club_name']}")
                    except Exception as e:
                        print(f"[Profile] Error loading old club data: {e}")
        
        # Create view with navigation (pass old_club_info for display)
        view = StatsView(
            member_name=found_member,
            club_name=found_club,
            df_member=df_member,
            club_config=club_config
        )
        
        # Show initial overview
        embed = view._create_overview_embed()
        view._update_buttons()
        
        # Add old club note if applicable
        old_club_note = ""
        if old_club_info:
            old_club_note = f"\n\n📦 **Previous Club:** {old_club_info['club_name']}"
            if old_club_df is not None and not old_club_df.empty:
                old_total = old_club_df['Total'].iloc[-1] if 'Total' in old_club_df.columns else 0
                old_club_note += f" (Last record: {old_total:,} fans)"
        
        # Add note if profile was auto-updated
        if profile_updated:
            await interaction.followup.send(
                f"📝 **Profile auto-updated!** Found you in **{found_club}** as **{found_member}**.{old_club_note}",
                embed=embed, 
                view=view
            )
        else:
            if old_club_note:
                await interaction.followup.send(content=old_club_note, embed=embed, view=view)
            else:
                await interaction.followup.send(embed=embed, view=view)
        
        # ================================================================
        # PROMPT FOR OLD CLUB (if mid-month joiner and no old club found)
        # ================================================================
        if is_mid_month_joiner and not old_club_info and viewer_id:
            # Show prompt to add old club data
            prompt_view = OldClubPromptView(
                viewer_id=str(viewer_id),
                current_club=found_club,
                member_name=found_member
            )
            await interaction.followup.send(
                f"📦 **Detected mid-month join!**\n"
                f"You joined **{found_club}** on Day {first_day_with_data}.\n\n"
                f"Want to add data from your previous club?\n"
                f"(This helps calculate your accurate CarryOver)",
                view=prompt_view,
                ephemeral=True
            )
        
    except Exception as e:
        print(f"Error in /profile command: {e}")
        import traceback
        traceback.print_exc()
        await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


# ============================================================================
# ERROR HANDLER
# ============================================================================

@client.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    """Global error handler for app commands - sends failed logs to DEBUG channel"""
    import traceback
    
    # Special handling for autocomplete interactions
    if interaction.type == discord.InteractionType.autocomplete:
        print(f"Autocomplete error (ignored): {error}")
        return
    
    # Get command info
    command_name = interaction.command.name if interaction.command else "Unknown"
    
    # Build parameters string
    params = []
    if interaction.namespace:
        for key, value in interaction.namespace.__dict__.items():
            if not key.startswith('_'):
                if isinstance(value, discord.Member) or isinstance(value, discord.User):
                    params.append(f"{key}=@{value.name}")
                else:
                    params.append(f"{key}={value}")
    params_str = ", ".join(params) if params else "No parameters"
    
    # Determine error message and log details
    error_message = f"An unknown error occurred: {error}"
    error_type = "Unknown Error"
    error_category = "⚠️ Unknown"
    
    if isinstance(error, app_commands.MissingPermissions):
        error_message = f"❌ **Permission Denied**\n\nYou don't have permission to use this command.\nRequired permissions: {', '.join(error.missing_permissions)}"
        error_type = "Missing Permissions"
        error_category = "🔒 Permission"
    elif isinstance(error, app_commands.CheckFailure):
        error_message = "❌ **Permission Denied**\n\nYou don't have permission to use this command."
        error_type = "Permission Check Failed"
        error_category = "🔒 Permission"
    elif isinstance(error, app_commands.CommandOnCooldown):
        error_message = f"⏰ **Cooldown Active**\n\nPlease wait {error.retry_after:.1f} seconds before using this command again."
        error_type = "Cooldown"
        error_category = "⏰ Cooldown"
    elif isinstance(error, app_commands.CommandInvokeError):
        # This wraps the actual error - get more details
        actual_error = error.original
        error_message = f"❌ **Command Failed**\n\nError: {str(actual_error)}"
        error_type = f"CommandInvokeError ({type(actual_error).__name__})"
        error_category = "💥 Execution"
    
    # ========== CONSOLE DEBUG LOGGING ==========
    timestamp = datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
    server_name = interaction.guild.name if interaction.guild else "DM"
    server_id = interaction.guild_id if interaction.guild_id else "N/A"
    channel_name = interaction.channel.name if hasattr(interaction.channel, 'name') else "Unknown"
    
    print(f"\n{'='*60}")
    print(f"❌ COMMAND FAILED: /{command_name}")
    print(f"{'='*60}")
    print(f"⏰ Time: {timestamp}")
    print(f"👤 User: {interaction.user.name} (ID: {interaction.user.id})")
    print(f"🏠 Server: {server_name} (ID: {server_id})")
    print(f"📺 Channel: #{channel_name} (ID: {interaction.channel_id})")
    print(f"📋 Parameters: {params_str}")
    print(f"❌ Error Type: {error_type}")
    print(f"📍 Error: {str(error)[:500]}")
    print(f"{'='*60}\n")
    # ========== END CONSOLE DEBUG LOGGING ==========
    
    # ========== SAVE ERROR TO LOCAL LOG FILE ==========
    try:
        actual_error = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        log_error(
            actual_error,
            f"Command /{command_name}",
            {
                "user": f"{interaction.user.name} ({interaction.user.id})",
                "server": f"{server_name} ({server_id})",
                "channel": f"#{channel_name} ({interaction.channel_id})",
                "parameters": params_str,
                "error_type": error_type
            }
        )
    except Exception as file_log_err:
        print(f"⚠️ Failed to write error to log file: {file_log_err}")
    # ========== END LOG FILE SAVING ==========
    
    # Log the failed command to DEBUG channel
    try:
        # Create detailed error log embed
        embed = discord.Embed(
            title=f"❌ Command Failed: /{command_name}",
            description=f"**{error_category} | {error_type}**",
            color=discord.Color.red(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        
        # User info
        embed.add_field(
            name="👤 User Info",
            value=f"**Name:** {interaction.user.name}\n**ID:** `{interaction.user.id}`\n**Mention:** {interaction.user.mention}",
            inline=True
        )
        
        # Server info
        if interaction.guild:
            embed.add_field(
                name="🏠 Server Info",
                value=f"**Name:** {interaction.guild.name}\n**ID:** `{interaction.guild_id}`",
                inline=True
            )
        
        # Channel info
        embed.add_field(
            name="📺 Channel",
            value=f"<#{interaction.channel_id}>\n**ID:** `{interaction.channel_id}`",
            inline=True
        )
        
        # Parameters
        embed.add_field(
            name="📋 Parameters",
            value=f"```{params_str}```",
            inline=False
        )
        
        # Error details
        error_details = str(error)
        if len(error_details) > 800:
            error_details = error_details[:800] + "..."
        embed.add_field(
            name="⚠️ Error Details",
            value=f"```{error_details}```",
            inline=False
        )
        
        # Stack trace (for CommandInvokeError)
        if isinstance(error, app_commands.CommandInvokeError):
            tb = ''.join(traceback.format_exception(type(error.original), error.original, error.original.__traceback__))
            if len(tb) > 800:
                tb = tb[:800] + "\n..."
            embed.add_field(
                name="📜 Stack Trace",
                value=f"```python\n{tb}```",
                inline=False
            )
        
        # Timestamp footer
        embed.set_footer(text=f"Timestamp: {timestamp} (Vietnam)")
        
        # Send to DEBUG channel (not regular logging channel)
        await send_debug_log(embed)
        
    except Exception as discord_log_err:
        print(f"⚠️ Error logging failed command to Discord: {discord_log_err}")
    
    # Send error message to user
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(error_message, ephemeral=True)
        else:
            await interaction.followup.send(error_message, ephemeral=True)
    except discord.errors.HTTPException as e:
        # Handle cases where we can't respond (interaction expired, etc.)
        if e.code in [10062, 40060]:
            print(f"Could not send error message (interaction expired): {error_message}")
        else:
            print(f"ERROR IN ERROR HANDLER: {e}")
    except Exception as e:
        print(f"ERROR IN ERROR HANDLER: {e}")   


# ============================================================================
# PROMO MESSAGE AFTER SUCCESSFUL COMMANDS
# ============================================================================

@client.event
async def on_app_command_completion(interaction: discord.Interaction, command: app_commands.Command):
    """
    Called after a successful command execution.
    Triggers promo message with 25% chance and 1 minute cooldown per user.
    Also logs command to web dashboard and tracks performance.
    """
    try:
        # Calculate execution time
        execution_time = None
        if interaction.id in command_start_times:
            start_time = command_start_times.pop(interaction.id)
            execution_time = time.time() - start_time
            
            # Log slow commands to DEBUG channel
            if execution_time > SLOW_COMMAND_THRESHOLD:
                command_name = command.name if command else "Unknown"
                
                # Build params string
                params_list = []
                if interaction.namespace:
                    for key, value in interaction.namespace.__dict__.items():
                        if not key.startswith('_'):
                            params_list.append(f"{key}={value}")
                params_str = ", ".join(params_list) if params_list else "No parameters"
                
                # Console warning
                print(f"⚠️ SLOW COMMAND: /{command_name} took {execution_time:.2f}s")
                
                # Send to DEBUG channel
                embed = discord.Embed(
                    title=f"⚠️ Slow Command: /{command_name}",
                    description=f"**Execution Time:** {execution_time:.2f} seconds (threshold: {SLOW_COMMAND_THRESHOLD}s)",
                    color=discord.Color.orange(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                embed.add_field(
                    name="👤 User",
                    value=f"{interaction.user.name} (ID: {interaction.user.id})",
                    inline=True
                )
                if interaction.guild:
                    embed.add_field(
                        name="🏠 Server",
                        value=f"{interaction.guild.name}",
                        inline=True
                    )
                embed.add_field(
                    name="📋 Parameters",
                    value=f"```{params_str}```",
                    inline=False
                )
                embed.set_footer(text="Consider optimizing this command")
                
                await send_debug_log(embed)
        
        # Log command to web dashboard
        params_list = []
        if interaction.namespace:
            for key, value in interaction.namespace.__dict__.items():
                if not key.startswith('_'):
                    params_list.append(f"{key}={value}")
        
        await send_log_to_web(
            log_type="command",
            command=command.name if command else "unknown",
            user=str(interaction.user),
            user_id=interaction.user.id,
            server=interaction.guild.name if interaction.guild else "DM",
            server_id=interaction.guild_id if interaction.guild else None,
            channel=interaction.channel.name if hasattr(interaction.channel, 'name') else "Unknown",
            params=", ".join(params_list) if params_list else None,
            status="success"
        )
        
        # Only trigger for successful commands (response already sent)
        if interaction.response.is_done():
            await maybe_send_promo_message(interaction)
    except Exception as e:
        # Silently fail - promo is not critical
        print(f"Promo/log message error (non-critical): {e}")


# ============================================================================
# GUILD JOIN/LEAVE EVENTS - AUTO CREATE INVITE LINKS
# ============================================================================

@client.event
async def on_guild_join(guild: discord.Guild):
    """Called when bot joins a new server - creates invite link automatically"""
    print(f"\n{'='*60}")
    print(f"🎉 BOT JOINED NEW SERVER")
    print(f"{'='*60}")
    print(f"   Server: {guild.name}")
    print(f"   ID: {guild.id}")
    print(f"   Members: {guild.member_count}")
    print(f"   Owner: {guild.owner}")
    print(f"{'='*60}\n")
    
    # Try to create an invite link
    invite_url = None
    try:
        # Find a text channel to create invite from
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).create_instant_invite:
                invite = await channel.create_invite(max_age=0, max_uses=0)  # Never expires
                invite_url = invite.url
                print(f"✅ Created invite link: {invite_url}")
                break
        
        if not invite_url:
            print("⚠️ Could not create invite - no suitable channel with permissions")
    except Exception as e:
        print(f"⚠️ Could not create invite: {e}")
    
    # Save invite to file
    if invite_url:
        save_server_invite(guild.id, guild.name, invite_url, guild.member_count)
    
    # Send notification to debug channel
    embed = discord.Embed(
        title="🎉 Bot Joined New Server!",
        color=discord.Color.green(),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.add_field(name="🏠 Server", value=f"**{guild.name}**", inline=True)
    embed.add_field(name="🆔 ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="👥 Members", value=str(guild.member_count), inline=True)
    embed.add_field(name="👑 Owner", value=str(guild.owner) if guild.owner else "Unknown", inline=True)
    
    if invite_url:
        embed.add_field(name="🔗 Invite Link", value=invite_url, inline=False)
    else:
        embed.add_field(name="🔗 Invite Link", value="❌ Could not create", inline=False)
    
    await send_debug_log(embed)
    
    # Update channel list message
    await update_channel_list_message()


@client.event
async def on_guild_remove(guild: discord.Guild):
    """Called when bot leaves/is kicked from a server - auto cleanup clubs and data"""
    print(f"\n{'='*60}")
    print(f"👋 BOT LEFT SERVER")
    print(f"{'='*60}")
    print(f"   Server: {guild.name}")
    print(f"   ID: {guild.id}")
    print(f"{'='*60}\n")
    
    # Remove invite from file
    remove_server_invite(guild.id)
    
    # Auto-delete clubs associated with this server
    deleted_clubs = []
    try:
        # Load config sheet
        config_ws = gs_manager.sh.worksheet(config.CONFIG_SHEET_NAME)
        all_rows = config_ws.get_all_values()
        
        if len(all_rows) > 1:
            header = all_rows[0]
            # Find Server_ID column index (should be column J = index 9)
            server_id_col = -1
            club_name_col = 0  # Column A
            
            for idx, col_name in enumerate(header):
                if col_name.lower() == 'server_id':
                    server_id_col = idx
                    break
            
            if server_id_col >= 0:
                # Find rows to delete (from bottom to top to avoid index shifting)
                rows_to_delete = []
                for row_idx, row in enumerate(all_rows[1:], start=2):  # Start from 2 (1-indexed, skip header)
                    if len(row) > server_id_col:
                        row_server_id = row[server_id_col]
                        if str(row_server_id) == str(guild.id):
                            club_name = row[club_name_col] if len(row) > club_name_col else "Unknown"
                            rows_to_delete.append((row_idx, club_name))
                
                # Delete rows from bottom to top
                for row_idx, club_name in sorted(rows_to_delete, reverse=True):
                    try:
                        config_ws.delete_rows(row_idx)
                        deleted_clubs.append(club_name)
                        print(f"🗑️ Deleted club '{club_name}' (Server {guild.id} left)")
                    except Exception as e:
                        print(f"⚠️ Error deleting club {club_name}: {e}")
        
        # Update cache after deletion
        if deleted_clubs:
            await client.update_caches()
            print(f"✅ Auto-deleted {len(deleted_clubs)} club(s) from server {guild.name}")
    
    except Exception as e:
        print(f"⚠️ Error auto-deleting clubs: {e}")
    
    # Send notification to debug channel
    embed = discord.Embed(
        title="👋 Bot Left Server",
        description=f"**{guild.name}** (ID: `{guild.id}`)",
        color=discord.Color.orange(),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    
    if deleted_clubs:
        embed.add_field(
            name="🗑️ Auto-Deleted Clubs",
            value="\n".join([f"• {club}" for club in deleted_clubs][:10]),
            inline=False
        )
    
    await send_debug_log(embed)
    
    # Update channel list message (will auto-remove the channel)
    await update_channel_list_message()


# ============================================================================
# RUN BOT
# ============================================================================

@client.tree.command(name="cache_stats", description="Admin: View cache statistics")
@is_admin_or_has_role()
async def cache_stats(interaction: discord.Interaction):
    """Show cache statistics for monitoring"""
    await interaction.response.defer(ephemeral=True)
    
    stats = smart_cache.get_stats()
    
    embed = discord.Embed(
        title="📊 Cache Statistics",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="Smart Cache (In-Memory)",
        value=(
            f"**Entries:** {stats['total_entries']}\n"
            f"**Size:** {stats['total_size_mb']} MB\n"
            f"**TTL:** {stats['ttl_minutes']} minutes"
        ),
        inline=False
    )
    
    if stats['keys']:
        # Show cache ages
        cache_ages = stats.get('cache_ages', {})
        if cache_ages:
            age_info = []
            for key in stats['keys'][:5]:
                age = cache_ages.get(key, 0)
                age_info.append(f"{key}: {age:.1f} min")
            
            if len(stats['keys']) > 5:
                age_info.append(f"... and {len(stats['keys']) - 5} more")
            
            embed.add_field(
                name="Cached Keys (Age)",
                value=f"```{chr(10).join(age_info)}```",
                inline=False
            )
        else:
            keys_preview = '\n'.join(stats['keys'][:5])
            if len(stats['keys']) > 5:
                keys_preview += f"\n... and {len(stats['keys']) - 5} more"
            
            embed.add_field(
                name="Cached Keys",
                value=f"```{keys_preview}```",
                inline=False
            )
    
    embed.add_field(
        name="Bot Cache",
        value=(
            f"**Clubs:** {len(client.config_cache)}\n"
            f"**Members:** {sum(len(m) for m in client.member_cache.values())}"
        ),
        inline=False
    )
    
    await interaction.followup.send(embed=embed, ephemeral=True)


# ============================================================================
# CHANNEL MANAGEMENT COMMANDS (Server Owner)
# ============================================================================

@client.tree.command(name="set_channel", description="Set THIS channel as allowed for bot (Admin/Owner)")
@is_admin_or_has_role()
async def set_channel(interaction: discord.Interaction):
    """Set current channel as allowed channel - ONE channel per server"""
    
    await interaction.response.defer(ephemeral=False)
    
    if not interaction.guild:
        await interaction.followup.send(
            "❌ This command can only be used in a server!",
            ephemeral=True
        )
        return
    
    try:
        server_id = interaction.guild_id
        channel_id = interaction.channel_id
        channel = interaction.channel
        
        # Load existing channels
        channels_config = load_channels_config()
        
        # Check if this server already has a channel
        old_channel = None
        for ch in channels_config:
            if ch.get('server_id') == server_id:
                old_channel = ch
                break
        
        # Remove old channel for this server
        channels_config = [ch for ch in channels_config if ch.get('server_id') != server_id]
        
        # Add new channel
        channels_config.append({
            'server_id': server_id,
            'server_name': interaction.guild.name,
            'channel_id': channel_id,
            'channel_name': channel.name,
            'added_by': interaction.user.id,
            'added_by_name': str(interaction.user),
            'added_at': datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
        })
        
        # Update in-memory config
        config.ALLOWED_CHANNEL_IDS = [ch['channel_id'] for ch in channels_config]
        
        # Save to file
        config_data = {
            "channels": channels_config,
            "last_updated": datetime.datetime.now(pytz.timezone('Asia/Ho_Chi_Minh')).strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(ALLOWED_CHANNELS_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        
        # Update permanent channel list message
        await update_channel_list_message()
        
        # Build response message
        if old_channel:
            response = (
                f"✅ **Channel Updated**\n\n"
                f"**Server:** {interaction.guild.name}\n"
                f"**Old Channel:** #{old_channel.get('channel_name', 'Unknown')} (ID: `{old_channel.get('channel_id')}`)\n"
                f"**New Channel:** {channel.mention} (ID: `{channel_id}`)\n\n"
                f"Bot commands can now only be used in this channel for this server."
            )
        else:
            response = (
                f"✅ **Channel Set**\n\n"
                f"**Server:** {interaction.guild.name}\n"
                f"**Channel:** {channel.mention} (ID: `{channel_id}`)\n\n"
                f"Bot commands can now only be used in this channel for this server."
            )
        
        await interaction.followup.send(response, ephemeral=False)
        
        # Sync to web dashboard
        await sync_channels_to_web()
        await sync_stats_to_web()
    
    except Exception as e:
        await interaction.followup.send(
            f"❌ Error: {e}",
            ephemeral=True
        )

# /list_channels command removed - replaced by permanent message in a designated channel


# Duplicate clear_cache command removed - see line 2633 for the main implementation


# ============================================================================
# SCHEDULED TASK: UPDATE CLUB RANKS + SYNC MEMBER DATA (7:30 AM/PM Vietnam)
# ============================================================================

async def fetch_club_data_full(club_id: str, max_retries: int = 3, use_proxy: bool = True) -> dict:
    """Fetch full club data from uma.moe API including members
    
    Args:
        club_id: The club/circle ID to fetch
        max_retries: Number of retry attempts for transient errors
        use_proxy: Whether to use proxy rotation (default: True)
    
    Returns:
        {
            'circle': {..., 'monthly_rank': int},
            'members': [{'viewer_id': int, 'trainer_name': str, 'daily_fans': [int, ...]}]
        }
    """
    url = f"https://uma.moe/api/v4/circles?circle_id={club_id}"
    
    for attempt in range(max_retries):
        try:
            # Use proxy rotation for faster requests
            proxy_url = proxy_manager.get_next_proxy() if use_proxy else None
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, 
                    timeout=aiohttp.ClientTimeout(total=30),
                    proxy=proxy_url
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data if isinstance(data, dict) else None
                    
                    # Check for retryable HTTP errors (502, 503, 504)
                    if response.status in [502, 503, 504]:
                        if attempt + 1 < max_retries:
                            wait_time = 2 * (2 ** attempt)  # 2s, 4s, 8s (faster with proxy rotation)
                            print(f"⚠️ API returned {response.status} for club {club_id}. Retrying with different proxy in {wait_time}s... ({attempt + 1}/{max_retries})")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            print(f"❌ API returned {response.status} for club {club_id} after {max_retries} attempts")
                            return None
                    
                    return None
                    
        except asyncio.TimeoutError:
            if attempt + 1 < max_retries:
                wait_time = 2 * (2 ** attempt)
                print(f"⚠️ Timeout fetching club {club_id}. Retrying with different proxy in {wait_time}s... ({attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                continue
            else:
                print(f"❌ Timeout for club {club_id} after {max_retries} attempts")
                return None
                
        except Exception as e:
            error_str = str(e).lower()
            # Check if retryable error
            if any(kw in error_str for kw in ['502', '503', '504', 'server error', 'bad gateway', 'temporarily unavailable', 'proxy']):
                if attempt + 1 < max_retries:
                    wait_time = 2 * (2 ** attempt)
                    print(f"⚠️ Error fetching club {club_id}: {e}. Retrying with different proxy in {wait_time}s... ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                    continue
            
            print(f"❌ Error fetching data for club {club_id}: {e}")
            return None
    
    return None


def calculate_daily_gains_from_cumulative(cumulative_fans: list) -> list:
    """
    Convert cumulative fans to daily gains using fetch-back approach.
    
    Formula: daily_gain[Day N] = cumulative[Day N+1] - cumulative[Day N]
    
    Args:
        cumulative_fans: [514871769, 518769609, 525082220, ...]
                            Day 1      Day 2      Day 3
    
    Returns:
        [3897840, 6312611, ...]  
        Day 1    Day 2   (calculated from Day 2, Day 3)
    
    Note: Last element of cumulative cannot have daily gain yet
        (today's data is incomplete until tomorrow)
    """
    if not cumulative_fans or len(cumulative_fans) < 2:
        return []
    
    daily_gains = []
    for i in range(len(cumulative_fans) - 1):  # Can't calculate last day
        if cumulative_fans[i] == 0:
            daily_gains.append(None)  # No baseline yet (member not joined)
        elif cumulative_fans[i+1] == 0:
            daily_gains.append(None)  # Member left/inactive
        else:
            gain = cumulative_fans[i+1] - cumulative_fans[i]
            daily_gains.append(gain if gain >= 0 else None)  # Negative = invalid
    return daily_gains


def apply_yui_logic(daily_gains: list, target_per_day: int) -> tuple:
    """
    Apply Yui Logic: Calculate adjusted target for late joiners
    
    Args:
        daily_gains: List of daily gains (may contain None for days not active)
        target_per_day: Default target per day
    
    Returns:
        (start_day, adjusted_target, is_new_member)
        - start_day: First day with data (1-indexed)
        - adjusted_target: Target adjusted for days active
        - is_new_member: True if joined after Day 1
    """
    if not daily_gains:
        return (1, target_per_day, False)
    
    # Find first day with data (non-None value)
    start_day = 1
    for i, gain in enumerate(daily_gains):
        if gain is not None and gain > 0:
            start_day = i + 1  # 1-indexed
            break
    else:
        # No valid data found
        return (1, target_per_day, False)
    
    total_days = len(daily_gains)
    days_active = total_days - start_day + 1
    
    is_new_member = start_day > 1
    adjusted_target = target_per_day * days_active
    
    return (start_day, adjusted_target, is_new_member)


def calculate_data_sheet_rows(trainer_name: str, daily_gains: list, cumulative_fans: list, target_per_day: int, max_days: int = None) -> list:
    """
    Calculate Data sheet rows for a member.
    
    Data sheet format: Name, Day, Total Fans, Daily, Target, CarryOver
    
    Args:
        trainer_name: Member name
        daily_gains: List of daily gains (from calculate_daily_gains_from_cumulative)
        cumulative_fans: Original cumulative fans from API
        target_per_day: Club's KPI per day
    
    Returns:
        List of rows: [[name, day, total_fans, daily, target, carryover], ...]
    """
    if not daily_gains or not cumulative_fans:
        return []
    
    rows = []
    
    # Find first day with positive gain (Yui logic - start counting from this day)
    start_day = 1
    for i, gain in enumerate(daily_gains):
        if gain is not None and gain > 0:
            start_day = i + 1  # 1-indexed
            break
    
    # Get the base fan count (before first active day)
    if start_day > 1 and start_day <= len(cumulative_fans):
        fan_base = cumulative_fans[start_day - 2] if start_day >= 2 else 0
    else:
        fan_base = 0
    
    effective_day_counter = 0
    
    # Limit to max_days if provided (don't generate rows for days without data)
    days_to_process = min(len(daily_gains), max_days) if max_days else len(daily_gains)
    
    for day_idx in range(days_to_process):
        gain = daily_gains[day_idx] if day_idx < len(daily_gains) else None
        day_num = day_idx + 1  # 1-indexed
        
        # Get Total Fans for this day (cumulative from start of month)
        # Total Fans = sum of daily gains from Day 1 to current day
        total_fans = sum(g for g in daily_gains[:day_idx + 1] if g is not None and g >= 0)
        
        # Daily = current day's gain
        daily = gain if gain is not None and gain >= 0 else 0
        
        # Calculate effective days for Target (Yui logic)
        if day_num >= start_day:
            effective_day_counter += 1
            target = effective_day_counter * target_per_day
        else:
            # Before member started, no target
            target = 0
        
        # CarryOver = Total Fans - Target
        carryover = total_fans - target
        
        rows.append([
            trainer_name,
            day_num,
            total_fans,
            daily,
            target,
            carryover
        ])
    
    return rows

def get_member_last_active_day(daily_gains: list) -> int:
    """Get the last day a member had activity (non-None gain)"""
    if not daily_gains:
        return 0
    
    last_day = 0
    for i, gain in enumerate(daily_gains):
        if gain is not None:
            last_day = i + 1  # 1-indexed
    return last_day


def is_member_in_club(daily_gains: list, max_day: int) -> bool:
    """
    Check if member is still in the club.
    
    Logic: uma.moe updates ALL members at once. If a member has no data for 
    the current max day, they have left the club. Even 0 is valid data.
    
    Args:
        daily_gains: Member's daily gains list (None = no data, 0 = valid data)
        max_day: Current max day number (1-indexed)
    
    Returns:
        True if member is in club (has data for max day), False if left
    """
    if not daily_gains or max_day <= 0:
        return False
    
    # Check if we have enough days in the list
    if len(daily_gains) < max_day:
        return False
    
    # Check if the member has data for max day (None = no data = left club)
    max_day_data = daily_gains[max_day - 1]  # 0-indexed
    return max_day_data is not None


def is_member_active(daily_gains: list, current_day: int, tolerance: int = 2) -> bool:
    """
    Check if member is still active (hasn't left the club)
    DEPRECATED: Use is_member_in_club instead for accurate detection.
    
    Args:
        daily_gains: Member's daily gains list
        current_day: Current day number (max day in all members)
        tolerance: Number of days behind allowed before considering inactive
    
    Returns:
        True if member is active, False if likely left the club
    """
    # Use the new logic
    return is_member_in_club(daily_gains, current_day)


def detect_inactive_members(club_members: list, max_day: int, inactive_threshold: int = 2) -> list:
    """
    Detect members who haven't updated daily fan data for 2+ days.
    
    uma.moe API updates ALL members at once (success all or fail all).
    If a member has fewer days of data than max_day, they are either:
    - A new member (joined later)
    - Inactive/left the club
    
    Args:
        club_members: List of {'viewer_id', 'trainer_name', 'daily_fans': [cumulative values]}
        max_day: Current max day of the club (most data any member has)
        inactive_threshold: Number of missing days to flag as inactive (default: 2)
    
    Returns:
        List of inactive members: [{'viewer_id', 'trainer_name', 'last_active_day', 'missing_days'}]
    
    Example:
        If max_day = 10 and member has 7 days of data:
        - missing_days = 10 - 7 = 3
        - If threshold = 2, this member is flagged as INACTIVE
    """
    if not club_members or max_day <= 0:
        return []
    
    inactive = []
    
    for member in club_members:
        viewer_id = member.get('viewer_id', 'Unknown')
        trainer_name = member.get('trainer_name', 'Unknown')
        daily_fans = member.get('daily_fans', [])
        
        # Count actual days with data
        member_days = len(daily_fans) if daily_fans else 0
        
        # Calculate missing days
        if member_days < max_day:
            missing_days = max_day - member_days
            
            # Only flag if missing >= threshold
            if missing_days >= inactive_threshold:
                inactive.append({
                    'viewer_id': viewer_id,
                    'trainer_name': trainer_name,
                    'last_active_day': member_days,
                    'missing_days': missing_days
                })
    
    return inactive


def get_club_max_day(club_members: list) -> int:
    """
    Get the maximum day number from all club members.
    This represents the most up-to-date data point.
    
    Args:
        club_members: List of member dicts with 'daily_fans' arrays
    
    Returns:
        Maximum number of days any member has data for
    """
    if not club_members:
        return 0
    
    max_day = 0
    for member in club_members:
        daily_fans = member.get('daily_fans', [])
        if daily_fans:
            max_day = max(max_day, len(daily_fans))
    
    return max_day


# ============================================================================
# MONTHLY ARCHIVE HELPER FUNCTIONS
# ============================================================================

async def get_current_month_from_sheet(worksheet) -> str:
    """
    Get the current month from sheet's first row
    Supports both formats: "=== CURRENT: 12/2024 ===" and "=== CURRENT 12/2025 ==="
    Returns: "MM/YYYY" or None if not found
    """
    try:
        first_cell = await asyncio.to_thread(worksheet.acell, 'A1')
        if first_cell and first_cell.value:
            value = first_cell.value
            # Check if it's a CURRENT header (with or without colon)
            if '=== CURRENT' in value:
                # Extract MM/YYYY from header (works for both formats)
                import re
                match = re.search(r'(\d{1,2}/\d{4})', value)
                if match:
                    return match.group(1)
        return None
    except Exception as e:
        print(f"    [Archive] Error reading sheet month: {e}")
        return None


async def archive_current_data(worksheet, current_month: str):
    """
    Move current month's data to archive section at bottom of sheet.
    
    1. Read all current data
    2. Find where archive starts (empty row or "=== ARCHIVE")
    3. Insert current data as new archive section after last row
    
    Includes retry logic for rate limiting (429 errors).
    """
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            # Add delay between retries (exponential backoff)
            if attempt > 0:
                wait_time = 10 * (2 ** (attempt - 1))  # 10s, 20s, 40s
                print(f"    [Archive] Retry {attempt + 1}/{max_retries} in {wait_time}s...")
                await asyncio.sleep(wait_time)
            
            # Get all values from sheet
            all_values = await asyncio.to_thread(worksheet.get_all_values)
            
            if len(all_values) <= 1:
                print(f"    [Archive] No data to archive")
                return True
            
            # Find the current data section (from row 1 until empty row or ARCHIVE)
            current_data = []
            archive_start_row = None
            
            for idx, row in enumerate(all_values):
                # Check if this is an archive header or empty row
                if not row or not row[0]:
                    archive_start_row = idx
                    break
                if '=== ARCHIVE' in row[0]:
                    archive_start_row = idx
                    break
                current_data.append(row)
            
            if archive_start_row is None:
                # All rows are current data
                archive_start_row = len(all_values)
            
            if len(current_data) <= 1:
                print(f"    [Archive] Only header, nothing to archive")
                return True
            
            # Get existing archive data (everything from archive_start_row onwards)
            existing_archive = all_values[archive_start_row:] if archive_start_row < len(all_values) else []
            
            # Build archive header for the current month
            archive_header = [f"=== ARCHIVE: {current_month} ==="]
            # Pad to match column count
            if current_data:
                archive_header.extend([''] * (len(current_data[0]) - 1))
            
            # Build new sheet structure:
            # 1. Empty (will be filled with new current data later)
            # 2. Empty spacer rows
            # 3. Archive header for this month
            # 4. Current data (now archived)
            # 5. Empty spacer rows
            # 6. Existing archive data
            
            # Calculate where to put archive
            spacer = [[''] * len(current_data[0])] * 2 if current_data else [['', '']] * 2
            
            # Only update the archive section (don't touch the top - that will be updated separately)
            # We append the new archive at the bottom
            new_archive_section = []
            new_archive_section.append(archive_header)
            new_archive_section.extend(current_data)  # The data being archived (including header)
            new_archive_section.extend(spacer)
            
            # Append to existing archive
            start_row = archive_start_row + 1 if archive_start_row > 0 else len(current_data) + 3
            
            print(f"    [Archive] Archiving {len(current_data)} rows from {current_month}")
            
            # Add small delay before write to avoid rate limiting
            await asyncio.sleep(2)
            
            # First, add spacer after current data
            await asyncio.to_thread(
                worksheet.update,
                f'A{len(current_data) + 1}',
                spacer + [archive_header] + current_data[1:] + spacer  # Skip first header in archived data
            )
            
            return True
            
        except Exception as e:
            error_str = str(e).lower()
            is_rate_limit = '429' in error_str or 'quota' in error_str
            
            if is_rate_limit and attempt < max_retries - 1:
                print(f"    [Archive] Rate limit hit, will retry...")
                continue
            else:
                print(f"    [Archive] Error archiving data: {e}")
                import traceback
                traceback.print_exc()
                return False
    
    return False


def get_current_month_string() -> str:
    """Get current month as MM/YYYY string (Vietnam timezone)"""
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    now = datetime.datetime.now(vietnam_tz)
    return f"{now.month:02d}/{now.year}"


def get_days_in_month(month_str: str) -> int:
    """
    Get the number of days in a month from MM/YYYY string.
    
    Args:
        month_str: Month string in format "MM/YYYY" (e.g., "01/2026", "02/2024")
    
    Returns:
        Number of days in that month (28-31)
    """
    try:
        parts = month_str.split('/')
        month = int(parts[0])
        year = int(parts[1])
        
        # Months with 31 days
        if month in [1, 3, 5, 7, 8, 10, 12]:
            return 31
        # Months with 30 days
        elif month in [4, 6, 9, 11]:
            return 30
        # February - check for leap year
        elif month == 2:
            # Leap year: divisible by 4, except century years must be divisible by 400
            is_leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
            return 29 if is_leap else 28
        else:
            return 30  # Fallback
    except Exception:
        return 30  # Safe fallback


async def get_archived_member_ids(worksheet, target_month: str) -> set:
    """
    Get Trainer IDs from archived data of a specific month.
    
    Args:
        worksheet: Google Sheets worksheet object
        target_month: Month to search in format "MM/YYYY" (e.g., "01/2026")
    
    Returns:
        Set of Trainer IDs found in the archive for that month
    """
    try:
        all_values = await asyncio.to_thread(worksheet.get_all_values)
        
        archived_ids = set()
        in_target_archive = False
        
        for row in all_values:
            if not row or not row[0]:
                continue
            
            # Check if this is the archive header for our target month
            if f"=== ARCHIVE: {target_month}" in row[0]:
                in_target_archive = True
                continue
            
            # Check if we've hit another archive section (end of our target)
            if in_target_archive and "=== ARCHIVE:" in row[0]:
                break
            
            # Skip header rows within archive
            if in_target_archive and row[0] in ['Trainer ID', 'Name', '']:
                continue
            
            # Collect Trainer ID (first column)
            if in_target_archive and row[0]:
                archived_ids.add(str(row[0]))
        
        return archived_ids
        
    except Exception as e:
        print(f"Error getting archived member IDs: {e}")
        return set()


def calculate_last_day_gain(cumulative_fans: list, expected_days: int) -> int:
    """
    Calculate the daily gain for the last day of the month.
    
    When uma.moe provides Day 1 of new month, we can calculate:
    last_day_gain = cumulative[Day1_new] - cumulative[last_day_old]
    
    Args:
        cumulative_fans: List of cumulative fans including Day 1 of new month
        expected_days: Number of days in the old month (e.g., 31 for January)
    
    Returns:
        Daily gain for the last day, or None if cannot calculate
    """
    try:
        # cumulative_fans = [Day1, Day2, ..., Day31, Day1_new]
        # For 31-day month: index 30 = Day 31, index 31 = Day 1 new
        if len(cumulative_fans) <= expected_days:
            return None  # No new month data yet
        
        last_day_cumulative = cumulative_fans[expected_days - 1]  # Day 31 = index 30
        new_month_day1_cumulative = cumulative_fans[expected_days]  # Day 1 new = index 31
        
        if last_day_cumulative == 0 or new_month_day1_cumulative == 0:
            return None
        
        gain = new_month_day1_cumulative - last_day_cumulative
        return gain if gain >= 0 else None
        
    except Exception as e:
        print(f"Error calculating last day gain: {e}")
        return None


async def sync_club_member_data_to_sheet(
    club_name: str, 
    club_config: dict, 
    members: list, 
    config_ws
) -> dict:
    """
    Sync member daily data from uma.moe API to Google Sheets Data sheet.
    
    Args:
        club_name: Name of the club
        club_config: Club configuration dict containing Data_Sheet_Name, Target_Per_Day
        members: List of members from uma.moe API with daily_fans data
        config_ws: Config worksheet reference for error handling
    
    Returns:
        {'success': bool, 'rows_written': int, 'errors': list}
    """
    result = {'success': False, 'rows_written': 0, 'errors': []}
    
    try:
        data_sheet_name = club_config.get('Data_Sheet_Name')
        if not data_sheet_name:
            result['errors'].append(f"No Data_Sheet_Name configured")
            return result
        
        target_per_day = int(club_config.get('Target_Per_Day', 1000000))
        
        # Get the data worksheet
        try:
            data_ws = await asyncio.to_thread(gs_manager.sh.worksheet, data_sheet_name)
        except Exception as e:
            result['errors'].append(f"Cannot access sheet {data_sheet_name}: {e}")
            return result
        
        # Calculate max_day from all members
        max_day = get_club_max_day(members)
        if max_day < 2:
            result['errors'].append("Not enough data (need at least 2 days)")
            return result
        
        # Build all rows for all members
        all_rows = []
        header = ['Name', 'Day', 'Total Fans', 'Daily', 'Target', 'CarryOver']
        all_rows.append(header)
        
        for member in members:
            trainer_name = member.get('trainer_name', 'Unknown')
            cumulative_fans = member.get('daily_fans', [])
            
            if not cumulative_fans:
                continue
            
            # Calculate daily gains from cumulative
            daily_gains = calculate_daily_gains_from_cumulative(cumulative_fans)
            
            if not daily_gains:
                continue
            
            # Calculate rows with Yui logic applied
            # max_days = completed days only (not including today)
            completed_days = len(daily_gains)  # Already excludes incomplete last day
            
            member_rows = calculate_data_sheet_rows(
                trainer_name=trainer_name,
                daily_gains=daily_gains,
                cumulative_fans=cumulative_fans,
                target_per_day=target_per_day,
                max_days=completed_days
            )
            
            all_rows.extend(member_rows)
        
        if len(all_rows) <= 1:  # Only header
            result['errors'].append("No valid member data to write")
            return result
        
        # Clear existing data and write new data
        # First, clear the sheet (keep only the area we'll write to)
        try:
            # Clear existing content
            await asyncio.to_thread(data_ws.clear)
            
            # Write all rows at once (batch update for efficiency)
            await asyncio.to_thread(
                data_ws.update,
                'A1',
                all_rows
            )
            
            result['success'] = True
            result['rows_written'] = len(all_rows) - 1  # Exclude header
            
        except Exception as e:
            result['errors'].append(f"Error writing to sheet: {e}")
            return result
        
    except Exception as e:
        result['errors'].append(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    
    return result


@tasks.loop(time=[
    dt_time(hour=0, minute=0, tzinfo=pytz.UTC),     # 00:00 UTC = 7:00 AM Vietnam (GMT+7)
    dt_time(hour=15, minute=30, tzinfo=pytz.UTC),   # 15:30 UTC = 10:30 PM Vietnam (GMT+7)
    dt_time(hour=17, minute=0, tzinfo=pytz.UTC),    # 17:00 UTC = 12:00 AM Vietnam (midnight, GMT+7)
])
async def update_club_data_task():
    """Three times daily task to update club ranks AND member data from uma.moe API
    
    Runs at:
    - 7:00 AM Vietnam (00:00 UTC)
    - 10:30 PM Vietnam (15:30 UTC)  
    - 12:00 AM Vietnam/midnight (17:00 UTC)
    
    - Updates club monthly rank (column K)
    - Syncs member daily fan data to each club's member sheet
    - Applies Yui logic for late joiners
    - Auto-detects new month and archives old data
    """
    print(f"\n[{datetime.datetime.now()}] ====== STARTING CLUB DATA SYNC ======")
    
    try:
        # Read clubs directly from Google Sheets (no cache dependency)
        config_ws = await asyncio.to_thread(
            gs_manager.sh.worksheet, config.CONFIG_SHEET_NAME
        )
        all_configs = await asyncio.to_thread(config_ws.get_all_records)
        
        total_clubs = len(all_configs)
        print(f"  📊 Found {total_clubs} clubs in Sheets")
        
        if total_clubs == 0:
            print("  ⚠️ No clubs found in config sheet!")
            return
        
        rank_updated = 0
        members_synced = 0
        error_count = 0
        skipped_no_id = 0
        failed_clubs = []  # Track clubs that failed to get rank data for retry
        transfer_warnings_log = []  # Track potential club transfers
        
        for idx, club_config in enumerate(all_configs, start=2):  # Start from row 2 (after header)
            club_name = club_config.get('Club_Name', '')
            club_id = str(club_config.get('Club_ID', '')).strip()
            members_sheet_name = club_config.get('Members_Sheet_Name', '')
            target_per_day = int(club_config.get('Target_Per_Day', 0))
            
            if not club_id:
                skipped_no_id += 1
                continue
            
            # Rate limit: 0.5s between clubs (reduced with proxy rotation)
            if idx > 2:  # Skip delay for first club
                await asyncio.sleep(0.5)
            
            try:
                # Fetch full club data from API
                api_data = await fetch_club_data_full(club_id)
                
                if not api_data:
                    print(f"  ⚠️ {club_name} (ID: {club_id}): API returned no data")
                    failed_clubs.append({'idx': idx, 'config': club_config, 'reason': 'no_data'})
                    continue
                
                # ===== UPDATE RANK =====
                circle_data = api_data.get('circle', {})
                rank = circle_data.get('monthly_rank')
                
                # Ensure rank is not None before comparison
                if rank is not None and rank > 0:
                    # Update column K (Rank) - column 11
                    await asyncio.to_thread(
                        config_ws.update_cell, idx, 11, rank
                    )
                    
                    # Update config_cache directly
                    if club_name in client.config_cache:
                        client.config_cache[club_name]['Rank'] = rank
                    
                    rank_updated += 1
                    print(f"  ✅ {club_name}: Rank #{rank}")
                else:
                    # No rank data - add to retry list
                    print(f"  ⏭️ {club_name}: No rank data available (will retry)")
                    failed_clubs.append({'idx': idx, 'config': club_config, 'reason': 'no_rank'})
                
                # ===== SYNC MEMBER DATA =====
                if not members_sheet_name:
                    print(f"  ⏭️ {club_name}: No Members_Sheet_Name configured, skipping member sync")
                    continue
                
                api_members = api_data.get('members', [])
                if not api_members:
                    print(f"  ⏭️ {club_name}: No members in API response")
                    continue
                
                # Get current month for labeling
                vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
                current_date = datetime.datetime.now(vietnam_tz)
                
                # Find max_days by looking at the highest day with data across ALL members
                # API returns 31 slots (Day 1-31), 0 means no data for that day
                max_data_day = 0
                for member in api_members:
                    cumulative = member.get('daily_fans', [])
                    for day_idx in range(len(cumulative) - 1, -1, -1):  # Reverse search
                        if cumulative[day_idx] > 0:
                            max_data_day = max(max_data_day, day_idx + 1)  # 1-indexed
                            break
                
                # max_days for daily_gains is max_data_day - 1 (because we calculate gain from consecutive days)
                max_days = max_data_day - 1 if max_data_day > 1 else 0
                current_day = max_days
                
                if max_days < 1:
                    print(f"  ⏭️ {club_name}: Not enough data yet (max_data_day={max_data_day}), skipping")
                    continue
                
                print(f"    [DEBUG] Current date: {current_date.strftime('%Y-%m-%d')}, max_days={max_days} (max_data_day={max_data_day})")
                
                # Build rows for sheet update
                rows_data = []
                data_sheet_rows = []  # For Data sheet: Name, Day, Total Fans, Daily, Target, CarryOver
                skipped_inactive = 0
                transfer_warnings_log = []  # Collect transfer warnings for Discord notification
                
                # Check if this is a new month transition - need to calculate last day of old month
                current_month = get_current_month_string()
                expected_days_old_month = None
                has_new_month_data = False
                
                # Calculate expected days for PREVIOUS month (not current)
                parts = current_month.split('/')
                prev_month_num = int(parts[0]) - 1
                prev_year = int(parts[1])
                if prev_month_num < 1:
                    prev_month_num = 12
                    prev_year -= 1
                prev_month_str = f"{prev_month_num:02d}/{prev_year}"
                expected_days_prev_month = get_days_in_month(prev_month_str)
                
                # Pre-check for new month data
                for member in api_members:
                    member_cumulative = member.get('daily_fans', [])
                    if len(member_cumulative) > expected_days_prev_month:  # Has data beyond previous month
                        has_new_month_data = True
                        expected_days_old_month = expected_days_prev_month
                        break
                
                # Get archived member IDs for transfer detection (only if we have archive)
                archived_ids = set()
                try:
                    # Get previous month string
                    parts = current_month.split('/')
                    prev_month = int(parts[0]) - 1
                    prev_year = int(parts[1])
                    if prev_month < 1:
                        prev_month = 12
                        prev_year -= 1
                    previous_month_str = f"{prev_month:02d}/{prev_year}"
                    
                    # Get worksheet and archived IDs
                    temp_ws = await asyncio.to_thread(
                        gs_manager.sh.worksheet, members_sheet_name
                    )
                    archived_ids = await get_archived_member_ids(temp_ws, previous_month_str)
                    if archived_ids:
                        print(f"    📋 Found {len(archived_ids)} members in {previous_month_str} archive for transfer detection")
                except Exception as e:
                    print(f"    ⚠️ Could not load archived IDs for transfer detection: {e}")
                
                for idx_m, member in enumerate(api_members):
                    trainer_id = str(member.get('viewer_id', ''))
                    trainer_name = member.get('trainer_name', '')
                    cumulative = member.get('daily_fans', [])
                    
                    if not trainer_id or not cumulative:
                        continue
                    
                    # Calculate daily gains using fetch-back formula
                    daily_gains = calculate_daily_gains_from_cumulative(cumulative)
                    
                    # If we have new month data, calculate last day gain and append
                    if has_new_month_data and expected_days_old_month:
                        last_day_gain = calculate_last_day_gain(cumulative, expected_days_old_month)
                        if last_day_gain is not None:
                            daily_gains.append(last_day_gain)
                    
                    # Debug: Print first member's data
                    if idx_m == 0:
                        print(f"    [DEBUG] First member: {trainer_name}")
                        print(f"    [DEBUG] Cumulative (first 5): {cumulative[:5]}")
                        print(f"    [DEBUG] Daily gains (first 5): {daily_gains[:5] if daily_gains else 'empty'}")
                        print(f"    [DEBUG] max_days={max_days}, current_day={current_day}")
                        if daily_gains:
                            last_active = get_member_last_active_day(daily_gains)
                            print(f"    [DEBUG] last_active_day={last_active}")
                    
                    # Filter: Skip members who have left the club
                    # Check if member has cumulative data for max_data_day (not daily_gains)
                    # This correctly handles new members who only have 1 day of data
                    has_current_data = len(cumulative) >= max_data_day and cumulative[max_data_day - 1] > 0
                    if not has_current_data:
                        skipped_inactive += 1
                        print(f"    ❌ Filtered out: {trainer_name} (ID: {trainer_id}) - Không có data ngày {max_data_day}, đã rời club")
                        continue
                    
                    # Apply Yui logic
                    start_day, adjusted_target, is_new = apply_yui_logic(daily_gains, target_per_day)
                    
                    # Transfer detection: Check if member is new and starts from Day 2
                    transfer_warning = ""
                    if archived_ids:  # Only check if we have archived data
                        is_not_in_previous_month = trainer_id not in archived_ids
                        starts_from_day_2 = start_day == 2
                        if is_not_in_previous_month and starts_from_day_2:
                            transfer_warning = "⚠️ Possible Transfer"
                            transfer_warnings_log.append({
                                'club': club_name,
                                'trainer_name': trainer_name,
                                'trainer_id': trainer_id
                            })
                            print(f"    ⚠️ Transfer warning: {trainer_name} (ID: {trainer_id}) - Không có trong archive tháng trước + bắt đầu từ Day 2")
                    
                    # Build row: Trainer ID, Name, Day 1..N, TotalFans, _YuiStartDay, _YuiTarget, _IsNewMember, _TransferWarning
                    row = [trainer_id, trainer_name]
                    
                    for day in range(max_days):
                        if day < len(daily_gains) and daily_gains[day] is not None:
                            row.append(daily_gains[day])
                        else:
                            row.append('')  # Empty cell
                    
                    # Get Day 31 cumulative (last day's total fans for month-end calculation)
                    # This is the cumulative fans for the last day with data in old month
                    day31_cumulative = 0
                    if len(cumulative) >= expected_days_prev_month:
                        # Get the cumulative for the last day of previous month
                        day31_cumulative = cumulative[expected_days_prev_month - 1] if cumulative[expected_days_prev_month - 1] > 0 else 0
                    else:
                        # Fallback: get last non-zero cumulative
                        for c in reversed(cumulative):
                            if c > 0:
                                day31_cumulative = c
                                break
                    
                    row.extend([
                        start_day if start_day <= max_days else 'N/A',
                        adjusted_target,
                        'Yes' if is_new else 'No',
                        day31_cumulative,  # _Day31_Cumulative column
                        transfer_warning
                    ])
                    
                    rows_data.append(row)
                    
                    # Build Data sheet rows for this member (limit to max_days)
                    member_data_rows = calculate_data_sheet_rows(
                        trainer_name, daily_gains, cumulative, target_per_day, max_days
                    )
                    data_sheet_rows.extend(member_data_rows)
                
                if not rows_data:
                    print(f"  ⏭️ {club_name}: No active members to sync (skipped {skipped_inactive} inactive)")
                    continue
                
                # Build header row
                header = ['Trainer ID', 'Name']
                header.extend([f'Day {i}' for i in range(1, max_days + 1)])
                header.extend(['_YuiStartDay', '_YuiTarget', '_IsNewMember', '_Day31_Cumulative', '_TransferWarning'])
                
                # Write to member sheet
                try:
                    member_ws = await asyncio.to_thread(
                        gs_manager.sh.worksheet, members_sheet_name
                    )
                    
                    # Get current month string (MM/YYYY)
                    current_month = get_current_month_string()
                    
                    # Read existing sheet data to check for archives
                    await asyncio.sleep(0.5)  # Reduced rate limiting with proxies
                    all_values = await asyncio.to_thread(member_ws.get_all_values)
                    
                    # Find existing archives (everything after === ARCHIVE)
                    existing_archives = []
                    archive_start = None
                    sheet_month = None
                    has_current_header = False
                    legacy_complete_data = False  # Flag for legacy sheets with complete month data
                    legacy_day_count = 0
                    
                    for idx_v, row in enumerate(all_values):
                        if row and row[0]:
                            if '=== CURRENT' in row[0]:
                                has_current_header = True
                                # Extract month from header
                                import re
                                match = re.search(r'(\d{1,2}/\d{4})', row[0])
                                if match:
                                    sheet_month = match.group(1)
                            elif '=== ARCHIVE' in row[0]:
                                archive_start = idx_v
                                break
                    
                    # Handle legacy sheets without CURRENT header
                    if not has_current_header and len(all_values) > 1:
                        # Sheet has data but no CURRENT header - this is legacy format
                        print(f"    📋 Legacy format detected (no CURRENT header)")
                        
                        # SMART DETECTION: Count Day columns to determine which month's data is on sheet
                        # Find header row (first row with "Trainer ID" or "Name")
                        sheet_header = None
                        for row in all_values:
                            if row and ('Trainer ID' in row or 'Name' in row):
                                sheet_header = row
                                break
                        
                        if sheet_header:
                            # Count "Day X" columns
                            day_columns = [col for col in sheet_header if col.startswith('Day ')]
                            sheet_day_count = len(day_columns)
                            
                            # Check if _Day31_Cumulative exists and has data
                            has_cumulative_col = '_Day31_Cumulative' in sheet_header or '_LastDayCumulative' in sheet_header
                            
                            # Calculate previous month's expected days
                            parts = current_month.split('/')
                            prev_month_num = int(parts[0]) - 1
                            prev_year = int(parts[1])
                            if prev_month_num < 1:
                                prev_month_num = 12
                                prev_year -= 1
                            prev_month_str = f"{prev_month_num:02d}/{prev_year}"
                            expected_days_prev = get_days_in_month(prev_month_str)
                            
                            print(f"    📋 Sheet has {sheet_day_count} Day columns, previous month ({prev_month_str}) expects {expected_days_prev} days")
                            
                            # If sheet has data for complete month (e.g., 30-31 days), assume it's from prev month
                            if sheet_day_count >= expected_days_prev - 1:
                                sheet_month = prev_month_str
                                legacy_complete_data = True  # Flag to bypass API max_days check
                                legacy_day_count = sheet_day_count  # Store actual sheet day count
                                print(f"    📋 ✅ Sheet appears to have complete {sheet_month} data - will archive")
                            else:
                                # Sheet has partial data - might be current month, don't archive
                                print(f"    📋 ⏳ Sheet has partial data ({sheet_day_count} days) - treating as current month")
                                sheet_month = current_month  # Don't archive, treat as current
                        else:
                            # Fallback: assume previous month
                            parts = current_month.split('/')
                            prev_month_num = int(parts[0]) - 1
                            prev_year = int(parts[1])
                            if prev_month_num < 1:
                                prev_month_num = 12
                                prev_year -= 1
                            sheet_month = f"{prev_month_num:02d}/{prev_year}"
                            print(f"    📋 No header found, assuming data is from {sheet_month}")
                    
                    # If archive exists, preserve it
                    red_cells_to_format = []  # Track cells needing red formatting [(row, col), ...]
                    if archive_start is not None:
                        existing_archives = all_values[archive_start:]
                        print(f"    📦 [DEBUG] Found {len(existing_archives)} archive rows starting at row {archive_start + 1}")
                        
                        # ============================================================
                        # AUTO-UPDATE EXISTING ARCHIVE WITH DAY 31 IF MISSING
                        # ============================================================
                        if existing_archives and len(existing_archives) > 1:
                            # Find the archive header row (first row after === ARCHIVE header)
                            archive_data_header_idx = None
                            for idx, row in enumerate(existing_archives):
                                if row and len(row) > 0:
                                    if row[0] == 'Trainer ID' or row[0] == 'Name':
                                        archive_data_header_idx = idx
                                        break
                            
                            if archive_data_header_idx is not None:
                                archive_data_header = existing_archives[archive_data_header_idx]
                                
                                # Check if Day 31 column already exists
                                last_day_col_name = None
                                last_day_col_idx = None
                                for idx, col_name in enumerate(archive_data_header):
                                    if col_name and str(col_name).startswith('Day '):
                                        last_day_col_name = col_name
                                        last_day_col_idx = idx
                                
                                # Extract the day number from last Day column
                                if last_day_col_name:
                                    try:
                                        last_day_num = int(str(last_day_col_name).replace('Day ', ''))
                                    except ValueError:
                                        last_day_num = 30
                                    
                                    # Get expected days for archive month
                                    archive_month = None
                                    for row in existing_archives:
                                        if row and '=== ARCHIVE' in str(row[0]):
                                            import re
                                            match = re.search(r'(\d{1,2}/\d{4})', str(row[0]))
                                            if match:
                                                archive_month = match.group(1)
                                                break
                                    
                                    if archive_month:
                                        expected_days_archive = get_days_in_month(archive_month)
                                        
                                        # Check if Day 31 already exists
                                        day31_exists = (last_day_num >= expected_days_archive)
                                        
                                        # Even if Day 31 exists, check if it needs recalculation (has N/A values)
                                        needs_recalc = False
                                        day31_col_idx = None
                                        if day31_exists:
                                            # Find Day 31 column index
                                            for idx, col_name in enumerate(archive_data_header):
                                                if col_name == f'Day {expected_days_archive}':
                                                    day31_col_idx = idx
                                                    break
                                            
                                            if day31_col_idx:
                                                # Count N/A values
                                                na_count = 0
                                                for row in existing_archives[archive_data_header_idx + 1:]:
                                                    if row and len(row) > day31_col_idx:
                                                        if row[day31_col_idx] == 'N/A':
                                                            na_count += 1
                                                if na_count > 0:
                                                    print(f"    📊 Archive {archive_month} has Day {expected_days_archive} but {na_count} N/A values - recalculating...")
                                                    needs_recalc = True
                                        
                                        # Calculate Day 31 if missing OR has N/A values to recalculate
                                        if not day31_exists or needs_recalc:
                                            if not day31_exists:
                                                print(f"    📊 Archive {archive_month} missing Day {expected_days_archive} - calculating...")
                                            
                                            # Find Trainer ID and _Day31_Cumulative columns
                                            trainer_id_col = None
                                            day31_cumulative_col = None
                                            for idx, col_name in enumerate(archive_data_header):
                                                if col_name == 'Trainer ID':
                                                    trainer_id_col = idx
                                                elif col_name == '_Day31_Cumulative':
                                                    day31_cumulative_col = idx
                                            
                                            if trainer_id_col is not None and day31_cumulative_col is not None:
                                                # Build API member lookup with transfer detection
                                                # Members who transferred: cumulative[0] > 0 but cumulative[1] = 0
                                                api_member_data = {}
                                                for member in api_members:
                                                    member_id = str(member.get('viewer_id', ''))
                                                    member_cumulative = member.get('daily_fans', [])
                                                    if member_cumulative and len(member_cumulative) > 0:
                                                        is_transferred = False
                                                        if len(member_cumulative) > 1 and member_cumulative[0] > 0 and member_cumulative[1] == 0:
                                                            is_transferred = True  # Has Day 1 but no Day 2 = transferred out
                                                        api_member_data[member_id] = {
                                                            'day1_cumulative': member_cumulative[0],
                                                            'is_transferred': is_transferred
                                                        }
                                                
                                                # Debug: count transferred members in API
                                                transferred_in_api = sum(1 for m in api_member_data.values() if m['is_transferred'])
                                                print(f"    [DEBUG] API members: {len(api_member_data)}, transferred: {transferred_in_api}")
                                                
                                                # Determine column position for Day 31
                                                if needs_recalc and day31_col_idx is not None:
                                                    # Updating existing Day 31 column
                                                    day31_insert_pos = day31_col_idx
                                                    is_insert_mode = False
                                                    print(f"    🔄 Updating existing Day {expected_days_archive} column at position {day31_insert_pos}")
                                                else:
                                                    # Adding new Day 31 column
                                                    day31_insert_pos = last_day_col_idx + 1
                                                    is_insert_mode = True
                                                    existing_archives[archive_data_header_idx] = list(archive_data_header)
                                                    existing_archives[archive_data_header_idx].insert(day31_insert_pos, f'Day {expected_days_archive}')
                                                
                                                # Calculate Day 31 for each member
                                                members_with_day31 = 0
                                                members_transferred = 0
                                                members_not_found = 0
                                                for row_idx in range(archive_data_header_idx + 1, len(existing_archives)):
                                                    row = existing_archives[row_idx]
                                                    if not row or not row[0] or '===' in str(row[0]):
                                                        # Skip empty rows or section headers
                                                        row = list(row) if row else ['']
                                                        if is_insert_mode:
                                                            row.insert(day31_insert_pos, '')
                                                        existing_archives[row_idx] = row
                                                        continue
                                                    
                                                    row = list(row)
                                                    member_trainer_id = str(row[trainer_id_col]) if len(row) > trainer_id_col else ''
                                                    old_cumulative = 0
                                                    try:
                                                        if len(row) > day31_cumulative_col:
                                                            old_cumulative = int(row[day31_cumulative_col]) if row[day31_cumulative_col] else 0
                                                    except (ValueError, TypeError):
                                                        old_cumulative = 0
                                                    
                                                    # Calculate Day 31
                                                    day31_value = 'N/A'
                                                    is_transferred_member = False
                                                    
                                                    if member_trainer_id in api_member_data:
                                                        member_info = api_member_data[member_trainer_id]
                                                        new_day1_cumulative = member_info['day1_cumulative']
                                                        is_transferred_member = member_info['is_transferred']
                                                        
                                                        if old_cumulative > 0 and new_day1_cumulative > 0:
                                                            day31_value = new_day1_cumulative - old_cumulative
                                                            if day31_value < 0:
                                                                day31_value = 'N/A'
                                                                members_not_found += 1
                                                            else:
                                                                if is_transferred_member:
                                                                    members_transferred += 1
                                                                    # Track for red formatting - calculate actual row in full_data
                                                                    # This is relative position in existing_archives
                                                                    red_cells_to_format.append((row_idx, day31_insert_pos))
                                                                else:
                                                                    members_with_day31 += 1
                                                                
                                                                # Build CROSS_CLUB_CACHE for transfer lookup
                                                                update_cross_club_cache(
                                                                    member_trainer_id, 
                                                                    club_name, 
                                                                    old_cumulative,
                                                                    archive_month
                                                                )
                                                        else:
                                                            members_not_found += 1
                                                    else:
                                                        # Member not in API at all
                                                        members_not_found += 1
                                                    
                                                    # Set Day 31 value (insert or update)
                                                    if is_insert_mode:
                                                        row.insert(day31_insert_pos, day31_value)
                                                    else:
                                                        # Update existing column
                                                        while len(row) <= day31_insert_pos:
                                                            row.append('')
                                                        row[day31_insert_pos] = day31_value
                                                    existing_archives[row_idx] = row
                                                
                                                print(f"    ✅ Day {expected_days_archive} added: {members_with_day31} stayed, {members_transferred} transferred (red), {members_not_found} not found (N/A)")
                                                print(f"    📦 CROSS_CLUB_CACHE: Added {members_with_day31 + members_transferred} members from {club_name}")
                    else:
                        print(f"    📦 [DEBUG] No existing archives found in sheet")
                    
                    # Check if we need to archive current data (month changed)
                    new_archive_section = []
                    if sheet_month and sheet_month != current_month:
                        # First, check if sheet has actual data to archive
                        # Count data rows (excluding headers like === CURRENT, === ARCHIVE)
                        sheet_data_rows = 0
                        for row in all_values:
                            if row and row[0]:
                                if '===' in row[0]:
                                    continue  # Skip header lines
                                if row[0] == 'Name' or row[0] == 'Trainer ID':
                                    continue  # Skip column header
                                sheet_data_rows += 1
                            else:
                                break  # Stop at empty row
                        
                        if sheet_data_rows < 1:
                            # NEW CLUB or empty sheet - nothing to archive
                            print(f"    📋 New club detected (no old data on sheet) - skipping archive check")
                        else:
                            # Sheet has data - continue with archive logic
                            # YUI LOGIC: Only archive if old month's data is COMPLETE
                            # uma.moe updates with 1 day delay, so on first day of new month,
                            # we may not have complete data for the last day of previous month
                            expected_days = get_days_in_month(sheet_month)
                            
                            # Get current calendar day (in Vietnam timezone)
                            vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
                            current_calendar_day = datetime.datetime.now(vietnam_tz).day
                            
                            # Check if we have Day 1 of new month (needed to calculate last day of old month)
                            # API returns cumulative for each day, so len > expected_days means new month data exists
                            has_new_month_data = False
                            for member in api_members:
                                member_cumulative = member.get('daily_fans', [])
                                if len(member_cumulative) > expected_days:
                                    has_new_month_data = True
                                    break
                            
                            # Determine if we should archive
                            should_archive = False
                            archive_reason = ""
                            
                            if has_new_month_data:
                                # CASE 1: Have Day 1 of new month - can calculate last day of old month
                                should_archive = True
                                archive_reason = f"✅ Day 1 new month available - calculating Day {expected_days} daily gains"
                                print(f"    📊 Day 1 of {current_month} detected - will calculate Day {expected_days} of {sheet_month}")
                            elif legacy_complete_data:
                                # CASE 0: Legacy sheet with complete data - bypass API max_days check
                                # Use sheet's day count instead of API data
                                should_archive = True
                                archive_reason = f"✅ Legacy sheet has {legacy_day_count} Day columns (complete data for {sheet_month})"
                            elif max_days >= expected_days - 1 and current_calendar_day >= 2:
                                # CASE 2: FALLBACK - uma.moe skipped last day
                                # If it's already Day 2+ of new month and we have (expected - 1) days,
                                # uma.moe probably skipped the last day. Archive anyway.
                                should_archive = True
                                archive_reason = f"⚠️ Fallback archive: {max_days}/{expected_days} days (Day {expected_days} skipped by uma.moe)"
                            elif max_days >= expected_days - 2 and current_calendar_day >= 3:
                                # CASE 3: Force archive after Day 3 if we have at least expected - 2 days
                                # This is a safety net to prevent data being stuck
                                should_archive = True
                                archive_reason = f"⚠️ Force archive (Day 3+): {max_days}/{expected_days} days"
                            
                            if should_archive:
                                print(f"    🗓️ New month detected ({sheet_month} → {current_month})")
                                print(f"    {archive_reason} - proceeding with archive")
                                # Get current data to archive (rows between CURRENT header and archive/empty)
                                current_data_to_archive = []
                                for row in all_values:
                                    if not row or not row[0]:
                                        break
                                    if '=== ARCHIVE' in row[0]:
                                        break
                                    current_data_to_archive.append(row)
                                
                                if len(current_data_to_archive) > 1:
                                    # Build archive section for old month
                                    archive_header = [f"=== ARCHIVE: {sheet_month} ==="]
                                    archive_header.extend([''] * (len(header) - 1))
                                    spacer = [[''] * len(header)] * 2
                                    
                                    # Determine where actual data starts
                                    # If first row is CURRENT header, skip it
                                    # If first row is data header (legacy), keep it
                                    skip_first = 0
                                    if current_data_to_archive[0] and '=== CURRENT' in str(current_data_to_archive[0][0]):
                                        skip_first = 1  # Skip CURRENT header
                                    
                                    archive_data = current_data_to_archive[skip_first:]
                                    
                                    # ============================================================
                                    # CALCULATE DAY 31 FOR ARCHIVE
                                    # ============================================================
                                    # Find _Day31_Cumulative and Trainer ID columns in archive header
                                    if archive_data and len(archive_data) > 1:
                                        archive_data_header = archive_data[0]
                                        day31_cumulative_col = None
                                        trainer_id_col = None
                                        
                                        for idx, col_name in enumerate(archive_data_header):
                                            if col_name == '_Day31_Cumulative':
                                                day31_cumulative_col = idx
                                            elif col_name == 'Trainer ID':
                                                trainer_id_col = idx
                                        
                                        # Build API member lookup for Day 1 new month cumulative
                                        api_member_day1 = {}
                                        for member in api_members:
                                            member_id = str(member.get('viewer_id', ''))
                                            member_cumulative = member.get('daily_fans', [])
                                            if member_cumulative and len(member_cumulative) > 0:
                                                api_member_day1[member_id] = member_cumulative[0]
                                        
                                        if day31_cumulative_col and trainer_id_col:
                                            # Add Day 31 column to header
                                            archive_data[0] = list(archive_data[0])
                                            # Find position after Day 30 (or last Day column)
                                            last_day_col = None
                                            for idx, col_name in enumerate(archive_data[0]):
                                                if col_name and col_name.startswith('Day '):
                                                    last_day_col = idx
                                            
                                            # Insert Day 31 after last Day column
                                            if last_day_col is not None:
                                                day31_insert_pos = last_day_col + 1
                                                archive_data[0].insert(day31_insert_pos, f'Day {expected_days}')
                                                
                                                # Calculate Day 31 for each member row
                                                members_with_day31 = 0
                                                members_transferred = 0
                                                for row_idx in range(1, len(archive_data)):
                                                    row = list(archive_data[row_idx])
                                                    
                                                    # Get member's Trainer ID and _Day31_Cumulative
                                                    member_trainer_id = str(row[trainer_id_col]) if len(row) > trainer_id_col else ''
                                                    old_cumulative = 0
                                                    try:
                                                        if len(row) > day31_cumulative_col:
                                                            old_cumulative = int(row[day31_cumulative_col]) if row[day31_cumulative_col] else 0
                                                    except (ValueError, TypeError):
                                                        old_cumulative = 0
                                                    
                                                    # Calculate Day 31
                                                    day31_value = 'N/A'
                                                    if member_trainer_id in api_member_day1:
                                                        new_day1_cumulative = api_member_day1[member_trainer_id]
                                                        if old_cumulative > 0 and new_day1_cumulative > 0:
                                                            day31_value = new_day1_cumulative - old_cumulative
                                                            if day31_value < 0:
                                                                day31_value = 'N/A'  # Invalid calculation
                                                            else:
                                                                members_with_day31 += 1
                                                        else:
                                                            members_transferred += 1
                                                    else:
                                                        members_transferred += 1
                                                    
                                                    # Insert Day 31 value
                                                    row.insert(day31_insert_pos, day31_value)
                                                    archive_data[row_idx] = row
                                                
                                                print(f"    📊 Day {expected_days} calculated: {members_with_day31} members, {members_transferred} transferred (N/A)")
                                    
                                    new_archive_section = spacer + [archive_header] + archive_data + spacer
                                    print(f"    📦 Archiving {len(archive_data)} rows from {sheet_month}")
                            else:
                                # Data not complete yet - DON'T archive, wait for uma.moe to update
                                print(f"    🗓️ New month detected ({sheet_month} → {current_month})")
                                print(f"    ⏳ Data INCOMPLETE: only {max_days}/{expected_days} days - SKIPPING archive")
                                print(f"    💡 Will auto-archive on Day 2 if uma.moe skips last day")
                    
                    # Build CURRENT header with month
                    current_header = [f"=== CURRENT: {current_month} ==="]
                    current_header.extend([''] * (len(header) - 1))
                    
                    # Build full data: CURRENT header + data header + rows + spacer + new archive + existing archives
                    full_data = [current_header, header] + rows_data
                    
                    # Add spacer and archives
                    if new_archive_section or existing_archives:
                        spacer = [[''] * len(header)] * 2
                        full_data.extend(spacer)
                        if new_archive_section:
                            full_data.extend(new_archive_section)
                        if existing_archives:
                            full_data.extend(existing_archives)
                    
                    # SAFETY CHECK: Verify archives are preserved before clearing
                    # This prevents accidental data loss if archive detection fails
                    if archive_start is not None and len(existing_archives) > 0:
                        # We found archives in original sheet - make sure they're in full_data
                        archive_preserved = any('=== ARCHIVE' in str(row[0]) for row in full_data if row and row[0])
                        if not archive_preserved:
                            print(f"    ⚠️ SAFETY: Archives detected but not preserved! Skipping write to prevent data loss.")
                            print(f"    📦 Original had {len(existing_archives)} archive rows")
                            continue  # Skip this club, don't write
                    
                    # Clear and rewrite entire sheet
                    await asyncio.sleep(0.3)
                    await asyncio.to_thread(member_ws.clear)
                    await asyncio.sleep(0.3)
                    await asyncio.to_thread(
                        member_ws.update, 'A1', full_data
                    )
                    
                    # ============================================================
                    # APPLY YELLOW FORMATTING FOR TRANSFERRED MEMBERS
                    # - Members who LEFT: in archive but not in current → yellow in archive
                    # - Members who JOINED: in current but not in archive → yellow in current
                    # ============================================================
                    try:
                        from gspread_formatting import format_cell_range, CellFormat, Color
                        
                        # Build current member IDs set
                        current_member_ids = set()
                        for member in api_members:
                            member_id = str(member.get('viewer_id', ''))
                            if member_id:
                                current_member_ids.add(member_id)
                        
                        print(f"    [DEBUG] Current members from API: {len(current_member_ids)}")
                        if current_member_ids:
                            sample_current = list(current_member_ids)[:3]
                            print(f"    [DEBUG] Sample current IDs: {sample_current}")
                        
                        # Build archive member IDs set (from existing_archives)
                        archive_member_ids = set()
                        archive_member_rows = {}  # {trainer_id: row_index_in_archive}
                        
                        # Use new_archive_section FIRST (just created), then existing_archives
                        archive_to_compare = None
                        archive_source = None
                        
                        if new_archive_section and len(new_archive_section) > 3:
                            # New archive was just created from previous month - use this
                            archive_to_compare = new_archive_section
                            archive_source = "new_archive_section"
                        elif existing_archives and len(existing_archives) > 3:
                            # Use existing archives
                            archive_to_compare = existing_archives
                            archive_source = "existing_archives"
                        
                        print(f"    [DEBUG] Archive source: {archive_source}, length: {len(archive_to_compare) if archive_to_compare else 0}")
                        
                        if archive_to_compare:
                            archive_header_idx = None
                            trainer_id_col = None
                            
                            # Find header and Trainer ID column
                            for idx, row in enumerate(archive_to_compare):
                                if row and len(row) > 0:
                                    if row[0] == 'Trainer ID':
                                        archive_header_idx = idx
                                        trainer_id_col = 0
                                        print(f"    [DEBUG] Found archive header at idx {idx}, Trainer ID at col 0")
                                        break
                                    elif 'Trainer ID' in row:
                                        archive_header_idx = idx
                                        trainer_id_col = row.index('Trainer ID')
                                        print(f"    [DEBUG] Found archive header at idx {idx}, Trainer ID at col {trainer_id_col}")
                                        break
                            
                            if archive_header_idx is None:
                                print(f"    [DEBUG] ⚠️ Could not find archive header row!")
                                print(f"    [DEBUG] First 5 rows of archive_to_compare:")
                                for i, row in enumerate(archive_to_compare[:5]):
                                    print(f"    [DEBUG]   Row {i}: {row[:3] if row else 'empty'}")
                            
                            if archive_header_idx is not None and trainer_id_col is not None:
                                for idx in range(archive_header_idx + 1, len(archive_to_compare)):
                                    row = archive_to_compare[idx]
                                    if row and len(row) > trainer_id_col and row[trainer_id_col]:
                                        if '===' not in str(row[0]):  # Skip section headers
                                            archive_member_ids.add(str(row[trainer_id_col]))
                                            archive_member_rows[str(row[trainer_id_col])] = idx
                        else:
                            print(f"    [DEBUG] No archive data to compare - skipping transfer highlighting")
                        
                        print(f"    [DEBUG] Archive members found: {len(archive_member_ids)}")
                        if archive_member_ids:
                            sample_archive = list(archive_member_ids)[:3]
                            print(f"    [DEBUG] Sample archive IDs: {sample_archive}")
                        
                        # Calculate who LEFT and who JOINED (only if we have archive to compare)
                        if not archive_to_compare:
                            print(f"    [DEBUG] ⏭️ Skipping transfer highlighting - no archive to compare against")
                            members_left = set()
                            members_joined = set()
                        else:
                            members_left = archive_member_ids - current_member_ids  # In archive but not current
                            members_joined = current_member_ids - archive_member_ids  # In current but not archive
                        
                        print(f"    [DEBUG] Members LEFT (archive - current): {len(members_left)}")
                        print(f"    [DEBUG] Members JOINED (current - archive): {len(members_joined)}")
                        
                        if members_left or members_joined:
                            print(f"    🔄 Transfer detection: {len(members_left)} OUT, {len(members_joined)} NEW")
                            
                            # Yellow color for highlighting
                            yellow_format = CellFormat(
                                backgroundColor=Color(1.0, 1.0, 0.0)  # Yellow #FFFF00
                            )
                            
                            formatted_count = 0
                            ranges_to_format = []  # Collect all ranges first
                            
                            # Collect LEFT members in archive (only A:B columns)
                            if members_left and archive_to_compare:
                                # Calculate archive start position in sheet
                                # full_data = [CURRENT header, data header, rows_data..., spacer, new_archive, existing_archives]
                                if archive_source == "new_archive_section":
                                    # New archive position: after current data + spacer
                                    archive_offset = 2 + len(rows_data) + 2  # Headers + data + spacer
                                else:
                                    # Existing archive position: after current data + spacer + new_archive
                                    archive_offset = 2 + len(rows_data) + 2
                                    if new_archive_section:
                                        archive_offset += len(new_archive_section)
                                
                                for trainer_id in members_left:
                                    if trainer_id in archive_member_rows:
                                        rel_row = archive_member_rows[trainer_id]
                                        actual_row = archive_offset + rel_row + 1  # +1 for 1-indexed
                                        ranges_to_format.append(f"A{actual_row}:B{actual_row}")
                            
                            # Collect JOINED members in current data (only A:B columns)
                            if members_joined:
                                # Current data starts at row 3 (after CURRENT header + data header)
                                # rows_data has Trainer ID at column 0
                                for row_idx, row in enumerate(rows_data):
                                    if row and len(row) > 0:
                                        if str(row[0]) in members_joined:
                                            actual_row = 3 + row_idx  # Row 3 is first data row
                                            ranges_to_format.append(f"A{actual_row}:B{actual_row}")
                            
                            # Apply formatting in batches to avoid rate limit
                            if ranges_to_format:
                                print(f"    [DEBUG] Formatting {len(ranges_to_format)} ranges...")
                                batch_size = 10  # Max 10 formats per batch
                                
                                for i in range(0, len(ranges_to_format), batch_size):
                                    batch = ranges_to_format[i:i+batch_size]
                                    for row_range in batch:
                                        try:
                                            await asyncio.to_thread(
                                                format_cell_range, member_ws, row_range, yellow_format
                                            )
                                            formatted_count += 1
                                        except Exception as fmt_err:
                                            print(f"    ⚠️ Format error for {row_range}: {fmt_err}")
                                    
                                    # Wait between batches to avoid rate limit
                                    if i + batch_size < len(ranges_to_format):
                                        await asyncio.sleep(2)  # 2 second delay between batches
                            
                            if formatted_count > 0:
                                print(f"    🟡 Applied yellow highlighting to {formatted_count} transferred member rows")
                    except ImportError:
                        print(f"    ⚠️ gspread_formatting not installed - skipping yellow formatting")
                    except Exception as e:
                        print(f"    ⚠️ Error applying yellow formatting: {e}")
                    
                    members_synced += 1
                    print(f"  📊 {club_name}: Synced {len(rows_data)} members, {max_days} days ({current_month})")
                    print(f"    📦 [DEBUG] Wrote {len(full_data)} total rows (new_archive={len(new_archive_section)}, existing_archive={len(existing_archives)})")
                    
                    # ===== SYNC DATA SHEET =====
                    # Data sheet name: from config, or derive from Members_Sheet_Name
                    # Pattern: Eden03_Members → Eden03_Data
                    if club_config.get('Data_Sheet_Name'):
                        data_sheet_name = club_config.get('Data_Sheet_Name')
                    elif members_sheet_name and '_Members' in members_sheet_name:
                        data_sheet_name = members_sheet_name.replace('_Members', '_Data')
                    else:
                        data_sheet_name = f"{club_name}_Data"
                    
                    if data_sheet_rows:
                        try:
                            # Get or create Data sheet
                            try:
                                data_ws = await asyncio.to_thread(
                                    gs_manager.sh.worksheet, data_sheet_name
                                )
                            except Exception:
                                # Sheet doesn't exist - skip
                                print(f"    ⏭️ Data sheet '{data_sheet_name}' not found, skipping Data sync")
                                data_ws = None
                            
                            if data_ws:
                                # Build Data sheet header
                                data_header = ['Name', 'Day', 'Total Fans', 'Daily', 'Target', 'CarryOver']
                                
                                # Build CURRENT header with month
                                current_data_header = [f"=== CURRENT: {current_month} ==="]
                                current_data_header.extend([''] * (len(data_header) - 1))
                                
                                # Build full data
                                full_data_sheet = [current_data_header, data_header] + data_sheet_rows
                                
                                # Simple approach: Always clear and rewrite
                                await asyncio.sleep(0.5)  # Reduced with proxies
                                await asyncio.to_thread(data_ws.clear)
                                await asyncio.sleep(0.3)
                                await asyncio.to_thread(
                                    data_ws.update, 'A1', full_data_sheet
                                )
                                
                                print(f"    📈 Data sheet: Synced {len(data_sheet_rows)} rows")
                        except Exception as e:
                            print(f"    ⚠️ Data sheet sync failed: {e}")
                    
                except Exception as e:
                    print(f"  ❌ {club_name}: Failed to update member sheet: {e}")
                    error_count += 1
                    # Add to retry list
                    failed_clubs.append({'idx': idx, 'config': club_config, 'reason': 'exception', 'error': str(e)})
                    
            except Exception as e:
                print(f"  ❌ Error updating {club_name}: {e}")
                error_count += 1
                # Add to retry list
                failed_clubs.append({'idx': idx, 'config': club_config, 'reason': 'exception', 'error': str(e)})
        
        print(f"\n[{datetime.datetime.now()}] ====== SYNC COMPLETE ======")
        print(f"  ✅ Ranks updated: {rank_updated}")
        print(f"  📊 Clubs synced: {members_synced}")
        print(f"  ⏭️ Skipped (no Club_ID): {skipped_no_id}")
        print(f"  ❌ Errors: {error_count}")
        print(f"  🔄 Failed clubs (to retry): {len(failed_clubs)}")
        
        # Send transfer warnings to Discord
        if transfer_warnings_log:
            try:
                debug_channel = bot.get_channel(DEBUG_LOG_CHANNEL_ID)
                if debug_channel:
                    # Build transfer warning message
                    warning_lines = [f"⚠️ **Transfer Warnings Detected** ({len(transfer_warnings_log)} members):\n"]
                    for warn in transfer_warnings_log[:20]:  # Limit to 20 to avoid message too long
                        warning_lines.append(f"• **{warn['club']}**: {warn['trainer_name']} (ID: `{warn['trainer_id']}`)")
                    
                    if len(transfer_warnings_log) > 20:
                        warning_lines.append(f"\n... and {len(transfer_warnings_log) - 20} more")
                    
                    warning_msg = "\n".join(warning_lines)
                    await debug_channel.send(warning_msg)
                    print(f"  📤 Sent {len(transfer_warnings_log)} transfer warnings to Discord")
            except Exception as e:
                print(f"  ⚠️ Failed to send transfer warnings to Discord: {e}")
        
        # ===== RETRY LOOP FOR FAILED CLUBS =====
        if failed_clubs:
            max_retries = 3
            retry_delay = 10  # seconds between retries
            
            for retry_round in range(1, max_retries + 1):
                if not failed_clubs:
                    break
                    
                print(f"\n  🔄 RETRY ROUND {retry_round}/{max_retries} - {len(failed_clubs)} clubs to retry")
                print(f"  ⏳ Waiting {retry_delay}s before retry...")
                await asyncio.sleep(retry_delay)
                
                still_failed = []
                retry_success = 0
                
                for failed_item in failed_clubs:
                    idx = failed_item['idx']
                    club_config = failed_item['config']
                    club_name = club_config.get('Club_Name', '')
                    club_id = str(club_config.get('Club_ID', '')).strip()
                    retry_reason = failed_item.get('reason', 'unknown')
                    
                    try:
                        # Rate limit
                        await asyncio.sleep(1)
                        
                        # Determine retry type based on reason
                        if retry_reason == 'exception':
                            # FULL SYNC RETRY - exception during member sync, need full retry
                            print(f"    🔄 [Retry {retry_round}] {club_name}: Full sync (exception)")
                            api_data = await fetch_club_data_full(club_id)
                            
                            if not api_data:
                                print(f"    ⚠️ [Retry {retry_round}] {club_name}: Still no data")
                                still_failed.append(failed_item)
                                continue
                            
                            circle_data = api_data.get('circle', {})
                            api_members = api_data.get('members', [])
                            rank = circle_data.get('monthly_rank')
                            
                            # Update rank first
                            if rank is not None and rank > 0:
                                await asyncio.to_thread(
                                    config_ws.update_cell, idx, 11, rank
                                )
                                if club_name in client.config_cache:
                                    client.config_cache[club_name]['Rank'] = rank
                                rank_updated += 1
                            
                            # Try to sync member sheet
                            if api_members:
                                try:
                                    members_sheet_name = club_config.get('Members_Sheet_Name', '')
                                    target_per_day = int(club_config.get('Target_Per_Day', 0))
                                    
                                    if members_sheet_name:
                                        member_ws = await asyncio.to_thread(
                                            gs_manager.sh.worksheet, members_sheet_name
                                        )
                                        
                                        # Build simple current data (no archive logic in retry)
                                        current_day = datetime.datetime.now(tz=pytz.timezone('Asia/Tokyo')).day
                                        current_month = datetime.datetime.now(tz=pytz.timezone('Asia/Tokyo')).strftime('%m/%Y')
                                        
                                        # Build rows data
                                        rows_data = []
                                        for member in api_members:
                                            trainer_id = str(member.get('viewer_id', ''))
                                            trainer_name = member.get('trainer_name', '')
                                            cumulative = member.get('daily_fans', [])
                                            
                                            if not trainer_id or not cumulative:
                                                continue
                                            
                                            # Calculate daily gains
                                            daily_gains = calculate_daily_gains_from_cumulative(cumulative)
                                            max_days = len(daily_gains)
                                            
                                            if max_days == 0:
                                                continue
                                            
                                            # Build row
                                            row = [trainer_id, trainer_name]
                                            for day_num in range(1, max_days + 1):
                                                row.append(daily_gains[day_num - 1] if day_num <= len(daily_gains) else '')
                                            
                                            # Calculate totals
                                            total = sum(g for g in daily_gains if g is not None and isinstance(g, (int, float)))
                                            expected = target_per_day * max_days
                                            carryover = total - expected
                                            
                                            row.extend([total, expected, carryover])
                                            rows_data.append(row)
                                        
                                        if rows_data:
                                            # Build header
                                            max_days_in_data = max(len(r) - 5 for r in rows_data) if rows_data else 1
                                            header = ['Trainer ID', 'Name']
                                            for d in range(1, max_days_in_data + 1):
                                                header.append(f"Day {d}")
                                            header.extend(['Total', 'Expected', 'CarryOver'])
                                            
                                            # Current header
                                            current_header = [f"=== CURRENT: {current_month} ==="]
                                            current_header.extend([''] * (len(header) - 1))
                                            
                                            full_data = [current_header, header] + rows_data
                                            
                                            # ========== SMART ARCHIVE DETECTION WITH LEGACY SUPPORT ==========
                                            existing_data = await asyncio.to_thread(member_ws.get_all_values)
                                            existing_archives = []
                                            archive_start = None
                                            
                                            print(f"    📋 [Retry] Sheet has {len(existing_data)} rows total")
                                            
                                            # STEP 1: Detect sheet format and month
                                            has_current_header = False
                                            legacy_complete_data = False
                                            sheet_month = None
                                            
                                            for row in existing_data:
                                                if row and len(row) > 0 and row[0]:
                                                    cell_upper = str(row[0]).strip().upper()
                                                    if '=== CURRENT' in cell_upper:
                                                        has_current_header = True
                                                        # Extract month from CURRENT header
                                                        import re
                                                        match = re.search(r'(\d{1,2}/\d{4})', str(row[0]))
                                                        if match:
                                                            sheet_month = match.group(1)
                                                    elif '=== ARCHIVE' in cell_upper:
                                                        # Found existing archive section
                                                        archive_start = existing_data.index(row)
                                                        break
                                            
                                            # STEP 2: Handle LEGACY FORMAT (no === CURRENT === header)
                                            if not has_current_header and len(existing_data) > 0:
                                                print(f"    📋 [Retry] Legacy format detected (no CURRENT header)")
                                                
                                                # Find header row with "Trainer ID" or "Name"
                                                sheet_header = None
                                                for row in existing_data:
                                                    if row and ('Trainer ID' in row or 'Name' in row):
                                                        sheet_header = row
                                                        break
                                                
                                                if sheet_header:
                                                    # Count "Day X" columns to determine which month
                                                    day_columns = [col for col in sheet_header if isinstance(col, str) and col.startswith('Day ')]
                                                    sheet_day_count = len(day_columns)
                                                    
                                                    # Calculate previous month's expected days
                                                    parts = current_month.split('/')
                                                    prev_month_num = int(parts[0]) - 1
                                                    prev_year = int(parts[1])
                                                    if prev_month_num < 1:
                                                        prev_month_num = 12
                                                        prev_year -= 1
                                                    prev_month_str = f"{prev_month_num:02d}/{prev_year}"
                                                    expected_days_prev = get_days_in_month(prev_month_str)
                                                    
                                                    print(f"    📋 [Retry] Sheet has {sheet_day_count} Day columns")
                                                    print(f"    📋 [Retry] Previous month ({prev_month_str}) expects {expected_days_prev} days")
                                                    
                                                    # If sheet has complete month data (≥ 29 days), it's OLD MONTH
                                                    if sheet_day_count >= expected_days_prev - 1:
                                                        legacy_complete_data = True
                                                        sheet_month = prev_month_str
                                                        print(f"    📦 [Retry] ✅ Legacy sheet has complete {sheet_month} data ({sheet_day_count} days)")
                                                        
                                                        # Archive ENTIRE legacy sheet
                                                        archive_header_row = [f"=== ARCHIVE: {sheet_month} ==="]
                                                        if len(existing_data) > 0 and len(existing_data[0]) > 1:
                                                            archive_header_row.extend([''] * (len(existing_data[0]) - 1))
                                                        
                                                        existing_archives = [[''], archive_header_row] + existing_data
                                                        print(f"    📦 [Retry] Archived {len(existing_data)} legacy rows")
                                                    else:
                                                        # Partial data - current month, don't archive
                                                        print(f"    ℹ️ [Retry] Legacy sheet has partial data ({sheet_day_count} days) - no archive")
                                                        existing_archives = []
                                            
                                            # STEP 3: Handle NEW FORMAT (has === CURRENT === header)
                                            elif has_current_header:
                                                print(f"    📋 [Retry] New format with CURRENT header detected")
                                                
                                                # Check if sheet month matches current month
                                                if sheet_month and sheet_month != current_month:
                                                    print(f"    📦 [Retry] Sheet month ({sheet_month}) != current ({current_month})")
                                                    
                                                    # Preserve existing archive section if found
                                                    if archive_start is not None:
                                                        existing_archives = existing_data[archive_start:]
                                                        print(f"    📦 [Retry] Preserved {len(existing_archives)} existing archive rows")
                                                    else:
                                                        # No archive yet - create new archive from current section
                                                        # Find where current data ends (empty row or end of data)
                                                        current_data_end = None
                                                        for row_idx in range(len(existing_data)):
                                                            if row_idx > 1:  # Skip headers
                                                                row = existing_data[row_idx]
                                                                if not row or not any(row):  # Empty row
                                                                    current_data_end = row_idx
                                                                    break
                                                        
                                                        if current_data_end is None:
                                                            current_data_end = len(existing_data)
                                                        
                                                        # Create archive from old month data
                                                        archive_header_row = [f"=== ARCHIVE: {sheet_month} ==="]
                                                        archive_header_row.extend([''] * (len(existing_data[0]) - 1))
                                                        
                                                        existing_archives = [[''], archive_header_row] + existing_data[:current_data_end]
                                                        print(f"    📦 [Retry] Created archive for {sheet_month} ({current_data_end} rows)")
                                                else:
                                                    # Same month - only preserve existing archives
                                                    if archive_start is not None:
                                                        existing_archives = existing_data[archive_start:]
                                                        print(f"    📦 [Retry] Same month - preserved {len(existing_archives)} archive rows")
                                                    else:
                                                        print(f"    ℹ️ [Retry] Same month - no archives found")
                                            
                                            # STEP 4: Empty sheet
                                            else:
                                                print(f"    ℹ️ [Retry] Empty or unrecognized sheet format")
                                            
                                            # Build final data with archives
                                            if existing_archives:
                                                spacer = [[''] * len(header)]
                                                full_data = full_data + spacer + existing_archives
                                                print(f"    ✅ [Retry] Final: {len(full_data)} rows (with archives)")
                                            else:
                                                print(f"    ✅ [Retry] Final: {len(full_data)} rows (no archives)")

                                            
                                            # Clear and write (with archives preserved)
                                            await asyncio.sleep(0.5)
                                            await asyncio.to_thread(member_ws.clear)
                                            await asyncio.sleep(0.3)
                                            await asyncio.to_thread(member_ws.update, 'A1', full_data)
                                            
                                            print(f"    ✅ [Retry {retry_round}] {club_name}: Synced {len(rows_data)} members + Rank #{rank}")
                                            retry_success += 1
                                            members_synced += 1
                                        else:
                                            print(f"    ⚠️ [Retry {retry_round}] {club_name}: No member data to sync")
                                            still_failed.append(failed_item)
                                    else:
                                        # No members sheet, just rank update
                                        print(f"    ✅ [Retry {retry_round}] {club_name}: Rank #{rank} (no member sheet)")
                                        retry_success += 1
                                except Exception as member_err:
                                    print(f"    ⚠️ [Retry {retry_round}] {club_name}: Member sync failed - {member_err}")
                                    still_failed.append(failed_item)
                            else:
                                print(f"    ⏭️ [Retry {retry_round}] {club_name}: No members in API response")
                                still_failed.append(failed_item)
                        else:
                            # RANK-ONLY RETRY - just need rank data
                            api_data = await fetch_club_data_full(club_id)
                            
                            if not api_data:
                                print(f"    ⚠️ [Retry {retry_round}] {club_name}: Still no data")
                                still_failed.append(failed_item)
                                continue
                            
                            circle_data = api_data.get('circle', {})
                            rank = circle_data.get('monthly_rank')
                            
                            if rank is not None and rank > 0:
                                # Success! Update rank only
                                await asyncio.to_thread(
                                    config_ws.update_cell, idx, 11, rank
                                )
                                
                                if club_name in client.config_cache:
                                    client.config_cache[club_name]['Rank'] = rank
                                
                                retry_success += 1
                                rank_updated += 1
                                print(f"    ✅ [Retry {retry_round}] {club_name}: Rank #{rank}")
                            else:
                                print(f"    ⏭️ [Retry {retry_round}] {club_name}: Still no rank data")
                                still_failed.append(failed_item)
                            
                    except Exception as e:
                        print(f"    ❌ [Retry {retry_round}] {club_name}: Error - {e}")
                        still_failed.append(failed_item)
                
                failed_clubs = still_failed
                print(f"  📊 Retry round {retry_round}: {retry_success} succeeded, {len(failed_clubs)} still failed")
                
                # If all succeeded, break early
                if not failed_clubs:
                    print(f"  🎉 All clubs recovered after {retry_round} retry round(s)!")
                    break
            
            # Final summary
            if failed_clubs:
                print(f"\n  ⚠️ FINAL: {len(failed_clubs)} clubs still failed after {max_retries} retries:")
                for failed_item in failed_clubs:
                    club_name = failed_item['config'].get('Club_Name', 'Unknown')
                    print(f"     - {club_name}")
        
        # Log completion
        if members_synced > 0:
            print(f"\n  ✅ Data sync completed successfully")
        
    except Exception as e:
        print(f"[{datetime.datetime.now()}] Error in sync task: {e}")
        import traceback
        traceback.print_exc()



# NOTE: The on_ready event handler is defined earlier in the file (around line 2103)
# Do NOT add another @client.event async def on_ready() here as it would override the main one


# ============================================================================
# SCHEDULE TASK - Auto-fetch from GitHub every 30 minutes
# ============================================================================

async def send_schedule_notification():
    """Send notification to saved channel + ping user when schedule updates"""
    try:
        config = load_schedule_config()
        channel_id = config.get("channel_id", SCHEDULE_DEFAULT_CHANNEL_ID)
        channel = client.get_channel(channel_id)
        
        if not channel:
            channel = client.get_channel(SCHEDULE_DEFAULT_CHANNEL_ID)
        if not channel:
            print("⚠️ Schedule notification: No valid channel found")
            return
        
        embed = discord.Embed(
            title="📅 Game Schedule Updated!",
            description=f"**{len(schedule_cache)} events** available.\nUse `/schedule` to view the full schedule.",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        
        if schedule_cache:
            preview = "\n".join([f"• {e.get('event_name', 'Unknown')}" for e in schedule_cache[:3]])
            if len(schedule_cache) > 3:
                preview += f"\n*...+{len(schedule_cache) - 3} more*"
            embed.add_field(name="📋 Upcoming Events", value=preview, inline=False)
        
        # Send with ping
        await channel.send(content=f"<@{SCHEDULE_NOTIFY_USER_ID}>", embed=embed)
        print(f"✅ Schedule notification sent to channel {channel_id} + pinged user")
    except Exception as e:
        print(f"⚠️ Schedule notification error: {e}")


@tasks.loop(minutes=30)
async def fetch_schedule_task():
    """Fetch schedule.json from GitHub - only notify if CONTENT actually changed"""
    global schedule_last_etag, schedule_cache
    
    try:
        headers = {"If-None-Match": schedule_last_etag} if schedule_last_etag else {}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(SCHEDULE_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 304:
                    # Not modified
                    return
                
                if resp.status == 200:
                    # GitHub raw returns text/plain, so use text() + json.loads()
                    text_data = await resp.text()
                    new_data = json.loads(text_data)
                    new_etag = resp.headers.get("ETag")
                    
                    # CONTENT COMPARISON - only notify if events actually changed
                    old_events = {e.get('event_name') for e in schedule_cache}
                    new_events = {e.get('event_name') for e in new_data}
                    content_changed = old_events != new_events
                    
                    # Update cache
                    had_data = len(schedule_cache) > 0
                    schedule_cache = new_data
                    schedule_last_etag = new_etag
                    
                    # Save to disk
                    with open(SCHEDULE_CACHE_FILE, 'w', encoding='utf-8') as f:
                        json.dump({
                            "etag": new_etag,
                            "data": new_data,
                            "updated_at": datetime.datetime.now().isoformat()
                        }, f, indent=2)
                    
                    print(f"✅ Schedule fetched: {len(new_data)} events, changed={content_changed}")
                    
                    # Only notify if had previous data AND content actually changed
                    if had_data and content_changed:
                        await send_schedule_notification()
                else:
                    print(f"⚠️ Schedule fetch: HTTP {resp.status}")
    except Exception as e:
        print(f"⚠️ Schedule fetch error: {e}")


@fetch_schedule_task.before_loop
async def before_fetch_schedule():
    """Load cached data on startup and wait for bot ready"""
    global schedule_cache, schedule_last_etag
    
    await client.wait_until_ready()
    
    # Load cached data from disk
    if os.path.exists(SCHEDULE_CACHE_FILE):
        try:
            with open(SCHEDULE_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                schedule_cache = data.get("data", [])
                schedule_last_etag = data.get("etag")
                print(f"✅ Loaded {len(schedule_cache)} cached schedule events")
        except Exception as e:
            print(f"⚠️ Failed to load schedule cache: {e}")
    
    print("✅ Schedule fetch task ready (runs every 30 min)")


# ============================================================================
# /SCHEDULE COMMAND - Admin only, saves channel ID for future notifications
# ============================================================================

@client.tree.command(name="schedule", description="View game schedule (Admin only)")
@is_admin_or_has_role()
async def schedule_command(interaction: discord.Interaction):
    """Display game event schedule and save this channel for future auto-notifications"""
    
    await interaction.response.defer()
    
    # Save this channel for future notifications
    save_schedule_channel(interaction.channel_id)
    print(f"📅 Schedule channel saved: {interaction.channel_id}")
    
    if not schedule_cache:
        await interaction.followup.send(
            "❌ No schedule data available yet. The bot fetches schedule every 30 minutes.\n"
            "Please try again later.",
            ephemeral=True
        )
        return
    
    # Send message about channel being saved
    await interaction.followup.send(
        f"📅 **Game Schedule** ({len(schedule_cache)} events)\n"
        f"💡 *This channel has been saved for future schedule update notifications.*"
    )
    
    # Send each event as a separate embed - TazunaBot style with large image
    for event in schedule_cache:
        color = SCHEDULE_COLORS.get(event.get('event_type'), SCHEDULE_COLORS['Default'])
        
        embed = discord.Embed(
            title=event.get('event_name', 'Unknown Event'),
            description=event.get('date', 'TBD'),  # Date as description like TazunaBot
            color=color
        )
        
        # Use set_image for full-width banner (like TazunaBot's MEDIA_GALLERY)
        if event.get('thumbnail'):
            embed.set_image(url=event['thumbnail'])
        
        await interaction.followup.send(embed=embed)
    
    # Maybe send promo message
    await maybe_send_promo_message(interaction)



# ============================================================================
# START SCHEDULED TASKS ON READY
# ============================================================================
@client.event
async def on_ready():
    """Start scheduled tasks when bot is ready"""
    print(f"✅ Logged in as {client.user} (ID: {client.user.id})")
    
    # Register persistent views - REQUIRED for button handlers to work after restart
    try:
        from god_mode_panel import GodModeControlPanel, update_god_mode_panel
        client.add_view(GodModeControlPanel())
        
        # Register ChannelListView with empty data (will be populated later)
        client.add_view(ChannelListView([], {}))
        
        # Register ServerListView with empty data (will be populated later)
        client.add_view(ServerListView([], servers_per_page=10))
        
        print("✅ Persistent views registered")
        
        # Load Tournament cog (direct import since we use discord.Client not commands.Bot)
        try:
            from cogs.tournament import AdminPanelView, RegistrationView, ResultSubmitView, fetch_uma_list
            from tournament_manager import load_active_tournaments, get_active_tournament
            
            # Register persistent views
            client.add_view(AdminPanelView())
            client.add_view(RegistrationView())
            
            # Fetch uma list and load active tournaments
            await fetch_uma_list()
            load_active_tournaments()
            
            # Register ResultSubmitView for all active matches
            for guild in client.guilds:
                tournament = get_active_tournament(guild.id)
                if tournament:
                    for match in tournament.matches:
                        if match.thread_id and not match.match_winner:
                            client.add_view(ResultSubmitView(match.match_id))
            
            # Auto-refresh admin panel for all guilds
            for guild in client.guilds:
                tournament = get_active_tournament(guild.id)
                if tournament and tournament.admin_channel_id:
                    try:
                        admin_channel = guild.get_channel(tournament.admin_channel_id)
                        if admin_channel:
                            # Find and update existing panel message
                            async for msg in admin_channel.history(limit=50):
                                if msg.author == client.user and msg.embeds:
                                    if any("CONTROL PANEL" in (e.title or "") for e in msg.embeds):
                                        # Update with fresh view
                                        await msg.edit(view=AdminPanelView())
                                        print(f"✅ Refreshed tournament panel in {guild.name}")
                                        break
                    except Exception as panel_e:
                        print(f"⚠️ Panel refresh error in {guild.name}: {panel_e}")
            
            print("✅ Tournament module loaded")
        except Exception as cog_e:
            print(f"⚠️ Tournament module error: {cog_e}")
        
        # Update God Mode panel
        await update_god_mode_panel(client)
    except Exception as e:
        print(f"⚠️ God Mode panel error: {e}")
    
    # Update channel list message with pagination
    try:
        await update_channel_list_message()
        print("✅ Channel list updated")
    except Exception as e:
        print(f"⚠️ Channel list update error: {e}")
    
    # Update server list message with pagination
    try:
        await update_server_list_message()
        print("✅ Server list updated")
    except Exception as e:
        print(f"⚠️ Server list update error: {e}")
    
    # Update caches - REQUIRED for other commands to work
    try:
        await client.update_caches()
        print("✅ Caches updated")
    except Exception as e:
        print(f"⚠️ Cache update error: {e}")
    
    # Start scheduled tasks
    if not auto_refresh_data_cache.is_running():
        auto_refresh_data_cache.start()
    if not update_club_data_task.is_running():
        update_club_data_task.start()
    if not fetch_schedule_task.is_running():
        fetch_schedule_task.start()
    if not auto_backup_configs.is_running():
        auto_backup_configs.start()
    
    print("✅ All scheduled tasks started")
    print(f"🚀 Bot is ready! Serving {len(client.guilds)} guilds")


@client.event
async def on_message(message: discord.Message):
    """Process messages for tournament ban/pick flow"""
    # Skip bot messages
    if message.author.bot:
        return
    
    # Skip if not in a guild
    if not message.guild:
        return
    
    # Process tournament messages
    try:
        from cogs.tournament import process_match_message
        await process_match_message(message, client)
    except Exception as e:
        # Silently fail - not all messages are tournament related
        pass


if __name__ == "__main__":
    # Load bot token from environment variable (secure method)
    BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    
    if not BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN environment variable is not set!")
        print("Please create a .env file with:")
        print("    DISCORD_BOT_TOKEN=your_bot_token_here")
        print("")
        print("Or set the environment variable directly.")
        sys.exit(1)
    
    try:
        print("Starting bot...")
        client.run(BOT_TOKEN)
    except discord.LoginFailure:
        print("ERROR: Invalid Bot Token.")
        print("Please check your token at: https://discord.com/developers/applications")
    except Exception as e:
        print(f"Error running bot: {e}")