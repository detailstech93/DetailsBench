"""A Windows question over a game (the Firewall's "allow networks to access this app?"), shown on the phone page.

Owner, NucBox K10 2026-11-13: it came over RDR2 the first time the game ran, while the run was started from the
phone with nobody at the PC. The page shows its words and buttons; the owner's tap is the answer, never the app's.
"""
import threading
from typing import List, Optional

_lock = threading.Lock()
_asked: Optional[dict] = None
_answer: Optional[str] = None


def ask(text: str, choices: List[str]) -> None:
    global _asked, _answer
    with _lock:
        _asked, _answer = {"text": text, "choices": list(choices)}, None


def asked() -> Optional[dict]:
    with _lock:
        return dict(_asked, choices=list(_asked["choices"])) if _asked else None


def answer(choice: str) -> bool:
    """The owner's tap: True when it is one of the question's buttons."""
    global _answer
    with _lock:
        if not _asked or choice not in _asked["choices"]:
            return False
        _answer = choice
        return True


def take() -> Optional[str]:
    global _answer
    with _lock:
        out, _answer = _answer, None
        return out


def clear() -> None:
    global _asked, _answer
    with _lock:
        _asked, _answer = None, None
