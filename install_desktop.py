#!/usr/bin/env python3
import os
import sys
import argparse
from pathlib import Path

APP_NAME = "TTracker"
APP_ID = "ttracker"
COMMENT_EN = "Hierarchical time tracker"
COMMENT_RU = "Иерархический трекер времени"
ICON_NAME = "appointment-new"  # use theme icon by default

LAUNCHER_FILENAME = f"{APP_ID}.desktop"
AUTOSTART_DIR = Path.home() / ".config" / "autostart"
APPLICATIONS_DIR = Path.home() / ".local" / "share" / "applications"


def detect_repo_root() -> Path:
    # repo root is the directory containing this script
    return Path(__file__).resolve().parent


def default_python(repo_root: Path) -> Path:
    # Prefer local .venv/bin/python
    venv_py = repo_root / ".venv" / "bin" / "python"
    if venv_py.exists():
        return venv_py
    # Fallback to current interpreter
    return Path(sys.executable)


def build_exec(python_path: Path, repo_root: Path) -> str:
    main_py = repo_root / "main.py"
    return f"{python_path} {main_py}"


def make_desktop_entry(exec_cmd: str, icon: str = ICON_NAME, name: str = APP_NAME, comment: str = COMMENT_RU) -> str:
    # NOTE: we avoid quoting; Exec field accepts spaces separated args
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        f"Comment={comment}\n"
        f"Exec={exec_cmd}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Utility;Office;\n"
        f"StartupWMClass={APP_NAME}\n"
        "X-GNOME-UsesNotifications=true\n"
    )


def install_launcher(app_dir: Path, content: str) -> Path:
    app_dir.mkdir(parents=True, exist_ok=True)
    target = app_dir / LAUNCHER_FILENAME
    target.write_text(content, encoding="utf-8")
    # Make it executable per desktop spec recommendations (not strictly required on GNOME)
    try:
        target.chmod(0o755)
    except Exception:
        pass
    return target


def uninstall(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Install GNOME launcher and autostart entries for TTracker")
    p.add_argument("--python", dest="python", metavar="PATH", help="Path to Python interpreter (defaults to .venv/bin/python or current interpreter)")
    p.add_argument("--name", dest="name", default=APP_NAME, help="Application name visible in menu")
    p.add_argument("--icon", dest="icon", default=ICON_NAME, help="Icon name or absolute path to icon file")
    p.add_argument("--no-autostart", action="store_true", help="Do not create autostart entry")
    p.add_argument("--only-autostart", action="store_true", help="Create only autostart entry (no launcher)")
    p.add_argument("--uninstall-autostart", action="store_true", help="Remove autostart entry and exit")
    p.add_argument("--uninstall-launcher", action="store_true", help="Remove app launcher entry and exit")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = detect_repo_root()

    if args.uninstall_autostart:
        uninstall(AUTOSTART_DIR / LAUNCHER_FILENAME)
        print(f"Removed autostart: {AUTOSTART_DIR / LAUNCHER_FILENAME}")
        return 0
    if args.uninstall_launcher:
        uninstall(APPLICATIONS_DIR / LAUNCHER_FILENAME)
        print(f"Removed launcher: {APPLICATIONS_DIR / LAUNCHER_FILENAME}")
        return 0

    py_path = Path(args.python) if args.python else default_python(repo_root)
    if not py_path.exists():
        print(f"ERROR: Python interpreter not found at {py_path}", file=sys.stderr)
        return 2

    exec_cmd = build_exec(py_path, repo_root)
    desktop_text = make_desktop_entry(exec_cmd, icon=args.icon, name=args.name)

    created = []
    if not args.only_autostart:
        launcher = install_launcher(APPLICATIONS_DIR, desktop_text)
        created.append(str(launcher))
    if not args.no_autostart:
        # Autostart entry may include autostart-specific keys
        autostart_text = desktop_text + "X-GNOME-Autostart-enabled=true\nOnlyShowIn=GNOME;Unity;\n"
        auto = install_launcher(AUTOSTART_DIR, autostart_text)
        created.append(str(auto))

    print("Installed:")
    for p in created:
        print("  ", p)
    if not args.only_autostart:
        print("\nYou can now find the app in your desktop menu as:", args.name)
    if not args.no_autostart:
        print("Autostart is enabled — the app will start automatically after login.")
    print("\nTips:")
    print("- To change the global hotkey for showing the window, edit ~/.ttracker/settings.yaml (app_hotkey).")
    print("- If you move the project or recreate the venv, re-run this installer to update Exec path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
