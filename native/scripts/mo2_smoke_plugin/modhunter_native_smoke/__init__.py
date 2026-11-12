"""Native MO2 smoke harness; copied only into an isolated CI portable instance."""


def createPlugin():
    from .runner import NativeSmokePlugin

    return NativeSmokePlugin()
