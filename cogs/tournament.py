"""
Tournament Module - Views and Helpers for Tournament 1v1v1
Compatible with discord.Client (no commands.Bot required)
"""
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Select, Modal, TextInput
from typing import Optional, List
import asyncio

from tournament_manager import (
    Tournament, Match, TournamentPlayer,
    get_active_tournament, set_active_tournament, create_tournament,
    load_active_tournaments
)
from uma_data import fetch_uma_list, search_uma, validate_uma_names, get_uma_names


# ============================================================================
# MODULE-LEVEL HELPER FUNCTIONS
# ============================================================================

def create_registration_embed(tournament: Tournament) -> discord.Embed:
    """Create simple, clean embed for registration message"""
    
    embed = discord.Embed(
        title=f"🏆 {tournament.name}",
        color=0xFFD700  # Gold color
    )
    
    # Build description
    desc_lines = [f"**Format:** 1v1v1 × BO3"]
    
    # Only show prize pool if set
    if tournament.prize_pool:
        desc_lines.append(f"**Prize Pool:** {tournament.prize_pool}")
    
    desc_lines.append(f"**Registrants:** {len(tournament.players)} / {tournament.max_players}")
    desc_lines.append(f"**Status:** {'🟢 Open' if len(tournament.players) < tournament.max_players else '🔴 Full'}")
    
    embed.description = "\n".join(desc_lines)
    embed.set_footer(text="Click button to join/leave")
    
    return embed


import re

def parse_uma_names(message: str) -> List[str]:
    """
    Parse uma names from message with flexible delimiters.
    Supports: / - , + and (case insensitive)
    """
    # Split by delimiters: / - , + and
    delimiters = r'[/\-,+]|\band\b'
    names = re.split(delimiters, message, flags=re.IGNORECASE)
    
    # Clean up names
    cleaned = []
    for name in names:
        name = name.strip()
        if name:
            cleaned.append(name)
    
    return cleaned


async def process_match_message(message: discord.Message, client) -> bool:
    """
    Process messages in forum match posts for ban/pick flow.
    Returns True if message was processed.
    """
    # Skip bot messages
    if message.author.bot:
        return False
    
    # Check if in a thread (forum post)
    if not isinstance(message.channel, discord.Thread):
        return False
    
    # Get tournament
    tournament = get_active_tournament(message.guild.id)
    if not tournament:
        return False
    
    # Find match by thread ID
    match = None
    for m in tournament.matches:
        if m.thread_id == message.channel.id:
            match = m
            break
    
    if not match:
        return False
    
    # Handle based on match status
    if match.status == "banning":
        return await handle_ban_message(message, match, tournament)
    elif match.status == "picking":
        return await handle_pick_message(message, match, tournament)
    
    return False


