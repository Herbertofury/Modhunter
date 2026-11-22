# Benchmark-only extension to the hash-pinned BasicGame fixture.
import os
from pathlib import Path


class BenchmarkGamePlugins(mobase.GamePlugins):
    def __init__(self, organizer):
        super().__init__()
        self.organizer = organizer
        self.root = Path(os.environ["MO2_BENCH_ROOT"]).resolve(strict=True)
        self.profile = self.root / "instance" / "profiles" / "Modhunter Smoke"
        self.size = int(os.environ["MO2_BENCH_SIZE"])
        if (self.size not in (40, 1000)
                or os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
                or (self.root / "portable.txt").read_text().strip() != "Owned CI benchmark fixture"):
            raise RuntimeError("GamePlugins is for the owned test fixture only")
        self.names = tuple(f"Bench{i:04d}.esp" for i in range(self.size))
        self.known = frozenset(self.names)
        self.order = None
        self.reading = False

    def _file(self, name):
        if Path(self.organizer.profilePath()).resolve() != self.profile.resolve(strict=True):
            raise RuntimeError("refusing plugin lists outside the exact owned profile")
        p = self.profile / name
        if p.resolve(strict=True) != p or p.stat().st_size > 32_768:
            raise RuntimeError("invalid fixture plugin-list path or size")
        return p

    def _read(self, name, starred):
        entries = [line.strip() for line in self._file(name).read_text(encoding="utf-8-sig").splitlines()
                   if line.strip() and not line.lstrip().startswith("#")]
        if starred:
            if any(not line.startswith("*") for line in entries):
                raise RuntimeError("fixture plugins.txt expects explicit active entries")
            entries = [line[1:] for line in entries]
        if not set(entries) <= self.known or len(set(entries)) != len(entries):
            raise RuntimeError("unexpected fixture plugin identity")
        return entries

    def lightPluginsAreSupported(self):
        return False

    def mediumPluginsAreSupported(self):
        return False

    def getLoadOrder(self):
        return list(self.order or self.names)

    def readPluginLists(self, plugin_list):
        order = self._read("loadorder.txt", False)
        active = set(self._read("plugins.txt", True))
        if set(order) != self.known or len(active) > 32:
            raise RuntimeError("unexpected fixture plugin capacity")
        available = set(plugin_list.pluginNames())
        self.reading = True
        try:
            plugin_list.setLoadOrder([name for name in order if name in available])
            for name in order:
                if name in available:
                    state = mobase.PluginState.ACTIVE if name in active else mobase.PluginState.INACTIVE
                    if plugin_list.state(name) != state:
                        plugin_list.setState(name, state)
            self.order = tuple(order)
        finally:
            self.reading = False

    def writePluginLists(self, plugin_list):
        if self.order is None or self.reading:
            return  # The native feature must not persist an uninitialized list.
        available = set(plugin_list.pluginNames())
        if not self.known <= available:
            return  # A partial transient UI scan must not erase fixture state.
        order = sorted(self.names, key=lambda name: (plugin_list.priority(name), name))
        active = [name for name in order if plugin_list.state(name) == mobase.PluginState.ACTIVE]
        if len(active) > 32:
            raise RuntimeError("unexpected fixture active plugin count")
        for fn, lines in (("loadorder.txt", order), ("plugins.txt", ["*" + name for name in active])):
            path = self._file(fn)
            content = "".join(line + "\r\n" for line in lines).encode("utf-8")
            if path.read_bytes() != content:
                staging = path.with_suffix(".pilot-new")
                with staging.open("xb") as stream:
                    stream.write(content)
                staging.replace(path)
        self.order = tuple(order)
