import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
from mo2_update_arsenal.shared_cards import shared_cards_path
from mo2_update_arsenal.shared_card_host import create_server,runtime_html

class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        environment=patch.dict(os.environ,{'HOME':self.temp.name,'LOCALAPPDATA':self.temp.name,'XDG_DATA_HOME':self.temp.name})
        environment.start();self.addCleanup(environment.stop)
        self.folder=shared_cards_path().parent/'runtime-v1';self.folder.mkdir(parents=True)
        self.page=b'<html><!--__CARD_HOST_BOOT__--><main>Private fixture</main></html>'
        (self.folder/'cardgame.html').write_bytes(self.page)
        self.manifest={'htmlSha256':hashlib.sha256(self.page).hexdigest()}
        (self.folder/'manifest.json').write_text(json.dumps(self.manifest))
        self.runtime=patch('mo2_update_arsenal.shared_card_host.runtime_installation',return_value=(self.folder/'known-helper',self.manifest))
        self.runtime.start();self.addCleanup(self.runtime.stop)
    def server(self,port=0):
        server=create_server(port);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return server
    def request(self,server,method='GET',path='/',headers=None,body=None):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            connection.request(method,path,body,headers or {});response=connection.getresponse()
            return response.status,response.read().decode(),dict(response.getheaders())
        finally:connection.close()
    def test_page_integrity_and_boot_contract(self):
        self.assertIn('Private fixture',runtime_html())
        (self.folder/'cardgame.html').write_bytes(self.page+b'changed')
        with self.assertRaises(ValueError):runtime_html()
    def test_server_host_auth_and_card_only_routes(self):
        server=self.server();origin=f'http://127.0.0.1:{server.server_port}'
        status,body,headers=self.request(server)
        self.assertEqual(status,200);self.assertNotIn('<!--__CARD_HOST_BOOT__-->',body)
        self.assertEqual(headers['X-Frame-Options'],'DENY');self.assertEqual(headers['Cache-Control'],'no-store')
        token=json.loads(re.search(r'window.__CARD_HOST_BOOT__=(.*?);',body).group(1))['token']
        self.assertEqual(self.request(server,headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request(server,path='/manifest.json')[0],404)
        self.assertEqual(self.request(server,'POST','/api/shared-cards/invoke',body='{}')[0],403)
        auth={'Origin':origin,'X-Arsenal-Token':token,'Content-Type':'application/json'}
        self.assertEqual(self.request(server,'POST','/api/install',auth,'{}')[0],404)
        with patch('mo2_update_arsenal.shared_card_host.invoke_shared_card_runtime',return_value={'ok':True,'result':None}) as runtime:
            self.assertEqual(self.request(server,'POST','/api/shared-cards/invoke',auth,'{"action":"load","slot":"main"}')[0],200)
            runtime.assert_called_once_with({'action':'load','slot':'main'})
    def test_port_conflict_does_not_fallback(self):
        server=self.server()
        with self.assertRaises(OSError):create_server(server.server_port)
    def test_origin_stable_after_restart_token_rotates(self):
        first=create_server(0);port=first.server_port
        thread=threading.Thread(target=first.serve_forever,daemon=True);thread.start()
        old=self.request(first)[1];first.shutdown();first.server_close()
        second=self.server(port)
        self.assertEqual(second.server_port,port);self.assertNotEqual(self.request(second)[1],old)
    def test_deep_json_is_controlled(self):
        server=self.server();body=self.request(server)[1]
        token=json.loads(re.search(r'window.__CARD_HOST_BOOT__=(.*?);',body).group(1))['token']
        auth={'Origin':f'http://127.0.0.1:{server.server_port}','X-Arsenal-Token':token,'Content-Type':'application/json'}
        self.assertEqual(self.request(server,'POST','/api/shared-cards/invoke',auth,'['*2000+'0'+']'*2000)[0],400)

if __name__=='__main__':unittest.main()
