"""NiceGUI User Interface for PulzWaveArtNetMidiBridge."""

import os
import platform
import subprocess
import sys

from nicegui import ui, app

from src.config import config, logger, log_user_action, LOG_FILE_PATH, LOG_DIR, APP_NAME, USER_DATA_DIR, set_logging_level
from src.midi_manager import midi_manager
from src.artnet_listener import ArtNetReceiver

# GitHub repository URL
GITHUB_REPO_URL = "https://github.com/pulzwave/PulzWaveArtNetMidiBridge"

# Global flag to track if LoopMIDI check has passed
_loopmidi_check_passed = False


def is_loopmidi_running() -> bool:
    """
    Check if LoopMIDI.exe process is running on Windows.
    Returns True on non-Windows platforms (not applicable).
    """
    if platform.system() != 'Windows':
        return True  # Not applicable on non-Windows
    
    try:
        # Use tasklist to check for loopMIDI.exe process
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq loopMIDI.exe', '/NH'],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        # tasklist returns "INFO: No tasks are running..." if process not found
        # or the process info if found
        output = result.stdout.lower()
        return 'loopmidi.exe' in output
    except Exception as e:
        logger.error(f"Error checking for LoopMIDI process: {e}")
        return False


def check_loopmidi_startup() -> bool:
    """
    Check if LoopMIDI is running on Windows before proceeding.
    Shows a dialog if not found and allows retry or exit.
    Returns True if LoopMIDI is found or not required (non-Windows).
    This should be called from within a UI context (after page load).
    """
    global _loopmidi_check_passed
    
    # Skip check on non-Windows platforms
    if platform.system() != 'Windows':
        _loopmidi_check_passed = True
        return True
    
    # Check if LoopMIDI is running
    if is_loopmidi_running():
        logger.info("LoopMIDI: Process found running")
        _loopmidi_check_passed = True
        return True
    
    logger.warning("LoopMIDI: Process not found, showing dialog")
    
    def show_loopmidi_dialog():
        """Show dialog asking user to start LoopMIDI."""
        with ui.dialog() as dialog:
            dialog.props('persistent')  # Prevent closing by clicking outside
            
            with ui.card().classes('w-96'):
                ui.label('LoopMIDI Required').classes('text-lg font-bold text-negative')
                ui.label('LoopMIDI.exe must be running before starting PulzWaveArtNetMidiBridge.').classes('text-sm mt-2')
                ui.label('Please start LoopMIDI and click Retry.').classes('text-sm text-grey mt-1')
                
                # Add clickable link to download LoopMIDI
                with ui.row().classes('text-xs text-grey mt-2 gap-1'):
                    ui.label('Download LoopMIDI from')
                    ui.link('Tobias Erichsen\'s website', 'https://www.tobias-erichsen.de/software/loopmidi.html', new_tab=True) \
                        .classes('text-blue-500 underline')
                
                def on_retry():
                    """Retry checking for LoopMIDI."""
                    global _loopmidi_check_passed
                    logger.info("LoopMIDI: Retry button clicked")
                    
                    if is_loopmidi_running():
                        logger.info("LoopMIDI: Process found after retry")
                        _loopmidi_check_passed = True
                        dialog.close()
                        ui.notify('LoopMIDI detected!', type='positive')
                        # Now proceed with MIDI port check
                        check_midi_port_startup()
                    else:
                        logger.warning("LoopMIDI: Process still not found after retry")
                        ui.notify('LoopMIDI not found. Please start it and try again.', type='negative')
                
                def on_exit():
                    """Exit the application."""
                    logger.info("LoopMIDI: User chose to exit application")
                    dialog.close()
                    app.shutdown()
                
                with ui.row().classes('w-full gap-2 justify-end mt-4'):
                    ui.button('Retry', on_click=on_retry).props('color=primary')
                    ui.button('Exit', on_click=on_exit).props('color=negative')
        
        dialog.open()
    
    show_loopmidi_dialog()
    return False


