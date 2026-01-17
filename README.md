# Claude Usage Tracker (PyQt5)

A medium-weight desktop widget to monitor your Claude.ai message limits in real-time.

![Showcase Image](showcase.png)

## Features

- **Usage Tracking**: Monitor 5-hour and weekly usage limits
- **Auto Login**: Browser-based automatic session key retrieval
- **Clickthrough Mode**: Widget stays visible but clicks pass through
- **Compact Mode**: Minimized view showing only 5-hour usage
- **System Tray**: Minimize to tray, quick access menu
- **Customizable**: Adjust opacity, colors, poll interval, and border styles
- **API Status Indicator**: Shows connection status (green/yellow/red dot)
- **Auto Retry**: Automatic retry logic on API failures

## Getting Started

1. Install dependencies:
   ```
   pip install PyQt5 cloudscraper python-dateutil pystray pillow undetected-chromedriver
   ```

2. Run the script:
   ```
   python claude_usage_overlay_pyqt5.py
   ```

3. When prompted, click **Sign In** - a browser will open automatically for login

## Controls

| Icon | Action |
|:----:|--------|
| **👆** | Toggle clickthrough mode |
| **─** | Toggle compact mode |
| **⟲** | Manual refresh |
| **⚙** | Settings |
| **×** | Close (or minimize to tray) |

## Building .exe

To compile to a standalone executable:

```bash
pip install pyinstaller
python build_exe.py
```

The .exe will be in the `dist/` folder.

## Requirements

- Python 3.8+
- Windows (uses Windows API for clickthrough)
- Chrome browser (for Auto Login feature)
