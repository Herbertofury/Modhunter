"""Creator Hub interaction and layout with production assets and fixture transport."""
import json
import os
from pathlib import Path
import unittest

import test_frontend_responsiveness as dashboard


def follows():
    return {"revision": 1, "autoFollow": True, "job": {"state": "idle"}, "review": [],
            "creators": [
                {"id": "nexus:199502561", "name": "SethSaidThat", "provider": "nexus",
                 "role": "uploader", "profileUrl": "https://www.nexusmods.com/profile/SethSaidThat",
                 "state": "following", "origin": "download", "unreadCount": 2,
                 "followedAt": 1800000000, "lastCheckedAt": 1800000010,
                 "projects": [{"itemId": "item-0", "title": "Estates of Skyrim",
                               "url": "https://www.nexusmods.com/skyrimspecialedition/mods/196696",
                               "installedVersion": "0.1.0", "latestVersion": "0.1.21"}]},
                {"id": "modhold:another", "name": "Another Creator", "provider": "modhold",
                 "role": "mod author", "profileUrl": "https://modholdmods.com/u/Another",
                 "state": "following", "origin": "installed", "unreadCount": 1,
                 "projects": [{"itemId": "item-1", "title": "A Different Adventure",
                               "url": "https://modholdmods.com/g/skyrim-se/mods/4/adventure",
                               "installedVersion": "1.0", "latestVersion": "1.1"}]}
            ]}


