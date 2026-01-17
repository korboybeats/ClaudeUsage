"""Build script to create .exe with orange circle icon"""
from PIL import Image, ImageDraw
import os
import subprocess
import sys
import glob

def find_python():
    """Find Python 3.12 or 3.13 (recommended for PyInstaller compatibility)"""
    # Common Python install locations on Windows
    search_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python31*\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe"),
        r"C:\Python31*\python.exe",
        r"C:\Python312\python.exe",
        r"C:\Python313\python.exe",
        r"C:\Program Files\Python31*\python.exe",
        r"C:\Program Files\Python312\python.exe",
        r"C:\Program Files\Python313\python.exe",
    ]

    for pattern in search_paths:
        matches = glob.glob(pattern)
        for match in sorted(matches, reverse=True):  # Prefer newer versions
            if os.path.exists(match):
                # Skip Python 3.14+ (SSL issues with PyInstaller)
                version_check = subprocess.run([match, '--version'], capture_output=True, text=True)
                version = version_check.stdout.strip()
                if '3.12' in version or '3.13' in version:
                    print(f"Found compatible Python: {version} at {match}")
                    return match

    # Fallback to current Python (may have issues if 3.14+)
    print(f"Warning: Using current Python {sys.version.split()[0]} - may have SSL issues if 3.14+")
    return sys.executable

def get_ssl_dlls(python_path):
    """Get SSL DLL paths from Python installation"""
    python_dir = os.path.dirname(python_path)
    dlls_dir = os.path.join(python_dir, 'DLLs')

    ssl_files = ['libssl-3.dll', 'libcrypto-3.dll', '_ssl.pyd']
    found_dlls = []

    for dll in ssl_files:
        dll_path = os.path.join(dlls_dir, dll)
        if os.path.exists(dll_path):
            found_dlls.append(('--add-binary', f'{dll_path};.'))

    return found_dlls

# Find Python
PYTHON = find_python()

# Create icon
print("\nCreating icon...")
size = 256
img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
draw.ellipse([10, 10, size-10, size-10], fill='#CC785C')

# Save as .ico
icon_path = 'app_icon.ico'
img.save(icon_path, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print(f"Icon saved to {icon_path}")

# Build command
print("\nBuilding .exe...")
cmd = [
    PYTHON, '-m', 'PyInstaller',
    '--onefile',
    '--windowed',
    '--name', 'ClaudeUsage',
    '--icon', icon_path,
    # Exclude unused Qt plugins
    '--exclude-module', 'PyQt5.QtBluetooth',
    '--exclude-module', 'PyQt5.QtDBus',
    '--exclude-module', 'PyQt5.QtDesigner',
    '--exclude-module', 'PyQt5.QtHelp',
    '--exclude-module', 'PyQt5.QtLocation',
    '--exclude-module', 'PyQt5.QtMultimedia',
    '--exclude-module', 'PyQt5.QtMultimediaWidgets',
    '--exclude-module', 'PyQt5.QtNetwork',
    '--exclude-module', 'PyQt5.QtNfc',
    '--exclude-module', 'PyQt5.QtOpenGL',
    '--exclude-module', 'PyQt5.QtPositioning',
    '--exclude-module', 'PyQt5.QtPrintSupport',
    '--exclude-module', 'PyQt5.QtQml',
    '--exclude-module', 'PyQt5.QtQuick',
    '--exclude-module', 'PyQt5.QtQuickWidgets',
    '--exclude-module', 'PyQt5.QtRemoteObjects',
    '--exclude-module', 'PyQt5.QtSensors',
    '--exclude-module', 'PyQt5.QtSerialPort',
    '--exclude-module', 'PyQt5.QtSql',
    '--exclude-module', 'PyQt5.QtSvg',
    '--exclude-module', 'PyQt5.QtTest',
    '--exclude-module', 'PyQt5.QtWebChannel',
    '--exclude-module', 'PyQt5.QtWebEngine',
    '--exclude-module', 'PyQt5.QtWebEngineCore',
    '--exclude-module', 'PyQt5.QtWebEngineWidgets',
    '--exclude-module', 'PyQt5.QtWebSockets',
    '--exclude-module', 'PyQt5.QtXml',
    '--exclude-module', 'PyQt5.QtXmlPatterns',
    # Exclude other unused modules
    '--exclude-module', 'tkinter',
    '--exclude-module', 'unittest',
    '--exclude-module', 'pydoc',
    '--exclude-module', 'doctest',
    '--exclude-module', 'numpy',
    '--exclude-module', 'pandas',
    '--exclude-module', 'matplotlib',
    '--noupx',
]

# Add SSL DLLs
for arg in get_ssl_dlls(PYTHON):
    cmd.extend(arg)

cmd.append('claude_usage_overlay_pyqt5.py')

subprocess.run(cmd, check=True)
print("\nBuild complete! .exe is in the dist/ folder")
