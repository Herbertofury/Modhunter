"""Test fixture loaded by MO2's bundled basic_games plugin. Never distribute it."""

from ..basic_game import BasicGame


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

    def gameVersion(self):
        # fixture.exe is a text marker, never a real executable or an owned game.
        # The native BasicGame path validation still checks that the file exists.
        return "1.0.0"
