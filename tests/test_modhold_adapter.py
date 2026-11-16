import time
import unittest
from mo2_update_arsenal.source_adapters import adapter_capabilities, normalize_adapter_record
from mo2_update_arsenal.source_registry import provider_of, canonical_source_url

class ModholdTests(unittest.TestCase):
    def test_public_metadata_capability_is_not_download_access(self):
        c=adapter_capabilities('modhold')
        self.assertTrue(c['versionCheck']);self.assertFalse(c['reviewedDownload'])
        self.assertFalse(adapter_capabilities('modhold',helper_available=False)['versionCheck'])
        self.assertIn('Rust helper',adapter_capabilities('modhold',helper_available=False)['reason'])
    def test_exact_host_and_secret_redaction(self):
        self.assertEqual(provider_of('https://modholdmods.com/g/skyrim-se/mods/203/title'),'modhold')
        self.assertNotEqual(provider_of('https://modholdmods.com.evil.test/g/skyrim-se/mods/203/title'),'modhold')
        self.assertNotIn('secret',canonical_source_url('https://modholdmods.com/g/skyrim-se/mods/203/title?modhold_key=secret'))
    def test_real_version_comparison_and_staleness(self):
        url='https://modholdmods.com/g/skyrim-se/mods/203/title'; now=time.time()
        item={'id':'fixture','installedVersion':'1.0.0','source':{'url':url,'verified':True,'kind':'project'}}
        raw={'provider':'modhold','sourceUrl':url,'outcome':'release','freshness':'fresh','checkedAt':now,'expiresAt':now+900,'latestVersion':'1.0.1','projectId':'203','releaseId':'203:1.0.1','releaseAssets':[{'name':'Fixture.zip','installable':False,'url':None}]}
        out=normalize_adapter_record(item,raw,now=now)
        self.assertEqual(out['verdict'],'candidate');self.assertFalse(out['releaseAssets'][0]['installable'])
        raw['expiresAt']=now-1
        self.assertEqual(normalize_adapter_record(item,raw,now=now)['verdict'],'unknown')

if __name__=='__main__':unittest.main()
