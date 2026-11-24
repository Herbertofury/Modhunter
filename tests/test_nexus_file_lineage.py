"""Nexus project versions do not establish the installed archive's release line."""
import copy
import tempfile
import time
import unittest
from pathlib import Path

from mo2_update_arsenal.file_advisor import NexusFileIndex
from mo2_update_arsenal.quick_update import choose_nexus_file
from mo2_update_arsenal.scanner import Cache, NexusClient, NexusError, UpdateScanner, public_project_metadata
from mo2_update_arsenal.source_registry import discover_local_source

GAME = "skyrimspecialedition"
MID = 50307
ARCHIVE = "AE - Bone Wolf Patch-50307-V2-6-1640992174.zip"
PATCH = {"fileId":252976, "fileName":ARCHIVE, "name":"Anniversary Edition - Bone Wolf Patch",
         "category":1, "version":"2.6"}
MAIN = {"fileId":696437, "fileName":"Alternate Perspective.7z", "name":"Alternate Perspective",
        "category":1, "version":"4.1.0"}
ITEM = {"id":"installed-patch", "name":"AE - Bone Wolf Patch", "nexusId":MID, "installedVersion":"2.6.0.0",
        "installationArchive":ARCHIVE, "installedFiles":[[MID,252976]], "latestKnownVersion":"2.6.0.0"}


def edge(old, new, archive=""):
    return {"old_file_id":old, "new_file_id":new, "old_file_name":archive}


class ExactFileTests(unittest.TestCase):
    def test_production_scan_uses_file_identity_not_parent_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"meta.ini").write_text("[General]\ngameName=SkyrimSE\nmodid=50307\ninstallationFile="
                                          + ARCHIVE + "\n[installedFiles]\n1\\modid=50307\n1\\fileid=252976\n")
            identity = discover_local_source(root)["_mo2FileIdentity"]
            calls = []
            def fetch(path):
                calls.append(path)
                if path.endswith("/files.json"):
                    return {"files":[PATCH,MAIN], "file_updates":[]}
                return {"mod_id":MID, "domain_name":GAME, "version":"4.1.0", "status":"published"}
            scan = UpdateScanner(Cache(root/"cache.json"), NexusClient("fixture", fetch))
            main = {**ITEM, "id":"main", "installationArchive":"", "installedFiles":[[MID,MAIN["fileId"]]]}
            profile = {"game":GAME, "items":[{**ITEM, **identity}, main]}
            result = scan.scan(profile, "deep")["itemRecords"]
            self.assertEqual(len(calls), 2)
            self.assertEqual(result["installed-patch"]["verdict"], "current")
            self.assertEqual(result["installed-patch"]["targetFileId"], PATCH["fileId"])
            self.assertEqual(result["installed-patch"]["latestVersion"], "2.6")
            self.assertEqual(result["main"]["verdict"], "candidate")
            self.assertEqual(scan.scan(profile, "startup")["itemRecords"], result)
            self.assertEqual(len(calls), 2)

    def test_archived_filename_and_successor_through_unlisted_intermediate(self):
        old = {**ITEM, "installedFiles":[], "installationArchive":"prior.zip", "installedVersion":"2.4"}
        catalog = {"files":[MAIN, PATCH], "file_updates":[edge(17,18,"prior.zip"),edge(18,252976)]}
        checked = NexusFileIndex(catalog).resolve(old)
        self.assertEqual((checked["installedFileId"], checked["targetFileId"], checked["verdict"]),
                         (17,252976,"candidate"))
        now = time.time()
        row = {**old, "update":{**checked, "source":"nexus-api", "checkedAt":now, "expiresAt":now+60}}
        self.assertEqual(choose_nexus_file(row, [MAIN, PATCH])[0]["fileId"], 252976)
        self.assertIsNone(choose_nexus_file(row, [MAIN, dict(PATCH,fileId=1)])[0])
        self.assertIsNone(choose_nexus_file({**row, "update":{**row["update"],"expiresAt":now-1}}, [PATCH])[0])

    def test_conflicting_ambiguous_missing_and_withdrawn_evidence_stays_review(self):
        for files, history, item in [
                ([MAIN,PATCH],None,ITEM), ([MAIN,PATCH],[edge(252976,1),edge(1,252976)],ITEM),
                ([MAIN,PATCH],[edge(252976,1),edge(252976,2)],ITEM), ([MAIN],[],ITEM),
                ([MAIN,dict(PATCH,category=6)],[],ITEM), ([PATCH, PATCH],[],ITEM),
                ([PATCH,MAIN],[],dict(ITEM,installedFiles=[[MID,252976],[MID,88]])),
                ([PATCH,MAIN],[],dict(ITEM,installationArchive=MAIN["fileName"])),
                ([PATCH,MAIN],[],dict(ITEM,installedFiles=[[999,252976]])),
                ([PATCH,MAIN],[],dict(ITEM,installedFiles=[],installationArchive="not a receipt.zip")),
                ([PATCH,dict(MAIN,version="2.6")],[edge(252976,696437)],ITEM)]:
            with self.subTest(files=files, history=history, receipt=item["installedFiles"]):
                self.assertEqual(NexusFileIndex({"files":files,"file_updates":history}).resolve(item)["verdict"],"review")

    def test_complete_public_file_groups_resolve_only_within_game_and_project(self):
        project = {"modId":str(MID),"game":{"domainName":GAME},"name":"Alternate Perspective","version":"4.1.0",
                   "status":"published"}
        rows = [{**project,"fileId":f["fileId"],"uri":f["fileName"],"name":f["name"],"categoryId":f["category"],
                 "groupId":group,"version":f["version"]} for f,group in [(PATCH,5384665),(MAIN,2971540)]]
        def index(data):
            return NexusFileIndex(public_project_metadata(GAME,MID,project,data)["fileCatalog"])
        self.assertEqual(index(rows).resolve(ITEM)["verdict"],"current")
        self.assertEqual(index(rows+[dict(rows[0], fileId=55)]).resolve(ITEM)["verdict"],"review")
        newer = dict(rows[0], fileId=55, uri="patch.zip", version="2.7", categoryId=3)
        self.assertEqual(index([dict(rows[0],categoryId=4),rows[1],newer]).resolve(ITEM)["targetFileId"],55)
        with self.assertRaises(NexusError):
            index(rows+[dict(rows[1],game={"domainName":"anothergame"})])
