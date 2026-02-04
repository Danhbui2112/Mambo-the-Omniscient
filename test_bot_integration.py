#!/usr/bin/env python
"""Test bot-github.py integration"""

import importlib.util
import sys

try:
    # Load bot-github.py dynamically
    spec = importlib.util.spec_from_file_location("bot_github", "bot-github.py")
    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)
    
    print("✅ bot-github.py loaded successfully!")
    print("✅ All Phase 2 imports working")
    print("✅ No runtime errors on load")
    sys.exit(0)
    
except Exception as e:
    print(f"❌ Error loading bot-github.py:")
    print(f"   {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