async def handle_ban_message(message: discord.Message, match: Match, tournament: Tournament) -> bool:
    """Handle ban phase messages"""
    current_player = match.get_current_ban_player()
    
    # Check if it's this player's turn
    if message.author.id != current_player:
        return False
    
    # Parse uma names
    uma_names = parse_uma_names(message.content)
    
    if len(uma_names) < tournament.bans_per_player:
        await message.reply(
            f"❌ Bạn cần ban {tournament.bans_per_player} uma, mới nhận được {len(uma_names)}!",
            mention_author=True
        )
        return True
    
    # Take only required number
    uma_names = uma_names[:tournament.bans_per_player]
    
    # Validate uma names (optional - skip if uma_data not loaded)
    try:
        valid, invalid = validate_uma_names(uma_names)
        if invalid:
            await message.reply(
                f"⚠️ Một số tên không tìm thấy: {', '.join(invalid)}\n"
                f"Vẫn tiếp tục với: {', '.join(uma_names)}",
                mention_author=True
            )
    except:
        pass  # Skip validation if uma_data not available
    
    # Add bans
    match.add_bans(message.author.id, uma_names)
    tournament.save()
    
    # Confirm and tag next player
    next_player = match.get_current_ban_player()
    
    if next_player:
        await message.reply(
            f"✅ **{message.author.display_name}** đã ban: {', '.join(uma_names)}\n\n"
            f"<@{next_player}> hãy gửi {tournament.bans_per_player} uma để ban:"
        )
    else:
        # Ban phase complete - move to pick phase
        banned_summary = "\n".join([
            f"• **{tournament.players[pid].display_name}**: {', '.join(bans)}"
            for pid, bans in match.bans.items()
        ])
        
        # Sync bans to Google Sheets
        try:
            from tournament_sheets import get_tournament_sheets
            sheets = get_tournament_sheets()
            players = [{'id': pid, 'name': tournament.players[pid].display_name} for pid in match.players]
            sheets.sync_match_data(tournament.name, match.match_id, match.round_num, players, match.bans, {})
        except Exception as e:
            print(f"Sheets ban sync error: {e}")
        
        first_picker = match.get_current_pick_player()
        
        await message.channel.send(
            f"✅ **BAN PHASE COMPLETE!**\n\n"
            f"**🚫 Banned Uma:**\n{banned_summary}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **PICK PHASE**\n"
            f"Each player picks {tournament.picks_per_player} uma\n"
            f"(Không được chọn uma đã bị ban)\n\n"
            f"<@{first_picker}> hãy gửi {tournament.picks_per_player} uma để thi đấu:"
        )
    
    return True


async def handle_pick_message(message: discord.Message, match: Match, tournament: Tournament) -> bool:
    """Handle pick phase messages"""
    current_player = match.get_current_pick_player()
    
    # Check if it's this player's turn
    if message.author.id != current_player:
        return False
    
    # Parse uma names
    uma_names = parse_uma_names(message.content)
    
    if len(uma_names) < tournament.picks_per_player:
        await message.reply(
            f"❌ Bạn cần pick {tournament.picks_per_player} uma, mới nhận được {len(uma_names)}!",
            mention_author=True
        )
        return True
    
    # Take only required number
    uma_names = uma_names[:tournament.picks_per_player]
    
    # Check if any picked uma is banned
    all_banned = match.get_all_banned_uma()
    banned_picks = [name for name in uma_names if name.lower() in [b.lower() for b in all_banned]]
    
    if banned_picks:
        await message.reply(
            f"❌ Uma này đã bị ban: {', '.join(banned_picks)}\n"
            f"Hãy chọn lại!",
            mention_author=True
        )
        return True
    
    # Add picks
    match.add_picks(message.author.id, uma_names)
    tournament.save()
    
    # Confirm and tag next player
    next_player = match.get_current_pick_player()
    
    if next_player:
        await message.reply(
            f"✅ **{message.author.display_name}** đã pick: {', '.join(uma_names)}\n\n"
            f"<@{next_player}> hãy gửi {tournament.picks_per_player} uma để thi đấu:"
        )
    else:
        # Pick phase complete - ready to play
        picks_summary = "\n".join([
            f"• **{tournament.players[pid].display_name}**: {', '.join(picks)}"
            for pid, picks in match.picks.items()
        ])
        
        # Sync picks to Google Sheets
        try:
            from tournament_sheets import get_tournament_sheets
            sheets = get_tournament_sheets()
            players = [{'id': pid, 'name': tournament.players[pid].display_name} for pid in match.players]
            sheets.sync_match_data(tournament.name, match.match_id, match.round_num, players, match.bans, match.picks)
        except Exception as e:
            print(f"Sheets pick sync error: {e}")
        
        # Send summary with Result button
        from cogs.tournament import ResultSubmitView
        await message.channel.send(
            f"✅ **PICK PHASE COMPLETE!**\n\n"
            f"**🎮 Selected Uma:**\n{picks_summary}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏁 **READY TO RACE!**\n"
            f"Referee bấm nút bên dưới để nhập kết quả:",
            view=ResultSubmitView(match.match_id)
        )
    
    return True


