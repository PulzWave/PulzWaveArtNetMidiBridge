"""
PulzWaveArtNetMidiBridge - Art-Net to MIDI Bridge
Entry point for the application.
"""

import sys
import multiprocessing

# CRITICAL: Must be called at the very start for PyInstaller on macOS
# This prevents the app from spawning multiple instances
if __name__ == "__main__":
    multiprocessing.freeze_support()

import asyncio
import time
import colorsys
from dataclasses import dataclass

# Initialize logger and exception handling FIRST, before any other imports
# This ensures any import errors are properly logged
import logging
from pathlib import Path

# Import config first to set up logging
from src.config import config, logger, APP_NAME, handle_async_exception

try:
    from nicegui import ui, app
    from src.artnet_listener import ArtNetReceiver
    from src.midi_manager import midi_manager
    from src.ui import create_ui
    from src.setup_wizard import create_setup_wizard, is_first_run
except Exception as e:
    logger.critical(f"Failed to import required modules: {e}", exc_info=True)
    print(f"CRITICAL: {e}")
    sys.exit(1)

app.native.window_args['resizable'] = False

# ==============================================================================
# APPLICATION STATE
# ==============================================================================

@dataclass
class AppState:
    """Holds real-time data for the UI and Processing."""
    connected: bool = False
    last_packet_time: float = 0.0
    
    # Raw DMX Inputs (0-255)
    dmx_r: int = 0
    dmx_g: int = 0
    dmx_b: int = 0
    dmx_w: int = 0
    dmx_uv: int = 0
    dmx_brightness: int = 0  # Brightness channel
    dmx_strobe: int = 0
    dmx_attr: int = 0
    dmx_hold: int = 0  # Hold time in milliseconds (16-bit: channels 9+10, 0 = continuous, 1-10000 = ms)
    
    # Calculated Outputs
    midi_hue: int = 0
    midi_inv_hue: int = 0
    midi_intensity: int = 0
    midi_color_slider: int = 0  # Combined RGB to single CC
    active_note: int = None  # Currently playing note
    note_trigger_time: float = 0.0  # When the note was triggered
    last_attr: int = 0  # Track previous attr for blackout detection
    note_held_released: bool = False  # Track if note was released due to hold expiry


# Global state instance
state = AppState()


# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

def map_range(x: int, in_min: int, in_max: int, out_min: int, out_max: int) -> int:
    """Map a value from one range to another."""
    if in_max == in_min:
        return out_min
    return (x - in_min) * (out_max - out_min) // (in_max - in_min) + out_min


def rgb_to_color_slider_cc(red: int, green: int, blue: int) -> int:
    """
    Converts RGB values (0-255) to a single MIDI CC value (0-127) for the color slider.
    
    Color slider mapping:
    - 0-12: Normal mode (no color override / black)
    - 13-110: Color gradient (red -> orange -> yellow -> green -> cyan -> blue -> purple -> pink -> red)
    - 111-127: White
    
    Args:
        red, green, blue: DMX values (0-255)
    
    Returns:
        MIDI CC value (0-127)
    """
    # If all RGB values are very low, return normal mode (black)
    if red < 1 and green < 1 and blue < 1:
        return 0
    
    # If all RGB values are very high, return white
    if red > 240 and green > 240 and blue > 240:
        return 127
    
    # Normalize RGB to 0-1 range
    r = red / 255.0
    g = green / 255.0
    b = blue / 255.0
    
    # Find the dominant color
    max_val = max(r, g, b)
    min_val = min(r, g, b)
    
    # If it's very close to grayscale, return white zone
    if max_val - min_val < 0.1:
        brightness = (r + g + b) / 3.0
        return int(111 + brightness * 16)
    
    # Calculate hue (0-360 degrees)
    if max_val == min_val:
        hue = 0
    elif max_val == r:
        hue = (60 * ((g - b) / (max_val - min_val)) + 360) % 360
    elif max_val == g:
        hue = (60 * ((b - r) / (max_val - min_val)) + 120) % 360
    else:  # max_val == b
        hue = (60 * ((r - g) / (max_val - min_val)) + 240) % 360
    
    # Map hue to color slider range (13-110)
    color_range = 110 - 13  # 97 steps
    hue_normalized = hue / 360.0
    cc_value = int(13 + hue_normalized * color_range)
    
    return max(13, min(110, cc_value))


