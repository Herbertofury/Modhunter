"""Whether this MO2 process may dispatch an ordinary interactive native install."""
import os

PROTECTED_INSTALL_REASON = (
    "This MO2 installer accepts a changeable destination and permits post-install activation. "
    "It cannot guarantee a new, immutable target that stays disabled."
)


def protected_installs():
    return os.environ.get("MODHUNTER_PROTECTED_INSTALLS", "").strip() not in ("", "0")


def protected_refusal():
    return {"ok":False, "state":"unsupported", "code":"protected-install-unavailable",
            "nativeStarted":False, "error":PROTECTED_INSTALL_REASON}
