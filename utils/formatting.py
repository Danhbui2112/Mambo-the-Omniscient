"""
Text formatting utilities for Discord embeds and messages.

Provides functions for formatting fan counts, text alignment, and statistics display.
"""

from typing import List, Optional
from wcwidth import wcswidth


def format_fans(n) -> str:
    """Format fan count with K/M suffix
    
    Examples:
        format_fans(1500) -> "+1K"
        format_fans(1500000) -> "+1.5M"
        format_fans(150000000) -> "+150M"
    
    Args:
        n: Number to format
        
    Returns:
        Formatted string with K/M suffix
    """
    try:
        n_int = int(float(str(n).replace(',', '')))
    except ValueError:
        return str(n)
    
    if n_int == 0:
        return "0"
    
    sign = "+" if n_int > 0 else "-" if n_int < 0 else ""
    n_abs = abs(n_int)
    
    if n_abs >= 1_000_000:
        # Nếu >= 100M: chỉ hiện số nguyên (ví dụ: +139M)
        # Nếu < 100M: hiện 1 chữ số thập phân (ví dụ: +13.6M)
        if n_abs >= 100_000_000:
            return f"{sign}{n_abs // 1_000_000}M"
        else:
            return f"{sign}{n_abs / 1_000_000:.1f}M"
    if n_abs >= 1_000:
        # Giữ nguyên làm tròn cho K
        return f"{sign}{n_abs // 1_000}K"
    
    return f"{sign}{n_abs}"


def format_fans_full(n) -> str:
    """Format fan count with full number and sign
    
    Examples:
        format_fans_full(1500) -> "+1,500"
        format_fans_full(-250) -> "-250"
    
    Args:
        n: Number to format
        
    Returns:
        Formatted string with thousand separators and sign
    """
    try:
        n_int = int(float(str(n).replace(',', '')))
    except ValueError:
        return str(n)
    return f"{n_int:+,}"


def format_fans_billion(n) -> str:
    """Format fan count to Billion unit
    
    Examples:
        format_fans_billion(1500000000) -> "1.50B"
        format_fans_billion(15000000000) -> "15.0B"
    
    Args:
        n: Number to format
        
    Returns:
        Formatted string with B suffix
    """
    try:
        n_int = int(float(str(n).replace(',', '')))
    except ValueError:
        return str(n)
    
    if n_int == 0:
        return "0B"
    
    # Convert to billion
    n_billion = n_int / 1_000_000_000
    
    if n_billion >= 10:
        return f"{n_billion:.1f}B"
    else:
        return f"{n_billion:.2f}B"


def calculate_daily_from_cumulative(cumulative: List[int]) -> List[int]:
    """Convert cumulative fan totals to daily differences
    
    Args:
        cumulative: List of cumulative fan totals (one per day)
    
    Returns:
        List of daily fan gains
        
    Example:
        Input:  [0, 0, 238644810, 242678516, 245877460]
        Output: [0, 0, 238644810, 4033706, 3198944]
                            ^first    ^diff    ^diff
    """
    daily = []
    for i, total in enumerate(cumulative):
        if i == 0:
            # First day: use total as daily (first non-zero is starting point)
            daily.append(total if total > 0 else 0)
        else:
            if total > 0 and cumulative[i-1] >= 0:
                # Calculate difference from previous day
                diff = total - cumulative[i-1]
                daily.append(max(0, diff))  # Prevent negative values
            else:
                # No data or invalid
                daily.append(0)
    
    return daily


def center_text_exact(text: str, total_width: int = 56) -> str:
    """Center text exactly, accounting for emoji width
    
    Args:
        text: Text to center
        total_width: Total width for centering (default: 56)
        
    Returns:
        Centered text string
    """
    # Calculate actual display width
    display_width = wcswidth(text) if wcswidth(text) != -1 else len(text)
    
    if display_width >= total_width:
        return text[:total_width]
    
    padding_total = total_width - display_width
    padding_left = padding_total // 2
    padding_right = padding_total - padding_left
    
    result = (' ' * padding_left) + text + (' ' * padding_right)
    return result


def format_stat_line_compact(label: str, value: str, label_width: int = 30) -> str:
    """Format stat line with LEFT-ALIGNED value, accounting for emoji
    
    Args:
        label: Label text (will add ':' if not present)
        value: Value text
        label_width: Width for label section (default: 30)
        
    Returns:
        Formatted stat line
    """
    if not label.endswith(':'):
        label += ':'
    
    # Calculate actual display width of label
    label_display_width = wcswidth(label) if wcswidth(label) != -1 else len(label)
    
    # Add spaces to reach target width
    spaces_needed = label_width - label_display_width
    left = label + (' ' * max(0, spaces_needed))
    
    line = left + value
    
    # Truncate if too long (based on display width)
    if wcswidth(line) > 56:
        return line[:56]
    return line
