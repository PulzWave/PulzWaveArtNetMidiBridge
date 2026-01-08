"""MIDI output management for PulzWaveArtNetMidiBridge with cross-platform support."""

import platform
import re
import mido

from src.config import logger

class MidiManager:
    """
    Handles MIDI output with cross-platform support.
    Supports virtual ports on macOS and physical ports on Windows.
    """
    
    # MIDI CC Mappings
    CC_RED = 10
    CC_GREEN = 11
    CC_BLUE = 12
    CC_WHITE = 13
    CC_UV = 14
    CC_COLOR_SLIDER = 15  # Combined RGB to single CC value
    CC_INTENSITY = 16
    CC_HUE = 17
    CC_INV_HUE = 18
    
    # Blackout settings (MIDI Channel 2, Note 0)
    BLACKOUT_NOTE = 0
    BLACKOUT_CHANNEL = 1  # 0-indexed, so channel 2
    BLACKOUT_VELOCITY = 100
    
    VIRTUAL_PORT_NAME = "PulzWaveArtNetMidiBridge Virtual"
    VIRTUAL_PORT_OPTION = "Create Virtual Port"
    
    def __init__(self):
        self.port = None
        self.port_name = None  # The actual full port name (with instance number)
        self.port_base_name = None  # The clean base name (without instance number)
        self._is_mac = platform.system() == 'Darwin'
        self._is_windows = platform.system() == 'Windows'
        
        # Strobe internal state
        self._strobe_state = False
        self._last_strobe_toggle = 0.0
        
        # Track last sent CC values to avoid spamming
        self._last_cc_values = {}

    @property
    def is_mac(self) -> bool:
        """Check if running on macOS."""
        return self._is_mac

    def _strip_windows_port_number(self, port_name: str) -> str:
        """
        Strip the trailing instance number from Windows MIDI port names.
        e.g., "loopMIDI Port 2" -> "loopMIDI Port"
             "Microsoft GS Wavetable Synth 0" -> "Microsoft GS Wavetable Synth"
             "MIDIOUT2 (ESI MIDIMATE eX) 4" -> "MIDIOUT2 (ESI MIDIMATE eX)"
        
        On non-Windows platforms, returns the port name unchanged.
        """
        if not self._is_windows:
            return port_name
        
        # Match trailing space + number at end of string
        # This handles names like "loopMIDI Port 2" and "Device Name 10"
        return re.sub(r'\s+\d+$', '', port_name)
    
    def _get_raw_ports(self, force_refresh: bool = False) -> list:
        """
        Get raw list of available MIDI output ports (with instance numbers on Windows).
        
        Args:
            force_refresh: If True, forces a refresh of the port list
        """
        try:
            if force_refresh:
                import rtmidi
                temp_out = rtmidi.MidiOut()
                ports = temp_out.get_ports()
                temp_out.delete()
                del temp_out
                logger.debug(f"MIDI: Forced port refresh, found: {ports}")
            else:
                ports = list(mido.get_output_names())
        except ImportError:
            logger.debug("MIDI: rtmidi not available for force refresh, using mido")
            ports = list(mido.get_output_names())
        except AttributeError:
            logger.debug("MIDI: rtmidi delete() not available, falling back to mido")
            ports = list(mido.get_output_names())
        except Exception as e:
            logger.error(f"MIDI: Error getting available ports: {e}")
            ports = []
        
        return ports

    def get_available_ports(self, force_refresh: bool = False) -> list:
        """
        Get list of available MIDI output ports with clean names.
        On Windows, strips the instance numbers for a cleaner display.
        On macOS, includes option to create virtual port.
        
        Args:
            force_refresh: If True, forces a refresh of the port list by
                           creating a new backend instance (Windows/rtmidi workaround)
        """
        raw_ports = self._get_raw_ports(force_refresh)
        
        # On Windows, strip instance numbers and deduplicate
        if self._is_windows:
            # Create a dict to map clean names to raw names (keep first occurrence)
            seen = {}
            for raw_name in raw_ports:
                clean_name = self._strip_windows_port_number(raw_name)
                if clean_name not in seen:
                    seen[clean_name] = raw_name
            ports = list(seen.keys())
            logger.debug(f"MIDI: Clean port names: {ports}")
        else:
            ports = raw_ports
        
        # Add virtual port option on macOS
        if self._is_mac:
            ports.insert(0, self.VIRTUAL_PORT_OPTION)
        
        return ports
    
    def _find_matching_port(self, base_name: str) -> str | None:
        """
        Find the actual port name that matches the given base name.
        On Windows, this finds the port with instance number that matches the base name.
        
        Args:
            base_name: The clean port name (without instance number on Windows)
            
        Returns:
            The actual port name to use with mido, or None if not found
        """
        raw_ports = self._get_raw_ports(force_refresh=True)
        
        if self._is_windows:
            # Find the first port whose base name matches
            for raw_name in raw_ports:
                if self._strip_windows_port_number(raw_name) == base_name:
                    logger.debug(f"MIDI: Matched base name '{base_name}' to port '{raw_name}'")
                    return raw_name
            return None
        else:
            # On other platforms, exact match
            return base_name if base_name in raw_ports else None

    def open_port(self, port_name: str) -> tuple[bool, str]:
        """
        Open a MIDI output port.
        
        Args:
            port_name: Name of the port to open (base name without instance number on Windows),
                       or "Create Virtual Port" on macOS
            
        Returns:
            Tuple of (success: bool, message: str)
            - (True, "Port opened successfully")
            - (False, "Port not found") 
            - (False, "Port in use")
            - (False, error message)
        """
        # Avoid re-opening same port (compare base names)
        if self.port_base_name == port_name and self.port:
            return True, "Port already open"

        self.close_port()
        
        if not port_name:
            return False, "No port specified"
        
        try:
            if self._is_mac and port_name == self.VIRTUAL_PORT_OPTION:
                # Create virtual port on macOS
                self.port = mido.open_output(self.VIRTUAL_PORT_NAME, virtual=True)
                self.port_name = self.VIRTUAL_PORT_NAME
                self.port_base_name = self.VIRTUAL_PORT_NAME
            else:
                # Find the actual port name (with instance number on Windows)
                actual_port_name = self._find_matching_port(port_name)
                
                if actual_port_name is None:
                    logger.error(f"MIDI Error: No port matching '{port_name}' found")
                    return False, f"Port not found: {port_name}"
                
                # Open physical port with the actual name
                self.port = mido.open_output(actual_port_name)
                self.port_name = actual_port_name  # Store the actual full name
                self.port_base_name = port_name    # Store the clean base name
            
            logger.info(f"MIDI Port opened: {self.port_name} (base: {self.port_base_name})")
            return True, "MIDI port opened successfully"
        except OSError as e:
            error_msg = str(e).lower()
            self.port = None
            self.port_name = None
            self.port_base_name = None
            
            # Check if port not found or in use
            if "not found" in error_msg or "unknown port" in error_msg:
                logger.error(f"MIDI Error: Port not found: {port_name}")
                return False, f"Port not found: {port_name}"
            elif "in use" in error_msg or "access denied" in error_msg:
                logger.error(f"MIDI Error: Port in use: {port_name}")
                return False, f"Port in use by another application: {port_name}"
            else:
                logger.error(f"MIDI Error: {e}")
                return False, str(e)
        except Exception as e:
            logger.error(f"MIDI Error: {e}")
            self.port = None
            self.port_name = None
            self.port_base_name = None
            return False, str(e)

    def close_port(self):
        """Close the current MIDI port."""
        if self.port:
            try:
                self.port.close()
            except Exception:
                pass
            self.port = None
            self.port_name = None
            self.port_base_name = None
        # Clear last sent values so they get re-sent when port reopens
        self._last_cc_values.clear()

    def send_cc(self, cc: int, value: int, channel: int = 0):
        """
        Send a MIDI Control Change message, only if value has changed.
        
        Args:
            cc: Control Change number (0-127)
            value: Value to send (0-127, will be clamped)
            channel: MIDI channel (0-15)
        """
        if not self.port:
            return
        
        # Clamp value to valid MIDI range
        val = max(0, min(127, int(value)))
        
        # Only send if value changed (track by cc+channel combo)
        key = (cc, channel)
        if self._last_cc_values.get(key) == val:
            return  # Value unchanged, don't spam
        
        self._last_cc_values[key] = val
        msg = mido.Message('control_change', control=cc, value=val, channel=channel)
        self.port.send(msg)

    def send_note_on(self, note: int, velocity: int = 100, channel: int = 0):
        """
        Send a MIDI Note On message.
        
        Args:
            note: MIDI note number (0-127)
            velocity: Note velocity (0-127)
            channel: MIDI channel (0-15)
        """
        if not self.port:
            return
        
        note = max(0, min(127, int(note)))
        velocity = max(0, min(127, int(velocity)))
        msg = mido.Message('note_on', note=note, velocity=velocity, channel=channel)
        self.port.send(msg)

    def send_note_off(self, note: int, channel: int = 0):
        """
        Send a MIDI Note Off message.
        
        Args:
            note: MIDI note number (0-127)
            channel: MIDI channel (0-15)
        """
        if not self.port:
            return
        
        note = max(0, min(127, int(note)))
        msg = mido.Message('note_off', note=note, channel=channel)
        self.port.send(msg)

    def send_blackout_note(self):
        """
        Send a blackout trigger note.
        Used when DMX cue channel goes to 0 (blackout).
        Sends a short note pulse on MIDI Channel 2, Note 0.
        """
        if not self.port:
            return
        
        # Send note on and immediately note off
        self.send_note_on(self.BLACKOUT_NOTE, self.BLACKOUT_VELOCITY, self.BLACKOUT_CHANNEL)
        # Note: In practice, the receiving software should handle this as a trigger
        # We send note off after a very short time in the async loop if needed
        logger.info("Blackout note triggered")


# Global MIDI manager instance
midi_manager = MidiManager()