def check_midi_port_startup():
    """
    Check if the configured MIDI port is available on startup.
    Shows a dialog if there are issues and allows retry.
    This should be called from within a UI context (after page load).
    
    On Windows, this should only be called after LoopMIDI check has passed.
    """
    global _loopmidi_check_passed
    
    # On Windows, don't proceed if LoopMIDI check hasn't passed yet
    if platform.system() == 'Windows' and not _loopmidi_check_passed:
        logger.info("MIDI: Skipping port check - LoopMIDI check not passed yet")
        return
    
    configured_port = config.get("midi_port")
    
    if not configured_port:
        logger.info("MIDI: No port configured, skipping startup check")
        return
    
    logger.info(f"MIDI: Checking configured port '{configured_port}' on startup")
    
    def show_midi_error_dialog():
        """Main dialog handler with retry logic."""
        # Initial check - if port opens successfully, don't show dialog
        available_ports = midi_manager.get_available_ports(force_refresh=True)
        logger.info(f"MIDI: Available ports: {available_ports}")
        
        if configured_port in available_ports:
            success, message = midi_manager.open_port(configured_port)
            if success:
                logger.info(f"MIDI: Port '{configured_port}' opened successfully on startup")
                return
            else:
                logger.warning(f"MIDI: Port '{configured_port}' found but failed to open: {message}")
                initial_error = "in_use"
        else:
            logger.warning(f"MIDI: Configured port '{configured_port}' not found in available ports")
            initial_error = "not_found"
        
        # Port has issues, show dialog
        with ui.dialog() as dialog:
            dialog.props('persistent')  # Prevent closing by clicking outside
            
            with ui.card().classes('w-96'):
                title_label = ui.label('MIDI Port Not Found').classes('text-lg font-bold text-negative')
                msg_label = ui.label('').classes('text-sm')
                detail_label = ui.label('').classes('text-xs text-grey mt-2')
                
                def update_dialog_for_error(error_type: str):
                    """Update dialog UI based on error type."""
                    if error_type == "not_found":
                        title_label.text = 'MIDI Port Not Found'
                        title_label.classes('text-negative', remove='text-warning')
                        msg_label.text = f"Configured port '{configured_port}' is not available."
                        detail_label.text = 'Start LoopMIDI or connect your MIDI device, then click Retry.\nOr use Continue and select a port in Configuration tab.'
                    else:  # in_use
                        title_label.text = 'MIDI Port In Use'
                        title_label.classes('text-warning', remove='text-negative')
                        msg_label.text = f"Port '{configured_port}' is already in use."
                        detail_label.text = 'Close other applications using this port, then click Retry.'
                
                def on_retry():
                    """Retry connecting to MIDI port."""
                    logger.info(f"MIDI: Retry button clicked, attempting to connect to '{configured_port}'")
                    
                    # Refresh available ports (force refresh to detect newly connected devices)
                    available_ports = midi_manager.get_available_ports(force_refresh=True)
                    logger.info(f"MIDI: Available ports after refresh: {available_ports}")
                    
                    if configured_port not in available_ports:
                        logger.warning(f"MIDI: Port '{configured_port}' still not found after retry")
                        update_dialog_for_error("not_found")
                        ui.notify(f"Port not found. Use Configuration tab to select a different port.", type='warning')
                        return
                    
                    # Port found, try to open it
                    logger.info(f"MIDI: Port '{configured_port}' found, attempting to open...")
                    success, message = midi_manager.open_port(configured_port)
                    if success:
                        logger.info(f"MIDI: Successfully connected to '{configured_port}' after retry")
                        dialog.close()
                        ui.notify('MIDI port connected successfully', type='positive')
                    else:
                        logger.warning(f"MIDI: Failed to open port '{configured_port}': {message}")
                        update_dialog_for_error("in_use")
                        ui.notify(f"Failed to open port: {message}", type='negative')
                
                def on_continue():
                    """Continue without MIDI."""
                    logger.info("MIDI: User chose to continue without MIDI")
                    dialog.close()
                    ui.notify('Use Configuration tab to select a MIDI port', type='info')
                
                # Set initial dialog state
                update_dialog_for_error(initial_error)
                
                # Add buttons
                with ui.row().classes('w-full gap-2 justify-end mt-4'):
                    ui.button('Retry', on_click=on_retry).props('color=primary')
                    ui.button('Continue', on_click=on_continue).props('color=grey')
        
        dialog.open()
    
    # Show the dialog
    show_midi_error_dialog()


