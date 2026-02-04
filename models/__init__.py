"""Models package - Core data structures and managers"""

from .cache import SmartCache, CROSS_CLUB_CACHE, update_cross_club_cache, get_cross_club_data
from .proxy import ProxyManager
from .database import GoogleSheetsManager, gs_manager, supabase_db, USE_SUPABASE, hybrid_db, get_gs_manager, get_hybrid_db

__all__ = [
    'SmartCache',
    'CROSS_CLUB_CACHE',
    'update_cross_club_cache',
    'get_cross_club_data',
    'ProxyManager',
    'GoogleSheetsManager',
    'gs_manager',
    'supabase_db',
    'USE_SUPABASE',
    'hybrid_db',
    'get_gs_manager',
    'get_hybrid_db',
]
