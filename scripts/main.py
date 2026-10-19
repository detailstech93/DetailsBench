"""Entry point for PC Benchmark Manager."""

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


def main():
    """Main entry point."""
    # Import system information first to populate global data
    from app.system_info import get_system_summary
    
    # Get system info
    system_info = get_system_summary()
    
    # Initialize database (per-user data folder, see app/paths.py)
    from app.database import Database
    from app.paths import resolve_database_path

    db_path, db_notes = resolve_database_path()
    db = Database(str(db_path))

    print("PC Benchmark Manager v0.1.0")
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
            
        window = MainWindow(db, system_info)
        
        if sys.platform == "win32":
            window.show()
            
        qt_app.exec()
    except Exception as e:
        print(f"Error starting application: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
