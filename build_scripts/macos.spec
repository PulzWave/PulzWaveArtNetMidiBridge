# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for macOS build."""

import sys
from pathlib import Path

block_cipher = None

# Get the project root
project_root = Path(SPECPATH).parent

a = Analysis(
    [str(project_root / 'src' / 'main.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / 'src' / 'image'), 'src/image'),
        (str(project_root / 'src' / 'texts.json'), 'src'),
        (str(project_root / 'src' / 'styles.css'), 'src'),
    ],
    hiddenimports=[
        'nicegui',
        'mido',
        'mido.backends',
        'mido.backends.rtmidi',
        'rtmidi',
        'platformdirs',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PulzWaveArtNetMidiBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PulzWaveArtNetMidiBridge',
)

app = BUNDLE(
    coll,
    name='PulzWaveArtNetMidiBridge.app',
    icon=str(project_root / 'src' / 'image' / 'pulzwave_icon.icns'),
    bundle_identifier='com.pulzwave.PulzWaveArtNetMidiBridge',
    info_plist={
        'CFBundleName': 'PulzWaveArtNetMidiBridge',
        'CFBundleDisplayName': 'PulzWaveArtNetMidiBridge',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'NSMicrophoneUsageDescription': 'PulzWaveArtNetMidiBridge needs access to MIDI devices.',
    },
)