# ==============================================================================
# ART-NET PACKET PROCESSING
# ==============================================================================

def process_packet(data: list):
    """
    Callback function when ArtNet data is received.
    Runs in the ArtNet thread.
    
    Args:
        data: List of DMX channel values (0-255)
    """
    state.last_packet_time = time.time()
    state.connected = True
    
    start_ch = int(config.get("dmx_start_channel")) - 1  # Convert to 0-index
    
    # Ensure we have enough data (10 channels: R,G,B,W,UV,Brightness,Strobe,Attr,Hold_MSB,Hold_LSB)
    if len(data) < start_ch + 10:
        return

    # 1. Parse Raw DMX
    state.dmx_r = data[start_ch + 0]
    state.dmx_g = data[start_ch + 1]
    state.dmx_b = data[start_ch + 2]
    state.dmx_w = data[start_ch + 3]
    state.dmx_uv = data[start_ch + 4]
    state.dmx_brightness = data[start_ch + 5]  # Brightness channel
    state.dmx_strobe = data[start_ch + 6]
    state.dmx_attr = data[start_ch + 7]
    # Hold time is 16-bit (channels 9+10): MSB * 256 + LSB = milliseconds (capped at 10000)
    hold_msb = data[start_ch + 8]
    hold_lsb = data[start_ch + 9]
    state.dmx_hold = min((hold_msb * 256) + hold_lsb, 10000)  # Cap at 10000ms

    # 2. Logic: Color (RGB -> HSV -> MIDI)
    h, s, v = colorsys.rgb_to_hsv(
        state.dmx_r / 255.0, 
        state.dmx_g / 255.0, 
        state.dmx_b / 255.0
    )
    
    midi_hue = int(h * 127)
    midi_inv_hue = int(((h + 0.5) % 1.0) * 127)  # Complementary color
    
    state.midi_hue = midi_hue
    state.midi_inv_hue = midi_inv_hue
    
    # 3. Logic: Color Slider (RGB -> single CC value)
    state.midi_color_slider = rgb_to_color_slider_cc(state.dmx_r, state.dmx_g, state.dmx_b)

    # 4. Logic: Notes (Attributes Channel) with Hold Time
    # Hold time: 0 = continuous (note stays on), 1-10000 = hold in milliseconds
    desired_note = None
    if state.dmx_attr > 0:
        desired_note = state.dmx_attr - 1  # MIDI note numbers start at 0
    
    # Only trigger note if:
    # - Note actually changed, OR
    # - We're going from no note to a note (and not held-released state)
    attr_changed = (state.dmx_attr != state.last_attr)
    
    if attr_changed:
        # Attribute value changed - handle note transition
        if state.active_note is not None:
            midi_manager.send_note_off(state.active_note)
            state.active_note = None
        
        state.note_held_released = False  # Reset hold-release flag on attr change
        
        if desired_note is not None:
            midi_manager.send_note_on(desired_note)
            state.note_trigger_time = time.time()  # Record when note was triggered
            state.active_note = desired_note
    
    # 5. Logic: Blackout detection (when cue changes to 0)
    if state.dmx_attr == 0 and state.last_attr > 0:
        # Cue went to 0, trigger blackout
        midi_manager.send_blackout_note()
    state.last_attr = state.dmx_attr

    # 6. Send Simple CCs (Map 0-255 to 0-127)
    midi_manager.send_cc(midi_manager.CC_RED, state.dmx_r // 2)
    midi_manager.send_cc(midi_manager.CC_GREEN, state.dmx_g // 2)
    midi_manager.send_cc(midi_manager.CC_BLUE, state.dmx_b // 2)
    midi_manager.send_cc(midi_manager.CC_WHITE, state.dmx_w // 2)
    midi_manager.send_cc(midi_manager.CC_UV, state.dmx_uv // 2)
    midi_manager.send_cc(midi_manager.CC_HUE, midi_hue)
    midi_manager.send_cc(midi_manager.CC_INV_HUE, midi_inv_hue)
    midi_manager.send_cc(midi_manager.CC_COLOR_SLIDER, state.midi_color_slider)


# ==============================================================================
# ASYNC PROCESSING LOOP
# ==============================================================================

async def fast_processing_loop():
    """
    Handles Strobe simulation, note hold timing, and UI connection timeouts.
    Runs at approximately 60Hz.
    """
    while True:
        # 1. Connection Watchdog
        if time.time() - state.last_packet_time > 2.0:
            state.connected = False
            state.dmx_strobe = 0  # Safety: kill strobe if signal lost

        # 2. Note Hold Time Logic
        # If hold time > 0, release note after the specified duration
        if state.active_note is not None and state.dmx_hold > 0:
            # dmx_hold is already in milliseconds (0-10000)
            hold_ms = state.dmx_hold
            elapsed_ms = (time.time() - state.note_trigger_time) * 1000
            
            if elapsed_ms >= hold_ms:
                midi_manager.send_note_off(state.active_note)
                state.active_note = None
                state.note_held_released = True  # Mark as released due to hold

        # 3. Strobe & Intensity Logic
        min_intensity = int(config.get("min_intensity"))
        
        if config.get("strobe_enabled") and state.dmx_strobe > 10:
            # Strobe is active - OVERRIDE brightness with oscillating 0-127
            now = time.time() * 1000
            # Map DMX Strobe 10-255 to 200ms-20ms delay (faster strobe = shorter delay)
            speed = map_range(state.dmx_strobe, 10, 255, 200, 20)
            
            if now - midi_manager._last_strobe_toggle > speed:
                midi_manager._strobe_state = not midi_manager._strobe_state
                midi_manager._last_strobe_toggle = now
            
            # Strobe oscillates between 0 and 127 (full intensity)
            if midi_manager._strobe_state:
                final_intensity = 127
            else:
                final_intensity = 0
        else:
            # No strobe - use brightness channel normally
            target_intensity_midi = map_range(state.dmx_brightness, 0, 255, 0, 127)
            # Apply Minimum Intensity Floor
            final_intensity = max(min_intensity, target_intensity_midi)
        
        state.midi_intensity = final_intensity
        midi_manager.send_cc(midi_manager.CC_INTENSITY, final_intensity)

        await asyncio.sleep(0.016)  # ~60fps


# ==============================================================================
# ART-NET SERVER INSTANCE
# ==============================================================================

artnet_server = ArtNetReceiver(
    callback=process_packet, 
    universe=int(config.get("artnet_universe"))
)


# ==============================================================================
# APP LIFECYCLE
# ==============================================================================

async def startup_tasks():
    """Initialize application components on startup."""
    logger.info("Application Starting...")
    
    # Set up async exception handler for logging
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_async_exception)
    
    # Start ArtNet
    artnet_server.start()
    
    # Start Async Processing Loop
    asyncio.create_task(fast_processing_loop())


def shutdown():
    """Clean up resources on shutdown."""
    try:
        artnet_server.stop()
        midi_manager.close_port()
        logger.info("Application Shutdown - Resources cleaned up")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}", exc_info=True)


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def main():
    """Main entry point for the application."""
    
    # Check if this is first run (no config file exists)
    first_run = is_first_run()
    
    if first_run:
        # Create setup wizard for first-time users
        logger.info("First run detected - launching setup wizard")
        create_setup_wizard(state, artnet_server)
        
        # Override the root page to redirect to setup
        @ui.page('/')
        def redirect_to_setup():
            ui.navigate.to('/setup')
    else:
        # Create normal UI for returning users
        create_ui(state, artnet_server)
        
        # Add redirect from /setup to / in case user tries to access it
        @ui.page('/setup')
        def redirect_from_setup():
            ui.navigate.to('/')
    
    # Register lifecycle hooks
    app.on_startup(startup_tasks)
    app.on_shutdown(shutdown)
    
    # Run the application with fixed window size
    ui.run(
        title=APP_NAME, 
        native=True, 
        window_size=(750, 750),
        fullscreen=False,
        reload=False,
        frameless=False,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Application crashed: {e}", exc_info=True)
        print(f"CRITICAL ERROR: {e}")
        sys.exit(1)