async def create_match_posts(guild: discord.Guild, tournament: Tournament, matches: List[Match]):
    """Create forum posts for matches (only match players + referee can reply)"""
    
    # Get forum channel
    forum_channel = guild.get_channel(tournament.forum_channel_id)
    if not forum_channel:
        print(f"Forum channel {tournament.forum_channel_id} not found")
        return
    
    # Get announce channel for bracket
    announce_channel = guild.get_channel(tournament.public_channel_id)
    
    # Post bracket to announce channel
    if announce_channel:
        bracket_text = f"**🏆 {tournament.name} - BRACKET**\n\n"
        for match in matches:
            player_names = [tournament.players[pid].display_name for pid in match.players if pid in tournament.players]
            bracket_text += f"🎮 **{match.match_id}**: {' vs '.join(player_names)}\n"
        
        await announce_channel.send(bracket_text)
    
    # Get referee role for adding to match permissions
    referee_role = guild.get_role(tournament.referee_role_id) if tournament.referee_role_id else None
    
    # Create forum post for each match
    for match in matches:
        player_names = [tournament.players[pid].display_name for pid in match.players if pid in tournament.players]
        post_name = f"Match {match.match_id}: {' vs '.join(player_names[:3])}"
        
        # Create match role (temp - for reply permissions)
        role = await guild.create_role(
            name=f"{tournament.name} - Match {match.match_id}",
            color=discord.Color.orange(),
            mentionable=True,
            reason=f"Match role for {tournament.name}"
        )
        match.role_id = role.id
        
        # Assign role to players
        for pid in match.players:
            member = guild.get_member(pid)
            if member:
                await member.add_roles(role)
        
        # Create forum post with initial content
        mentions = " ".join([f"<@{pid}>" for pid in match.players])
        first_player = match.players[0]
        
        initial_content = (
            f"🎮 **MATCH {match.match_id}**\n"
            f"Players: {mentions}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚫 **BAN PHASE**\n"
            f"Each player bans {tournament.bans_per_player} uma\n"
            f"(Dùng , / - + hoặc 'and' để ngăn cách)\n\n"
            f"<@{first_player}> hãy gửi {tournament.bans_per_player} uma để ban:"
        )
        
        # Create forum thread (post)
        thread_with_message = await forum_channel.create_thread(
            name=post_name[:100],
            content=initial_content
        )
        
        match.thread_id = thread_with_message.thread.id
        
        # Set permissions: only match role + referee can reply
        await thread_with_message.thread.edit(
            locked=False
        )
        
        # Add permission overwrite for match role to reply
        await forum_channel.set_permissions(
            role,
            send_messages_in_threads=True
        )
        
        # Set match status to banning
        match.status = "banning"
        
        tournament.save()


# ============================================================================
# ADMIN DROPDOWN PANEL
# ============================================================================

