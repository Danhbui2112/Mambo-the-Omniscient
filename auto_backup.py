"""
Auto-Backup System for Critical Config Files
Creates timestamped backup folders and keeps maximum 5 backups
"""

import os
import json
import shutil
from datetime import datetime
from typing import List, Optional

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
BACKUP_DIR = os.path.join(SCRIPT_DIR, "backups")

# Files to backup automatically
CRITICAL_FILES = [
    # Channel & Server configs
    "allowed_channels_config.json",
    "server_list_config.json",
    "channel_list_config.json",
    "channel_change_log.json",
    
    # User & Profile data
    "profile_data.json",
    "admin_list.json",
    
    # Club & Role configs
    "club_roles.json",
    "global_leaderboard_config.json",
    "server_invites.json",
    
    # System state
    "god_mode_panel_config.json",
    "lockdown_state.json",
    
    # Transfer tracking
    "transfer_requests.json",
]

# Maximum number of backup folders to keep
MAX_BACKUPS = 5


def ensure_backup_dir():
    """Create backup directory if it doesn't exist"""
    os.makedirs(BACKUP_DIR, exist_ok=True)


def get_backup_folders() -> List[str]:
    """Get list of backup folders sorted by date (newest first)"""
    ensure_backup_dir()
    
    folders = []
    for item in os.listdir(BACKUP_DIR):
        item_path = os.path.join(BACKUP_DIR, item)
        if os.path.isdir(item_path) and item.startswith("backup_"):
            folders.append(item)
    
    # Sort by name (which contains timestamp) - newest first
    folders.sort(reverse=True)
    return folders


def cleanup_old_backups():
    """Remove oldest backups if we have more than MAX_BACKUPS"""
    folders = get_backup_folders()
    
    if len(folders) <= MAX_BACKUPS:
        return 0
    
    deleted = 0
    for old_folder in folders[MAX_BACKUPS:]:
        folder_path = os.path.join(BACKUP_DIR, old_folder)
        try:
            shutil.rmtree(folder_path)
            print(f"🗑️ Removed old backup: {old_folder}")
            deleted += 1
        except Exception as e:
            print(f"❌ Failed to remove {old_folder}: {e}")
    
    return deleted


def backup_all_critical_files(reason: str = "scheduled") -> dict:
    """
    Backup all critical config files to a timestamped folder.
    Keeps maximum MAX_BACKUPS folders.
    """
    ensure_backup_dir()
    
    # Create timestamped folder: backup_YYYYMMDD_HHMMSS
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    folder_name = f"backup_{timestamp}"
    backup_folder = os.path.join(BACKUP_DIR, folder_name)
    
    os.makedirs(backup_folder, exist_ok=True)
    
    results = {
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "folder": folder_name,
        "files": []
    }
    
    for filename in CRITICAL_FILES:
        source_path = os.path.join(SCRIPT_DIR, filename)
        
        if not os.path.exists(source_path):
            results["skipped"] += 1
            continue
        
        dest_path = os.path.join(backup_folder, filename)
        
        try:
            shutil.copy2(source_path, dest_path)
            results["success"] += 1
            results["files"].append(filename)
            print(f"💾 Backed up: {filename}")
        except Exception as e:
            results["failed"] += 1
            print(f"❌ Backup failed for {filename}: {e}")
    
    # Log the backup
    log_backup(folder_name, reason, results)
    
    # Cleanup old backups
    deleted = cleanup_old_backups()
    results["deleted_old"] = deleted
    
    return results


def list_backups() -> List[dict]:
    """List all available backup folders with details"""
    folders = get_backup_folders()
    
    backups = []
    for folder in folders:
        folder_path = os.path.join(BACKUP_DIR, folder)
        
        # Count files
        files = [f for f in os.listdir(folder_path) if f.endswith('.json') and f != 'backup_info.json']
        
        # Get folder creation time
        stat = os.stat(folder_path)
        
        # Parse timestamp from folder name: backup_YYYYMMDD_HHMMSS
        try:
            ts_str = folder.replace("backup_", "")
            dt = datetime.strptime(ts_str, '%Y%m%d_%H%M%S')
            created = dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            created = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        
        backups.append({
            "folder": folder,
            "path": folder_path,
            "files": files,
            "file_count": len(files),
            "created": created
        })
    
    return backups


def restore_backup(folder_name: str, files: List[str] = None) -> dict:
    """
    Restore files from a backup folder.
    If files not specified, restores all files in the backup.
    """
    folder_path = os.path.join(BACKUP_DIR, folder_name)
    
    if not os.path.exists(folder_path):
        return {"success": False, "error": f"Backup folder not found: {folder_name}"}
    
    results = {
        "success": True,
        "restored": 0,
        "failed": 0,
        "files": []
    }
    
    # Get files to restore
    if files is None:
        files = [f for f in os.listdir(folder_path) if f.endswith('.json') and f != 'backup_info.json']
    
    for filename in files:
        source = os.path.join(folder_path, filename)
        dest = os.path.join(SCRIPT_DIR, filename)
        
        if not os.path.exists(source):
            results["failed"] += 1
            continue
        
        try:
            shutil.copy2(source, dest)
            results["restored"] += 1
            results["files"].append(filename)
            print(f"✅ Restored: {filename}")
        except Exception as e:
            results["failed"] += 1
            print(f"❌ Failed to restore {filename}: {e}")
    
    return results


def log_backup(folder_name: str, reason: str, results: dict):
    """Save backup info to the backup folder"""
    folder_path = os.path.join(BACKUP_DIR, folder_name)
    info_file = os.path.join(folder_path, "backup_info.json")
    
    info = {
        "folder": folder_name,
        "reason": reason,
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "files_backed_up": results.get("files", []),
        "success": results.get("success", 0),
        "failed": results.get("failed", 0),
        "skipped": results.get("skipped", 0)
    }
    
    try:
        with open(info_file, 'w', encoding='utf-8') as f:
            json.dump(info, f, indent=2, ensure_ascii=False)
    except:
        pass


# Quick access functions
def daily_backup():
    """Run daily backup routine"""
    print("📦 Starting daily backup...")
    results = backup_all_critical_files("daily")
    print(f"📦 Backup complete: {results['success']} files → {results['folder']}")
    if results.get('deleted_old', 0) > 0:
        print(f"🗑️ Removed {results['deleted_old']} old backup(s)")
    return results


def manual_backup():
    """Run manual backup"""
    return backup_all_critical_files("manual")


def pre_destructive_backup(filename: str) -> Optional[str]:
    """Backup before a destructive operation - backs up single file"""
    source_path = os.path.join(SCRIPT_DIR, filename)
    
    if not os.path.exists(source_path):
        return None
    
    ensure_backup_dir()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f"{os.path.splitext(filename)[0]}_pre_delete_{timestamp}.json"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    
    try:
        shutil.copy2(source_path, backup_path)
        print(f"💾 Pre-destructive backup: {backup_name}")
        return backup_path
    except:
        return None
