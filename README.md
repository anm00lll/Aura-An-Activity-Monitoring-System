# AURA — AI-based User Routine Analyzer ✨

A friendly Windows app that helps you stay focused by monitoring your active window, classifying distractions, and guiding you with a Pomodoro-style Focus Timer. It can run quietly in your system tray while you work. 🙌

## What You Get 🚀
- Advanced activity tracking (window title, idle, screen-hash, heuristics)
- Distraction classification with configurable rules (websites/apps/keywords)
- System tray integration (pystray + Pillow) with live stats
- Focus Timer (Pomodoro) with work/break cycles and notifications
- Preferences persistence (mode, keywords, geometry)

## Requirements ✅

- Windows with Python 3.10+
- Tkinter (bundled with the standard Python installer on Windows)
- See `requirements.txt` for pip dependencies

Not sure if Python is installed? In PowerShell:

```powershell
py --version
```
If that fails, install Python from https://www.python.org/downloads/ and check “Add Python to PATH” during setup.

## Quick Start (Windows PowerShell) 🧰

```powershell
# 1) Create and activate a virtual environment
python -m venv .venv
. .venv\Scripts\Activate.ps1

# 2) Upgrade pip and install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3) Run the app (full UI)
python .\run.py
# or
python -m aura.main

# Optional: minimal UI (faster to start)
python .\run.py --minimal
# or (environment variable)
$Env:AURA_UI = "minimal"; python .\run.py

# Optional: tray-only demo
python -m aura.aura_with_tray
```

## Using AURA 🧠

### Sessions ▶️
- Session → Start Session: starts tracking (basic or advanced depending on Settings)
- Session → Stop Session: stops tracking and shows an optional summary pie chart

### Settings ⚙️
- Set Focus Keywords: comma-separated keywords matched against active window title; if empty, everything counts as focused
- Use Advanced Tracker: toggles the multi-signal tracker (applies on next Start)
- Start Break (5 min) / Stop Break: coordinates with the Focus Timer and classifier so breaks won’t be flagged as distractions
- Edit Distraction List…: open the embedded editor to manage websites/apps/keywords (apply immediately)
- Whitelist Current App/Domain: quickly add currently seen app/domain to the whitelist

### Focus Timer (Pomodoro) 🕒
- Controls are in the “Focus Timer” panel (Start/Pause/Reset)
- Presets for 25/50 minute work sessions and 5/10 minute breaks
- When a break starts, AURA relaxes notifications and marks a break period
- When a break ends, AURA resumes normal strict tracking

### System Tray 🧷
- Window → Minimize to Tray hides the window; the app keeps running in the tray
- Left-click toggles show/hide; right-click opens the menu (Start/Stop/Stats/Exit)
- Tray tooltip and icon color reflect focus state; stats refresh periodically

### CLI Options
- `--minimal` or `-m`: launch a simpler UI (faster to start)
- Env var `AURA_UI=minimal|ttk|simple`: force minimal UI mode
- Default behavior launches the full UI defined in `aura.main`

Tip: On Windows, you can also use `py run.py`.

## Preferences & Data 💾
- Preferences: `%USERPROFILE%\.aura_prefs.json`
	- `use_advanced`, `allowed_keywords`, `window_geometry`
- Distraction rules: `%USERPROFILE%\.aura_distractions.json`
	- Manage via the Distraction List Editor (import/export JSON)

### Privacy
- AURA stores small JSON files under your user profile for preferences and distraction rules.
- No network calls are made; tracking stays on your machine.

## Project Structure 🧭
- App entry: `run.py` (chooses full or minimal UI)
- Core package: `src/aura/`
	- `main.py`: full UI entry and app wiring
	- `ui.py`: minimal/TTK UI entry
	- `aura_with_tray.py`: standalone tray demo
	- `system_tray.py`: tray icon, menu, and status
	- `activity_tracker.py` / `tracker.py`: active window + heuristics
	- `classifier.py`: focus vs distraction logic
	- `focus_timer.py`: Pomodoro cycles and notifications
	- `session.py` / `session_data.py`: session lifecycle & persistence
	- `notification.py` / `notifier.py`: toast/alerts
	- `plotter.py`: charts for summaries

See also: [SYSTEM_TRAY_STATUS.md](SYSTEM_TRAY_STATUS.md) for tray states.

### Common Words (Glossary)
- Focus: Time spent on windows/apps matching your keywords.
- Distraction: Time spent on apps/sites listed in your distraction rules.
- Session: A single period of tracking you start/stop.

## Troubleshooting 🛠️
- Tray icon doesn’t appear: ensure `pystray` and `Pillow` are installed
	```powershell
	python -m pip install pystray Pillow
	```
- Notifications don’t show: Windows notification system can vary; AURA continues even if a toast fails
- Permissions: if idle or window monitoring seems limited, run the terminal as Administrator

### FAQ ❓
- “Python not found” or `py` not recognized?
	- Install Python from https://www.python.org/downloads/ and check “Add Python to PATH”.
- Tkinter errors on launch?
	- Use the official Python installer (not the Microsoft Store variant). Reinstall if needed.
- App won’t start or crashes?
	- Ensure venv is active and run: `pip install -r requirements.txt`
- Want faster startup?
	- Use minimal UI: `python .\run.py --minimal`

## Testing 🧪

```powershell
# From the repo root after activating the venv
python -m pytest -q
```

## License & Contributions 🤝
- License: MIT Licence
- Contributions: PRs welcome. Please include a brief description, steps to reproduce, and tests when relevant.
Need help? Open an issue with your steps and screenshots (if possible). We’re happy to guide beginners. 😊

## Clean Up the Environment 🧹

```powershell
Deactivate; Remove-Item -Recurse -Force .venv
```

## Uninstall 🗑️
To remove dependencies and the virtual environment:
```powershell
Deactivate; Remove-Item -Recurse -Force .venv
```
Optionally delete your AURA preference files:
```powershell
Remove-Item -Force "$Env:USERPROFILE\.aura_prefs.json","$Env:USERPROFILE\.aura_distractions.json"
```
