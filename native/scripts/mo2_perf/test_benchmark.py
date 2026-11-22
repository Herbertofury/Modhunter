import tempfile
import unittest
from pathlib import Path

import benchmark as p


def sample():
    return [dict(size=s,block=i,arm=a,valid=True,failed_pings=0,payload_id=p.payload_id(s),
                 startup_ms=1000, idle_core_pct=.05, early_wm_p95_ms=4.0, wm_p95_ms=4.0, work_p95_ms=10.0)
            for s,i,a in p.order()]

class BenchmarkContract(unittest.TestCase):
    def test_counts_and_balances_every_arm_in_every_order_position(self):
        rows=list(p.order())
        self.assertEqual(36,len(rows))
        for s in p.SIZES:
            batch=[a for size,_,a in rows if size==s]
            for i in range(3):
                self.assertEqual({a:2 for a in p.ARMS},{a:batch[i::3].count(a) for a in p.ARMS})

    def test_guardrails_compare_published_and_absent_independently(self):
        data=sample()
        first=next(x for x in data if x["size"]==40 and x["arm"]=="candidate")
        first["work_p95_ms"]=36
        self.assertFalse(p.deterministic_report(40,data)["absent"]["work_p95_ms"]["within_guardrail"])
        for row in data:
            if row["arm"]=="released":
                row["work_p95_ms"]=12
        self.assertTrue(p.deterministic_report(40,data)["released"]["work_p95_ms"]["within_guardrail"])

    def test_host_timer_noise_or_failed_send_is_never_a_better_sample(self):
        for key,value in (("valid",False),("failed_pings",1),("work_p95_ms",float("nan")),("wm_p95_ms",-1)):
            data=sample(); data[0][key]=value
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError,"invalid/missed"):
                p.deterministic_report(40,data)

    def test_missing_duplicate_wrong_order_and_wrong_profile_rejected(self):
        for mode in ("missing","duplicate","wrong_order","fixture"):
            data=sample()
            if mode=="missing": data.pop(0)
            if mode=="duplicate": data[1]=dict(data[0])
            if mode=="wrong_order": data[0],data[1]=data[1],data[0]
            if mode=="fixture": data[0]["payload_id"]=p.payload_id(1000)
            with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                p.deterministic_report(40,data)

    def test_work_tail_is_not_a_mean(self):
        self.assertEqual(1,p.pct([1]*95+[50]*4+[400],.95))
        self.assertEqual(50,p.pct([1]*95+[50]*4+[400],.99))
        self.assertEqual(400,p.pct([1]*95+[50]*4+[400],1))
        with self.assertRaises(RuntimeError): p.pct([], .95)

    def test_fresh_fixture_inventory_and_controlled_enabled_ratio(self):
        with tempfile.TemporaryDirectory() as parent:
            root=Path(parent)
            for sz in p.SIZES:
                t=root/str(sz);t.mkdir()
                names=p.stage(t,sz);self.assertEqual(sz,len(names)); self.assertEqual(len(set(names)),sz)
                modlist=(t/"instance/profiles/Modhunter Smoke/modlist.txt").read_text().splitlines()[1:]
                self.assertEqual(names,[s[1:] for s in modlist])
                self.assertEqual(sz//4,sum(s.startswith("-") for s in modlist))
                self.assertEqual(sz//2,sum(s.startswith("+Bench Alpha") or s.startswith("-Bench Alpha") for s in modlist))
                self.assertTrue((t/"synthetic-game/fixture.exe").read_bytes().startswith(b"Inert text"))
                markers=sorted((t/"instance/mods").glob("*/Bench*.esp"))
                self.assertEqual(sz,len(markers))
                self.assertEqual(42,len(p.tes4_marker()))
                self.assertEqual([p.tes4_marker()]*sz,[x.read_bytes() for x in markers])
                self.assertEqual(p.fixture_marker_map(sz),{m.name:m.parent.name for m in markers})
                self.assertTrue(set(m.parent.name for m in markers)
                                <= {row[1:] for row in modlist if row.startswith("+")})
                self.assertIn(b"noregister=true",(t/"nxmhandler.ini").read_bytes())

    def test_release_zip_members_are_bounded_and_win_path_safe(self):
        from zipfile import ZipFile
        with tempfile.TemporaryDirectory() as tmp:
            z=Path(tmp)/"plugin.zip"
            with ZipFile(z,"w") as f:
                f.writestr("MO2/mo2_update_arsenal/plugin.py",b"fixture")
                f.writestr("MO2/mo2_update_arsenal/bin/modhunter-qt-bridge.dll",b"fixture-dll")
            self.assertEqual(2,len(p.published_manifest(z,p.sha(z))))
            with self.assertRaisesRegex(RuntimeError,"identity mismatch"):
                p.published_manifest(z,"0"*64)
            with ZipFile(z,"a") as f:
                f.writestr("MO2/mo2_update_arsenal/../secret.data",b"unsafe")
            with self.assertRaisesRegex(RuntimeError,"unsafe published"):
                p.published_manifest(z,p.sha(z))

    def test_native_ui_registration_requires_and_uses_one_window_argument(self):
        import importlib.util
        import inspect
        import os
        import sys
        import types
        from unittest.mock import patch

        host_binding=types.ModuleType("mobase")
        host_binding.IPlugin=object
        qt=types.ModuleType("PyQt6")
        qt.QtCore=qt.QtGui=qt.QtWidgets=object()
        spec=importlib.util.spec_from_file_location(
            "mo2_pilot_observer_contract",Path(p.__file__).parent/"observer/__init__.py"
        )
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{"mobase":host_binding,"PyQt6":qt}), patch.dict(os.environ,{"MO2_BENCH_DIAGNOSTIC":"0"}):
            spec.loader.exec_module(module)
        window=object()
        class Organizer:
            def onUserInterfaceInitialized(self, callback):
                # Contract double for the verified one-QMainWindow Mobase ABI.
                inspect.signature(callback).bind(window)
                self.callback=callback
                return True
        observer=module.Bench.__new__(module.Bench)
        organizer=Organizer()
        scheduled=[]
        observer.queue=lambda delay,action: scheduled.append((delay,action))
        self.assertTrue(observer.init(organizer))
        self.assertEqual([],scheduled)
        self.assertIsNone(organizer.callback(window))
        self.assertIs(window,observer._main_window)
        self.assertEqual([(0,observer.start)],scheduled)

    def test_first_observer_failure_does_not_wait_for_ready_timeout(self):
        class Alive:
            def poll(self): return None
        with tempfile.TemporaryDirectory() as tmp:
            events=Path(tmp)
            p.dump_new(events/"result.json",{"ok":False,"phase":"native-list-start"})
            with self.assertRaisesRegex(RuntimeError,"native observer stopped before ready.json: native-list-start"):
                p.wait_file(events/"ready.json",Alive(),.1)

    def test_nxm_setup_never_reuses_or_changes_another_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"ModOrganizer"
            p.stage_nxm(root,"own-fixture")
            p.nxm_fixture(root,"own-fixture")
            with self.assertRaisesRegex(RuntimeError,"existing global MO2 data"):
                p.stage_nxm(root,"another-fixture")
            with self.assertRaisesRegex(RuntimeError,"owner"):
                p.nxm_fixture(root,"another-fixture")
            (root/"nxmhandler.ini").write_text("[General]\nnoregister=false\n")
            with self.assertRaisesRegex(RuntimeError,"preference changed"):
                p.nxm_fixture(root,"own-fixture")
            self.assertIn("false",(root/"nxmhandler.ini").read_text())

    def test_log_tail_bound_and_known_private_literal_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            pth=root/"child.stdout"
            pth.write_bytes(b"x"*20000+b"last: "+str(root).encode()+b" Bearer pretendsecret")
            tail=p.log_tail(pth,[(root,"<FIXTURE>")],128)
            self.assertLessEqual(len(tail),128)
            self.assertIn("<FIXTURE> Bearer [REDACTED]",tail)
            self.assertNotIn(str(root),tail)
            self.assertNotIn("pretendsecret",tail)

    def test_disposable_shim_registers_typed_provider_and_roundtrips_fixture_lists(self):
        import os
        import sys
        import types
        from unittest.mock import patch
        fixture=Path(__file__).resolve().parent.parent/"mo2_smoke_plugin/game_modhunter_smoke.py"
        addon=Path(p.__file__).parent/"game_plugins.inc"
        host=types.ModuleType("mobase")
        host.ModDataChecker=type("ModDataChecker",(),{})
        host.GamePlugins=type("GamePlugins",(),{})
        host.PluginState=types.SimpleNamespace(ACTIVE="active",INACTIVE="inactive")
        base=types.ModuleType("pilot_fake.basic_game")
        class BasicGame:
            def init(self, organizer):
                self.organizer=organizer
                return True
            def _register_feature(self, feature):
                self.organizer.providers.append(feature)
                return True
        base.BasicGame=BasicGame
        with tempfile.TemporaryDirectory() as tmp:
            for size in p.SIZES:
                root=Path(tmp)/str(size);root.mkdir()
                p.stage(root,size)
                profile=root/"instance/profiles/Modhunter Smoke"
                names=list(p.fixture_marker_map(size))
                first=min(32,size//5)
                class Organizer:
                    providers=[]
                    def profilePath(self): return str(profile)
                class PluginList:
                    order=[]
                    states={name:"inactive" for name in names}
                    def pluginNames(self): return names
                    def state(self,n): return self.states[n]
                    def setState(self,n,state): self.states[n]=state
                    def setLoadOrder(self,n): self.order=list(n)
                    def priority(self,n): return self.order.index(n)
                namespace={"__name__":"pilot_fake.games.fixture","__package__":"pilot_fake.games"}
                env={"MO2_BENCH_ROOT":str(root),"MO2_BENCH_SIZE":str(size),
                     "GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"github-hosted"}
                with patch.dict(sys.modules,{"mobase":host,"pilot_fake.basic_game":base}),patch.dict(os.environ,env):
                    exec(p.game_fixture_source(fixture,addon),namespace)
                    organizer=Organizer()
                    self.assertTrue(namespace["ModhunterSmokeGame"]().init(organizer))
                    self.assertEqual(2,len(organizer.providers))
                    feature=organizer.providers[1]
                    self.assertIsInstance(feature,host.GamePlugins)
                    plugin_list=PluginList()
                    feature.readPluginLists(plugin_list)
                    self.assertEqual(names,feature.getLoadOrder())
                    self.assertEqual(names,plugin_list.order)
                    self.assertEqual(set(names[:first]),{n for n in names if plugin_list.state(n)=="active"})
                    plugin_list.order.reverse()
                    plugin_list.states[names[0]]="inactive"
                    feature.writePluginLists(plugin_list)
                    self.assertEqual(list(reversed(names)),(profile/"loadorder.txt").read_text().splitlines())
                    self.assertEqual(["*"+n for n in reversed(names[1:first])],(profile/"plugins.txt").read_text().splitlines())
                    feature.readPluginLists(plugin_list)
                    self.assertEqual(list(reversed(names)),feature.getLoadOrder())
                    organizer.profilePath=lambda: str(root)
                    with self.assertRaisesRegex(RuntimeError,"outside the exact owned profile"):
                        feature.readPluginLists(plugin_list)

    def test_staged_shim_refuses_mutated_addon_bytes(self):
        fixture=Path(__file__).resolve().parent.parent/"mo2_smoke_plugin/game_modhunter_smoke.py"
        addon=Path(p.__file__).parent/"game_plugins.inc"
        with tempfile.TemporaryDirectory() as tmp:
            bad=Path(tmp)/"game_plugins.inc"
            bad.write_bytes(addon.read_bytes()+b"# modified")
            with self.assertRaisesRegex(RuntimeError,"extension identity mismatch"):
                p.game_fixture_source(fixture,bad)

    def test_runtime_intake_refuses_symlink_and_bytes_over_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"asset").write_bytes(b"abcd")
            with self.assertRaisesRegex(RuntimeError,"exceeds bound"): p.inspect_tree(root,3)
            self.assertEqual({"asset":p.sha(root/"asset")},p.inspect_tree(root,4))
            try:
                (root/"alias").symlink_to("asset")
            except OSError:
                self.skipTest("OS cannot create a symlink")
            with self.assertRaisesRegex(RuntimeError,"linked"): p.inspect_tree(root,16)

if __name__=="__main__":
    unittest.main()
