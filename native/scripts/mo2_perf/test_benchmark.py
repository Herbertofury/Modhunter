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
