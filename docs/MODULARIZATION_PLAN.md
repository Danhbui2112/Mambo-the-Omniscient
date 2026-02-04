# Modularization Plan for bot-github.py

**File Size:** 12,218 lines (MASSIVE - should be split into modules)

---

## Current Structure Overview

The `bot-github.py` file contains:
- **4 Core System Classes**: `SmartCache`, `BotConfig`, `ProxyManager`, `GoogleSheetsManager`
- **10+ UI/View Classes**: `ProfileOwnershipView`, `FilterModal`, `OldClubModal`, `ClubManagementBot`, etc.
- **50+ Discord Commands**: `/stats`, `/leaderboard`, `/search_club`, `/profile`, etc.
- **5+ Scheduled Tasks** (@tasks.loop decorators)
- **100+ Utility Functions**: Profile verification, member lookup, transfer detection, etc.
- **Configuration & Constants**: Paths, channel IDs, URLs, colors, etc.

**Issues with Current Monolithic Structure:**
1. ❌ Hard to navigate & maintain (12K+ lines)
2. ❌ Difficult to test individual features
3. ❌ Code reuse between features is mixed
4. ❌ Slow IDE performance & autocomplete
5. ❌ Hard for new developers to understand
6. ❌ No clear separation of concerns

---

## Recommended Module Structure

```
Mambo-the-Omniscient/
├── bot-github.py                    # Main bot entry point (simplified)
├── config.py                        # All configuration
├── models/
│   ├── __init__.py
│   ├── cache.py                     # SmartCache, CROSS_CLUB_CACHE
│   ├── database.py                  # GoogleSheetsManager, Supabase setup
│   └── proxy.py                     # ProxyManager
├── managers/
│   ├── __init__.py
│   ├── schedule_manager.py          # Schedule system & tasks
│   ├── profile_manager.py           # Profile verification & linking
│   └── tournament_manager.py        # (Already exists - integrate better)
├── commands/
│   ├── __init__.py
│   ├── stats_commands.py            # /stats, /profile, /leaderboard, /search_club
│   ├── club_commands.py             # /club_add, /club_edit, /club_search, /sync_club
│   ├── admin_commands.py            # /set_channel, /cache_stats, /tournament_setup
│   └── system_commands.py           # /status, /uptime, /schedule, /help
├── tasks/
│   ├── __init__.py
│   ├── daily_sync.py                # Daily sync task (23:00 UTC)
│   ├── schedule_updates.py          # Schedule fetch task (6 hours)
│   └── verification_cleanup.py      # 1-minute cleanup task
├── views/
│   ├── __init__.py
│   ├── stats_views.py               # StatsView, LeaderboardView, SearchClubView
│   ├── club_views.py                # ClubListView, ClubTypeSelectionView
│   ├── modals.py                    # All Modal classes (Filter, OldClub, Setup, etc)
│   ├── profile_views.py             # ProfileOwnershipView
│   └── admin_views.py               # AdminApprovalView, ChannelListView, etc
├── utils/
│   ├── __init__.py
│   ├── formatting.py                # Format functions (format_fans, center_text, etc)
│   ├── error_handling.py            # log_error, is_retryable_error
│   ├── member_lookup.py             # find_member_*, find_viewer_*, get_viewer_id_from_sheets
│   ├── verification.py              # Profile verification helpers (call_ocr_service, etc)
│   └── web_logging.py               # send_log_to_web, sync_*_to_web
└── data/
    └── cache/                       # Auto-created during runtime
```

---

## Detailed Module Breakdown

### 1. **config.py** - Central Configuration
```python
# Contains:
- parse_int_list()
- BotConfig (dataclass)
- All environment variables loading
- File paths (SCRIPT_DIR, CACHE_DIR, etc)
- Channel IDs
- Constants (SUPPORT_SERVER_URL, DONATION_URL, VOTE_URL, PROMO_CHANCE, etc)
- SCHEDULE_COLORS dict
- schedule_last_etag, schedule_cache

# Size: ~150 lines
```

