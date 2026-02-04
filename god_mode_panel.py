"""
God Mode Control Panel
Persistent message with buttons for bot owner commands
"""

import discord
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
import json
import os
import sys
import time
import subprocess
import datetime
import pytz
import importlib.util
from dotenv import load_dotenv

# Helper to import bot-hosting.py (hyphen in name requires special import)
_bot_hosting_module = None

def _get_bot_module():
    """Get the bot-hosting module (with caching)"""
    global _bot_hosting_module
    if _bot_hosting_module is None:
        bot_file = os.path.join(os.path.dirname(__file__), "bot-hosting.py")
        spec = importlib.util.spec_from_file_location("bot_hosting", bot_file)
        _bot_hosting_module = importlib.util.module_from_spec(spec)
        # Don't execute the module - just use it to access already-loaded objects
        # Since god_mode_panel is imported by bot-hosting, we can use sys.modules
        import sys
        if 'bot_hosting' in sys.modules:
            _bot_hosting_module = sys.modules['bot_hosting']
        elif '__main__' in sys.modules:
            # If running as main, use __main__
            _bot_hosting_module = sys.modules['__main__']
    return _bot_hosting_module

# Load environment variables from .env file
load_dotenv()
# Control Panel Channel - loaded from environment variables
GOD_MODE_PANEL_CHANNEL_ID = int(os.getenv('GOD_MODE_PANEL_CHANNEL_ID', '0'))

# God Mode User ID - loaded from environment variables
GOD_MODE_USER_ID = int(os.getenv('GOD_MODE_USER_ID', '0'))
GOD_MODE_USER_IDS = [GOD_MODE_USER_ID] if GOD_MODE_USER_ID else []

# Lockdown state file
LOCKDOWN_FILE_PATH = os.path.join(os.path.dirname(__file__), "lockdown_state.json")

def is_lockdown_active() -> bool:
    """Check if bot is in lockdown mode"""
    try:
        if os.path.exists(LOCKDOWN_FILE_PATH):
            with open(LOCKDOWN_FILE_PATH, 'r') as f:
                data = json.load(f)
                return data.get('active', False)
    except:
        pass
    return False

def set_lockdown_state(active: bool, reason: str = None) -> dict:
    """Set lockdown state"""
    state = {
        'active': active,
        'reason': reason or ("Maintenance in progress" if active else None),
        'updated_at': datetime.datetime.now().isoformat(),
        'updated_by': GOD_MODE_USER_ID
    }
    with open(LOCKDOWN_FILE_PATH, 'w') as f:
        json.dump(state, f, indent=2)
    return state

def get_lockdown_state() -> dict:
    """Get current lockdown state"""
    try:
        if os.path.exists(LOCKDOWN_FILE_PATH):
            with open(LOCKDOWN_FILE_PATH, 'r') as f:
                return json.load(f)
    except:
        pass
    return {'active': False, 'reason': None}