class CreatorHubBrowserTests(unittest.TestCase):
    setUpClass = classmethod(dashboard.FrontendResponsivenessTests.setUpClass.__func__)
    tearDownClass = classmethod(dashboard.FrontendResponsivenessTests.tearDownClass.__func__)
    setUp = dashboard.FrontendResponsivenessTests.setUp
    tearDown = dashboard.FrontendResponsivenessTests.tearDown
    connected = dashboard.FrontendResponsivenessTests.connected

    def open_fixture(self):
        data = dashboard.catalog()
        data["executionEpoch"] = 7
        data["items"][0].update(author="SethSaidThat", nexusId=196696)
        data["items"][0]["source"]["url"] = "https://www.nexusmods.com/skyrimspecialedition/mods/196696"
        boot = dashboard.BOOT.replace("__CATALOG__", json.dumps(data)).replace("__FAIL__", "0")
        boot = boot.replace("let result={ok:true};", "let result={ok:true};\n"
                            "x.followBodies=x.followBodies||[];\n"
                            "x.creatorFollows=x.creatorFollows||" + json.dumps(follows()) + ";")
        marker = "else if(path==='creator-follows')result={ok:true,profileId:c.profileId,revision:0,autoFollow:true,creators:[],review:[],job:{state:'idle'}};"
        self.assertIn(marker, boot)
        boot = boot.replace(marker, """else if(path==='creator-follows'){
 if(method==='POST'){
  x.followBodies.push(structuredClone(body));
  if(x.hubSaveError)throw Error('Follow save failed');
  const next=structuredClone(x.creatorFollows),states={pause:'paused',resume:'following',unfollow:'unfollowed',restore:'following'};
  if(body.action==='settings')next.autoFollow=body.autoFollow;
  if(body.action==='check')next.job={state:'running',kind:'check',checked:0,total:2};
  if(body.action==='cancel')next.job={state:'cancelled',kind:'check',checked:0,total:2};
  for(const creator of next.creators)if((body.creatorIds||[]).includes(creator.id)){
   if(states[body.action])creator.state=states[body.action];
   if(body.action==='mark-seen')creator.unreadCount=0;
  }
  next.revision++;x.creatorFollows=next;
 }
 result={ok:true,profileId:c.profileId,...structuredClone(x.creatorFollows)};
}""")
        html = self.html.replace("<!--__MO2_BOOT__-->", boot)
        self.page.route("http://creator-hub.fixture.test/",
                        lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        self.page.goto("http://creator-hub.fixture.test/")
        self.connected()
        self.page.locator('#workspaceCreators').click()
        self.page.locator('#creatorHub .hub-card').first.wait_for()
        self.assertTrue(self.page.locator("#creatorHub").is_visible())

    def test_follow_management_search_pause_unfollow_restore_and_mark_seen(self):
        self.open_fixture()
        self.page.locator("#creatorHubSearch").fill("estates")
        self.assertEqual(self.page.locator("#creatorHub .hub-card").count(), 1)
        self.assertIn("SethSaidThat", self.page.locator("#creatorHubDetail").inner_text())
        self.page.locator('#creatorHubDetail [data-hub-action="pause"]').click()
        self.page.wait_for_function("__RESP__.creatorFollows.creators[0].state==='paused'")
        self.page.wait_for_function("document.activeElement?.dataset.hubFilter==='paused'")
        self.page.locator('.hub-tabs [data-hub-filter="paused"]').click()
        self.page.locator('#creatorHubDetail [data-hub-action="resume"]').click()
        self.page.wait_for_function("__RESP__.creatorFollows.creators[0].state==='following'")
        self.page.locator('.hub-tabs [data-hub-filter="following"]').click()
        self.page.locator('#creatorHubDetail [data-hub-action="unfollow"]').click()
        self.page.wait_for_function("__RESP__.creatorFollows.creators[0].state==='unfollowed'")
        self.page.locator('.hub-tabs [data-hub-filter="unfollowed"]').click()
        self.assertIn("Automatic downloads respect this choice", self.page.locator("#creatorHubDetail").inner_text())
        self.page.locator('#creatorHubDetail [data-hub-action="restore"]').click()
        self.page.wait_for_function("__RESP__.creatorFollows.creators[0].state==='following'")
        self.page.locator('.hub-tabs [data-hub-filter="following"]').click()
        self.page.locator('#creatorHubDetail [data-hub-action="mark-seen"]').click()
        self.page.wait_for_function("__RESP__.creatorFollows.creators[0].unreadCount===0")
        self.page.wait_for_function("document.querySelector('#creatorHubBadge').textContent==='1'")
        bodies = self.page.evaluate("__RESP__.followBodies")
        self.assertEqual([body["action"] for body in bodies], ["pause", "resume", "unfollow", "restore", "mark-seen"])
        self.assertTrue(all(body["profileId"] == "profile-a" and body["executionEpoch"] == 7 for body in bodies))
        self.assertFalse(self.page.evaluate("Object.keys(__RESP__.counts).some(key=>/^POST (download|install)/.test(key))"))

    def test_auto_follow_acknowledgement_and_failed_save_retain_previous_state(self):
        self.open_fixture()
        self.page.evaluate("__RESP__.defer['creator-follows']=true")
        self.page.locator("#creatorAutoFollow").click()
        self.page.wait_for_function("__RESP__.pending.some(item=>item.path==='creator-follows')")
        self.assertTrue(self.page.locator("#creatorAutoFollow").is_disabled())
        self.assertTrue(self.page.locator("#creatorAutoFollow").is_checked())
        self.page.evaluate("__RESP__.release('creator-follows')")
        self.page.wait_for_function("!document.querySelector('#creatorAutoFollow').disabled")
        self.assertFalse(self.page.locator("#creatorAutoFollow").is_checked())
        self.page.evaluate("__RESP__.hubSaveError=true")
        self.page.locator('#creatorHubDetail [data-hub-action="unfollow"]').click()
        self.page.wait_for_function("document.querySelector('#creatorHubStatus').textContent.includes('Follow save failed')")
        self.assertEqual(self.page.evaluate("__RESP__.creatorFollows.creators[0].state"), "following")
        self.assertIn("SethSaidThat", self.page.locator("#creatorHubGrid").inner_text())

    def test_profile_switch_rejects_delayed_creator_snapshot(self):
        self.open_fixture()
        self.page.evaluate("__RESP__.defer['creator-follows']=true")
        self.page.locator("#creatorHubReload").click()
        self.page.wait_for_function("__RESP__.pending.some(item=>item.path==='creator-follows')")
        other = dashboard.catalog("profile-b")
        other["executionEpoch"] = 8
        self.page.evaluate("""c=>{
 __RESP__.catalog=c;
 __RESP__.creatorFollows={revision:0,autoFollow:true,creators:[],review:[],job:{state:'idle'}};
 __ARSENAL_QA__.setCatalog(c);
}""", other)
        self.page.wait_for_function("__ARSENAL_QA__.getCatalog().profileId==='profile-b'")
        self.page.evaluate("__RESP__.release('creator-follows')")
        self.page.wait_for_function("!__RESP__.pending.some(row=>row.path==='creator-follows') && (__RESP__.active['GET creator-follows']||0)===0")
        self.page.wait_for_function("document.querySelector('#creatorFollowingCount').textContent==='0'")
        self.assertNotIn("SethSaidThat", self.page.locator("#creatorHubGrid").inner_text())

    def test_cancel_creator_check_keeps_followed_projects_available(self):
        self.open_fixture()
        self.page.locator('#creatorRefresh').click()
        self.page.locator('#creatorCancel').wait_for(state='visible')
        self.page.locator('#creatorCancel').click()
        self.page.locator('#creatorCancel').wait_for(state='hidden')
        self.assertEqual(self.page.evaluate('__RESP__.creatorFollows.job.state'), 'cancelled')
        self.assertEqual(self.page.locator('#creatorHub .hub-card').count(), 2)
        self.assertFalse(self.page.locator('#creatorHubStatus').evaluate("el=>el.classList.contains('is-error')"))
        self.assertEqual([row['action'] for row in self.page.evaluate('__RESP__.followBodies')], ['check', 'cancel'])

    def test_creator_hub_desktop_and_narrow_layout_evidence(self):
        self.open_fixture()
        output = Path(os.environ.get("MODHUNTER_QA_OUTPUT", "/tmp/modhunter-creator-hub-proof"))
        output.mkdir(parents=True, exist_ok=True)
        for width, height, label in [(1440, 1000, "desktop"), (800, 1100, "tablet"), (390, 900, "mobile")]:
            self.page.set_viewport_size({"width": width, "height": height})
            self.page.locator("#creatorHub").scroll_into_view_if_needed()
            self.page.screenshot(path=str(output / ("creator-hub-" + label + ".png")), full_page=True)
            self.assertTrue(self.page.locator("#creatorHubSearch").is_visible())
            self.assertTrue(self.page.locator("#creatorAutoFollow").is_visible())
            self.assertTrue(self.page.locator("#creatorHubDetail").is_visible())
            self.assertTrue(self.page.evaluate("""()=>{
 const root=document.querySelector('#creatorHub'),bounds=root.getBoundingClientRect();
 return root.scrollWidth<=root.clientWidth+1 && bounds.width<=innerWidth+1;
}"""), label)


if __name__ == "__main__":
    unittest.main()
