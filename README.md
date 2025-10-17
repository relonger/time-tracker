ttracker — simple hierarchical time tracker for Ubuntu 22.04

Requirements
- Python 3.11
- GTK 3 (PyGObject), AppIndicator3, Keybinder3 available via Ubuntu packages
- Virtualenv + requirements.txt

System packages you will likely need (Ubuntu 22.04):
sudo apt install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-keybinder-3.0 libnotify-bin libgirepository1.0-dev gobject-introspection

For tray icon support use one of the following (depending on availability):
Classic AppIndicator
```
sudo apt install -y gir1.2-appindicator3-0.1
```

OR Ayatana AppIndicator (preferred on Ubuntu 22.04+)
```
sudo apt install -y gir1.2-ayatanaappindicator3-0.1
```

Ensure the GNOME AppIndicator extension is enabled (usually enabled by default).

Create venv and install
```
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Run
```
python main.py
```

Data and settings
- Data: ~/.ttracker/data.yaml (auto-saved on start/stop and every 30 minutes). Backup at ~/.ttracker/data-backup.yaml.
- Settings: ~/.ttracker/settings.yaml (global show UI hotkey and per-task hotkeys).

Notes
- Global hotkeys use Keybinder3. On Wayland sessions global hotkeys may be limited. If your desktop uses Wayland, consider logging into the “Ubuntu on Xorg” session for full global hotkey support.
- PyGObject is pinned in requirements (>=3.42,<3.43) for Ubuntu 22.04 compatibility. If you are on a newer distro with GIRepository 2.0 (gobject-introspection 2.0), you may raise/adjust the pin accordingly.
 

Desktop launcher and autostart (GNOME)
To create a GNOME menu entry (launcher) and enable autostart on login, run the installer script from the project root:
```
python install_desktop.py
```
Options:
```
# Do not enable autostart
python install_desktop.py --no-autostart

# Create only autostart entry (no Applications launcher)
python install_desktop.py --only-autostart

# Use a specific Python interpreter (if you don’t use .venv)
python install_desktop.py --python /path/to/python

# Uninstall entries
python install_desktop.py --uninstall-autostart
python install_desktop.py --uninstall-launcher
```
Notes:
- The script prefers ./.venv/bin/python if it exists; otherwise it uses the current Python.
- If you move the project folder or recreate the virtualenv, re-run the installer to refresh the paths.
- The launcher uses the themed icon name "appointment-new". You can pass a custom icon path with `--icon /path/to/icon.png`.
