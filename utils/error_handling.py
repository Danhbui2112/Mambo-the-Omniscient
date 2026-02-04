"""
Error handling and logging module.

Provides centralized error logging with file persistence and retry logic.
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from config import LOGS_DIR

# ============================================================================
# ERROR LOGGING SYSTEM
# ============================================================================

# Setup error logger with rotation (5MB per file, keep 5 backup files)
error_logger = logging.getLogger('bot_errors')
error_logger.setLevel(logging.ERROR)

# Rotating file handler - creates new file when size exceeds 5MB
error_log_file = os.path.join(LOGS_DIR, "errors.log")
error_handler = RotatingFileHandler(
    error_log_file,
    maxBytes=5*1024*1024,  # 5MB per file
    backupCount=5,          # Keep 5 backup files
    encoding='utf-8'
)
error_handler.setLevel(logging.ERROR)

# Format: timestamp | level | location | message
error_formatter = logging.Formatter(
    '%(asctime)s | %(levelname)s | %(funcName)s:%(lineno)d | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
error_handler.setFormatter(error_formatter)
error_logger.addHandler(error_handler)

print(f"📁 Error logs will be saved to: {LOGS_DIR}")


def log_error(error: Exception, context: str = None, extra_info: dict = None):
    """Log error to local file for debugging
    
    Args:
        error: The exception that occurred
        context: Description of what was happening when error occurred
        extra_info: Additional key-value pairs to log
    
    Example:
        try:
            something()
        except Exception as e:
            log_error(e, "Loading club data", {"club_name": club_name})
    """
    try:
        error_msg = f"{type(error).__name__}: {str(error)}"
        if context:
            error_msg = f"[{context}] {error_msg}"
        if extra_info:
            info_str = " | ".join(f"{k}={v}" for k, v in extra_info.items())
            error_msg = f"{error_msg} | {info_str}"
        
        error_logger.error(error_msg, exc_info=True)
        print(f"📝 Error logged to {error_log_file}")
    except Exception as log_e:
        print(f"⚠️ Failed to log error: {log_e}")


def is_retryable_error(e: Exception) -> bool:
    """Check if an error is network-related and can be retried
    
    Args:
        e: The exception to check
        
    Returns:
        True if error is retryable, False otherwise
    """
    error_str = str(e).lower()
    retryable_keywords = [
        "remotedisconnected", "connection aborted", "service unavailable",
        "429", "failed to resolve", "name resolution",
        # Server errors (typically transient)
        "500", "502", "503", "504",
        "server error", "bad gateway", "gateway timeout",
        "internal server error", "temporarily unavailable",
        # Google Sheets API specific patterns
        "apierror: [-1]", "error 502", "that's an error"
    ]
    return any(keyword in error_str for keyword in retryable_keywords)
