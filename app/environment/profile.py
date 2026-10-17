"""EnvironmentProfile data model.

Every field is a ProfileField {value, source, status}:
  detected    - read from the system by a documented method
  unavailable - could not be determined (value is None)
  unverified  - a value exists but is not confirmed on real hardware/OS
The whole profile is JSON-serializable (to_dict / from_dict).
"""

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

DETECTED = "detected"
UNAVAILABLE = "unavailable"
UNVERIFIED = "unverified"
STATUSES = frozenset({DETECTED, UNAVAILABLE, UNVERIFIED})


@dataclass
class ProfileField:
    value: Any = None
    source: Optional[str] = None
    status: str = UNAVAILABLE

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"invalid status {self.status!r}")


def detected(value: Any, source: str) -> ProfileField:
    return ProfileField(value, source, DETECTED)


def unavailable(source: Optional[str] = None) -> ProfileField:
    return ProfileField(None, source, UNAVAILABLE)


def unverified(value: Any, source: str) -> ProfileField:
    return ProfileField(value, source, UNVERIFIED)


@dataclass
class EnvironmentProfile:
    os_family: ProfileField = field(default_factory=ProfileField)       # windows | linux | other
    os_name: ProfileField = field(default_factory=ProfileField)
    os_version: ProfileField = field(default_factory=ProfileField)
    os_build: ProfileField = field(default_factory=ProfileField)
    is_wsl: ProfileField = field(default_factory=ProfileField)
    is_virtual_machine: ProfileField = field(default_factory=ProfileField)
    native_arch: ProfileField = field(default_factory=ProfileField)     # real machine architecture
    process_arch: ProfileField = field(default_factory=ProfileField)    # architecture of this Python
    x64_emulated: ProfileField = field(default_factory=ProfileField)    # x64 code runs emulated (ARM64 host)
    app_is_elevated: ProfileField = field(default_factory=ProfileField)
    cpu: ProfileField = field(default_factory=ProfileField)
    ram_gb: ProfileField = field(default_factory=ProfileField)
    gpu: ProfileField = field(default_factory=ProfileField)
    directx: ProfileField = field(default_factory=ProfileField)
    vulkan: ProfileField = field(default_factory=ProfileField)
    opengl: ProfileField = field(default_factory=ProfileField)
    display_server: ProfileField = field(default_factory=ProfileField)  # Linux: x11 | wayland
    # Not ProfileFields: per-step failures and timings
    diagnostics: List[str] = field(default_factory=list)
    step_seconds: Dict[str, float] = field(default_factory=dict)

    def field_names(self) -> List[str]:
        return [f.name for f in fields(self) if f.name not in ("diagnostics", "step_seconds")]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EnvironmentProfile":
        profile = cls()
        for name in profile.field_names():
            if name in data and isinstance(data[name], dict):
                setattr(profile, name, ProfileField(**data[name]))
        profile.diagnostics = list(data.get("diagnostics", []))
        profile.step_seconds = dict(data.get("step_seconds", {}))
        return profile
