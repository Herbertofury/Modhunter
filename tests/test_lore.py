"""Mod-derived combat names keep exact MO2 identity, provenance and category."""
import unittest
from mo2_update_arsenal.lore import describe_enemy_theme


class ModhunterLoreTests(unittest.TestCase):
    def test_named_weapon_from_title(self):
        lore = describe_enemy_theme({"name": "Electron Maul - SSE", "group": "Weapons"})
        self.assertEqual(lore["kind"], "weapon")
        self.assertEqual(lore["subject"], "Electron Maul")
        self.assertIn("Electron Maul", lore["bossTitle"])
        self.assertIn("Electron Maul", lore["foes"][0])
        self.assertEqual(lore["provenance"], "title-inference")

    def test_armor_and_plural_variants(self):
        for name in ["Iron Rose Armor 2k Complex Material", "Cloaks and Capes", "Glasses Pack Vol.1"]:
            with self.subTest(name=name):
                lore = describe_enemy_theme({"name": name})
                self.assertEqual(lore["kind"], "armor")
                self.assertTrue(lore["foes"])
        self.assertEqual(describe_enemy_theme({"name": "Iron Rose Armor 2k Complex Material"})["subject"], "Iron Rose Armor")

    def test_follower_npc_from_named_title(self):
        lore = describe_enemy_theme({"name": "Celuna - HDT Dark Elf Follower - ESPFE"})
        self.assertEqual(lore["kind"], "npc")
        self.assertIn("Celuna", lore["bossTitle"])
        self.assertNotIn("HDT", lore["subject"])

    def test_real_named_content_beats_generic_title(self):
        lore = describe_enemy_theme({"name": "Swords of Skyrim", "contentEntities": {"weapons": ["Frostbreaker"]}})
        self.assertEqual(lore["kind"], "weapon")
        self.assertEqual(lore["subject"], "Frostbreaker")
        self.assertEqual(lore["provenance"], "content-metadata")
        self.assertIn("Frostbreaker", lore["foes"][0])

    def test_explicit_npc_wins_over_armor_title(self):
        lore = describe_enemy_theme({"name": "The Armored Traveler", "contentEntities": {"npcs": ["Roxie"]}})
        self.assertEqual(lore["kind"], "npc")
        self.assertEqual(lore["subject"], "Roxie")

    def test_world_and_magic_lore(self):
        self.assertEqual(describe_enemy_theme({"name": "Animated Ice Floes"})["kind"], "world")
        self.assertEqual(describe_enemy_theme({"name": "Storm Calling Magic 2"})["kind"], "magic")

    def test_unknown_is_fictional_not_a_claim_of_actual_mod_content(self):
        lore = describe_enemy_theme({"name": "Yet Another Patch Bundle", "group": "Utilities"})
        self.assertEqual(lore["provenance"], "title-inference")
        self.assertEqual(lore["sourceMod"], "Yet Another Patch Bundle")
        self.assertNotIn("verified", lore)


if __name__ == "__main__":
    unittest.main()