def create_ui(state, artnet_server: ArtNetReceiver):
    """
    Create the main UI for PulzWaveArtNetMidiBridge.
    
    Args:
        state: AppState instance with real-time data
        artnet_server: ArtNetReceiver instance
    """
    
    @ui.page('/')
    def main_page():
        ui.colors(primary='#5898d4', secondary='#262626')
        
        # On Windows, first check if LoopMIDI is running
        # This will show a dialog and block further MIDI checks if not found
        if check_loopmidi_startup():
            # LoopMIDI is running (or not on Windows), check MIDI port availability
            check_midi_port_startup()
        
        # Load external CSS file
        css_path = os.path.join(os.path.dirname(__file__), 'styles.css')
        with open(css_path, 'r') as f:
            css_content = f.read()
        ui.add_head_html(f'<style>{css_content}</style>')
        
        # Helper to open external URLs
        def open_url(url: str):
            ui.navigate.to(url, new_tab=True)
        
        # HEADER
        with ui.header().classes('items-center justify-between').style('min-height: auto; padding: 2px;'):
            # Logo on the left with text beside it
            logo_path = os.path.join(os.path.dirname(__file__), 'image', 'pulzwave_logo.png')
            with ui.row().classes('items-center gap-2 flex-1'):
                ui.image(logo_path).classes('').style('height: 75px; width: 90px; flex-shrink: 0; margin-left: 25px; object-fit: cover !important;overflow: visible;')
                with ui.column().classes('gap-0 items-center flex-1'):
                    ui.label('PulzWaveArtNetMidiBridge').classes('text-lg font-bold')
                    status_badge = ui.badge('Disconnected', color='red').classes('text-xs')
            
            # DMX Color preview in center of header
            rgb_preview = ui.element('div').classes('rgb-preview').style('width: 30px; height: 30px;')
            
            with ui.row().classes('items-center gap-1'):
                    ui.button('Donate', icon='volunteer_activism', 
                          on_click=lambda: open_url('https://buymeacoffee.com/pulzwave')) \
                      .props('flat size=md color=white').style('font-weight: bold;').tooltip('Support PulzWaveArtNetMidiBridge development')
                    ui.link('View on GitHub', GITHUB_REPO_URL, new_tab=True)

        # TABS
        with ui.tabs().classes('w-full') as tabs:
            status_tab = ui.tab('Status', icon='monitor_heart')
            config_tab = ui.tab('Configuration', icon='settings')
            logs_tab = ui.tab('Logs', icon='description')

        # TAB PANELS
        with ui.tab_panels(tabs, value=status_tab).classes('w-full'):

            # --- STATUS TAB ---
            with ui.tab_panel(status_tab).classes('q-pa-sm'):
                
                # Main horizontal layout
                with ui.row().classes('w-full gap-3 items-stretch'):
                    
                    # LEFT: DMX Input Section
                    with ui.card().classes('status-card q-pa-sm flex-1'):
                        ui.label('DMX Input').classes('text-sm font-semibold text-white mb-2')
                        
                        def create_dmx_meter(label: str, color: str):
                            """Create a compact DMX meter with inline value."""
                            with ui.row().classes('w-full items-center meter-container mb-1'):
                                ui.label(label).classes('w-20 text-grey-4 text-xs font-medium')
                                progress = ui.linear_progress(value=0, show_value=False) \
                                    .props(f'color={color} track-color=grey-8 rounded size=12px instant') \
                                    .classes('flex-grow dmx-bar mx-1')
                                value_label = ui.badge('0', color='dark').classes('value-badge')
                            return progress, value_label
                        
                        # All channels in compact rows
                        with ui.row().classes('w-full gap-2'):
                            with ui.column().classes('flex-1 gap-0'):
                                bar_r, val_r = create_dmx_meter("Red", "red")
                                bar_g, val_g = create_dmx_meter("Green", "green")
                                bar_b, val_b = create_dmx_meter("Blue", "blue")
                            with ui.column().classes('flex-1 gap-0'):
                                bar_w, val_w = create_dmx_meter("White", "grey-5")
                                bar_uv, val_uv = create_dmx_meter("UV", "purple")
                                bar_brt, val_brt = create_dmx_meter("Brightness", "amber")
                        
                        # Strobe and Hold in a row
                        with ui.row().classes('w-full gap-2 mt-1'):
                            with ui.column().classes('flex-1 gap-0'):
                                bar_str, val_str = create_dmx_meter("Strobe", "cyan")
                            with ui.column().classes('flex-1 gap-0'):
                                bar_hold, val_hold = create_dmx_meter("Hold", "teal")
                    
                    # RIGHT: MIDI Output Section
                    with ui.card().classes('status-card q-pa-sm flex-1'):
                        ui.label('MIDI Output').classes('text-sm font-semibold text-white mb-2')
                        
                        def send_learn_cc(cc: int, name: str):
                            """Send a test CC value for MIDI learn."""
                            # Force send by clearing cached value first
                            key = (cc, 0)
                            if key in midi_manager._last_cc_values:
                                del midi_manager._last_cc_values[key]
                            midi_manager.send_cc(cc, 127)
                            ui.notify(f'Sent CC{cc} ({name}) = 127', type='info', position='bottom', timeout=1000)
                        
                        # Row 1: Intensity, Hue, Inv Hue, Color knobs
                        with ui.row().classes('w-full gap-1 items-center justify-around'):
                            # Intensity
                            with ui.column().classes('items-center gap-0'):
                                ui.label('Intensity').classes('text-grey-4 text-xs')
                                knob_int = ui.knob(0, min=0, max=127, show_value=True, size='50px') \
                                    .props('readonly color=orange thickness=0.22 track-color=grey-8 instant') \
                                    .classes('mini-knob')
                                ui.button(icon='cast', on_click=lambda: send_learn_cc(midi_manager.CC_INTENSITY, 'Intensity')) \
                                    .props('flat dense size=xs').classes('learn-btn').tooltip('Send CC16 for MIDI Learn')
                            
                            # Hue
                            with ui.column().classes('items-center gap-0'):
                                ui.label('Hue').classes('text-grey-4 text-xs')
                                knob_hue = ui.knob(0, min=0, max=127, show_value=True, size='50px') \
                                    .props('readonly color=pink thickness=0.22 track-color=grey-8 instant') \
                                    .classes('mini-knob')
                                ui.button(icon='cast', on_click=lambda: send_learn_cc(midi_manager.CC_HUE, 'Hue')) \
                                    .props('flat dense size=xs').classes('learn-btn').tooltip('Send CC17 for MIDI Learn')
                            
                            # Inv Hue (complementary color)
                            with ui.column().classes('items-center gap-0'):
                                ui.label('Inv Hue').classes('text-grey-4 text-xs')
                                knob_inv_hue = ui.knob(0, min=0, max=127, show_value=True, size='50px') \
                                    .props('readonly color=cyan thickness=0.22 track-color=grey-8 instant') \
                                    .classes('mini-knob')
                                ui.button(icon='cast', on_click=lambda: send_learn_cc(midi_manager.CC_INV_HUE, 'Inv Hue')) \
                                    .props('flat dense size=xs').classes('learn-btn').tooltip('Send CC18 for MIDI Learn')
                            
                            # Color Slider
                            with ui.column().classes('items-center gap-0'):
                                ui.label('Color').classes('text-grey-4 text-xs')
                                knob_color = ui.knob(0, min=0, max=127, show_value=True, size='50px') \
                                    .props('readonly color=deep-purple thickness=0.22 track-color=grey-8 instant') \
                                    .classes('mini-knob')
                                ui.button(icon='cast', on_click=lambda: send_learn_cc(midi_manager.CC_COLOR_SLIDER, 'Color')) \
                                    .props('flat dense size=xs').classes('learn-btn').tooltip('Send CC15 for MIDI Learn')
                        
                        # Row 2: Active Note and Hold Time
                        with ui.row().classes('w-full items-center justify-around mt-2'):
                            with ui.column().classes('items-center'):
                                ui.label('Active Note').classes('text-grey-4 text-xs')
                                lbl_note = ui.label('-').classes('text-xl font-bold text-white')
                            
                            with ui.column().classes('items-center'):
                                ui.label('Hold Time').classes('text-grey-4 text-xs')
                                lbl_hold_ms = ui.label('-').classes('text-sm font-medium text-teal-400')

                # Periodic UI Update
                def update_ui():
                    # Status Badge
                    if state.connected:
                        status_badge.text = '● Receiving'
                        status_badge.props('color=green')
                    else:
                        status_badge.text = '○ Waiting...'
                        status_badge.props('color=red')
                    
                    # DMX Bars
                    bar_r.value = state.dmx_r / 255.0
                    val_r.text = str(state.dmx_r)
                    
                    bar_g.value = state.dmx_g / 255.0
                    val_g.text = str(state.dmx_g)
                    
                    bar_b.value = state.dmx_b / 255.0
                    val_b.text = str(state.dmx_b)

                    bar_w.value = state.dmx_w / 255.0
                    val_w.text = str(state.dmx_w)

                    bar_uv.value = state.dmx_uv / 255.0
                    val_uv.text = str(state.dmx_uv)
                    
                    bar_brt.value = state.dmx_brightness / 255.0
                    val_brt.text = str(state.dmx_brightness)
                    
                    bar_str.value = state.dmx_strobe / 255.0
                    val_str.text = str(state.dmx_strobe)
                    
                    # Hold is now 16-bit milliseconds (0-10000)
                    bar_hold.value = state.dmx_hold / 10000.0
                    val_hold.text = str(state.dmx_hold)

                    # Output knobs
                    knob_int.value = state.midi_intensity
                    knob_hue.value = state.midi_hue
                    knob_inv_hue.value = state.midi_inv_hue
                    knob_color.value = state.midi_color_slider
                    
                    # Note name
                    if state.active_note is not None:
                        notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
                        octave = (state.active_note // 12) - 1
                        name = notes[state.active_note % 12]
                        lbl_note.text = f"{name}{octave}"
                    else:
                        lbl_note.text = "-"
                    
                    # Hold time display (dmx_hold is already in milliseconds)
                    if state.dmx_hold == 0:
                        lbl_hold_ms.text = "∞"
                    else:
                        hold_ms = state.dmx_hold
                        if hold_ms >= 1000:
                            lbl_hold_ms.text = f"{hold_ms/1000:.1f}s"
                        else:
                            lbl_hold_ms.text = f"{hold_ms}ms"
                    
                    # RGB Color preview - apply brightness to RGB
                    brightness_factor = state.dmx_brightness / 255.0
                    r = int(state.dmx_r * brightness_factor)
                    g = int(state.dmx_g * brightness_factor)
                    b = int(state.dmx_b * brightness_factor)
                    rgb_preview.style(f'background-color: rgb({r}, {g}, {b})')

                ui.timer(0.1, update_ui)

            # --- CONFIGURATION TAB ---
            with ui.tab_panel(config_tab).classes('q-pa-sm'):
                
                def on_save_click():
                    log_user_action("Saved Configuration")
                    ui.notify('Configuration Saved', type='positive')
                    
                    # Restart ArtNet listener with new config
                    artnet_server.stop()
                    new_universe = int(config.get("artnet_universe"))
                    artnet_server.set_universe(new_universe)
                    artnet_server.start()
                    
                    # Reopen MIDI
                    success, message = midi_manager.open_port(config.get("midi_port"))
                    if not success:
                        ui.notify(f'MIDI Error: {message}', type='negative')

                # Horizontal layout for config
                with ui.row().classes('w-full gap-3'):
                    # Art-Net Settings
                    with ui.card().classes('flex-1 q-pa-sm'):
                        ui.label('Art-Net').classes('text-sm font-semibold mb-2')
                        # Display Universe 1/2 but store as Art-Net 0/1
                        universe_options = {0: 'Universe 1', 1: 'Universe 2'}
                        ui.select(universe_options, label='Universe', value=config.get('artnet_universe'),
                                  on_change=lambda e: config.set('artnet_universe', int(e.value))) \
                            .classes('w-full compact-input')
                        ui.number('DMX Start Channel', value=config.get('dmx_start_channel'), 
                                  min=1, max=500, step=1,
                                  on_change=lambda e: config.set('dmx_start_channel', int(e.value))) \
                            .classes('w-full compact-input')
                    
                    # MIDI Settings
                    with ui.card().classes('flex-1 q-pa-sm'):
                        ui.label('MIDI').classes('text-sm font-semibold mb-2')
                        
                        # Create the port select with a reference so we can update it
                        avail_ports = midi_manager.get_available_ports(force_refresh=True)
                        current_midi_val = config.get('midi_port')
                        if current_midi_val not in avail_ports:
                            current_midi_val = None
                        
                        midi_port_select = ui.select(avail_ports, label='Output Port', value=current_midi_val,
                                  on_change=lambda e: config.set('midi_port', e.value)) \
                            .classes('w-full compact-input')
                        
                        def refresh_midi_ports():
                            """Refresh the MIDI port dropdown with current available ports."""
                            logger.info("MIDI: Refreshing port list from UI")
                            new_ports = midi_manager.get_available_ports(force_refresh=True)
                            logger.info(f"MIDI: Found ports: {new_ports}")
                            midi_port_select.options = new_ports
                            midi_port_select.update()
                            ui.notify(f'Found {len(new_ports)} MIDI ports', type='info')
                        
                        ui.button('Refresh', on_click=refresh_midi_ports, icon='refresh') \
                            .props('dense size=sm').classes('mt-1')
                        
                        ui.number('Min Intensity', value=config.get('min_intensity'), 
                                  min=0, max=127, step=1,
                                  on_change=lambda e: config.set('min_intensity', int(e.value))) \
                            .classes('w-full compact-input')
                    
                    # Processing Settings
                    with ui.card().classes('flex-1 q-pa-sm'):
                        ui.label('Processing').classes('text-sm font-semibold mb-2')
                        ui.switch('Strobe', value=config.get('strobe_enabled'),
                                  on_change=lambda e: config.set('strobe_enabled', e.value))
                        
                        # Logging level dropdown
                        def on_logging_level_change(e):
                            config.set('logging_level', e.value)
                            set_logging_level(e.value)
                        
                        log_level_options = ['INFO', 'DEBUG']
                        ui.select(log_level_options, label='Log Level', 
                                  value=config.get('logging_level') or 'INFO',
                                  on_change=on_logging_level_change) \
                            .classes('w-full compact-input mt-1')
                
                # Save & Apply button card below
                with ui.card().classes('w-full q-pa-sm'):
                    with ui.row().classes('w-full gap-2 items-center justify-center'):
                        ui.button('Save & Apply', on_click=on_save_click, icon='save') \
                            .props('color=primary dense')
                        
                        def open_settings_folder():
                            if platform.system() == "Windows":
                                os.startfile(USER_DATA_DIR)
                            elif platform.system() == "Darwin":
                                os.system(f"open '{USER_DATA_DIR}'")
                            else:
                                os.system(f"xdg-open '{USER_DATA_DIR}'")
                        
                        ui.button(icon='folder_open', on_click=open_settings_folder) \
                            .props('flat dense').tooltip('Open Settings Folder')

            # --- LOGS TAB ---
            with ui.tab_panel(logs_tab).classes('q-pa-sm'):
                with ui.row().classes('w-full items-center justify-between mb-1'):
                    ui.label(f"Log: {LOG_FILE_PATH}").classes('text-xs text-grey')
                    
                    def open_log_folder():
                        if platform.system() == "Windows":
                            os.startfile(LOG_DIR)
                        elif platform.system() == "Darwin":
                            os.system(f"open '{LOG_DIR}'")
                        else:
                            os.system(f"xdg-open '{LOG_DIR}'")
                    
                    ui.button('Open Folder', on_click=open_log_folder, icon='folder_open') \
                        .props('flat dense size=sm')
                
                log_area = ui.log().classes('w-full h-64 border rounded bg-black text-green-400 font-mono text-xs')
                
                def update_log_view():
                    if LOG_FILE_PATH.exists():
                        try:
                            with open(LOG_FILE_PATH, 'r') as f:
                                lines = f.readlines()[-30:]
                                log_area.clear()
                                for line in lines:
                                    log_area.push(line.rstrip())
                        except Exception:
                            pass
                
                ui.timer(2.0, update_log_view)
