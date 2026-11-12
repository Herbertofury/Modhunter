"""MO2 Update Arsenal plugin entry point (MO2 >= 2.5)."""


def createPlugin():
    from .plugin import ModUpdateArsenal
    return ModUpdateArsenal()
