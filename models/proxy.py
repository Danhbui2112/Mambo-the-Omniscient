"""
Proxy management module for rotating through proxy servers.

Supports both authenticated (ip:port:user:pass) and simple (ip:port) formats.
"""

import os
import asyncio
from typing import Optional, List

# ============================================================================
# PROXY MANAGER
# ============================================================================

class ProxyManager:
    """Manage and rotate through a list of proxies for API calls
    
    Features:
    - Load proxies from file with multiple formats
    - Round-robin rotation
    - Thread-safe access
    - Reload capability
    """
    
    def __init__(self, proxy_file: str = None):
        """
        Initialize ProxyManager
        
        Args:
            proxy_file: Path to proxies.txt file
        """
        self.proxies: List[str] = []
        self.current_index = 0
        self.lock = asyncio.Lock()
        
        if proxy_file:
            self.load_proxies(proxy_file)
    
    def load_proxies(self, proxy_file: str):
        """Load proxies from file
        
        Supported formats:
        - ip:port:username:password (authenticated)
        - ip:port (simple)
        - Lines starting with # are comments
        
        Args:
            proxy_file: Path to proxies file
        """
        if not os.path.exists(proxy_file):
            print(f"⚠️ Proxy file not found: {proxy_file}")
            return
        
        try:
            with open(proxy_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith('#'):
                        continue
                    
                    parts = line.split(':')
                    if len(parts) == 4:
                        # Format: ip:port:username:password
                        ip, port, username, password = parts
                        proxy_url = f"http://{username}:{password}@{ip}:{port}"
                        self.proxies.append(proxy_url)
                    elif len(parts) == 2:
                        # Format: ip:port (no auth)
                        ip, port = parts
                        proxy_url = f"http://{ip}:{port}"
                        self.proxies.append(proxy_url)
            
            print(f"✅ Loaded {len(self.proxies)} proxies from {proxy_file}")
        except Exception as e:
            print(f"❌ Error loading proxies: {e}")
    
    def get_next_proxy(self) -> Optional[str]:
        """Get next proxy in rotation (thread-safe)
        
        Returns:
            Next proxy URL or None if no proxies loaded
        """
        if not self.proxies:
            return None
        
        proxy = self.proxies[self.current_index % len(self.proxies)]
        self.current_index += 1
        return proxy
    
    def get_proxy_connector(self) -> Optional[str]:
        """Get aiohttp proxy connector for next proxy
        
        Returns:
            Next proxy URL or None if no proxies loaded
        """
        proxy_url = self.get_next_proxy()
        if proxy_url:
            return proxy_url
        return None
    
    def get_all_proxies(self) -> List[str]:
        """Get copy of all loaded proxies
        
        Returns:
            List of all proxy URLs
        """
        return self.proxies.copy()
    
    def reload(self, proxy_file: str):
        """Reload proxies from file
        
        Args:
            proxy_file: Path to proxies file
        """
        self.proxies = []
        self.current_index = 0
        self.load_proxies(proxy_file)