### 2. **models/cache.py** - Caching System
```python
# Contains:
- SmartCache class (280+ lines)
- CROSS_CLUB_CACHE dict
- update_cross_club_cache()
- get_cross_club_data()

# Exports:
- smart_cache instance (initialized in __init__)
- SmartCache class

# Size: ~300 lines
```

### 3. **models/database.py** - Database Managers
```python
# Contains:
- SupabaseManager (imported from supabase_manager)
- GoogleSheetsManager class (600+ lines)
- Global: supabase_db, USE_SUPABASE, gs_manager

# Size: ~700 lines
```

### 4. **models/proxy.py** - Proxy Management
```python
# Contains:
- ProxyManager class (70 lines)
- proxy_manager instance initialization
- PROXY_LIST_FILE constant

# Size: ~100 lines
```

### 5. **utils/formatting.py** - Text Formatting Utilities
```python
# Contains:
- format_fans()
- format_fans_full()
- format_fans_billion()
- calculate_daily_from_cumulative()
- center_text_exact()
- format_stat_line_compact()

# Size: ~70 lines
```

### 6. **utils/error_handling.py** - Error Management
```python
# Contains:
- Error logger setup
- log_error()
- is_retryable_error()

# Size: ~80 lines
```

### 7. **utils/member_lookup.py** - Member Search Functions
```python
# Contains:
- get_trainer_id_from_sheets()
- get_viewer_id_from_sheets()
- find_member_by_viewer_id_in_club()
- find_member_across_all_clubs()
- find_viewer_in_clubs_via_api()
- find_all_clubs_for_viewer()

# Size: ~300 lines
```

### 8. **utils/verification.py** - Profile Verification
```python
# Contains:
- call_ocr_service()
- load_profile_links()
- save_profile_link()
- PROFILE_LINKS_FILE constant
- EXAMPLE_PROFILE_IMAGE constant
- pending_verifications dict

# Size: ~80 lines
```

### 9. **utils/web_logging.py** - Web Dashboard Integration
```python
# Contains:
- send_log_to_web()
- sync_channels_to_web()
- sync_stats_to_web()

# Size: ~50 lines
```

### 10. **views/modals.py** - All Modal Classes
```python
# Contains:
- FilterModal
- OldClubModal
- CompetitiveClubSetupModal
- CasualClubSetupModal
- ClubQuotaFilterModal
- UserQuotaModal
- QuotaInputModal
- OldClubIDModal

# Size: ~700 lines
```

### 11. **views/stats_views.py** - Stats-Related Views
```python
# Contains:
- LeaderboardView
- StatsView (LARGE - 500+ lines)
- SearchClubView

# Size: ~800 lines
```

### 12. **views/club_views.py** - Club Management Views
```python
# Contains:
- ClubListView
- ClubTypeSelectionView
- ChannelListView
- ServerListView
- GlobalLeaderboardView
- OldClubPromptView

# Size: ~600 lines
```

### 13. **views/profile_views.py** - Profile Management Views
```python
# Contains:
- ProfileOwnershipView
- TransferWarningView
- TransferSubmitView

# Size: ~150 lines
```

### 14. **views/admin_views.py** - Admin Views
```python
# Contains:
- AdminApprovalView
- HelpView

# Size: ~250 lines
```

### 15. **commands/stats_commands.py** - Stats Commands
```python
# Contains:
- @app_commands: /stats, /profile, /leaderboard, /search_club
- Helper functions specific to these commands
- club_autocomplete()
- member_autocomplete()

# Size: ~2000+ lines (LARGE - may need further splitting)
```

### 16. **commands/club_commands.py** - Club Management Commands
```python
# Contains:
- @app_commands: /club_add, /club_edit, /club_search, /sync_club, /club_list
- Helper functions for club operations

# Size: ~700 lines
```

