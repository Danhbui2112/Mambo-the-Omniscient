"""
Uma Data Fetcher - Lấy danh sách uma từ TazunaDiscordBot GitHub
"""
import aiohttp
import json
import os
from typing import List, Dict, Optional
from rapidfuzz import fuzz, process

# URLs
CHARACTER_URL = "https://raw.githubusercontent.com/JustWastingTime/TazunaDiscordBot/main/assets/character.json"

# Cache
_uma_cache: List[Dict] = []
_uma_names: List[str] = []

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
UMA_CACHE_FILE = os.path.join(SCRIPT_DIR, "data_cache", "uma_characters.json")


async def fetch_uma_list(force_refresh: bool = False) -> List[Dict]:
    """
    Fetch danh sách uma từ GitHub, cache locally
    
    Returns:
        List of {character_name, costume, thumbnail, id}
    """
    global _uma_cache, _uma_names
    
    # Return cached if available
    if _uma_cache and not force_refresh:
        return _uma_cache
    
    # Try load from disk cache first
    if not force_refresh and os.path.exists(UMA_CACHE_FILE):
        try:
            with open(UMA_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _uma_cache = data
                _uma_names = list(set(uma['character_name'] for uma in data))
                print(f"✅ Loaded {len(_uma_cache)} uma from disk cache")
                return _uma_cache
        except Exception as e:
            print(f"⚠️ Failed to load uma cache: {e}")
    
    # Fetch from GitHub
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(CHARACTER_URL, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    # GitHub raw returns text/plain, need to ignore content-type
                    data = await response.json(content_type=None)
                    
                    # Extract relevant fields
                    _uma_cache = []
                    for uma in data:
                        _uma_cache.append({
                            'id': uma.get('id', ''),
                            'character_name': uma.get('character_name', ''),
                            'costume': uma.get('costume', ''),
                            'thumbnail': uma.get('thumbnail', ''),
                            'aliases': uma.get('aliases', [])
                        })
                    
                    _uma_names = list(set(uma['character_name'] for uma in _uma_cache))
                    
                    # Save to disk cache
                    os.makedirs(os.path.dirname(UMA_CACHE_FILE), exist_ok=True)
                    with open(UMA_CACHE_FILE, 'w', encoding='utf-8') as f:
                        json.dump(_uma_cache, f, indent=2)
                    
                    print(f"✅ Fetched {len(_uma_cache)} uma from GitHub")
                    return _uma_cache
                else:
                    print(f"❌ Failed to fetch uma: HTTP {response.status}")
                    return []
    except Exception as e:
        print(f"❌ Error fetching uma: {e}")
        return []


def get_uma_names() -> List[str]:
    """Get list of unique uma character names"""
    return _uma_names.copy()


def search_uma(query: str, limit: int = 25) -> List[str]:
    """
    Fuzzy search uma by name
    
    Args:
        query: Search query
        limit: Max results to return
        
    Returns:
        List of matching character names
    """
    if not _uma_names:
        return []
    
    if not query:
        return _uma_names[:limit]
    
    # Use rapidfuzz for fuzzy matching
    results = process.extract(
        query, 
        _uma_names, 
        scorer=fuzz.WRatio,
        limit=limit
    )
    
    return [name for name, score, _ in results if score > 50]


def get_uma_thumbnail(character_name: str) -> Optional[str]:
    """Get Gametora thumbnail URL for uma"""
    for uma in _uma_cache:
        if uma['character_name'].lower() == character_name.lower():
            return uma.get('thumbnail')
    return None


def validate_uma_names(names: List[str]) -> tuple[List[str], List[str]]:
    """
    Validate list of uma names
    
    Returns:
        (valid_names, invalid_names)
    """
    valid = []
    invalid = []
    
    uma_names_lower = [n.lower() for n in _uma_names]
    
    for name in names:
        name = name.strip()
        if name.lower() in uma_names_lower:
            # Get proper case name
            idx = uma_names_lower.index(name.lower())
            valid.append(_uma_names[idx])
        else:
            # Try fuzzy match
            matches = search_uma(name, limit=1)
            if matches and fuzz.ratio(name.lower(), matches[0].lower()) > 80:
                valid.append(matches[0])
            else:
                invalid.append(name)
    
    return valid, invalid
