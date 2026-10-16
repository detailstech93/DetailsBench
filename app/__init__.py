"""PC Benchmark Manager - Phase 1 Implementation."""

__version__ = "0.1.0"
__author__ = "Arhitectura"


def __getattr__(name):
    """Lazy import of modules to avoid circular dependencies."""
    if name == "database":
        from app.database import Database
        
        return database
    elif name == "system_info":
        from app.system_info import SystemModel
        
        return system_info
    elif name == "ui":
        from app.ui.main_window import MainWindow
        
        return ui

    raise AttributeError(f"Module '{name}' has no attribute '{name}'")