### 17. **commands/admin_commands.py** - Admin Commands
```python
# Contains:
- @app_commands: /set_channel, /cache_stats, /tournament_setup
- Helper functions for admin operations

# Size: ~300 lines
```

### 18. **commands/system_commands.py** - System Commands
```python
# Contains:
- @app_commands: /status, /uptime, /help, /schedule
- System health check helpers

# Size: ~200 lines
```

### 19. **tasks/daily_sync.py** - Daily Synchronization Task
```python
# Contains:
- @tasks.loop(time=dt_time(...)) - 23:00 UTC sync
- All daily sync logic
- update_cross_club_cache() calls
- Data persistence logic

# Size: ~250+ lines
```

### 20. **tasks/schedule_updates.py** - Schedule Fetching Task
```python
# Contains:
- @tasks.loop(hours=6) - Schedule fetch
- Schedule download & caching logic
- load_schedule_config()
- save_schedule_channel()

# Size: ~150 lines
```

### 21. **tasks/verification_cleanup.py** - Pending Verification Cleanup
```python
# Contains:
- @tasks.loop(minutes=1) - Cleanup expired verifications
- pending_verifications cleanup logic

# Size: ~50 lines
```

### 22. **managers/profile_manager.py** - Profile System Management
```python
# Contains:
- add_support_footer()
- maybe_send_promo_message()
- OCR_SERVICE_URL constant
- PROFILE_LINKS_FILE constant

# Size: ~100 lines
```

### 23. **managers/schedule_manager.py** - Schedule System Management
```python
# Contains:
- SCHEDULE_URL, SCHEDULE_COLORS
- Schedule-related utilities
- fetch & notify logic

# Size: ~100 lines
```

### 24. **bot-github.py** (MAIN FILE - Simplified)
```python
# Contains ONLY:
- imports from all modules
- Client class initialization (ClubManagementBot)
- Setup and startup logic
- client.run() call
- Global client instance

# New size: ~200 lines (vs 12,218 currently!)
```

---

## Implementation Roadmap

### Phase 1: Setup Foundation ✅
- [ ] Create folder structure (models/, managers/, commands/, tasks/, views/, utils/, data/)
- [ ] Create __init__.py files in each folder
- [ ] Create empty placeholder modules

### Phase 2: Extract Non-Command Code
- [ ] Extract config.py
- [ ] Extract models/ (cache, database, proxy)
- [ ] Extract utils/ (formatting, error_handling, member_lookup, verification, web_logging)
- [ ] Extract managers/ (profile_manager, schedule_manager)

### Phase 3: Extract Views
- [ ] Extract views/modals.py
- [ ] Extract views/stats_views.py
- [ ] Extract views/club_views.py
- [ ] Extract views/profile_views.py
- [ ] Extract views/admin_views.py

### Phase 4: Extract Commands
- [ ] Extract commands/stats_commands.py
- [ ] Extract commands/club_commands.py
- [ ] Extract commands/admin_commands.py
- [ ] Extract commands/system_commands.py

### Phase 5: Extract Tasks
- [ ] Extract tasks/daily_sync.py
- [ ] Extract tasks/schedule_updates.py
- [ ] Extract tasks/verification_cleanup.py

### Phase 6: Cleanup Main File
- [ ] Simplify bot-github.py (imports + main client class)
- [ ] Test all functionality
- [ ] Update imports if needed

### Phase 7: Documentation
- [ ] Add module docstrings
- [ ] Create module relationship diagram
- [ ] Add usage examples to each module

---

## Benefits After Modularization

✅ **Code Organization**
- Each module has single responsibility
- Clear folder structure
- Easy to locate specific features

✅ **Maintainability**
- Smaller files = easier to understand
- Reduced cognitive load
- Easier code reviews

✅ **Testing**
- Can unit test individual modules
- Mock dependencies easily
- Faster test execution

