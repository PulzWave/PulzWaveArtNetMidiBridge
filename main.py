"""
PulzWaveArtNetMidiBridge - PulzWave Art-Net to MIDI Bridge
Root launcher script.
"""

import multiprocessing

if __name__ == "__main__":
    # CRITICAL: Must be called before any other imports for PyInstaller on macOS
    # This prevents the app from spawning multiple dock icons
    multiprocessing.freeze_support()
    
    from src.main import main
    main()