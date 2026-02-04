#!/usr/bin/env python
"""
Phase 2 Module Integration Test

Tests that all extracted Phase 2 modules can be imported and initialized correctly.
"""

import sys

def test_phase2_modules():
    """Test all Phase 2 module imports"""
    print("=" * 60)
    print("PHASE 2 MODULE INTEGRATION TEST")
    print("=" * 60)
    
    try:
        print("\n1. Testing config module...")
        from config import config, BotConfig, SCRIPT_DIR
        print("   ✅ config imports working")
        print(f"   ✅ BotConfig initialized: {config.CONFIG_SHEET_NAME}")
        
        print("\n2. Testing models.cache module...")
        from models.cache import SmartCache, CROSS_CLUB_CACHE, update_cross_club_cache
        print("   ✅ cache imports working")
        print(f"   ✅ SmartCache class available: {SmartCache}")
        
        print("\n3. Testing models.proxy module...")
        from models.proxy import ProxyManager
        print("   ✅ proxy imports working")
        print(f"   ✅ ProxyManager class available: {ProxyManager}")
        
        print("\n4. Testing models.database module...")
        from models.database import GoogleSheetsManager, gs_manager, get_gs_manager
        print("   ✅ database imports working")
        print(f"   ✅ GoogleSheetsManager class available: {GoogleSheetsManager}")
        print(f"   ✅ gs_manager lazy proxy available: {gs_manager}")
        
        print("\n5. Testing utils.error_handling module...")
        from utils.error_handling import log_error, is_retryable_error
        print("   ✅ error_handling imports working")
        
        print("\n6. Testing utils.formatting module...")
        from utils.formatting import format_fans, format_fans_full, calculate_daily_from_cumulative
        print("   ✅ formatting imports working")
        result = format_fans(1500)
        print(f"   ✅ format_fans(1500) = '{result}'")
        
        print("\n7. Testing utils.timestamp module...")
        from utils.timestamp import get_last_update_timestamp, save_last_update_timestamp
        print("   ✅ timestamp imports working")
        
        print("\n8. Testing managers.profile_manager module...")
        from managers import add_support_footer, maybe_send_promo_message, pending_verifications
        print("   ✅ profile_manager imports working")
        
        print("\n9. Testing managers.schedule_manager module...")
        from managers import SCHEDULE_URL, SCHEDULE_COLORS
        print("   ✅ schedule_manager imports working")
        print(f"   ✅ SCHEDULE_COLORS has {len(SCHEDULE_COLORS)} event types")
        
        print("\n" + "=" * 60)
        print("✅ ALL PHASE 2 MODULES WORKING!")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\n❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_phase2_modules()
    sys.exit(0 if success else 1)
