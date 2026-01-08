"""Configuration management for PulzWaveArtNetMidiBridge."""

import json
import sys
import logging
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

import platformdirs

# ==============================================================================
# CONSTANTS
# ==============================================================================

APP_NAME = "PulzWaveArtNetMidiBridge"
AUTHOR = "PulzWave"

# Cross-platform data directory
USER_DATA_DIR = Path(platformdirs.user_data_dir(APP_NAME, AUTHOR))
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = USER_DATA_DIR / "config.json"
LOG_DIR = USER_DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Default Config Structure
DEFAULT_CONFIG = {
    "setup_completed": False,  # Tracks if first-time setup was completed
    "midi_port": "",
    "artnet_universe": 0,  # Default to Universe 1 (Art-Net universe 0)
    "dmx_start_channel": 1,  # 1-based index
    "min_intensity": 0,      # 0-127 MIDI value
    "strobe_enabled": True,
    "logging_level": "INFO"  # INFO or DEBUG
}

# ==============================================================================
# LOGGING SETUP
# ==============================================================================

LOG_FILE_PATH = LOG_DIR / "PulzWaveArtNetMidiBridge.log"

logger = logging.getLogger(APP_NAME)
logger.setLevel(logging.INFO)  # Set to DEBUG for troubleshooting

# Rotate logs every day at midnight, keep 7 days
_handler = TimedRotatingFileHandler(
    LOG_FILE_PATH, when="midnight", interval=1, backupCount=7, encoding='utf-8'
)
_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
_handler.setFormatter(_formatter)
logger.addHandler(_handler)

# Also log to console for development
_console = logging.StreamHandler()
_console.setFormatter(_formatter)
logger.addHandler(_console)


def log_user_action(action: str):
    """Log a user action."""
    logger.info(f"USER ACTION: {action}")


def log_exception(exc_type, exc_value, exc_traceback):
    """Global exception handler to log uncaught exceptions."""
    if issubclass(exc_type, KeyboardInterrupt):
        # Don't log keyboard interrupt
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


# Install global exception handler
sys.excepthook = log_exception


def handle_async_exception(loop, context):
    """Handle exceptions in asyncio tasks to ensure they are logged."""
    exception = context.get('exception')
    if exception:
        logger.error(f"Async exception: {context.get('message', 'Unknown')}", exc_info=exception)
    else:
        logger.error(f"Async error: {context.get('message', 'Unknown')}")


# ==============================================================================
# CONFIG MANAGER
# ==============================================================================

class ConfigManager:
    """Manages application configuration with persistent storage."""
    
    def __init__(self):
        self.data = DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        """Load configuration from file."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r') as f:
                    saved = json.load(f)
                    self.data.update(saved)
                logger.info("Configuration loaded.")
            except Exception as e:
                logger.error(f"Failed to load config: {e}")

    def save(self):
        """Save configuration to file."""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.data, f, indent=4)
            logger.info("Configuration saved.")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def get(self, key):
        """Get a configuration value."""
        return self.data.get(key)

    def set(self, key, value):
        """Set a configuration value and save."""
        self.data[key] = value
        self.save()


# Global config instance
config = ConfigManager()


def set_logging_level(level: str):
    """Set the logging level dynamically."""
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
    }
    log_level = level_map.get(level.upper(), logging.INFO)
    logger.setLevel(log_level)
    logger.info(f"Logging level set to {level.upper()}")


# Apply configured logging level on startup
set_logging_level(config.get("logging_level") or "INFO")
