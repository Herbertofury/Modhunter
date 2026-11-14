"""Test fixture loaded by MO2's bundled basic_games plugin. Never distribute it."""

import mobase

from ..basic_game import BasicGame


class ModhunterSmokeDataChecker(mobase.ModDataChecker):
    """Recognize only the three inert files produced by this owned probe.

    MO2's real Simple Installer requires a game-provided ModDataChecker. An
    absent feature routes even a flat ZIP to the manual installer instead.
    This definition does not fix, move, delete, or accept other archive data.
    """

    def dataLooksValid(self, filetree):
        entries = list(filetree)
        if len(entries) != 3:
            return mobase.ModDataChecker.INVALID
        named = {entry.name(): entry for entry in entries}
        if set(named) != {"smoke-fixture.txt", "ModhunterSmoke.esp", "textures"}:
            return mobase.ModDataChecker.INVALID
        if any(isinstance(named[name], mobase.IFileTree)
               for name in ("smoke-fixture.txt", "ModhunterSmoke.esp")):
            return mobase.ModDataChecker.INVALID
        textures = named["textures"]
        if not isinstance(textures, mobase.IFileTree):
            return mobase.ModDataChecker.INVALID
        contents = list(textures)
        if (len(contents) != 1 or contents[0].name() != "modhunter-smoke.txt"
                or isinstance(contents[0], mobase.IFileTree)):
            return mobase.ModDataChecker.INVALID
        return mobase.ModDataChecker.VALID


class ModhunterSmokeGame(BasicGame):
    Name = "Modhunter Native Smoke Game"
    Author = "Modhunter contributors"
    Version = "1.0.0"
    GameName = "Modhunter CI Fixture"
    GameShortName = "modhuntersmoke"
    GameNexusName = "modhuntersmoke"
    GameNexusId = 0
    GameBinary = "fixture.exe"
    GameDataPath = "Data"
    GameDocumentsDirectory = "%GAME_PATH%/Documents"
    GameSavesDirectory = "%GAME_PATH%/Saves"
    GameSaveExtension = "modhunter-fixture-save"

    def init(self, organizer):
        if not super().init(organizer):
            return False
        self._fixture_data_checker = ModhunterSmokeDataChecker()
        return bool(self._register_feature(self._fixture_data_checker))

    def gameVersion(self):
        # fixture.exe is a text marker, never a real executable or an owned game.
        # The native BasicGame path validation still checks that the file exists.
        return "1.0.0"
