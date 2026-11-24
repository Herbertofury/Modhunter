"""Exercise MO2's documented model fields independently of numeric ticket IDs."""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from mo2_update_arsenal.native_downloads import DownloadDirectoryIndex, archive_receipt, ready_receipt, verified_path
from mo2_update_arsenal.content_recovery import NativeContent


class Index:
    def __init__(self, row, col):
        self._row, self.col = row, col

    def row(self):
        return self._row


class DownloadList:
    def __init__(self, rows):
        self.rows = rows

    def index(self, row, col):
        return Index(row, col)

    def data(self, index, role):
        return self.rows[index.row()][index.col]

    def match(self, index, role, value, hits, flags):
        return [Index(i, index.col) for i, row in enumerate(self.rows) if row[index.col] == value]


class DownloadReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.archive("Viewer.zip", 199895, 842623)
        self.unrelated = self.archive("Other.zip", 88122, 702775)
        self.ticket = {"game": "skyrimspecialedition", "modId": 199895, "fileId": 842623}
        self.game = NS(gameNexusName=lambda:"skyrimspecialedition", gameShortName=lambda:"SkyrimSE")
        self.paths = {3:self.unrelated, 1:self.target}
        self.org = NS(downloadsPath=lambda:str(self.root), managedGame=lambda:self.game,
                      downloadManager=lambda:NS(downloadPath=lambda handle:str(self.paths.get(handle, ""))))
        self.qt = NS(Qt=NS(ItemDataRole=NS(DisplayRole=0), MatchFlag=NS(MatchExactly=0)),
                     QCoreApplication=NS(translate=lambda ctx,value:value))
        receipt = archive_receipt(self.root, self.target)
        created = NS(isValid=lambda:True,toMSecsSinceEpoch=lambda:receipt["createdMs"])
        self.row = ["Viewer (Main, v2.2)","Downloaded","","", "", "2.2.0.0", "199895"]
        self.row[3] = created
        self.rows = [["Other","Pending",None,None,None,"",""], self.row]

    def archive(self, name, project, file_id):
        archive = self.root / name
        archive.write_bytes(b"fixture archive")
        Path(str(archive)+".meta").write_text(
            f"[General]\ngameName=SkyrimSE\nmodID={project}\nfileID={file_id}\n"
            "name=Viewer\nversion=2.2.0.0\nrepository=Nexus\nremoved=false\npaused=false\n")
        return archive

    def ready(self):
        return ready_receipt(self.org,DownloadList(self.rows),self.qt,3,self.ticket)

    def test_correct_downloaded_native_row_binds_actual_archive_independently_of_requested_handle(self):
        receipt = self.ready()
        self.assertEqual((receipt["modId"],receipt["fileId"]),(199895,842623))
        self.assertEqual(verified_path(self.root,receipt),self.target)
        # Changes to the mapping of row numbers cannot redirect installation.
        self.paths[1]=self.unrelated
        self.assertEqual(verified_path(self.root,receipt),self.target)
        owner=NS(plugin=NS(org=self.org),check=lambda context:None)
        received=[]
        request=NS(cmd="content-download",payload={"context":{},"downloadId":3,"nativeArchiveProof":receipt},
                   finish=received.append)
        NativeContent.handle(owner,request,None)
        self.assertEqual(received,[{"ok":True,"archive":str(self.target)}])
        self.target.write_bytes(b"replacement")
        with self.assertRaises(ValueError):
            verified_path(self.root,receipt)
        with self.assertRaises(ValueError):
            NativeContent.handle(owner,request,None)

    def test_file_or_metadata_alone_never_promotes_a_download(self):
        for col,value in [(0,"Different label"),(1,"Pending"),(1,"Fetching Info"),(5,"1.0"),(6,"88122")]:
            with self.subTest(col=col,value=value):
                original=self.row[col];self.row[col]=value
                self.assertIsNone(self.ready())
                self.row[col]=original
        self.rows.append(self.row.copy())
        self.assertIsNone(self.ready())

    def test_reconcile_without_native_handle_or_callback_using_verified_physical_archive(self):
        self.paths.clear()
        directory = DownloadDirectoryIndex()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            proof = ready_receipt(self.org,DownloadList(self.rows),self.qt,3,self.ticket,directory)
            if proof is not None:
                break
            time.sleep(0.01)
        self.assertIsNotNone(proof)
        self.assertEqual(verified_path(self.root, proof), self.target)

    def test_cross_game_changed_file_and_removed_metadata_are_refused(self):
        self.ticket["game"]="fallout4";self.assertIsNone(self.ready())
        self.ticket["game"]="skyrimspecialedition"
        sidecar=Path(str(self.target)+".meta")
        previous=sidecar.read_text()
        for key,wrong in [("fileID=842623","fileID=842624"),("gameName=SkyrimSE","gameName=Fallout4"),
                          ("removed=false","removed=true"),("paused=false","paused=true"),
                          ("fileID=842623","fileID=842623\nfileID=842623")]:
            sidecar.write_text(previous.replace(key,wrong))
            self.assertIsNone(self.ready())