class TournamentActionsSelect(Select):
    """Dropdown for Tournament Actions"""
    def __init__(self):
        options = [
            discord.SelectOption(label="Create Tournament", description="Create & start registration", emoji="🏆", value="create"),
            discord.SelectOption(label="Close Registration", description="Close registration & generate bracket", emoji="🔒", value="close_reg"),
            discord.SelectOption(label="End Tournament", description="End the tournament", emoji="🏁", value="end"),
        ]
        super().__init__(placeholder="🎯 Tournament Actions", options=options, custom_id="tournament_actions")
    
    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        
        if value == "create":
            modal = CreateTournamentModal()
            await interaction.response.send_modal(modal)
        
        elif value == "close_reg":
            await self.close_registration(interaction)
        
        elif value == "end":
            await self.end_tournament(interaction)
    
    async def close_registration(self, interaction: discord.Interaction):
        tournament = get_active_tournament(interaction.guild_id)
        if not tournament:
            await interaction.response.send_message("❌ No active tournament!", ephemeral=True)
            return
        
        if len(tournament.players) < 3:
            await interaction.response.send_message("❌ Need at least 3 players!", ephemeral=True)
            return
        
        tournament.status = "in_progress"
        matches = tournament.generate_bracket()
        tournament.save()
        
        await interaction.response.send_message(
            f"✅ Registration closed! Created {len(matches)} matches for Round {tournament.current_round}",
            ephemeral=True
        )
        
        # Create forum posts for each match
        await create_match_posts(interaction.guild, tournament, matches)
    
    async def end_tournament(self, interaction: discord.Interaction):
        tournament = get_active_tournament(interaction.guild_id)
        if not tournament:
            await interaction.response.send_message("❌ No active tournament!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        # Cleanup: Delete match threads and temp roles
        deleted_threads = 0
        deleted_roles = 0
        
        for match in tournament.matches:
            # Delete match thread
            if match.thread_id:
                try:
                    thread = interaction.guild.get_thread(match.thread_id)
                    if thread:
                        await thread.delete()
                        deleted_threads += 1
                except Exception as e:
                    print(f"Error deleting thread {match.thread_id}: {e}")
            
            # Delete temp match role
            if match.role_id:
                try:
                    role = interaction.guild.get_role(match.role_id)
                    if role:
                        await role.delete()
                        deleted_roles += 1
                except Exception as e:
                    print(f"Error deleting role {match.role_id}: {e}")
        
        # Also delete bracket thread if exists
        public_channel = interaction.guild.get_channel(tournament.public_channel_id)
        if public_channel:
            try:
                async for thread in public_channel.archived_threads():
                    if tournament.name in thread.name and "Bracket" in thread.name:
                        await thread.delete()
                        deleted_threads += 1
                        break
                
                # Check active threads too
                for thread in public_channel.threads:
                    if tournament.name in thread.name and "Bracket" in thread.name:
                        await thread.delete()
                        deleted_threads += 1
                        break
            except Exception as e:
                print(f"Error deleting bracket thread: {e}")
        
        tournament.status = "completed"
        tournament.save()
        
        # Archive to Google Sheets
        archived = False
        try:
            from tournament_sheets import get_tournament_sheets
            sheets = get_tournament_sheets()
            archived = sheets.archive_tournament(tournament.name)
        except Exception as e:
            print(f"Sheets archive error: {e}")
        
        # Delete local JSON file after archiving
        try:
            import os
            from tournament_manager import TOURNAMENTS_DIR
            json_file = os.path.join(TOURNAMENTS_DIR, f"{tournament.id}.json")
            if os.path.exists(json_file):
                os.remove(json_file)
                print(f"🗑️ Deleted tournament file: {tournament.id}.json")
        except Exception as e:
            print(f"Error deleting tournament file: {e}")
        
        archive_msg = "📁 Archived to Sheets & deleted local file" if archived else "🗑️ Local file deleted"
        
        await interaction.followup.send(
            f"🏁 Tournament **{tournament.name}** ended!\n"
            f"🧹 Cleaned up: {deleted_threads} threads, {deleted_roles} roles\n"
            f"{archive_msg}",
            ephemeral=True
        )


class MatchManagementSelect(Select):
    """Dropdown for Match Management"""
    def __init__(self):
        options = [
            discord.SelectOption(label="View Bracket", description="View tournament bracket", emoji="📊", value="bracket"),
            discord.SelectOption(label="View Standings", description="View rankings", emoji="🏅", value="standings"),
        ]
        super().__init__(placeholder="📋 Match Management", options=options, custom_id="match_management")
    
    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        tournament = get_active_tournament(interaction.guild_id)
        
        if not tournament:
            await interaction.response.send_message("❌ No tournament found!", ephemeral=True)
            return
        
        if value == "bracket":
            embed = self.create_bracket_embed(tournament)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        elif value == "standings":
            embed = self.create_standings_embed(tournament)
            await interaction.response.send_message(embed=embed, ephemeral=True)
    
    def create_bracket_embed(self, tournament: Tournament) -> discord.Embed:
        embed = discord.Embed(
            title=f"📊 Bracket - {tournament.name}",
            color=discord.Color.blue()
        )
        
        for match in tournament.matches:
            players = [tournament.players.get(pid) for pid in match.players]
            player_names = [p.display_name if p else "???" for p in players]
            
            status_emoji = {"pending": "⏳", "banning": "🚫", "picking": "🎯", "playing": "🎮", "completed": "✅"}
            
            embed.add_field(
                name=f"{status_emoji.get(match.status, '❓')} {match.match_id}",
                value=f"{player_names[0]} vs {player_names[1]} vs {player_names[2]}",
                inline=True
            )
        
        return embed
    
    def create_standings_embed(self, tournament: Tournament) -> discord.Embed:
        embed = discord.Embed(
            title=f"🏅 Standings - {tournament.name}",
            color=discord.Color.gold()
        )
        
        # Calculate points
        points = {pid: 0 for pid in tournament.players.keys()}
        for match in tournament.matches:
            if match.status == "completed":
                for result in match.game_results:
                    for pid, position in result.items():
                        if position == 1:
                            points[int(pid)] = points.get(int(pid), 0) + 3
                        elif position == 2:
                            points[int(pid)] = points.get(int(pid), 0) + 1
        
        # Sort by points
        sorted_players = sorted(points.items(), key=lambda x: x[1], reverse=True)
        
        standings_text = ""
        for i, (pid, pts) in enumerate(sorted_players, 1):
            player = tournament.players.get(pid)
            name = player.display_name if player else "???"
            standings_text += f"**{i}.** {name} - {pts} pts\n"
        
        embed.description = standings_text or "No results yet"
        return embed


class SettingsSelect(Select):
    """Dropdown for Settings"""
    def __init__(self):
        options = [
            discord.SelectOption(label="Set Max Players", description="Set maximum participants", emoji="👥", value="max_players"),
            discord.SelectOption(label="Set Bans Count", description="Bans per player", emoji="🚫", value="bans"),
            discord.SelectOption(label="Set Picks Count", description="Uma per player", emoji="🎯", value="picks"),
        ]
        super().__init__(placeholder="⚙️ Settings", options=options, custom_id="tournament_settings")
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("⚙️ Settings - Coming soon!", ephemeral=True)


class AdminPanelView(View):
    """Main View for Admin Panel"""
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view
        self.add_item(TournamentActionsSelect())
        self.add_item(MatchManagementSelect())
        self.add_item(SettingsSelect())


# ============================================================================
# MODALS
# ============================================================================

class CreateTournamentModal(Modal, title="🏆 Create Tournament"):
    name = TextInput(
        label="Tournament Name",
        placeholder="e.g. Uma Cup Season 1",
        max_length=100
    )
    
    max_players = TextInput(
        label="Max Registrants",
        placeholder="12",
        default="12",
        max_length=3
    )
    
    prize_pool = TextInput(
        label="Prize Pool (Optional)",
        placeholder="e.g. 100k gems, SSR Ticket...",
        required=False,
        max_length=100
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        # Validate max registrants
        try:
            max_p = int(self.max_players.value)
            if max_p < 3 or max_p > 99:
                raise ValueError()
        except:
            await interaction.response.send_message("❌ Max registrants must be 3-99!", ephemeral=True)
            return
        
        # Check if tournament setup exists (has channel IDs)
        existing = get_active_tournament(interaction.guild_id)
        
        if existing and existing.public_channel_id:
            # Update existing tournament with new name/settings
            existing.name = self.name.value
            existing.max_players = max_p
            existing.prize_pool = self.prize_pool.value if self.prize_pool.value else None
            existing.status = "registration"
            existing.players = {}  # Reset players for new tournament
            existing.matches = []
            existing.current_round = 0
            tournament = existing
        else:
            # No setup exists - create new (won't have channels)
            tournament = create_tournament(
                guild_id=interaction.guild_id,
                name=self.name.value,
                created_by=interaction.user.id
            )
            tournament.max_players = max_p
            tournament.prize_pool = self.prize_pool.value if self.prize_pool.value else None
            tournament.status = "registration"
        
        tournament.save()
        
        # Send registration message to public channel
        if tournament.public_channel_id:
            channel = interaction.guild.get_channel(tournament.public_channel_id)
            if channel:
                embed = create_registration_embed(tournament)
                view = RegistrationView()
                
                # Ping Tournament role
                role_mention = ""
                if tournament.tournament_role_id:
                    role_mention = f"<@&{tournament.tournament_role_id}> "
                
                msg = await channel.send(content=f"{role_mention}🏆 **NEW TOURNAMENT!**", embed=embed, view=view)
                tournament.registration_message_id = msg.id
                tournament.save()
        
        await interaction.response.send_message(
            f"✅ Created **{tournament.name}** and opened registration!\n"
            f"Max players: {max_p}",
            ephemeral=True
        )


# ============================================================================
# REGISTRATION VIEW
# ============================================================================

class JoinTournamentButton(Button):
    """Join Tournament Button"""
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.success,
            label="🎮 JOIN TOURNAMENT",
            custom_id="join_tournament"
        )
    
    async def callback(self, interaction: discord.Interaction):
        try:
            tournament = get_active_tournament(interaction.guild_id)
            if not tournament:
                await interaction.response.send_message("❌ Tournament not found!", ephemeral=True)
                return
            
            if tournament.status != "registration":
                await interaction.response.send_message("❌ Registration is closed!", ephemeral=True)
                return
            
            if interaction.user.id in tournament.players:
                # Already joined - offer to leave
                tournament.remove_player(interaction.user.id)
                tournament.save()
                await interaction.response.send_message("👋 You left the tournament!", ephemeral=True)
                await self.update_registration_message(interaction, tournament)
                return
            
            if len(tournament.players) >= tournament.max_players:
                await interaction.response.send_message("❌ Tournament is full!", ephemeral=True)
                return
            
            # Join tournament
            tournament.add_player(interaction.user.id, interaction.user.display_name)
            tournament.save()
            
            await interaction.response.send_message(
                f"✅ You joined **{tournament.name}**!\n"
                f"Click the button again to leave.",
                ephemeral=True
            )
            
            # Sync to Google Sheets (after response)
            try:
                from tournament_sheets import get_tournament_sheets
                sheets = get_tournament_sheets()
                sheets.sync_registrant(tournament.name, interaction.user.display_name, interaction.user.id)
            except Exception as e:
                print(f"Sheets sync error: {e}")
            
            await self.update_registration_message(interaction, tournament)
            
        except discord.NotFound:
            pass  # Interaction expired
    
    async def update_registration_message(self, interaction: discord.Interaction, tournament: Tournament):
        """Update participant count in message"""
        try:
            embed = create_registration_embed(tournament)
            await interaction.message.edit(embed=embed)
        except Exception as e:
            print(f"Error updating registration message: {e}")


class RegistrationView(View):
    """View for Registration Message"""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(JoinTournamentButton())


# ============================================================================
# RESULT SUBMISSION (Referee Only)
# ============================================================================

class SubmitResultModal(Modal, title="Submit Game Result"):
    """Modal for referee to submit game results"""
    
    def __init__(self, match: Match, tournament: Tournament, players: dict):
        super().__init__()
        self.match = match
        self.tournament = tournament
        self.players = players  # {player_id: display_name}
        
        # Create player name list for hint
        player_names = [f"{i+1}. {name}" for i, (pid, name) in enumerate(players.items())]
        hint = "\n".join(player_names)
        
        self.first_place = TextInput(
            label="🥇 1st Place (Player Number)",
            placeholder="Enter 1, 2, or 3",
            max_length=1,
            required=True
        )
        self.second_place = TextInput(
            label="🥈 2nd Place (Player Number)",
            placeholder="Enter 1, 2, or 3",
            max_length=1,
            required=True
        )
        self.third_place = TextInput(
            label="🥉 3rd Place (Player Number)",
            placeholder="Enter 1, 2, or 3",
            max_length=1,
            required=True
        )
        
        self.add_item(self.first_place)
        self.add_item(self.second_place)
        self.add_item(self.third_place)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parse and validate
            first = int(self.first_place.value)
            second = int(self.second_place.value)
            third = int(self.third_place.value)
            
            if not all(1 <= x <= 3 for x in [first, second, third]):
                await interaction.response.send_message("❌ Invalid numbers! Use 1, 2, or 3.", ephemeral=True)
                return
            
            if len(set([first, second, third])) != 3:
                await interaction.response.send_message("❌ Each position must be different!", ephemeral=True)
                return
            
            # Map to player IDs
            player_list = list(self.players.keys())
            placements = {
                player_list[first - 1]: 1,
                player_list[second - 1]: 2,
                player_list[third - 1]: 3
            }
            
            # Add result
            winner = self.match.add_game_result(placements)
            self.tournament.save()
            
            # Sync result to Google Sheets
            game_num = len(self.match.game_results)
            try:
                from tournament_sheets import get_tournament_sheets
                sheets = get_tournament_sheets()
                sheets.sync_game_result(self.tournament.name, self.match.match_id, game_num, placements)
            except Exception as e:
                print(f"Sheets result sync error: {e}")
            
            # Get player names for display
            result_text = (
                f"🥇 **{self.players[player_list[first - 1]]}**\n"
                f"🥈 {self.players[player_list[second - 1]]}\n"
                f"🥉 {self.players[player_list[third - 1]]}"
            )
            
            if winner:
                # BO3 complete
                winner_name = self.players.get(winner, "Unknown")
                await interaction.response.send_message(
                    f"✅ **Game {game_num} Result:**\n{result_text}\n\n"
                    f"🏆 **MATCH WINNER: {winner_name}!**\n"
                    f"Match {self.match.match_id} is complete."
                )
            else:
                # More games needed
                await interaction.response.send_message(
                    f"✅ **Game {game_num} Result:**\n{result_text}\n\n"
                    f"📊 BO3 Progress: {game_num}/3 games\n"
                    f"Continue to next game!"
                )
                
        except ValueError:
            await interaction.response.send_message("❌ Please enter valid numbers (1, 2, or 3)!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class SubmitResultButton(Button):
    """Button for referee to submit game results"""
    
    def __init__(self, match_id: str):
        super().__init__(
            style=discord.ButtonStyle.success,
            label="🏆 Submit Result",
            custom_id=f"submit_result_{match_id}"
        )
        self.match_id = match_id
    
    async def callback(self, interaction: discord.Interaction):
        # Check if user has Referee role
        referee_role = discord.utils.get(interaction.guild.roles, name="Referee")
        
        if not referee_role or referee_role not in interaction.user.roles:
            await interaction.response.send_message(
                "❌ Chỉ Referee mới được submit kết quả!",
                ephemeral=True
            )
            return
        
        # Get tournament and match
        tournament = get_active_tournament(interaction.guild_id)
        if not tournament:
            await interaction.response.send_message("❌ No active tournament!", ephemeral=True)
            return
        
        match = None
        for m in tournament.matches:
            if m.match_id == self.match_id:
                match = m
                break
        
        if not match:
            await interaction.response.send_message("❌ Match not found!", ephemeral=True)
            return
        
        if match.status not in ["playing", "banning", "picking"]:
            await interaction.response.send_message("❌ Match chưa sẵn sàng hoặc đã kết thúc!", ephemeral=True)
            return
        
        if match.match_winner:
            await interaction.response.send_message("❌ Match đã có người thắng!", ephemeral=True)
            return
        
        # Get player info
        players = {pid: tournament.players[pid].display_name for pid in match.players if pid in tournament.players}
        
        # Show modal
        modal = SubmitResultModal(match, tournament, players)
        await interaction.response.send_modal(modal)


class ResultSubmitView(View):
    """View containing result submit button"""
    def __init__(self, match_id: str):
        super().__init__(timeout=None)
        self.add_item(SubmitResultButton(match_id))


# ============================================================================
# EXPORTS (for bot-hosting.py)
# ============================================================================

__all__ = [
    'AdminPanelView',
    'RegistrationView', 
    'create_registration_embed',
    'create_match_posts',
    'fetch_uma_list',
    'parse_uma_names',
    'process_match_message',
    'ResultSubmitView',
    'SubmitResultButton'
]
