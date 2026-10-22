"""Entry point for DetailsBench."""

import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# the administrator helper of a run of tests (app/frames/admin_helper.py): no window, no database
if len(sys.argv) > 1 and sys.argv[1] == "--admin-helper":
    from app.frames.admin_helper import helper_main
    sys.exit(helper_main(sys.argv[2:]))

# a command whose text goes only into its console (app/automation/console_capture.py): no window, no database
if len(sys.argv) > 1 and sys.argv[1] == "--console-capture":
    from app.automation.console_capture import helper_main
    sys.exit(helper_main(sys.argv[2:]))

# the password window sudo starts for an install on Linux without pkexec (app/install/linux_admin.py)
if len(sys.argv) > 1 and sys.argv[1] == "--askpass":
    from app.install.linux_admin import askpass_main
    sys.exit(askpass_main(sys.argv[2:]))


def main():
    """Main entry point."""
    from app.quiet_children import hide_console_windows
    hide_console_windows()          # no black windows from the scan's commands (app/quiet_children.py)
    from app.system_certs import use_system_certificates
    use_system_certificates()       # downloads check certificates the way Windows does (app/system_certs.py)
    # Import system information first to populate global data
    from app.system_info import get_system_summary
    
    # Get system info
    system_info = get_system_summary()
    
    # Initialize database (per-user data folder, see app/paths.py)
    from app.database import Database
    from app.paths import resolve_database_path

    db_path, db_notes = resolve_database_path()
    db = Database(str(db_path))

    print("DetailsBench v0.1.0")
    print("=" * 50)
    print(f"Database: {db_path}")
    for note in db_notes:
        print(f"  {note}")
    print(f"CPU: {system_info.get('cpu_brand', 'Unknown')}")
    print(f"Cores: {system_info.get('core_count', 'N/A')}")
    print(f"RAM: {system_info.get('ram_size_gb', 'N/A')} GB")
    print(f"GPU: {system_info.get('gpu_name', 'N/A')}")
    print("=" * 50)
    
    # Create and show main window
    from app.ui.main_window import MainWindow
    from PySide6.QtWidgets import QApplication
    
    try:
        qt_app = QApplication.instance()
        if qt_app is None:
            qt_app = QApplication(sys.argv)
            
        from app.ui import theme
        theme.apply(theme.saved())          # Light / Dark / System, as last chosen
        window = MainWindow(db, system_info)
        window.show()           # on every system (it was Windows only: Linux ran with no window)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(8000, window.update_flow.at_start)   # a newer version? (at most once a day, can be off)

        qt_app.exec()
    except Exception as e:
        print(f"Error starting application: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
