import unittest
from mo2_update_arsenal.enemy_art import enemy_art
class EnemyArtTests(unittest.TestCase):
    def test_preserves_identity_and_deduplicates(self):
        url = 'https://cdn.modrinth.com/a.png'
        self.assertEqual(enemy_art({'imageUrl':url,'update':{'thumbnailUrl':url,'author':{'avatarUrl':url}}}), [{'kind':'mod','url':url},{'kind':'author','url':url}])
    def test_no_arbitrary_urls(self):
        for url in ('http://cdn.modrinth.com/a','file:///tmp/a','https://user:pass@cdn.modrinth.com/a','https://cdn.modrinth.com.evil/a','https://cdn.modrinth.com:99/a'):
            self.assertEqual(enemy_art({'imageUrl':url}),[])
    def test_missing_and_wrong_metadata(self):
        self.assertEqual(enemy_art({'source':'Nexus','author':'A name','update':None}),[])
