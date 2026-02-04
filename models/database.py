"""
Database management module for Google Sheets and Supabase integration.

Provides:
- GoogleSheetsManager: Connection and retry logic for Google Sheets API
- Supabase integration for backup storage
- Hybrid failover system
"""

import time
import random
import asyncio
import gspread
from gspread.exceptions import WorksheetNotFound

# Import from local modules
from config import config
from utils.error_handling import log_error, is_retryable_error

# ============================================================================
# DATABASE INITIALIZATION
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


# ============================================================================
# GOOGLE SHEETS MANAGER
# ============================================================================

class GoogleSheetsManager:
    """Manages Google Sheets connection with retry logic
    
    Features:
    - Automatic connection establishment
    - Retry logic with exponential backoff
    - Config sheet verification
    - Async timeout protection
    """
    
    def __init__(self):
        """Initialize Google Sheets connection"""
        self.gc = None
        self.sh = None
        self.connected = False
        self._connect()
    
    def _connect(self):
        """Establish connection to Google Sheets"""
        try:
            self.gc = gspread.service_account(filename=config.SERVICE_ACCOUNT_FILE)
            self.sh = self.gc.open_by_key(config.GOOGLE_SHEET_ID)
            self.connected = True
            print("Bot: Connected to Google Sheets.")
            self._verify_config_sheet()
        except Exception as e:
            log_error(e, "Google Sheets connection", {"sheet_id": config.GOOGLE_SHEET_ID})
            if is_retryable_error(e):
                print(f"--- BOT WARNING: COULD NOT CONNECT TO GSHEETS: {e} ---")
                print("--- Will attempt to load from local cache... ---")
            else:
                print(f"--- BOT CRITICAL ERROR (NON-NETWORK): {e} ---")
                raise
    
    def _verify_config_sheet(self):
        """Verify the config sheet has correct headers"""
        try:
            config_ws = self.sh.worksheet(config.CONFIG_SHEET_NAME)
            headers = config_ws.row_values(1)
            expected_headers = [
                'Club_Name', 'Data_Sheet_Name', 'Members_Sheet_Name',
                'Target_Per_Day', 'Club_URL',
                'Club_Type', 'Club_ID', 'Leaders', 'Officers', 'Server_ID', 'Rank'
            ]
            if headers != expected_headers:
                print("ERROR (Bot): 'Clubs_Config' sheet has incorrect headers...")
                print(f"EXPECTED: {expected_headers}")
                print(f"ACTUAL:   {headers}")
        except WorksheetNotFound:
            print(f"ERROR (Bot): '{config.CONFIG_SHEET_NAME}' sheet not found.")
    
    def get_worksheet_with_retry(self, sheet_name: str, max_retries: int = None) -> list:
        """Get worksheet data with enhanced retry logic
        
        Enhanced with:
        - 5 retries (up from 3)
        - Exponential backoff with jitter
        - Better error logging
        
        Args:
            sheet_name: Name of the worksheet to fetch
            max_retries: Maximum number of retry attempts
            
        Returns:
            List of all values from worksheet
        """
        max_retries = max_retries or 5  # Increased default
        
        for attempt in range(max_retries):
            try:
                ws = self.sh.worksheet(sheet_name)
                return ws.get_all_values()
            except Exception as e:
                log_error(e, "Google Sheets worksheet", {"sheet_name": sheet_name, "attempt": attempt + 1})
                if is_retryable_error(e):
                    if attempt + 1 == max_retries:
                        print(f"❌ FATAL: All {max_retries} retries failed for sheet '{sheet_name}'.")
                        raise
                    
                    # Exponential backoff with jitter
                    base_wait = config.RETRY_DELAY * (2 ** attempt)
                    jitter = random.uniform(0, 0.5)
                    wait_time = base_wait + jitter
                    
                    print(f"⚠️ Attempt {attempt + 1}/{max_retries}: Network error getting '{sheet_name}'")
                    print(f"   Error: {str(e)[:100]}")
                    print(f"   ⏳ Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    print(f"❌ GSheet Read Error (non-retryable) for '{sheet_name}': {e}")
                    raise
        return []
    
    async def get_worksheet_async(self, sheet_name: str, timeout_seconds: int = 10, max_retries: int = 3) -> list:
        """Async version with timeout protection
        
        OPTIMIZATIONS:
        - asyncio.timeout(10) prevents hanging on slow responses
        - Reduced retries from 5 to 3 for faster failure
        - Uses asyncio.to_thread for sync gspread calls
        
        Args:
            sheet_name: Name of the worksheet to fetch
            timeout_seconds: Timeout per attempt (default 10s)
            max_retries: Number of retry attempts (default 3)
            
        Returns:
            List of all values from worksheet
        """
        for attempt in range(max_retries):
            try:
                # Timeout protection - prevents hanging
                async with asyncio.timeout(timeout_seconds):
                    ws = await asyncio.to_thread(self.sh.worksheet, sheet_name)
                    data = await asyncio.to_thread(ws.get_all_values)
                    return data
                    
            except asyncio.TimeoutError:
                log_error(
                    TimeoutError(f"Google Sheets timeout after {timeout_seconds}s"),
                    "Async worksheet fetch",
                    {"sheet_name": sheet_name, "attempt": attempt + 1}
                )
                if attempt + 1 == max_retries:
                    print(f"⏰ TIMEOUT: All {max_retries} attempts timed out for '{sheet_name}'")
                    raise TimeoutError(f"Google Sheets timeout after {max_retries} attempts")
                
                # Shorter wait for timeout (likely network issue)
                wait_time = 2 * (attempt + 1)
                print(f"⏰ Timeout on attempt {attempt + 1}/{max_retries} for '{sheet_name}'")
                print(f"   ⏳ Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                
            except Exception as e:
                log_error(e, "Async worksheet fetch", {"sheet_name": sheet_name, "attempt": attempt + 1})
                if is_retryable_error(e):
                    if attempt + 1 == max_retries:
                        print(f"❌ All {max_retries} retries failed for '{sheet_name}'")
                        raise
                    
                    # Exponential backoff with jitter
                    base_wait = 1 * (2 ** attempt)
                    jitter = random.uniform(0, 0.5)
                    wait_time = base_wait + jitter
                    
                    print(f"⚠️ Attempt {attempt + 1}/{max_retries}: Error getting '{sheet_name}'")
                    print(f"   Error: {str(e)[:80]}")
                    print(f"   ⏳ Retrying in {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"❌ Non-retryable error for '{sheet_name}': {e}")
                    raise
        return []


# ============================================================================
# LAZY INITIALIZATION FOR GOOGLE SHEETS MANAGER
# ============================================================================

_gs_manager_instance = None
_hybrid_db_instance = None

def get_gs_manager():
    """Get or create singleton GoogleSheetsManager instance (lazy initialization)"""
    global _gs_manager_instance
    if _gs_manager_instance is None:
        _gs_manager_instance = GoogleSheetsManager()
    return _gs_manager_instance

# Maintain backward compatibility - gs_manager can be accessed as property
class _GSManagerProxy:
    def __getattr__(self, name):
        return getattr(get_gs_manager(), name)
    
    def __repr__(self):
        return "<LazyProxy to GoogleSheetsManager (initialized on first use)>"

gs_manager = _GSManagerProxy()

# ============================================================================
# HYBRID DATABASE WRAPPER
# ============================================================================

def get_hybrid_db():
    """Get or create singleton hybrid database instance (lazy initialization)"""
    global _hybrid_db_instance, _gs_manager_instance
    if _hybrid_db_instance is None and USE_SUPABASE and supabase_db:
        try:
            from hybrid_database_wrapper import initialize_hybrid_db
            _gs_mgr = get_gs_manager()
            _hybrid_db_instance = initialize_hybrid_db(_gs_mgr, supabase_db)
            print("🔄 Hybrid database system active")
            print("   Strategy: Sheets first → Supabase on timeout/errors")
        except Exception as e:
            print(f"⚠️ Hybrid wrapper unavailable: {e}")
            print("   Using direct database access")
    return _hybrid_db_instance

hybrid_db = None  # Will be lazily initialized when needed
