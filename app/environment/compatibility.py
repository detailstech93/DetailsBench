"""check_compatibility(definition, profile): can this suite run on this machine?

Only PROVEN mismatches make a suite incompatible (OS family not supported,
or the native architecture explicitly "unsupported"). Missing information
never rejects a suite; it yields "unverified" with a reason instead.
"""

from dataclasses import asdict, dataclass, field
from typing import List

from app.environment.profile import DETECTED, UNAVAILABLE, EnvironmentProfile

COMPATIBLE = "compatible"
INCOMPATIBLE = "incompatible"
UNVERIFIED = "unverified"
_SEVERITY = {COMPATIBLE: 0, UNVERIFIED: 1, INCOMPATIBLE: 2}


@dataclass
class CompatibilityResult:
    status: str = COMPATIBLE
    reasons: List[str] = field(default_factory=list)
    # False when results would not be comparable to native runs (emulation)
    comparable_to_native: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    def _downgrade(self, status: str, reason: str) -> None:
        """Record a reason; keep the most severe status seen so far."""
        self.reasons.append(reason)
        if _SEVERITY[status] > _SEVERITY[self.status]:
            self.status = status


def _os_families(profile: EnvironmentProfile) -> set:
    families = {profile.os_family.value}
    if profile.is_wsl.status == DETECTED and profile.is_wsl.value:
        families.add("wsl")
    return families


def check_compatibility(definition, profile: EnvironmentProfile) -> CompatibilityResult:
    result = CompatibilityResult()

    # Operating system
    if profile.os_family.status == UNAVAILABLE or profile.os_family.value in (None, "other"):
        result._downgrade(UNVERIFIED, "operating system could not be determined")
    elif not _os_families(profile) & set(definition.supported_os):
        result._downgrade(INCOMPATIBLE, (
            f"supported OS: {', '.join(definition.supported_os)}; "
            f"this system: {profile.os_family.value}"))

    # Architecture (native machine architecture)
    native = profile.native_arch.value if profile.native_arch.status != UNAVAILABLE else None
    if native in (None, "unknown"):
        result._downgrade(UNVERIFIED, "native architecture could not be determined")
    else:
        support = definition.supported_architectures.get(native, "unverified")
        if support == "unsupported":
            result._downgrade(INCOMPATIBLE, f"not supported on {native}")
        elif support == "emulated":
            result.comparable_to_native = False
            result.reasons.append(
                f"runs emulated on {native}; results are not directly comparable to native results")
        elif support != "native":
            result._downgrade(UNVERIFIED, f"support on {native} not verified")

    # Graphics API
    api = definition.required_graphics_api
    if api:
        api_field = getattr(profile, api, None)
        if api_field is None or api_field.status == UNAVAILABLE:
            result._downgrade(UNVERIFIED, f"requires {api}; not detected on this system")
        elif api_field.status != DETECTED:
            result._downgrade(UNVERIFIED, f"requires {api}; present but driver support unverified")

    return result
