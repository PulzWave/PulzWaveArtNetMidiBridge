"""First-time setup wizard for PulzWaveArtNetMidiBridge."""

import json
import sys
from pathlib import Path

from nicegui import ui, app

from src.config import config, logger, log_user_action, CONFIG_FILE
from src.midi_manager import midi_manager

# Load wizard texts
TEXTS_FILE = Path(__file__).parent / "texts.json"
with open(TEXTS_FILE, 'r', encoding='utf-8') as f:
    TEXTS = json.load(f)

WIZARD = TEXTS["wizard"]


def is_first_run() -> bool:
    """Check if this is the first time the application is running."""
    # Check if setup was completed (flag in config)
    return not config.get("setup_completed")


def create_setup_wizard(state, artnet_server):
    """
    Create the first-time setup wizard.
    
    Args:
        state: AppState instance
        artnet_server: ArtNetReceiver instance  
    """
    # Import here to avoid circular imports
    from src.ui import create_ui, check_loopmidi_startup
    
    # Create the main UI pages (they won't interfere with setup)
    create_ui(state, artnet_server)
    
    @ui.page('/setup')
    def setup_page():
        # On Windows, first check if LoopMIDI is running
        # This will show a dialog and block further operations if not found
        check_loopmidi_startup()
        
        # Wizard state
        wizard_state = {"step": 0}
        
        ui.colors(primary='#5898d4', secondary='#262626')
        
        # Custom CSS
        ui.add_head_html('''
        <style>
            html, body { 
                overflow: hidden !important; 
                height: 100% !important; 
                background-color: #1d1d1d !important;
            }
            .nicegui-content {
                height: 100% !important;
                background-color: #1d1d1d !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
            }
            .wizard-card {
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                border-radius: 16px;
                max-width: 550px;
                margin: 0 auto;
            }
            .step-indicator {
                width: 28px;
                height: 28px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: bold;
                font-size: 12px;
            }
            .step-active { background: #5898d4; color: white; }
            .step-done { background: #4caf50; color: white; }
            .step-pending { background: #424242; color: #888; }
            .wizard-content { min-height: 200px; }
            .pulse-dot {
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0%, 100% { opacity: 1; transform: scale(1); }
                50% { opacity: 0.5; transform: scale(1.1); }
            }
            /* Fix input text colors */
            .wizard-input .q-field__native,
            .wizard-input .q-field__control,
            .wizard-input input,
            .wizard-input .q-select__dropdown-icon { color: white !important; }
            .wizard-input .q-field__label { color: #9e9e9e !important; }
            /* Fix expansion panel text */
            .wizard-expansion .q-item__label,
            .wizard-expansion .q-expansion-item__content { color: white !important; }
        </style>
        ''')
        
        # Main container
        with ui.column().classes('w-full h-full items-center justify-center p-2 bg-dark'):
            
            # Wizard card
            with ui.card().classes('wizard-card w-full q-pa-md'):
                
                # Step indicators
                with ui.row().classes('w-full justify-center gap-3 mb-4') as step_row:
                    step_indicators = []
                    for i in range(3):
                        with ui.element('div').classes('step-indicator step-pending') as indicator:
                            ui.label(str(i + 1))
                        step_indicators.append(indicator)
                
                # Content area
                content_container = ui.column().classes('wizard-content w-full')
                
                # Navigation buttons
                with ui.row().classes('w-full justify-between mt-4') as nav_row:
                    back_btn = ui.button(WIZARD["navigation"]["back"], icon='arrow_back') \
                        .props('flat')
                    next_btn = ui.button(WIZARD["navigation"]["next"], icon='arrow_forward') \
                        .props('color=primary icon-right')
                
                # Wizard data storage
                wizard_data = {
                    "universe": config.get("artnet_universe"),
                    "start_channel": config.get("dmx_start_channel"),
                    "midi_port": config.get("midi_port"),
                    "min_intensity": config.get("min_intensity"),
                }
                
                def update_step_indicators():
                    for i, indicator in enumerate(step_indicators):
                        indicator.classes(remove='step-active step-done step-pending')
                        if i < wizard_state["step"]:
                            indicator.classes(add='step-done')
                        elif i == wizard_state["step"]:
                            indicator.classes(add='step-active')
                        else:
                            indicator.classes(add='step-pending')
                
                def render_welcome():
                    content_container.clear()
                    with content_container:
                        ui.label(WIZARD["welcome"]["title"]).classes('text-2xl font-bold text-white mb-2')
                        ui.label(WIZARD["welcome"]["subtitle"]).classes('text-lg text-grey-4 mb-4')
                        ui.label(WIZARD["welcome"]["description"]).classes('text-grey-5 mb-6')
                        
                        with ui.row().classes('gap-4 mt-4'):
                            ui.icon('lightbulb', size='xl', color='amber')
                            ui.icon('arrow_forward', size='lg', color='grey')
                            ui.icon('music_note', size='xl', color='green')
                    
                    back_btn.set_visibility(False)
                    next_btn.text = WIZARD["welcome"]["start_button"]
                    next_btn.props(remove='icon-right')
                    next_btn.props(add='icon=rocket_launch icon-right')
                
                def render_dmx_step():
                    content_container.clear()
                    texts = WIZARD["steps"]["dmx"]
                    
                    with content_container:
                        ui.label(texts["subtitle"]).classes('text-xs text-grey-5 mb-1')
                        ui.label(texts["title"]).classes('text-xl font-bold text-white mb-2')
                        ui.label(texts["description"]).classes('text-grey-5 mb-6')
                        
                        # Universe dropdown (display 1/2, store as Art-Net 0/1)
                        ui.label(texts["universe_label"]).classes('text-sm font-medium text-white mb-1')
                        ui.label(texts["universe_help"]).classes('text-xs text-grey-5 mb-2')
                        universe_options = {0: "Universe 1", 1: "Universe 2"}
                        universe_select = ui.select(universe_options, value=wizard_data["universe"], label='Select Universe') \
                            .classes('w-full mb-4 wizard-input')
                        universe_select.on('update:model-value', lambda e: wizard_data.update({"universe": int(e.args)}))
                        
                        # Start channel input
                        ui.label(texts["channel_label"]).classes('text-sm font-medium text-white mb-1')
                        ui.label(texts["channel_help"]).classes('text-xs text-grey-5 mb-2')
                        channel_input = ui.number(value=wizard_data["start_channel"], min=1, max=504, step=1) \
                            .classes('w-full wizard-input')
                        channel_input.on('update:model-value', lambda e: wizard_data.update({"start_channel": int(e.args)}))
                    
                    back_btn.set_visibility(True)
                    next_btn.text = WIZARD["navigation"]["next"]
                    next_btn.props(remove='icon=rocket_launch')
                    next_btn.props(add='icon=arrow_forward icon-right')
                
                def render_midi_step():
                    content_container.clear()
                    texts = WIZARD["steps"]["midi"]
                    
                    with content_container:
                        ui.label(texts["subtitle"]).classes('text-xs text-grey-5 mb-1')
                        ui.label(texts["title"]).classes('text-xl font-bold text-white mb-2')
                        ui.label(texts["description"]).classes('text-grey-5 mb-6')
                        
                        # MIDI port selection
                        ui.label(texts["port_label"]).classes('text-sm font-medium text-white mb-1')
                        ui.label(texts["port_help"]).classes('text-xs text-grey-5 mb-2')
                        
                        avail_ports = midi_manager.get_available_ports()
                        if not avail_ports:
                            with ui.row().classes('items-center gap-2 p-3 bg-red-900 rounded mb-4'):
                                ui.icon('warning', color='yellow')
                                ui.label(texts["no_ports_warning"]).classes('text-sm text-yellow-300')
                        
                        current_val = wizard_data["midi_port"] if wizard_data["midi_port"] in avail_ports else None
                        port_select = ui.select(avail_ports, value=current_val, label='Select MIDI Port') \
                            .classes('w-full mb-4 wizard-input')
                        port_select.on('update:model-value', lambda e: wizard_data.update({"midi_port": e.args}))
                        
                        # Min intensity
                        ui.label(texts["intensity_label"]).classes('text-sm font-medium text-white mb-1')
                        ui.label(texts["intensity_help"]).classes('text-xs text-grey-5 mb-2')
                        intensity_input = ui.number(value=wizard_data["min_intensity"], min=0, max=127, step=1) \
                            .classes('w-full wizard-input')
                        intensity_input.on('update:model-value', lambda e: wizard_data.update({"min_intensity": int(e.args)}))
                    
                    back_btn.set_visibility(True)
                    next_btn.text = WIZARD["navigation"]["next"]
                
                def render_connection_step():
                    content_container.clear()
                    texts = WIZARD["steps"]["connection"]
                    
                    # Apply settings temporarily for test
                    config.set("artnet_universe", wizard_data["universe"])
                    config.set("dmx_start_channel", wizard_data["start_channel"])
                    artnet_server.set_universe(wizard_data["universe"])
                    if not artnet_server.running:
                        artnet_server.start()
                    
                    with content_container:
                        ui.label(texts["subtitle"]).classes('text-xs text-grey-5 mb-1')
                        ui.label(texts["title"]).classes('text-xl font-bold text-white mb-2')
                        ui.label(texts["description"]).classes('text-grey-5 mb-4')
                        
                        # Connection status card
                        with ui.card().classes('w-full p-4 bg-dark'):
                            with ui.row().classes('items-center gap-3') as status_row:
                                status_icon = ui.icon('radio_button_unchecked', size='xl', color='orange') \
                                    .classes('pulse-dot')
                                with ui.column().classes('gap-0'):
                                    status_title = ui.label(texts["waiting_title"]).classes('font-medium text-white')
                                    status_desc = ui.label(texts["waiting_description"]).classes('text-xs text-grey-5')
                        
                        # Troubleshooting section
                        with ui.expansion(texts["troubleshooting"]["title"], icon='help_outline').classes('w-full mt-4 wizard-expansion'):
                            with ui.column().classes('gap-1'):
                                for tip in texts["troubleshooting"]["tips"]:
                                    with ui.row().classes('items-start gap-2'):
                                        ui.icon('chevron_right', size='xs', color='grey')
                                        ui.label(tip).classes('text-xs text-white')
                        
                        # Skip link
                        ui.button(texts["skip_text"], on_click=lambda: finish_setup()) \
                            .props('flat dense').classes('mt-4 text-grey-5')
                        
                        # Update status based on connection
                        def check_connection():
                            if state.connected:
                                status_icon.props(remove='color=orange')
                                status_icon.props(add='color=green')
                                status_icon.classes(remove='pulse-dot')
                                status_icon.name = 'check_circle'
                                status_title.text = texts["success_title"]
                                status_desc.text = texts["success_description"]
                        
                        ui.timer(0.5, check_connection)
                    
                    back_btn.set_visibility(True)
                    next_btn.text = WIZARD["navigation"]["finish"]
                    next_btn.props(remove='icon=arrow_forward')
                    next_btn.props(add='icon=check icon-right')
                
                def finish_setup():
                    """Save configuration and show completion screen."""
                    # Extract midi_port - handle both string and dict formats
                    midi_port = wizard_data["midi_port"]
                    if isinstance(midi_port, dict):
                        midi_port = midi_port.get('label', midi_port.get('value', ''))
                    
                    # Save all wizard data
                    config.set("artnet_universe", int(wizard_data["universe"]))
                    config.set("dmx_start_channel", int(wizard_data["start_channel"]))
                    config.set("midi_port", midi_port or "")
                    config.set("min_intensity", int(wizard_data["min_intensity"]))
                    config.set("setup_completed", True)  # Mark setup as complete
                    
                    log_user_action("Completed first-time setup wizard")
                    logger.info(f"Setup complete with config: universe={wizard_data['universe']}, start_channel={wizard_data['start_channel']}, midi_port={midi_port}")
                    
                    # Force save
                    config.save()
                    
                    # Show completion screen with lock
                    content_container.clear()
                    
                    with content_container:
                        # Success message
                        ui.label('Setup Complete!').classes('text-2xl font-bold text-green-400 mb-4')
                        ui.label('Your configuration has been saved.').classes('text-lg text-white mb-6')
                        
                        # Instructions
                        with ui.card().classes('w-full p-4 bg-blue-900 rounded mb-6'):
                            ui.label('Please close this window and restart this app to load your configuration.').classes('text-white font-medium')
                        
                        # Close button
                        ui.button('Close', on_click=lambda: app.shutdown()) \
                            .props('color=primary size=lg') \
                            .classes('w-full')
                    
                    # Disable navigation
                    back_btn.set_visibility(False)
                    next_btn.set_visibility(False)
                    
                    # Disable all step indicators
                    for indicator in step_indicators:
                        indicator.enabled = False
                
                def go_next():
                    if wizard_state["step"] < 3:
                        wizard_state["step"] += 1
                        render_current_step()
                    else:
                        finish_setup()
                
                def go_back():
                    if wizard_state["step"] > 0:
                        wizard_state["step"] -= 1
                        render_current_step()
                
                def render_current_step():
                    update_step_indicators()
                    step = wizard_state["step"]
                    if step == 0:
                        render_welcome()
                    elif step == 1:
                        render_dmx_step()
                    elif step == 2:
                        render_midi_step()
                    elif step == 3:
                        render_connection_step()
                
                # Wire up navigation
                back_btn.on('click', go_back)
                next_btn.on('click', go_next)
                
                # Initial render
                render_current_step()
    
    # Redirect root to setup for first-time users
    @ui.page('/welcome')
    def welcome_redirect():
        ui.navigate.to('/setup')
