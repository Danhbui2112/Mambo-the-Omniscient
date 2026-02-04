"""
Cache management module for smart data caching with disk persistence.

Provides:
- SmartCache: In-memory cache with TTL and disk persistence
- Cross-club transfer detection system
"""

import os
import json
import time
import pandas as pd
from io import StringIO
from typing import Tuple, Optional

# ============================================================================
# SMART DATA CACHE WITH DISK PERSISTENCE
# ============================================================================

class SmartCache:
    """In-memory cache with disk persistence for reliability
    
    Features:
    - Automatic TTL expiration
    - Disk persistence for recovery after restart
    - Memory usage monitoring
    - Individual key invalidation
    """
    
    def __init__(self, cache_dir: str, ttl_seconds: int = 1800):
        """
        Initialize SmartCache
        
        Args:
            cache_dir: Directory for disk cache storage
            ttl_seconds: Time-to-live in seconds (default: 30 minutes)
        """
        self.cache = {}  # In-memory cache: {key: (data, timestamp)}
        self.cache_dir = cache_dir
        self.ttl = ttl_seconds  # Time-to-live in seconds
        os.makedirs(cache_dir, exist_ok=True)
        self._load_from_disk()
    
    def _get_cache_file(self, key: str) -> str:
        """Get cache file path for a key"""
        safe_key = key.replace('/', '_').replace('\\', '_')
        return os.path.join(self.cache_dir, f"{safe_key}.cache.json")
    
    def _load_from_disk(self):
        """Load all cache files from disk on startup"""
        try:
            cache_files = [f for f in os.listdir(self.cache_dir) if f.endswith('.cache.json')]
            loaded = 0
            for filename in cache_files:
                try:
                    filepath = os.path.join(self.cache_dir, filename)
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                    
                    key = data.get('key')
                    if key:
                        df = pd.read_json(StringIO(data['dataframe_json']), orient='records')
                        timestamp = data.get('timestamp', time.time())
                        self.cache[key] = (df, timestamp)
                        loaded += 1
                except Exception as e:
                    print(f"Warning: Failed to load cache file {filename}: {e}")
            
            if loaded > 0:
                print(f"✅ Loaded {loaded} cached datasets from disk")
        except Exception as e:
            print(f"Warning: Could not load cache from disk: {e}")
    
    def get(self, key: str):
        """Get data from cache (in-memory or disk) with TTL check
        
        Args:
            key: Cache key
            
        Returns:
            Tuple of (dataframe, timestamp) if found and not expired, None otherwise
        """
        # Try in-memory first
        if key in self.cache:
            df, timestamp = self.cache[key]
            age = time.time() - timestamp
            
            if age < self.ttl:
                # Cache still fresh
                return (df, timestamp)
            else:
                # Cache expired - invalidate it
                print(f"⏰ Cache EXPIRED for {key} (age: {age/60:.1f} min, TTL: {self.ttl/60:.1f} min)")
                self.invalidate(key)
                return None
        
        # Try disk fallback
        try:
            cache_file = self._get_cache_file(key)
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                
                df = pd.read_json(StringIO(data['dataframe_json']), orient='records')
                timestamp = data.get('timestamp', time.time())
                age = time.time() - timestamp
                
                # Check TTL for disk cache too
                if age < self.ttl:
                    # Load into memory
                    self.cache[key] = (df, timestamp)
                    return (df, timestamp)
                else:
                    # Disk cache expired - delete file
                    print(f"⏰ Disk cache EXPIRED for {key} (age: {age/60:.1f} min)")
                    try:
                        os.remove(cache_file)
                    except:
                        pass
                    return None
        except Exception as e:
            print(f"Warning: Failed to load cache from disk for {key}: {e}")
        
        return None
    
    def set(self, key: str, df):
        """Set data in cache (both memory and disk)
        
        Args:
            key: Cache key
            df: DataFrame to cache
        """
        timestamp = time.time()
        self.cache[key] = (df, timestamp)
        
        # Persist to disk
        try:
            cache_file = self._get_cache_file(key)
            data = {
                'key': key,
                'timestamp': timestamp,
                'dataframe_json': df.to_json(orient='records')
            }
            with open(cache_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Warning: Failed to save cache to disk for {key}: {e}")
    
    def invalidate(self, key: str = None):
        """Clear cache for specific key or all (memory + disk)
        
        Args:
            key: Specific key to invalidate, or None to clear all
        """
        if key:
            # Clear from memory
            if key in self.cache:
                del self.cache[key]
                print(f"🗑️ Cache INVALIDATED for {key}")
            
            # Clear from disk
            try:
                cache_file = self._get_cache_file(key)
                if os.path.exists(cache_file):
                    os.remove(cache_file)
                    print(f"🗑️ Disk cache file deleted for {key}")
            except Exception as e:
                print(f"Warning: Could not delete disk cache for {key}: {e}")
        else:
            # Clear all from memory
            self.cache.clear()
            print("🗑️ Cache CLEARED completely")
            
            # Clear all from disk
            try:
                cache_files = [f for f in os.listdir(self.cache_dir) if f.endswith('.cache.json')]
                for filename in cache_files:
                    try:
                        os.remove(os.path.join(self.cache_dir, filename))
                    except:
                        pass
                if cache_files:
                    print(f"🗑️ Deleted {len(cache_files)} disk cache files")
            except Exception as e:
                print(f"Warning: Could not delete disk cache files: {e}")
    
    def get_stats(self) -> dict:
        """Get cache statistics with age information
        
        Returns:
            Dictionary with cache statistics
        """
        total_size = 0
        cache_ages = {}
        current_time = time.time()
        
        for key, (df, timestamp) in self.cache.items():
            try:
                total_size += df.memory_usage(deep=True).sum()
                age_minutes = (current_time - timestamp) / 60
                cache_ages[key] = age_minutes
            except:
                pass
        
        return {
            "total_entries": len(self.cache),
            "keys": list(self.cache.keys()),
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "cache_ages": cache_ages,
            "ttl_minutes": round(self.ttl / 60, 1)
        }


# ============================================================================
# CROSS-CLUB TRANSFER CACHE
# ============================================================================

# Cache for detecting member transfers between clubs
# Structure: { 'trainer_id': {'club_name': str, 'day31_cumulative': int, 'month': str} }
# Built during sync task (24h cycle), used for /stats cross-club lookup
CROSS_CLUB_CACHE = {}


def update_cross_club_cache(trainer_id: str, club_name: str, day31_cumulative: int, month: str):
    """Update cross-club cache for a member
    
    Args:
        trainer_id: The trainer's ID
        club_name: Current club name
        day31_cumulative: Cumulative fans on day 31
        month: Month identifier
    """
    global CROSS_CLUB_CACHE
    CROSS_CLUB_CACHE[trainer_id] = {
        'club_name': club_name,
        'day31_cumulative': day31_cumulative,
        'month': month
    }


def get_cross_club_data(trainer_id: str) -> dict:
    """Get cross-club data for a trainer ID
    
    Args:
        trainer_id: The trainer's ID
        
    Returns:
        Dict with club data or None if not found
    """
    return CROSS_CLUB_CACHE.get(trainer_id)