✅ **Development Speed**
- New features = new module
- Less merge conflicts
- Parallel development possible

✅ **Performance**
- Faster IDE response
- Better autocomplete
- Quicker startup time

✅ **Scalability**
- Easy to add new commands
- Easy to extend existing features
- Reusable components

---

## Migration Strategy

### Step-by-Step Process:

1. **Create the folder structure** without changing any existing code
2. **Move files one module at a time** starting with utilities
3. **Test after each module move** to ensure nothing broke
4. **Update imports** in bot-github.py as modules are created
5. **Keep bot-github.py functional** at each step

### Example Migration - First Step:

```python
# Extract config.py
# In config.py:
from dotenv import load_dotenv
import os
# ... move all config code here

# In bot-github.py:
from config import config, BotConfig, parse_int_list, SCRIPT_DIR, ...
# Remove old config code
```

---

## File Size Estimates After Split

| Module | Est. Lines | Complexity |
|--------|-----------|-----------|
| bot-github.py | 200 | Low |
| config.py | 150 | Low |
| models/cache.py | 300 | Medium |
| models/database.py | 700 | High |
| models/proxy.py | 100 | Low |
| utils/formatting.py | 70 | Low |
| utils/error_handling.py | 80 | Low |
| utils/member_lookup.py | 300 | High |
| utils/verification.py | 80 | Low |
| utils/web_logging.py | 50 | Low |
| views/modals.py | 700 | Medium |
| views/stats_views.py | 800 | High |
| views/club_views.py | 600 | Medium |
| views/profile_views.py | 150 | Low |
| views/admin_views.py | 250 | Low |
| commands/stats_commands.py | 2000+ | Very High |
| commands/club_commands.py | 700 | Medium |
| commands/admin_commands.py | 300 | Low |
| commands/system_commands.py | 200 | Low |
| tasks/daily_sync.py | 250+ | High |
| tasks/schedule_updates.py | 150 | Low |
| tasks/verification_cleanup.py | 50 | Low |
| managers/profile_manager.py | 100 | Low |
| managers/schedule_manager.py | 100 | Low |
| **TOTAL** | **~8,400** | - |

---

## Key Imports Structure (in bot-github.py)

```python
# Models
from models.cache import smart_cache, SmartCache, CROSS_CLUB_CACHE
from models.database import gs_manager, supabase_db, USE_SUPABASE
from models.proxy import proxy_manager

# Utils
from utils.formatting import format_fans, center_text_exact
from utils.error_handling import log_error
from utils.member_lookup import find_viewer_in_clubs_via_api
from utils.verification import call_ocr_service
from utils.web_logging import send_log_to_web

# Managers
from managers.profile_manager import maybe_send_promo_message
from managers.schedule_manager import SCHEDULE_COLORS

# Views
from views.modals import FilterModal, OldClubModal
from views.stats_views import StatsView, LeaderboardView
from views.club_views import ClubListView
from views.profile_views import ProfileOwnershipView
from views.admin_views import AdminApprovalView

# Commands
from commands.stats_commands import setup_stats_commands
from commands.club_commands import setup_club_commands
from commands.admin_commands import setup_admin_commands
from commands.system_commands import setup_system_commands

# Tasks
from tasks.daily_sync import setup_daily_sync
from tasks.schedule_updates import setup_schedule_updates
```

---

## Notes for Implementation

1. **Circular Import Prevention**: Structure dependencies carefully (utils don't import commands, etc.)
2. **Client Instance**: Pass `client` to modules that need it (in __init__ or as parameter)
3. **Global State**: Keep minimal in bot-github.py, move to appropriate module
4. **Testing**: Create test_*.py files in each module folder after extraction
5. **Documentation**: Add docstrings and type hints as you extract each module

---

**Recommendation**: Start with Phase 1-2 immediately. The utilities are low-risk to extract and will give immediate benefits.
