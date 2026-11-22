"""MO2 benchmark observer; unchanged and present in every isolated arm."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time
import traceback

def breadcrumb(phase, **values):
    env=os.environ
    if (env.get("MO2_BENCH_DIAGNOSTIC") != "1" or env.get("GITHUB_ACTIONS")!="true"
            or env.get("RUNNER_ENVIRONMENT")!="github-hosted"):
        return
    root=Path(env["MO2_BENCH_ROOT"]).resolve(strict=True)
    events=Path(env["MO2_BENCH_EVENTS"]).resolve(strict=True)
    if Path(__file__).resolve().parents[2]!=root or events!=root.parent/"events":
        return
    p=events/("startup-"+phase+".json")
    with p.open("x",encoding="utf-8") as f:
        json.dump({"phase":phase,"pid":os.getpid(),**values},f)


breadcrumb("module-entered")
try:
    import mobase
    from PyQt6 import QtCore, QtGui, QtWidgets
except Exception as error:
    breadcrumb("native-import-failed",error_type=type(error).__name__)
    raise
breadcrumb("native-imports-ready")


def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def need_fixture_plugins(filenames, names, active, expected_count):
    fixtures=set(filenames)
    expected=set(filenames[:expected_count])
    present=fixtures & names
    missing=fixtures-names
    inactive=expected-active
    unexpected=(active & fixtures)-expected
    need(not(missing or inactive or unexpected),
         f"native fixture ESPs {len(present)}/{len(fixtures)}, active {len(active & fixtures)}/{expected_count}; "
         f"missing rows ({len(missing)}): {sorted(missing)[:8]}; "
         f"expected-active but inactive ({len(inactive)}): {sorted(inactive)[:8]}; "
         f"unexpectedly active ({len(unexpected)}): {sorted(unexpected)[:8]}")


class Bench(mobase.IPlugin):
    def __init__(self):
        super().__init__()
        env = os.environ
        self.root = Path(env["MO2_BENCH_ROOT"]).resolve(strict=True)
        self.events = Path(env["MO2_BENCH_EVENTS"]).resolve(strict=True)
        need(Path(__file__).resolve().parents[2] == self.root
             and self.events == self.root.parent / "events"
             and (self.root / "portable.txt").read_text().strip() == "Owned CI benchmark fixture"
             and env.get("GITHUB_ACTIONS") == "true"
             and env.get("RUNNER_ENVIRONMENT") == "github-hosted",
             "not the isolated official-host benchmark fixture")
        self.nonce = env["MO2_BENCH_NONCE"]
        self.size = int(env["MO2_BENCH_SIZE"])
        self.quiet = int(env["MO2_BENCH_QUIET"])
        self.steps = int(env["MO2_BENCH_STEPS"])
        self.phase = "constructor"
        need(self.size in (40,1000) and self.quiet==20 and self.steps==48,"unexpected test scope")
        self.work = []
        self.attempts = 0

    def name(self): return "ModHunter Performance Fixture Observer"
    def author(self): return "Modhunter contributors"
    def description(self): return "Observe the owned portable performance fixture only"
    def version(self): return mobase.VersionInfo("1.0.0")
    def settings(self): return []
    def isActive(self): return True
    def init(self, organizer):
        self.org=organizer
        self.phase = "organizer-init"
        breadcrumb("organizer-init")
        try:
            accepted=organizer.onUserInterfaceInitialized(self.ui_ready)
        except Exception as error:
            breadcrumb("callback-registration-failed",error_type=type(error).__name__)
            raise
        breadcrumb("callback-registered",accepted=accepted is not False)
        return accepted is not False

    def ui_ready(self, window):
        # MO2 supplies the native QMainWindow on this one-argument callback.
        self._main_window = window
        self.phase="native-ui-callback"
        breadcrumb("native-ui-callback")
        self.queue(0,self.start)

    def write(self,name,row):
        tmp=self.events/(name+".write")
        result=self.events/(name+".json")
        need(not result.exists(),"duplicate result")
        with tmp.open("x",encoding="utf-8") as f:
            json.dump({"nonce":self.nonce,**row},f,allow_nan=False)
        tmp.replace(result)

    def queue(self,delay,fn):
        def guarded():
            try: fn()
            except Exception:
                self.write("result",{"ok":False,"phase":self.phase,"error":traceback.format_exc(limit=8)})
                QtCore.QCoreApplication.quit()
        QtCore.QTimer.singleShot(delay,guarded)
        return True  # Preserve a concrete native callback result.

    def start(self):
        self.phase="native-list-start"
        self.attempts+=1
        if self.attempts==1:
            breadcrumb("first-qt-callback")
        windows=[w for w in QtWidgets.QApplication.topLevelWidgets() if w.objectName()=="MainWindow" and w.isVisible()]
        if not windows and self.attempts<100:
            self.queue(25,self.start)
            return
        need(len(windows)==1 and windows[0] == self._main_window
             and self.org.profileName()=="Modhunter Smoke", "callback's main fixture window/profile absent")
        win=self._main_window
        self.list=win.findChild(QtWidgets.QTreeView,"modList")
        self.filter=win.findChild(QtWidgets.QLineEdit,"modFilterEdit")
        self.tabs=win.findChild(QtWidgets.QTabWidget,"tabWidget")
        self.data=win.findChild(QtWidgets.QWidget,"dataTab")
        self.plugins=win.findChild(QtWidgets.QWidget,"espTab")
        self.refresh=win.findChild(QtWidgets.QPushButton,"dataTabRefresh")
        need(all(x is not None for x in (self.list,self.filter,self.tabs,self.data,self.plugins,self.refresh)),
             "MO2 widgets differ from the pinned source")
        need(self.tabs.indexOf(self.plugins)>=0, "native Plugins tab missing: fixture GamePlugins must be registered")
        self.plugin_view=self.plugins.findChild(QtWidgets.QAbstractItemView,"espList")
        need(self.plugin_view is not None, "native Plugins view is absent")
        feature=self.org.gameFeatures().gameFeature(mobase.GamePlugins)
        need(isinstance(feature,mobase.GamePlugins) and len(feature.getLoadOrder())==self.size,
             "native organizer did not return the fixture's typed GamePlugins provider")
        model=self.list.model()
        need(model is not None and model.rowCount()>=self.size and not self.filter.text(),
             "initial native fixture rows missing")
        names=sorted(n for n in self.org.modList().allMods() if n.startswith("Bench "))
        expected=sorted(f"Bench {'Alpha' if i%2==0 else 'Beta'} {i:04d}" for i in range(self.size))
        need(names==expected,"native fixture names differ from expected")
        module=(self.root/"plugins/mo2_update_arsenal/plugin.py").resolve()
        loaded=[m for m in list(sys.modules.values()) if getattr(m,"__file__",None)
                and os.path.normcase(str(Path(m.__file__).resolve()))==os.path.normcase(str(module))]
        actions=[a for a in win.findChildren(QtGui.QAction)
                 if a.text().replace("&","")=="Update Arsenal - Quest Board" and a.isEnabled()]
        need(len(loaded)<=1 and len(actions)<=1,"duplicate installed production plugin")
        self.write("ready",{"pid":os.getpid(),"hwnd":int(win.winId()),"initial_fixture_count":len(names),
                            "production_action":bool(actions),"production_path":bool(loaded)})
        # One wake only. No observer timer heartbeat, requests or graph capture while quiet.
        self.queue(self.quiet*1000,self.burst)

    def burst(self):
        self.phase="fixture-vfs"
        # Verify representative source capacity before timing. This controlled
        # pass does not assert gameplay validity or count ModHunter internals.
        mods=self.root/"instance"/"mods"
        enabled=[f"Bench {'Alpha' if i%2==0 else 'Beta'} {i:04d}"
                 for i in range(self.size) if i%4!=0]
        filenames=[f"Bench{i:04d}.esp" for i in range(self.size)]
        for i,name in enumerate(filenames):
            value=self.org.resolvePath(name)
            wanted=mods/enabled[i%len(enabled)]/name
            need(value and os.path.normcase(str(Path(value).resolve()))==os.path.normcase(str(wanted.resolve())),
                 "native VFS did not resolve "+name+" to the controlled fixture")
        pl=self.org.pluginList()
        names=set(pl.pluginNames())
        active={name for name in filenames if pl.state(name)==mobase.PluginState.ACTIVE}
        need_fixture_plugins(filenames,names,active,min(32,self.size//5))
        self.write("burst",{"native_vfs_verified":len(filenames),
                            "native_fixture_esp_rows":len(names.intersection(filenames)),
                            "active_fixture_plugins":len(active)})
        self.queue(150,self.step)

    def step(self):
        self.phase="ordinary-native-actions"
        if len(self.work)==self.steps:
            need(not self.filter.text() and self.list.model().rowCount()>=self.size, "original list was not restored")
            self.write("result",{"ok":True,"work_ms":self.work})
            self.queue(300,lambda: QtWidgets.QApplication.instance().quit())
            return
        op=("filter","data","refresh","plugins","clear","select")[len(self.work)%6]
        now=time.perf_counter()
        index=None
        match op:
            case "filter": self.filter.setText("Bench Alpha")
            case "data": self.tabs.setCurrentWidget(self.data)
            case "refresh":
                need(self.tabs.currentWidget()==self.data and self.refresh.isEnabled(),"native Data Refresh unavailable")
                self.refresh.click()
            case "plugins": self.tabs.setCurrentWidget(self.plugins)
            case "clear": self.filter.clear()
            case "select":
                model=self.list.model()
                old=self.list.currentIndex().row()
                need(model.rowCount()>1,"no second native row to select")
                index=model.index((old+1)%model.rowCount(),0)
                need(index.isValid(),"no native row to select")
                self.list.setCurrentIndex(index)
        def observe():
            rows=self.list.model().rowCount()
            observed={"filter":0<rows<self.size and self.filter.text()=="Bench Alpha",
                      "data":self.tabs.currentWidget()==self.data,
                      "refresh":self.tabs.currentWidget()==self.data,
                      "plugins":self.tabs.currentWidget()==self.plugins and self.plugin_view.model() is not None
                                and self.plugin_view.model().rowCount()>=self.size,
                      "clear":rows>=self.size and not self.filter.text(),"select":index is not None and self.list.currentIndex()==index}[op]
            elapsed=(time.perf_counter()-now)*1000
            need(elapsed<2500, "native state not confirmed: "+op)
            if not observed:
                self.queue(15,observe)
                return
            self.work.append(elapsed)
            self.queue(150,self.step)
        self.queue(0,observe)


def createPlugin():
    breadcrumb("factory-entered")
    try:
        observer=Bench()
    except Exception as error:
        breadcrumb("factory-failed",error_type=type(error).__name__)
        raise
    breadcrumb("factory-ready")
    return observer
