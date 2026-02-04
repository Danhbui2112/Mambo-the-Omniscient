"""
Profile management and verification system.

Handles profile linking, OCR verification, and support messages.
"""

import os
import json
import time
import random
import asyncio
import datetime
import discord
import aiohttp
from config import (
    PROFILE_LINKS_FILE,
    SUPPORT_MESSAGE,
    SUPPORT_SERVER_URL,
    DONATION_MESSAGE,
    DONATION_URL,
    VOTE_MESSAGE,
    VOTE_URL,
    PROMO_CHANCE,
    PROMO_COOLDOWN,
    OCR_SERVICE_URL,
    EXAMPLE_PROFILE_IMAGE,
)

# Track last promo time per user
promo_cooldowns = {}  # {user_id: last_promo_timestamp}

# Pending verification requests: {user_id: {"member_name": str, "club_name": str, "expires": datetime}}
pending_verifications = {}


def add_support_footer(embed: discord.Embed, extra_text: str = "") -> discord.Embed:
    """Add support server link to embed footer with embedded link
    
    Args:
        embed: Discord embed to add footer to
        extra_text: Optional additional text before support message
    
    Returns:
        Modified embed with support footer containing embedded link
    """
    # Use Discord markdown format for embedded link: [text](url)
    footer_link = f"[{SUPPORT_MESSAGE}]({SUPPORT_SERVER_URL})"
    
    if extra_text:
        footer_text = f"{extra_text}\n{footer_link}"
    else:
        footer_text = footer_link
    
    embed.set_footer(text=footer_text)
    return embed


async def maybe_send_promo_message(interaction: discord.Interaction):
    """Maybe send a promotional message with donation & vote links.
    
    Based on random chance (25%) and user cooldown (1 hour).
    Sends as PUBLIC message (not ephemeral).
    
    Args:
        interaction: Discord interaction object
    """
    user_id = interaction.user.id
    current_time = time.time()
    
    # Check cooldown
    if user_id in promo_cooldowns:
        time_since_last = current_time - promo_cooldowns[user_id]
        if time_since_last < PROMO_COOLDOWN:
            return  # Still in cooldown
    
    # Random chance check (25%)
    if random.random() > PROMO_CHANCE:
        return  # Not this time
    
    # Update cooldown
    promo_cooldowns[user_id] = current_time
    
    # Create promo embed
    embed = discord.Embed(
        title="💝 Support the Bot!",
        description="If you find this bot helpful, consider support the bot!",
        color=discord.Color.from_str("#FF69B4")  # Pink color
    )
    
    embed.add_field(
        name="☕ Donation",
        value=f"[{DONATION_MESSAGE}]({DONATION_URL})",
        inline=True
    )
    
    embed.add_field(
        name="⭐ Vote",
        value=f"[{VOTE_MESSAGE}]({VOTE_URL})",
        inline=True
    )
    
    embed.set_footer(text="Thank you for your support! 💕")
    
    try:
        # Send as PUBLIC message (not ephemeral)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"Error sending promo message: {e}")


def load_profile_links() -> dict:
    """Load Discord ID -> Trainer ID mappings from file
    
    Returns:
        Dictionary of profile links
    """
    if os.path.exists(PROFILE_LINKS_FILE):
        try:
            with open(PROFILE_LINKS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_profile_link(discord_id: int, trainer_id: str, member_name: str, club_name: str, viewer_id: str = None):
    """Save a verified profile link
    
    Args:
        discord_id: Discord user ID
        trainer_id: Trainer ID from OCR (12-digit number)
        member_name: In-game trainer name
        club_name: Club name at time of linking
        viewer_id: Player ID from uma.moe API (never changes even if user changes club/name)
    """
    links = load_profile_links()
    links[str(discord_id)] = {
        "viewer_id": viewer_id,  # Primary identifier - never changes
        "trainer_id": trainer_id,
        "member_name": member_name,
        "club_name": club_name,
        "linked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    with open(PROFILE_LINKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(links, f, indent=2)


async def call_ocr_service(image_data: bytes) -> dict:
    """Call Node.js OCR service to extract trainer data from image
    
    Args:
        image_data: Raw image bytes
        
    Returns:
        Dictionary with OCR results or empty dict if failed
    """
    try:
        import base64
        base64_image = base64.b64encode(image_data).decode('utf-8')
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OCR_SERVICE_URL}/api/extract",
                json={"base64Image": f"data:image/png;base64,{base64_image}"},
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('success'):
                        return result.get('data', {})
        return {}
    except Exception as e:
        print(f"OCR service error: {e}")
        return {}