class GlobalAnnouncementModal(Modal):
    """Modal for composing global announcement message"""
    
    def __init__(self):
        super().__init__(title="📢 Global Announcement")
        
        self.title_input = TextInput(
            label="Title",
            placeholder="e.g., Bot Update, Maintenance Notice",
            required=True,
            max_length=100,
            style=discord.TextStyle.short
        )
        
        self.message_input = TextInput(
            label="Message",
            placeholder="Enter your announcement message here...",
            required=True,
            max_length=2000,
            style=discord.TextStyle.paragraph
        )
        
        self.add_item(self.title_input)
        self.add_item(self.message_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        """Send announcement to allowed channels only"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Use interaction.client to get the actual running bot instance
            bot_client = interaction.client
            
            title = self.title_input.value
            message = self.message_input.value
            
            # Create announcement embed
            embed = discord.Embed(
                title=f"📢 {title}",
                description=message,
                color=discord.Color.gold(),
                timestamp=datetime.datetime.now()
            )
            embed.set_footer(text="Fan-Count Bot Announcement")
            
            # Load allowed channels from config file
            allowed_channels_file = os.path.join(os.path.dirname(__file__), "allowed_channels_config.json")
            allowed_channels = []
            
            try:
                if os.path.exists(allowed_channels_file):
                    with open(allowed_channels_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    allowed_channels = data.get('channels', [])
            except Exception as e:
                print(f"⚠️ Error loading channels config: {e}")
            
            if not allowed_channels:
                await interaction.followup.send(
                    "⚠️ **No channels configured!**\n\n"
                    "Use `/set_channel` to add channels first.",
                    ephemeral=True
                )
                return
            
            # Track results
            success_count = 0
            failed_count = 0
            failed_channels = []
            
            # Send to allowed channels only
            for ch_data in allowed_channels:
                channel_id = ch_data.get('channel_id')
                channel_name = ch_data.get('channel_name', 'Unknown')
                server_name = ch_data.get('server_name', 'Unknown')
                
                try:
                    channel = bot_client.get_channel(channel_id)
                    
                    if channel and channel.permissions_for(channel.guild.me).send_messages:
                        await channel.send(embed=embed)
                        success_count += 1
                    else:
                        failed_count += 1
                        failed_channels.append(f"#{channel_name} ({server_name})")
                        
                except discord.Forbidden:
                    failed_count += 1
                    failed_channels.append(f"#{channel_name} (forbidden)")
                except Exception as e:
                    failed_count += 1
                    failed_channels.append(f"#{channel_name} ({str(e)[:20]})")
            
            # Report results
            result_message = (
                f"✅ **Announcement sent!**\n\n"
                f"**Successful:** {success_count} channels\n"
                f"**Failed:** {failed_count} channels"
            )
            
            if failed_channels and len(failed_channels) <= 10:
                result_message += f"\n\n**Failed channels:**\n" + "\n".join(f"• {c}" for c in failed_channels)
            elif failed_channels:
                result_message += f"\n\n**Failed channels:** {len(failed_channels)} (too many to list)"
                
            await interaction.followup.send(result_message, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


class LockdownReasonModal(Modal):
    """Modal for entering lockdown reason"""
    
    def __init__(self):
        super().__init__(title="🔒 Enable Lockdown")
        
        self.reason_input = TextInput(
            label="Reason (optional)",
            placeholder="e.g., Bot update in progress, Maintenance",
            required=False,
            max_length=200,
            style=discord.TextStyle.short,
            default="Maintenance in progress"
        )
        
        self.add_item(self.reason_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        """Enable lockdown with reason"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            reason = self.reason_input.value or "Maintenance in progress"
            state = set_lockdown_state(True, reason)
            
            await interaction.followup.send(
                f"🔒 **Lockdown ENABLED**\n\n"
                f"**Reason:** {reason}\n\n"
                f"All commands are now restricted. Only God Mode users can use the bot.\n"
                f"Use the **Unlock** button to disable lockdown.",
                ephemeral=True
            )
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


class GodModeControlPanel(View):
    """Persistent control panel with God Mode buttons"""
    
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view
    
    def is_god_mode(self, interaction: discord.Interaction) -> bool:
        """Check if user is God mode"""
        return interaction.user.id in GOD_MODE_USER_IDS
    
    # Row 1: Cache Management
    @discord.ui.button(
        label="📊 Cache Stats",
        style=discord.ButtonStyle.secondary,
        custom_id="gm_cache_stats",
        row=0
    )
    async def cache_stats(self, interaction: discord.Interaction, button: Button):
        """Show cache statistics"""
        if not self.is_god_mode(interaction):
            await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            bot_module = _get_bot_module()
            smart_cache = bot_module.smart_cache
            client = bot_module.client
            stats = smart_cache.get_stats()
            
            embed = discord.Embed(
                title="📊 Cache Statistics",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="Smart Cache (In-Memory)",
                value=(
                    f"**Entries:** {stats['total_entries']}\n"
                    f"**Size:** {stats['total_size_mb']} MB"
                ),
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
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    @discord.ui.button(
        label="🗑️ Clear Cache",
        style=discord.ButtonStyle.danger,
        custom_id="gm_clear_cache",
        row=0
    )
    async def clear_cache(self, interaction: discord.Interaction, button: Button):
        """Clear all cache"""
        if not self.is_god_mode(interaction):
            await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            bot_module = _get_bot_module()
            smart_cache = bot_module.smart_cache
            before_stats = smart_cache.get_stats()
            total_entries = before_stats['total_entries']
            total_size_mb = before_stats['total_size_mb']
            
            smart_cache.invalidate()
            
            await interaction.followup.send(
                f"✅ **Cache cleared!**\n\n"
                f"**Cleared:**\n"
                f"• {total_entries} entries\n"
                f"• {total_size_mb} MB",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    # Row 2: Bot Control
    @discord.ui.button(
        label="🔄 Restart Bot",
        style=discord.ButtonStyle.danger,
        custom_id="gm_restart",
        row=1
    )
    async def restart_bot(self, interaction: discord.Interaction, button: Button):
        """Restart the bot"""
        if not self.is_god_mode(interaction):
            await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
            return
        
        await interaction.response.send_message("🔄 Restarting bot...", ephemeral=False)
        message = await interaction.original_response()
        
        print(f"--- RESTARTING (from control panel by {interaction.user.id}) ---")
        
        # Save restart info
        try:
            bot_module = _get_bot_module()
            RESTART_FILE_PATH = bot_module.RESTART_FILE_PATH
            restart_data = {
                "channel_id": interaction.channel_id,
                "message_id": message.id,
                "start_time": time.time()
            }
            with open(RESTART_FILE_PATH, "w") as f:
                json.dump(restart_data, f)
        except:
            pass
        
        # Restart
        try:
            os.execv(sys.executable, ['python'] + sys.argv)
        except Exception as e:
            print(f"Restart failed: {e}")
            await message.edit(content="❌ Restart failed!")
    
    # Row 2: Task Triggers
    @discord.ui.button(
        label="🔄 Refresh Cache",
        style=discord.ButtonStyle.primary,
        custom_id="gm_refresh_cache",
        row=1
    )
    async def refresh_cache(self, interaction: discord.Interaction, button: Button):
        """Refresh bot cache from Google Sheets"""
        if not self.is_god_mode(interaction):
            await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            bot_module = _get_bot_module()
            client = bot_module.client
            
            # Reset cooldown to force refresh
            client.last_cache_update_time = 0
            
            start_time = time.time()
            await client.update_caches()
            elapsed = time.time() - start_time
            
            await interaction.followup.send(
                f"✅ **Cache refreshed!**\n\n"
                f"**Loaded:**\n"
                f"• {len(client.config_cache)} clubs\n"
                f"• {sum(len(m) for m in client.member_cache.values())} members\n"
                f"⏱️ Time: {elapsed:.1f}s",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    @discord.ui.button(
        label="📡 Sync Data",
        style=discord.ButtonStyle.success,
        custom_id="gm_sync_data",
        row=1
    )
    async def sync_data(self, interaction: discord.Interaction, button: Button):
        """Manually trigger full data sync: ranks + member data from uma.moe API"""
        if not self.is_god_mode(interaction):
            await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
            return
        
        # Defer immediately to prevent timeout (interaction lasts only 3s)
        await interaction.response.defer(ephemeral=True)
        
        # Send initial message via followup (this won't expire)
        status_msg = await interaction.followup.send(
            "📡 **Starting full data sync...**\n"
            "• Fetching ranks from uma.moe API\n"
            "• Syncing member daily data to Google Sheets\n"
            "• Applying Yui logic for late joiners\n\n"
            "This may take a while...", 
            ephemeral=True,
            wait=True
        )
        
        try:
            bot_module = _get_bot_module()
            update_club_data_task = bot_module.update_club_data_task
            
            # Run the merged task (updates ranks + syncs member data)
            await update_club_data_task.coro()
            
            # Edit the status message (followup messages don't expire)
            await status_msg.edit(
                content=(
                    "✅ **Full data sync complete!**\n\n"
                    "• Ranks updated in Clubs_Config\n"
                    "• Member data synced to each club's sheet\n"
                    "• Check console for details"
                )
            )
        except Exception as e:
            await status_msg.edit(content=f"❌ Error: {e}")
    
    @discord.ui.button(
        label="📤 Sync to Supabase",
        style=discord.ButtonStyle.primary,
        custom_id="gm_sync_supabase",
        row=1
    )
    async def sync_supabase(self, interaction: discord.Interaction, button: Button):
        """Manually trigger sync to Supabase (deprecated - no longer used)"""
        if not self.is_god_mode(interaction):
            await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
            return
        
        # Defer immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        try:
            bot_module = _get_bot_module()
            auto_refresh_data_cache = bot_module.auto_refresh_data_cache
            
            # Run cache refresh instead (Supabase sync was removed)
            status_msg = await interaction.followup.send(
                "📤 **Starting data refresh...**\nThis may take a while.", 
                ephemeral=True,
                wait=True
            )
            
            await auto_refresh_data_cache.coro()
            
            await status_msg.edit(
                content="✅ **Data refresh complete!**\nCheck console for details."
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    # Row 3: Channel Management
    @discord.ui.button(
        label="🚫 Clear Channels",
        style=discord.ButtonStyle.danger,
        custom_id="gm_clear_channels",
        row=2
    )
    async def clear_channels(self, interaction: discord.Interaction, button: Button):
        """Clear all channel restrictions - with backup"""
        if not self.is_god_mode(interaction):
            await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            bot_module = _get_bot_module()
            ALLOWED_CHANNELS_CONFIG_FILE = bot_module.ALLOWED_CHANNELS_CONFIG_FILE
            config = bot_module.config
            
            if not os.path.exists(ALLOWED_CHANNELS_CONFIG_FILE):
                await interaction.followup.send("ℹ️ No channel config file exists.", ephemeral=True)
                return
            
            # Load current data for backup and count
            with open(ALLOWED_CHANNELS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                current_data = json.load(f)
            
            channel_count = len(current_data.get('channels', []))
            
            if channel_count == 0:
                await interaction.followup.send("ℹ️ No channels to clear.", ephemeral=True)
                return
            
            # Create backup before clearing
            backup_dir = os.path.join(os.path.dirname(__file__), "backups")
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = os.path.join(backup_dir, f"allowed_channels_backup_{timestamp}.json")
            
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(current_data, f, indent=2, ensure_ascii=False)
            
            # Now clear the channels
            os.remove(ALLOWED_CHANNELS_CONFIG_FILE)
            config.ALLOWED_CHANNEL_IDS = []
            
            await interaction.followup.send(
                f"✅ **All channel restrictions cleared**\n\n"
                f"🗑️ Removed: **{channel_count}** channels\n"
                f"💾 Backup saved: `backups/{os.path.basename(backup_file)}`\n\n"
                f"Bot can now be used in all channels.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    # Row 3: Global Controls
    @discord.ui.button(
        label="📢 Announcement",
        style=discord.ButtonStyle.primary,
        custom_id="gm_announcement",
        row=2
    )
    async def global_announcement(self, interaction: discord.Interaction, button: Button):
        """Send announcement to all servers"""
        if not self.is_god_mode(interaction):
            await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
            return
        
        # Show modal
        modal = GlobalAnnouncementModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(
        label="🔒 Lock Down",
        style=discord.ButtonStyle.danger,
        custom_id="gm_lockdown",
        row=2
    )
    async def lockdown(self, interaction: discord.Interaction, button: Button):
        """Enable lockdown mode"""
        if not self.is_god_mode(interaction):
            await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
            return
        
        # Check if already locked
        if is_lockdown_active():
            state = get_lockdown_state()
            await interaction.response.send_message(
                f"⚠️ **Already in lockdown mode**\n\n"
                f"**Reason:** {state.get('reason', 'N/A')}\n"
                f"**Since:** {state.get('updated_at', 'N/A')}\n\n"
                f"Use **Unlock** button to disable lockdown.",
                ephemeral=True
            )
            return
        
        # Show modal for reason
        modal = LockdownReasonModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(
        label="🔓 Unlock",
        style=discord.ButtonStyle.success,
        custom_id="gm_unlock",
        row=2
    )
    async def unlock(self, interaction: discord.Interaction, button: Button):
        """Disable lockdown mode"""
        if not self.is_god_mode(interaction):
            await interaction.response.send_message("❌ Unauthorized", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            if not is_lockdown_active():
                await interaction.followup.send(
                    "ℹ️ **Lockdown is not active**\n\n"
                    "Bot is already operating normally.",
                    ephemeral=True
                )
                return
            
            state = set_lockdown_state(False)
            
            await interaction.followup.send(
                "🔓 **Lockdown DISABLED**\n\n"
                "Bot is now operating normally. All commands are available.",
                ephemeral=True
            )
            
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


def create_control_panel_embed():
    """Create embed for control panel"""
    # Check lockdown status
    lockdown_status = "🔴 **LOCKDOWN ACTIVE**" if is_lockdown_active() else "🟢 Normal Operation"
    if is_lockdown_active():
        state = get_lockdown_state()
        lockdown_status += f"\n└ Reason: {state.get('reason', 'N/A')}"
    
    embed = discord.Embed(
        title="⚡ GOD MODE CONTROL PANEL",
        description=(
            "**System Controls**\n"
            "Essential bot management functions\n\n"
            f"🔐 **Access:** <@{GOD_MODE_USER_ID}> only\n"
            f"📡 **Status:** {lockdown_status}"
        ),
        color=0xFF0000,
        timestamp=datetime.datetime.now()
    )
    
    embed.add_field(
        name="📊 Row 1: Cache Management",
        value="• **Cache Stats** - View cache info\n• **Clear Cache** - Clear all cached data",
        inline=False
    )
    
    embed.add_field(
        name="🔄 Row 2: Task Triggers",
        value=(
            "• **Restart Bot** - Restart the bot\n"
            "• **Refresh Cache** - Reload from Google Sheets\n"
            "• **Sync Data** - Full sync: ranks + member data from uma.moe\n"
            "• **Sync to Supabase** - Sync data to Supabase"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🌐 Row 3: Global Controls",
        value=(
            "• **Clear Channels** - Remove all channel restrictions\n"
            "• **Announcement** - Send message to ALL servers\n"
            "• **Lock Down** - Restrict bot (maintenance mode)\n"
            "• **Unlock** - Disable lockdown"
        ),
        inline=False
    )
    
    embed.set_footer(text="Control Panel v2.4 - With Global Announcement & Lockdown")
    
    return embed


async def update_god_mode_panel(client):
    """Update or create God Mode control panel"""
    try:
        channel = client.get_channel(GOD_MODE_PANEL_CHANNEL_ID)
        if not channel:
            print(f"⚠️ Cannot find God Mode panel channel: {GOD_MODE_PANEL_CHANNEL_ID}")
            return
        
        embed = create_control_panel_embed()
        view = GodModeControlPanel()
        
        # Try to load existing message ID
        config_file = os.path.join(os.path.dirname(__file__), "god_mode_panel_config.json")
        message_id = None
        
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    data = json.load(f)
                    message_id = data.get('message_id')
            except:
                pass
        
        # Try to edit existing message
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed, view=view)
                print("✅ Updated God Mode control panel")
                return
            except:
                pass
        
        # Create new message
        message = await channel.send(embed=embed, view=view)
        
        # Save message ID
        with open(config_file, 'w') as f:
            json.dump({
                'message_id': message.id,
                'channel_id': channel.id,
                'created_at': datetime.datetime.now().isoformat()
            }, f)
        
        print(f"✅ Created God Mode control panel (Message ID: {message.id})")
        
    except Exception as e:
        print(f"❌ Error updating God Mode panel: {e}")
        import traceback
        traceback.print_exc()
