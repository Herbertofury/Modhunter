import unittest
from mo2_update_arsenal.companions import companion_candidates
from mo2_update_arsenal.model import mod_categories

class CompanionTests(unittest.TestCase):
    def test_name_category_group_and_review(self):
        rows=[{'id':'a','name':'Lyra - voiced follower'}, {'id':'b','name':'Moonlight','categories':['Followers & Companions']}, {'id':'c','name':'Travelers','group':'Companions'}, {'id':'d','name':'Follower Framework'}, {'id':'e','name':'Follower patch'}, {'id':'x','name':'Sword'}, {'id':'a','name':'duplicate follower'}]
        result=companion_candidates(rows)
        self.assertEqual([c['modId'] for c in result],list('abcde'))
        self.assertEqual(result[1]['evidence'],['category/group'])
        self.assertTrue(result[3]['needsReview']);self.assertTrue(result[4]['needsReview'])
        self.assertFalse(result[0]['needsReview'])
        self.assertTrue(all(c['representation']=='metadata-inspired' for c in result))
    def test_not_substrings_not_remote_not_separator(self):
        self.assertEqual(companion_candidates([{'id':'a','name':'Unfollowership'}, {'id':'b','name':'Follower','installed':False}, {'id':'c','name':'Followers','separator':True}]),[])
    def test_no_hidden_limit_private_path_or_name_dedupe(self):
        rows=[{'id':str(i),'name':'Same companion','path':'PRIVATE','token':'SECRET'} for i in range(300)]
        r=companion_candidates(rows);self.assertEqual(len(r),300)
        self.assertNotIn('PRIVATE',str(r));self.assertNotIn('SECRET',str(r))
    def test_native_category_labels_and_missing_api(self):
        class Mod:
            def categories(self):return ['Followers', 'Custom category']
        self.assertEqual(mod_categories(Mod()),['Followers','Custom category'])
        self.assertEqual(mod_categories(object()),[])
