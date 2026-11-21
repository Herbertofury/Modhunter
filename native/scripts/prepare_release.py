"""Validate one successful Actions run and stage public Modhunter release assets.

Usage:
  python native/scripts/prepare_release.py ARTIFACTS OUTPUT --commit SHA --run-url URL
  python native/scripts/prepare_release.py --print-version
  python native/scripts/prepare_release.py --benchmark-files
  python native/scripts/prepare_release.py --self-test

This command does not publish anything. OUTPUT must be absent or empty. It
copies the tested ZIP/installer unchanged and only publishes an explicit JSON/
PNG evidence allowlist; diagnostic logs, settings and session files are omitted.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import re
import shutil
import stat
import struct
import tempfile
import tomllib
import zlib
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
PORTABLE_NAME = "Modhunter-Rust-Windows.zip"
JSON_LIMIT = 4 * 1024 * 1024
PNG_LIMIT = 32 * 1024 * 1024
PORTABLE_EXPANDED_LIMIT = 512 * 1024 * 1024
PRODUCTION_FILES = ("plugin.py", "version.py", "download_ids.py", "bridge.py", "gameplay.py", "model.py", "focus.py", "scanner.py", "version_evidence.py", "native_nexus.py", "qt_bridge.py", "intelligence.py",
                    "plugin_intelligence.py", "mod_health.py", "source_adapters.py",
                    "source_registry.py", "update_plan.py", "quick_update.py",
                    "install_brief.py", "content_recovery.py", "duplicate_report.py", "notification_facts.py", "operation_journal.py", "web/index.html")
# Historical evidence retains its original filename and baseline. Packaging and
# public release staging share this list so a version bump cannot silently drop
# earlier measurements or relabel them as evidence from a newer implementation.
BENCHMARK_FILES = (
    "import-comparison.json",
    "plugin-audit-comparison-0.9.1.json",
    "mo2-preflight-comparison.json",
    "plugin-audit-comparison-0.9.2.json",
    "mo2-catalog-delivery-comparison-0.9.2.json",
)
BUNDLED_HELPER = "MO2/mo2_update_arsenal/bin/modhunter-cli.exe"
BUNDLED_QT_HELPER = "MO2/mo2_update_arsenal/bin/modhunter-qt-bridge.dll"
FIXTURE_PLUGIN_SHA256 = hashlib.sha256(
    struct.pack("<4sIIIIHH", b"TES4", 18, 0, 0, 0, 44, 0)
    + b"HEDR" + struct.pack("<HfII", 12, 1.7, 0, 0x800)
).hexdigest()
REQUIRED_MO2_CHECKS = {
    "native_bridge_autostart", "native_callback_subscriptions",
    "native_plugin_initialized", "native_nexus_gateway_created", "native_nexus_qt_binding", "native_ui_initialized",
    "native_profile_snapshot", "loopback_health", "protected_snapshot_authentication",
    "queued_refresh", "queued_focus", "native_dispatch_thread", "native_view_selection",
    "native_window_screenshot", "compact_game_response", "rust_cli_live_probe", "rust_plugin_audit",
    "rust_content_recovery", "native_content_install",
}
LIVE_DESKTOP_SOURCES = ('native/scripts/verify_live_desktop.cjs', 'native/ui/shell.js', 'native/ui/shell.html', 'native/ui/native.css', 'native/crates/modhunter-desktop/src/windows.rs', 'native/crates/modhunter-desktop/src/policy.rs', 'mo2_update_arsenal/plugin.py', 'mo2_update_arsenal/bridge.py', 'mo2_update_arsenal/download_ids.py', 'mo2_update_arsenal/scanner.py', 'mo2_update_arsenal/gameplay.py', 'mo2_update_arsenal/native_nexus.py', 'mo2_update_arsenal/web/atelier.css', 'mo2_update_arsenal/web/app.js', 'mo2_update_arsenal/web/app.template.html')
LIVE_DESKTOP_CHECKS = {"compact_workspace", "automatic_source_check", "automatic_discovery", "live_catalog_not_demo", "toolbar_refresh", "toolbar_plugin_audit", "connection_details", "content_native_ipc_denied", "manual_rediscovery", "disconnected_welcome", "setup_reconnect", "native_layout_1440_dark", "native_layout_900_light", "native_gallery_retains_live_catalog", "native_inspector_returns_focus", "native_dashboard_theme_control_acknowledged"}
LIVE_DESKTOP_SCREENSHOTS = {
    "desktop-window-1440-dark.png", "desktop-window-900-light.png",
    "desktop-window-gallery-light.png", "desktop-window-inspector-light.png",
}
REQUIRED_WATCHTOWER_CHECKS = {
    "trusted_manager_ipc", "remote_ipc_denied", "session_persistent_reopen",
    "session_account_isolation", "session_reset_generation", "authenticated_page_capture",
    "authenticated_page_change", "selected_text_capture", "incomplete_capture_rejected",
    "stale_operation_rejected", "cancelled_capture_rejected", "same_session_popup",
    "public_https_end_to_end",
    "download_requires_explicit_consent", "authenticated_direct_archive_download",
    "authenticated_blob_archive_download", "download_rejection_prevents_bytes",
    "download_cancel_cleans_partial", "download_html_rejected", "download_owner_close_cancels",
    "download_stale_source_cancels", "download_receipt_survives_reopen",
    "download_exact_file_authority",
    "native_privacy_quick_hide_and_capture", "native_browser_cache_and_resource_policy",
    "native_notification_exact_item_actions",
}
ARCHIVE_SOURCES = (
    "native/crates/modhunter-core/src/archive_review.rs", "native/crates/modhunter-core/src/acquisition/mod.rs",
    "native/crates/modhunter-core/src/content_vault/mod.rs", "native/crates/modhunter-core/src/bin/modhunter-cli.rs",
    "native/scripts/prepare_archive_tools.py", "native/scripts/verify_archive_review.py",
)
ARCHIVE_CHECKS = {
    "zip_flat_complete_bytes", "zip_unambiguous_wrapper", "fomod_choices_remain_unknown",
    "unknown_layout_preserves_files", "sidecars_not_silently_dropped", "seven_zip_lzma2_bytes",
    "rar4_stored_bytes", "rar5_stored_bytes", "parent_traversal", "case_collision", "file_directory_alias",
    "alternate_stream", "device_name", "symbolic_link_before_extract", "encrypted_requires_manual_handling",
    "corrupt_archive_does_not_leave_stage", "changed_receipt_hash", "untrusted_helper_not_executed",
    "selected_archive_exact_bytes_and_provenance", "selected_archive_preserves_full_requested_set",
    "selection_empty", "selection_unknown", "selection_traversal", "selection_duplicate",
    "fomod_cannot_be_filtered_around_native_choices", "source_files_and_helpers_stayed_unchanged",
}
ARCHIVE_PINS = {"7z.exe":"dc0493429fe28136fcb177a0e77c9875cbb3b0bff5f2f8eeba4df302e98cef76",
                "7z.dll":"500db9eb17f3c21b729ba41dd02ecfe1823da05028c7e4ba129084cc74cc364e"}
REQUIRED_WATCHTOWER_BROWSER_CHECKS = {
    "page_add_and_targeted_check", "sessions_and_reset", "observation_acknowledgment_and_restore",
    "import_preview_and_export", "preferences_and_mutes", "native_error_recovery",
    "stale_state_and_focus", "renderer_boundary", "private_text_and_untrusted_content",
    "responsive_complete_list",
}
WATCHTOWER_BROWSER_SOURCES = (
    "native/ui/watchtower.html", "native/ui/watchtower.css", "native/ui/watchtower.js",
    "tests/fixtures/watchtower_native_fixture.js", "tests/test_watchtower_browser.py",
)
NATIVE_MANAGER_SOURCES = ('native/ui/watchtower.html', 'native/ui/watchtower.css', 'native/ui/watchtower.js', 'native/ui/watchtower-review.js', 'native/ui/watchtower-maintenance.js', 'native/ui/watchtower-inventory.js', 'native/ui/watchtower-queue.js', 'native/ui/watchtower-notices.js', 'native/ui/watchtower-native.css', 'native/ui/locale-catalogs.js', 'native/ui/preferences.js', 'native/ui/preferences.css', 'tests/test_watchtower_native_ui.py', 'tests/test_watchtower_browser.py', 'tests/fixtures/watchtower_native_fixture.js')
NATIVE_MANAGER_SOURCES += ('tests/prepare_ci_fonts.py',)
CJK_TEXT = '外觀、隱私與維護'
CJK_EXPECTED_FONT = {
    'upstreamRepository': 'notofonts/noto-cjk',
    'upstreamCommit': 'f8d157532fbfaeda587e826d4cd5b21a49186f7c',
    'fileName': 'NotoSansCJKtc-Regular.otf', 'familyName': 'Noto Sans CJK TC',
    'postScriptName': 'NotoSansCJKtc-Regular', 'bytes': 16435884,
    'sha256': 'dce08bd4fd91aa8aa76ed8fea4b694c2dfb8550f67871e326843212ddbeb88b4',
}
CJK_FILES = {
    'Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf': {
        'bytes': 16435884, 'sha256': CJK_EXPECTED_FONT['sha256'],
        'gitBlob': 'f9376bae1d421520f73a3c6c9d50b22f89548301'},
    'Sans/LICENSE': {'bytes': 4301,
        'sha256': '6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2',
        'gitBlob': 'd952d62c065f3f35fb83a173496e90b21525aef3'},
}
FONT_SUPPLEMENT_COMMIT = '925ebb8364f5ec2cb0614305900782f44d32a3ab'
FONT_SUPPLEMENTS = {
    'Noto Sans Runic': {
        'path': 'hinted/ttf/NotoSansRunic/NotoSansRunic-Regular.ttf',
        'bytes': 9044, 'sha256': '49e540798dedccf24301437fb5ea5c8a3f42143b1b6c108c1d48c78a41c4ce0f',
        'gitBlob': '114595f54023e304a3e66c9f904da56e5d1b13f4',
        'postScriptName': 'NotoSansRunic-Regular', 'text': 'ᛟᚠ'},
    'Noto Sans Symbols2': {
        'path': 'hinted/ttf/NotoSansSymbols2/NotoSansSymbols2-Regular.ttf',
        'bytes': 656852, 'sha256': '876598db5f1e3bef2bc77c4d94c5b0d5d2c1620be4878a06f18fb5434e331688',
        'gitBlob': '45df8301c7bdfaccff52e30a0973132e94b2e95d',
        'postScriptName': 'NotoSansSymbols2-Regular', 'text': '⌖'},
}
FONT_PREREQUISITE_FILES = {
    **CJK_FILES,
    **{font['path']: {key: font[key] for key in ('bytes', 'sha256', 'gitBlob')}
       for font in FONT_SUPPLEMENTS.values()},
    'LICENSE': {'bytes': 4377,
                'sha256': '0dab92d0544f7b233403f14b84a663bdbfa746982eda629e7f4f9ffe1b036feb',
                'gitBlob': 'c82d72e422e2d08c5ab439b6bac7c2177ea0c565'},
}
FONT_VERIFIED_FILES = {
    CJK_EXPECTED_FONT['familyName']: {key: CJK_EXPECTED_FONT[key]
                                    for key in ('bytes', 'sha256', 'postScriptName')},
    **{family: {key: font[key] for key in ('bytes', 'sha256', 'postScriptName')}
       for family, font in FONT_SUPPLEMENTS.items()},
}
FONT_RENDERING_SOURCES = (
    'tests/ci_font_rendering_qa.py', 'tests/prepare_ci_fonts.py',
    'tests/visual_release_qa.py', 'tests/test_frontend_responsiveness.py',
)
FONT_RENDERING_ASSETS = tuple('mo2_update_arsenal/web/' + name for name in (
    'app.template.html', 'style.css', 'aether_fx.css', 'realm.css', 'siege.css',
    'atelier.css', 'mod_insights.css', 'source_insights.css', 'source_insights.js',
    'mod_insights.js', 'app.js', 'catalog.json', 'aether_fx.js', 'realm.js', 'siege.js',
    'realm_layout.html', 'siege_layout.html', 'MO2_Source_Exporter.ps1',
))
FONT_RENDERING_CHECKS = {
    'mo2-locate': ('#modList .mod-inline-locate', '⌖', 'Noto Sans Symbols2', True),
    'runic-crest': ('.realm-brand-rune', 'ᛟ', 'Noto Sans Runic', False),
    'ember-rune': ('.realm-actionbar [data-action="spell"] > b', 'ᚠ', 'Noto Sans Runic', False),
    'rift-rune': ('.realm-run-card:has([data-action="start-rift"]) .realm-run-icon', 'ᛟ', 'Noto Sans Runic', False),
    'zoom-in': ('button[data-action="zoom-in"]', '＋', 'Noto Sans CJK TC', True),
    'zoom-out': ('button[data-action="zoom-out"]', '－', 'Noto Sans CJK TC', True),
}
FONT_RENDERING_SCREENSHOTS = {'ci-font-library.png': (1440, 1000),
                            'ci-font-expedition.png': (900, 1000)}
NATIVE_MANAGER_CHECKS = {'duplicate_report_preserves_whole_mod_and_file_ownership', 'cleanup_requires_a_matching_preview_and_confirmation', 'inventory_caches_complete_results_and_reveals_exact_ids', 'review_preserves_complete_selection_and_exact_install_ids', 'global_preferences_preserve_unsaved_edits_and_switch_language', 'unknown_native_outcome_remains_visible_and_cannot_be_discarded', 'support_export_uses_the_exact_preview_identity', 'new_content_requires_an_explicit_new_target', 'application_reset_requires_exact_phrase_and_preview', 'native_substates_update_without_watchstore_revision', 'readiness_identifies_live_and_offline_profiles_without_scanning', 'notice_click_uses_only_opaque_id_and_opens_current_native_item', 'fomod_selection_is_explicitly_advisory', 'retained_reviews_remain_visible_without_replay', 'retention_requires_exact_preview_and_explicit_confirmation', 'four_locales_preserve_names_focus_and_theme', 'shared_queue_controls_preserve_action_boundaries', 'notice_list_is_private_and_accepts_native_substate_updates', 'stale_notice_rejection_never_opens_a_target', 'lost_acknowledgement_refreshes_submission_state'}
NATIVE_MANAGER_SCREENSHOTS = {'watchtower-exact-notice.png', 'watchtower-file-review.png', 'watchtower-duplicates.png', 'watchtower-file-inventory.png', 'watchtower-preferences-zh.png', 'watchtower-support-preview.png', 'watchtower-preferences-es.png'}
QT_FORWARD_SOURCES = (
    "mo2_update_arsenal/qt_bridge.py", "tests/test_native_qt_bridge.py",
    "native/scripts/verify_qt_forward.py", "native/qt_bridge/qt_bridge.cpp",
    "native/qt_bridge/qt_bridge_fixture.cpp",
    "native/scripts/mo2_smoke_plugin/modhunter_native_smoke/runner.py",
)
QT_FORWARD_CHECKS = {
    "test_cpp_receiver_protected_sender_gap_is_closed_by_typed_helper",
    "test_cpp_receiver_first_file_bootstrap_and_raw_qvariant_payload",
    "test_compiled_helper_rejects_worker_thread_before_qobject_use",
    "test_actual_native_probe_completes_and_checks_lifetimes_under_optimized_python",
    "test_optimized_native_probe_still_rejects_wrong_helper_hash",
    "test_optimized_native_probe_still_rejects_wrong_qt_runtime",
}
WATCHTOWER_SCREENSHOTS = {
    "watchtower-pages.png", "watchtower-sessions.png", "watchtower-details.png", "watchtower-mobile.png",
}
FORBIDDEN_JSON_KEYS = {
    "token", "accesstoken", "refreshtoken", "idtoken", "apikey", "authorization",
    "password", "passwd", "clientsecret", "cookie", "setcookie", "xarsenaltoken",
}
SECRET_TEXT = re.compile(
    r"(?i)(?:x-arsenal-token|authorization|access_token|refresh_token|api_key|apikey|password)"
    r"[\s\"']*[:=][\s\"']*[A-Za-z0-9_+/.=\-]{12,}"
)


class ReleaseError(ValueError):
    """An artifact failed a release gate; no output is publishable."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def source_version(root: Path = ROOT) -> str:
    """Validate the separately deployed app/plugin versions without loading MO2."""
    try:
        workspace = tomllib.loads((root / "native/Cargo.toml").read_text("utf-8"))
        version = workspace["workspace"]["package"]["version"]
        require(isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is not None,
                "The Rust workspace must specify a stable numeric release version")
        config = json.loads((root / "native/crates/modhunter-desktop/tauri.conf.json").read_text("utf-8"))
        require(config.get("version") == version, "Tauri version does not match the Rust workspace")
        locked = tomllib.loads((root / "native/Cargo.lock").read_text("utf-8"))
        for name in ("modhunter-core", "modhunter-desktop"):
            manifest = tomllib.loads((root / "native/crates" / name / "Cargo.toml").read_text("utf-8"))
            require(manifest.get("package", {}).get("version") == {"workspace": True},
                    f"{name} must inherit the Rust workspace version")
            matches = [package for package in locked.get("package", []) if package.get("name") == name]
            require(len(matches) == 1 and matches[0].get("version") == version,
                    f"Cargo.lock contains a missing or stale {name} version")
        plugin = ast.parse((root / "mo2_update_arsenal/version.py").read_text("utf-8"))
        declarations = [node.value for node in plugin.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)]
        require(len(declarations) == 1 and isinstance(declarations[0], ast.Constant)
                and declarations[0].value == version, "Packaged MO2 plugin version does not match the Rust workspace")
        return version
    except (OSError, KeyError, TypeError, SyntaxError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"Cannot read the release version manifests: {exc}") from exc


VERSION = source_version()
VERIFICATION_NAME = f"Modhunter-{VERSION}-Verification.zip"
INSTALLER_NAME = f"Modhunter_{VERSION}_x64-setup.exe"
MO2_PACKAGE_NAME = f"Modhunter-{VERSION}-MO2-Plugin.zip"
STANDALONE_NAME = f"Modhunter-{VERSION}-Standalone.zip"
MO2_PACKAGE_ROOT_FILES = frozenset({
    "BUILD_INFO.json", "Install_MO2_Plugin.ps1", "Install_MO2_Plugin.bat",
    "PROJECT_NOTICE.txt", "THIRD_PARTY_NOTICES.json",
    "START-HERE-MO2-253.md", "GameSync-Update-Techniques.md",
})
VISUAL_BASELINE = "0f2d399244dbe0cc69e82521c497f006832678c3"
INSTALLER_CHECKS = {
    "nsis_installs_executable", "installed_executable_matches_tested_binary",
    "installed_archive_resource_7z.exe", "installed_archive_resource_7z.dll",
    "installed_archive_resource_License.txt", "installed_archive_resource_provenance.json",
    "installed_application_runs_in_webview2", "nsis_provides_uninstaller",
    "nsis_removes_installed_executable", "uninstall_preserves_external_profile",
    "nsis_registers_current_user_installation", "nsis_removes_installed_payload",
    "nsis_removes_current_user_registration",
    "installer_artifacts_stayed_unchanged",
    "candidate_source_stayed_unchanged",
}
NATIVE_LIBRARY_COUNT = 11207
NATIVE_LIBRARY_FIXTURE_SHA = "afd855e4ac76f284630ffb4ed6d9dc37280113c9559aef4402516795b7fef0a0"
NATIVE_LIBRARY_FIXTURE_BYTES = 4696493
NATIVE_LIBRARY_ACTIONS = {
    "listDensity": ("list", "comfy"),
    "listDensityReset": ("list", "compact"),
    "gallery": ("gallery", "compact"),
    "galleryDensity": ("gallery", "comfy"),
    "list": ("list", "comfy"),
    "finalDensityReset": ("list", "compact"),
}
# Engineering ceilings for the complete 11,207-record native CI fixture, fixed
# before the next candidate run. These are publication gates, not promises for
# other PCs. In particular they reject candidate 6's 13.76 s Gallery / 8.61 s
# List stalls instead of treating any positive timing as acceptable.
NATIVE_LIBRARY_BUDGET_MS = {
    "render": 7000, "lastItemSearch": 1500, "searchRestore": 7000,
    "listDensity": 4000, "listDensityReset": 4000,
    "gallery": 5000, "galleryDensity": 4000,
    "list": 5000, "finalDensityReset": 4000,
}
NATIVE_LIBRARY_CHECKS = {
    "packaged_offline_dashboard", "owned_webview_profile", "owned_native_foreground",
    "visible_native_document", "fresh_list_compact", "import_complete",
    "exact_last_item_search", "search_restore_survivor", "no_renderer_errors",
    "unchanged_executable",
} | {name + "_complete" for name in NATIVE_LIBRARY_ACTIONS}
NATIVE_LIBRARY_METRICS = {
    "ScriptDuration", "LayoutDuration", "RecalcStyleDuration", "TaskDuration",
    "LayoutCount", "RecalcStyleCount", "Nodes", "JSHeapUsedSize",
}
VISUAL_SCREENSHOTS = {
    f"{variant}/{name}" for variant in ("before", "after") for name in (
        "library-1440-dark.png", "library-1440-light.png", "library-900-dark.png",
        "library-390-light.png", "updates-1440-dark.png", "inspector-1440-dark.png",
        "desktop-chrome-900-light.png", "watchtower-add-dialog-900-light.png",
    )
} | {"after/public-projects-gallery-1440-dark.png", "after/public-projects-gallery-1440-light.png"}


def validate_installer_report(report: dict, commit: str, installer_hash: str, executable_hash: str) -> None:
    require(report.get("schemaVersion") == 1 and report.get("ok") is True
            and report.get("candidateCommit") == commit and report.get("sourceDirty") is False,
            "Windows installer receipt does not identify the clean tested candidate")
    require(report.get("installerSha256") == installer_hash
            and report.get("executableSha256") == executable_hash
            and report.get("installedExecutableSha256") == executable_hash,
            "Windows installer did not install the exact tested executable")
    require(report.get("installerScriptSha256") == file_hash(ROOT / "native/scripts/installer_smoke.ps1"),
            "Windows installer acceptance script differs from the candidate")
    checks = report.get("checks")
    require(isinstance(checks, list) and len(checks) == len(INSTALLER_CHECKS)
            and all(isinstance(row, dict) and row.get("passed") is True for row in checks)
            and {row.get("name") for row in checks} == INSTALLER_CHECKS,
            "Windows installer acceptance omitted or failed an install/launch/uninstall check")
    resources = report.get("archiveResourceHashes")
    require(isinstance(resources, dict) and all(resources.get(name) == digest for name, digest in ARCHIVE_PINS.items()),
            "Installed application archive tools differ from the pinned helpers")


def validate_visual_report(report: dict, commit: str) -> None:
    require(report.get("ok") is True and report.get("candidateCommit") == commit
            and report.get("sourceDirty") is False and report.get("baselineCommit") == VISUAL_BASELINE,
            "Visual acceptance is incomplete or does not identify the clean candidate and v0.12.0 baseline")
    sources = (report.get("assets") or {}).get("after")
    require(isinstance(sources, dict) and bool(sources)
            and all(isinstance(name, str) and name.startswith("mo2_update_arsenal/web/")
                    and ".." not in PurePosixPath(name).parts and file_hash(ROOT / name) == digest
                    for name, digest in sources.items()),
            "Visual acceptance source hashes differ from the shipped dashboard")
    require({"mo2_update_arsenal/web/app.js", "mo2_update_arsenal/web/app.template.html",
             "mo2_update_arsenal/web/atelier.css"}.issubset(sources),
            "Visual acceptance omitted the primary dashboard sources")
    library = report.get("completeLibrary") or {}
    count, rounds = library.get("mods"), library.get("rounds")
    require(type(count) is int and count >= 10000 and type(rounds) is int and rounds >= 3,
            "Visual acceptance did not measure the complete large library")
    variants = library.get("variants") or {}
    require(set(variants) == {"before", "after"}, "Visual acceptance omitted a comparison variant")
    for samples in variants.values():
        require(isinstance(samples, list) and len(samples) == rounds and all(
            isinstance(row, dict) and row.get("rows") == count and row.get("complete") is True
            and row.get("allLaidOut") is True and row.get("noContentVisibility") is True
            and isinstance(row.get("renderMs"), (int, float)) and row["renderMs"] > 0
            and isinstance(row.get("lastItemSearchMs"), (int, float)) and row["lastItemSearchMs"] > 0
            for row in samples), "Visual comparison dropped, hid or failed to search library records")


def regular_file(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseError(f"Required artifact is missing: {path}") from exc
    require(stat.S_ISREG(metadata.st_mode) and not path.is_symlink(), f"Artifact is not a regular file: {path}")
    if hasattr(metadata, "st_file_attributes"):
        require(not metadata.st_file_attributes & 0x400, f"Reparse artifact rejected: {path}")
    return path


def artifact_directory(path: Path) -> Path:
    require(path.is_dir() and not path.is_symlink(), f"Required artifact directory is missing or linked: {path}")
    return path


def bounded_bytes(path: Path, maximum: int) -> bytes:
    regular_file(path)
    require(path.stat().st_size <= maximum, f"Artifact exceeds its {maximum}-byte limit: {path.name}")
    with path.open("rb") as handle:
        content = handle.read(maximum + 1)
    require(len(content) <= maximum, f"Artifact grew beyond its read limit: {path.name}")
    return content


def json_object(content: bytes, description: str) -> dict:
    require(len(content) <= JSON_LIMIT, f"JSON proof is too large: {description}")
    try:
        value = json.loads(content.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"Invalid JSON proof: {description}") from exc
    require(isinstance(value, dict), f"Expected a JSON object: {description}")
    reject_credentials(value, description)
    return value


def read_json(path: Path) -> dict:
    return json_object(bounded_bytes(path, JSON_LIMIT), path.name)


def reject_credentials(value: object, description: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            require(normalized not in FORBIDDEN_JSON_KEYS, f"Credential field must not appear in public proof: {description}")
            reject_credentials(child, description)
    elif isinstance(value, list):
        for child in value:
            reject_credentials(child, description)
    elif isinstance(value, str):
        require(SECRET_TEXT.search(value) is None, f"Credential text must not appear in public proof: {description}")


def png_bytes(path: Path, expected_dimensions: tuple[int, int] | None = None) -> bytes:
    data = bounded_bytes(path, PNG_LIMIT)
    require(data.startswith(b"\x89PNG\r\n\x1a\n"), f"Not a PNG screenshot: {path.name}")
    offset, seen_header, seen_data, seen_end = 8, False, False, False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        require(end <= len(data), f"Truncated PNG screenshot: {path.name}")
        payload = data[offset + 8:offset + 8 + length]
        checksum = struct.unpack(">I", data[offset + 8 + length:end])[0]
        require(zlib.crc32(kind + payload) & 0xFFFFFFFF == checksum, f"PNG checksum failed: {path.name}")
        if not seen_header:
            require(kind == b"IHDR" and length == 13, f"PNG has no valid image header: {path.name}")
            width, height = struct.unpack(">II", payload[:8])
            require(0 < width <= 65535 and 0 < height <= 65535, f"Invalid PNG dimensions: {path.name}")
            require(expected_dimensions is None or (width, height) == expected_dimensions,
                    f"Unexpected PNG screenshot dimensions: {path.name}")
            seen_header = True
        elif kind == b"IHDR":
            raise ReleaseError(f"PNG has duplicate image headers: {path.name}")
        if kind == b"IDAT":
            seen_data = True
        if kind == b"IEND":
            require(length == 0 and end == len(data), f"Invalid PNG end marker: {path.name}")
            seen_end = True
            break
        offset = end
    require(seen_header and seen_data and seen_end, f"Incomplete PNG screenshot: {path.name}")
    return data


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def public_json(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def validate_run(commit: str, run_url: str) -> tuple[str, str]:
    require(re.fullmatch(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?", commit) is not None, "--commit must be the full hexadecimal build commit")
    parsed = urlsplit(run_url)
    require(parsed.scheme == "https" and parsed.hostname == "github.com" and parsed.port is None
            and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment,
            "--run-url must be a canonical HTTPS GitHub Actions run URL")
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/actions/runs/[0-9]+(?:/attempts/[0-9]+)?/?", parsed.path)
    require(match is not None, "--run-url must identify a GitHub Actions run")
    return commit.lower(), "https://github.com/" + match.group(1)


def validate_native_report(report: dict) -> None:
    require(report.get("ok") is True, "Windows WebView2 smoke did not pass")
    require(report.get("nativeWebView2") is True and report.get("shellIpc") is True, "Windows report does not prove native WebView2 and toolbar IPC")
    require(report.get("exportDownloadVerified") is True, "Windows exported-backup verification did not pass")
    require(report.get("gameViewNativeIpcDenied") in ("1", True), "Game-view native IPC isolation was not verified")
    proof = report.get("proof")
    require(isinstance(proof, dict) and proof.get("chapterColors") == "1" and proof.get("chapterProgress") == "1",
            "Packaged offline chapter colors and zero-progress rendering were not verified")


def native_sources():
    spec=importlib.util.spec_from_file_location("modhunter_native_binding",ROOT/"native/scripts/bind_native_proof.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.source_names()


def validate_native_binding(report:dict,commit:str,executable_hash:str)->None:
    require(report.get("nativeBindingSchema")==1 and report.get("candidateCommit")==commit
            and report.get("sourceDirty") is False and report.get("executableSha256")==executable_hash,
            "Native fixture receipt does not identify the clean source commit and packaged executable")
    expected=native_sources();sources=report.get("sourceFiles")
    require(isinstance(sources,dict) and set(sources)==set(expected)
            and all(sources[name]==file_hash(ROOT/name) for name in expected),
            "Native fixture source hashes do not match this release")


def validate_native_library(report: dict, commit: str, executable_hash: str) -> None:
    """Require complete native proof, restored interactions and bounded CI timings."""
    validate_native_binding(report, commit, executable_hash)
    require(report.get("schemaVersion") == 1 and report.get("ok") is True
            and report.get("nativeWebView2") is True and report.get("phase") == "complete"
            and report.get("errors") == [], "Native library performance did not complete without errors")
    require(report.get("scriptSha256") == file_hash(ROOT / "native/scripts/measure_native_library.cjs")
            and report.get("launcherSha256") == file_hash(ROOT / "native/scripts/benchmark_native_library.ps1"),
            "Native library performance launcher or measurement worker differs from the candidate")
    require(type(report.get("records")) is int and report["records"] == NATIVE_LIBRARY_COUNT
            and report.get("fixtureSha256") == NATIVE_LIBRARY_FIXTURE_SHA
            and type(report.get("fixtureBytes")) is int and report["fixtureBytes"] == NATIVE_LIBRARY_FIXTURE_BYTES,
            "Native library performance did not use the exact shared 11,207-record fixture")
    checks = report.get("checks")
    require(isinstance(checks, list) and len(checks) == len(NATIVE_LIBRARY_CHECKS)
            and all(isinstance(row, dict) and row.get("passed") is True for row in checks)
            and {row.get("name") for row in checks} == NATIVE_LIBRARY_CHECKS,
            "Native library performance omitted or failed a required runtime/interaction check")

    def finite(value, positive=False):
        return type(value) in (int, float) and math.isfinite(value) and (not positive or value > 0)

    engine, initial = report.get("engine"), report.get("initial")
    require(type(report.get("nativeProcessId")) is int and report["nativeProcessId"] > 0
            and isinstance(engine, dict)
            and all(isinstance(engine.get(name), str) and engine[name].strip()
                    for name in ("protocolVersion", "product", "revision", "userAgent", "jsVersion", "executable"))
            and re.fullmatch(r"[^\s/]+/[0-9][^\s]*", engine["product"]) is not None
            and "Windows" in engine["userAgent"]
            and PureWindowsPath(engine["executable"]).name.lower() == "msedgewebview2.exe"
            and PureWindowsPath(engine["executable"]).is_absolute(),
            "Native library performance does not identify the actual Windows WebView2 engine")
    require(isinstance(initial, dict) and initial.get("url") == "http://tauri.localhost/Modhunter.html"
            and initial.get("visibilityState") == "visible"
            and initial.get("view") == "list" and initial.get("density") == "compact"
            and initial.get("prefersReducedMotion") is False
            and type(initial.get("documentHasFocus")) is bool
            and all(finite(initial.get(name), positive=True) for name in ("width", "height", "devicePixelRatio")),
            "Native library performance omitted the visible packaged native viewport and initial state")

    def geometry(value, view, density, label):
        require(isinstance(value, dict) and type(value.get("rows")) is int
                and value["rows"] == NATIVE_LIBRARY_COUNT and value.get("lastId") == "item-11206"
                and value.get("completeOrderedIds") is True and value.get("allLaidOut") is True
                and value.get("noContentVisibility") is True
                and value.get("view") == view and value.get("density") == density,
                f"Native library performance dropped, hid or reordered records at {label}")
        viewport = value.get("viewport")
        require(isinstance(viewport, dict)
                and all(finite(viewport.get(name), positive=True) for name in ("width", "height", "devicePixelRatio"))
                and all(isinstance(value.get(name), dict)
                        and all(finite(value[name].get(side), positive=True) for side in ("width", "height"))
                        for name in ("firstRect", "lastRect")),
                f"Native library performance omitted actual laid-out geometry at {label}")

    geometry(report.get("importGeometry"), "list", "compact", "import")
    geometry(report.get("restoreGeometry"), "list", "compact", "search restoration")
    timings, interactions = report.get("timingsMs"), report.get("interactions")
    require(isinstance(timings, dict) and set(timings) == {"render", "lastItemSearch", "searchRestore"} | set(NATIVE_LIBRARY_ACTIONS)
            and all(finite(value, positive=True) for value in timings.values()),
            "Native library performance timings must be complete, positive and finite")
    for name, ceiling in NATIVE_LIBRARY_BUDGET_MS.items():
        require(timings[name] <= ceiling,
                f"Native library performance exceeds the {name} release ceiling: {timings[name]:.1f} ms > {ceiling} ms")
    require(isinstance(interactions, dict) and set(interactions) == set(NATIVE_LIBRARY_ACTIONS),
            "Native library performance omitted a List/Gallery/density interaction")
    for name, (view, density) in NATIVE_LIBRARY_ACTIONS.items():
        action = interactions[name]
        require(isinstance(action, dict) and finite(action.get("wallMs"), positive=True)
                and action["wallMs"] == timings[name], f"Native library interaction timing differs: {name}")
        endpoint = action.get("frameEndpoint")
        require(isinstance(endpoint, dict) and endpoint.get("structuralBatchActive") is False
                and endpoint.get("transitionProperty") == "background"
                and endpoint.get("transitionDuration") == "0.12s",
                f"Native library interaction did not restore ordinary hover transitions at its measured endpoint: {name}")
        geometry(action.get("completeLibrary"), view, density, name)
        require(all(isinstance(action.get(key), dict) and set(action[key]) == NATIVE_LIBRARY_METRICS
                    and all(finite(value) for value in action[key].values())
                    for key in ("metricsBefore", "metricsAfter", "metricDeltas")),
                f"Native library performance omitted finite engine cost measurements at {name}")
    isolation = report.get("isolation")
    require(isinstance(isolation, dict) and isolation.get("schemaVersion") == 1
            and isolation.get("ok") is True and isolation.get("cleanupErrors") == []
            and type(isolation.get("policyValuesRemoved")) is int and isolation["policyValuesRemoved"] == 2
            and finite(isolation.get("elapsedMs"), positive=True),
            "Native library performance did not restore its owned configuration and WebView2 policies")
    folders = isolation.get("restoredFolders")
    require(isinstance(folders, list) and len(folders) == 2
            and all(isinstance(row, dict) and row.get("manifestMatched") is True
                    and type(row.get("originalExisted")) is bool and isinstance(row.get("path"), str)
                    and PureWindowsPath(row["path"]).is_absolute()
                    and PureWindowsPath(row["path"]).name.lower() == "com.modhunter.desktop" for row in folders)
            and len({str(PureWindowsPath(row["path"])).lower() for row in folders}) == 2,
            "Native library performance did not restore both complete known-folder manifests")


def validate_archive_report(report:dict,commit:str,cli_hash:str)->None:
    require(report.get("ok") is True and report.get("schema")==1 and report.get("kind")=="actual-archive-review"
            and report.get("platform")=="Windows" and report.get("candidateCommit")==commit
            and report.get("sourceDirty") is False and report.get("cliSha256")==cli_hash,
            "Archive proof does not identify the exact clean Windows release CLI")
    require(report.get("helperSha256")==ARCHIVE_PINS["7z.exe"] and report.get("helperDllSha256")==ARCHIVE_PINS["7z.dll"],
            "Archive proof did not execute the pinned official helper")
    sources=report.get("sources")
    require(isinstance(sources,dict) and set(sources)==set(ARCHIVE_SOURCES)
            and all(sources[name]==file_hash(ROOT/name) for name in ARCHIVE_SOURCES),
            "Archive proof does not match the current extraction and selection source")
    checks=report.get("checks")
    require(isinstance(checks,list) and len(checks)==len(ARCHIVE_CHECKS)
            and all(isinstance(row,dict) and row.get("ok") is True for row in checks)
            and {row.get("name") for row in checks}==ARCHIVE_CHECKS,
            "Archive proof omitted, repeated or failed a required format or safety case")
    require(report.get("nativeMo2Claim") is False and report.get("nativeWebView2Claim") is False,
            "Archive-only proof must not claim native UI execution")


def validate_watchtower_report(report: dict) -> None:
    require(report.get("ok") is True and report.get("nativeWebView2") is True,
            "Native Watchtower WebView2 fixture did not pass")
    require(type(report.get("watchtowerSchema")) is int and report["watchtowerSchema"] == 1,
            "Native Watchtower receipt must use schema 1")
    checks = report.get("checks")
    require(isinstance(checks, list) and bool(checks), "Native Watchtower receipt contains no checks")
    require(all(isinstance(check, dict) and isinstance(check.get("name"), str) and bool(check["name"])
                and check.get("passed") is True for check in checks),
            "Native Watchtower receipt contains a failed or invalid check")
    names = [check["name"] for check in checks]
    require(len(names) == len(set(names)), "Native Watchtower receipt repeats a check")
    missing = REQUIRED_WATCHTOWER_CHECKS - set(names)
    require(not missing, "Native Watchtower receipt omitted required passing checks: " + ", ".join(sorted(missing)))


def validate_watchtower_browser(report: dict, commit: str) -> None:
    require(report.get("ok") is True and report.get("realChromium") is True,
            "Watchtower Chromium interaction proof did not pass")
    require(type(report.get("watchtowerSchema")) is int and report["watchtowerSchema"] == 1,
            "Watchtower browser receipt must use schema 1")
    require(report.get("candidateCommit") == commit and report.get("sourceDirty") is False,
            "Watchtower browser proof does not identify the clean tested build commit")
    sources = report.get("sourceFiles")
    require(isinstance(sources, dict) and set(sources) == set(WATCHTOWER_BROWSER_SOURCES)
            and all(sources[name] == file_hash(ROOT / name) for name in WATCHTOWER_BROWSER_SOURCES),
            "Watchtower browser proof does not match the shipped UI and acceptance source")
    checks = report.get("checks")
    require(isinstance(checks, list) and bool(checks)
            and all(isinstance(check, dict) and isinstance(check.get("name"), str) and bool(check["name"])
                    and check.get("passed") is True for check in checks),
            "Watchtower browser proof contains a failed or invalid check")
    names = [check["name"] for check in checks]
    require(len(names) == len(set(names)), "Watchtower browser proof repeats a check")
    missing = REQUIRED_WATCHTOWER_BROWSER_CHECKS - set(names)
    require(not missing, "Watchtower browser proof omitted required passing checks: " + ", ".join(sorted(missing)))
    screenshots = report.get("screenshots")
    require(isinstance(screenshots, list) and all(isinstance(name, str) for name in screenshots)
            and WATCHTOWER_SCREENSHOTS.issubset(set(screenshots)),
            "Watchtower browser proof omitted required screenshots")


def validate_native_manager_ui(report: dict, commit: str) -> None:
    require(report.get("schema") == 1 and report.get("kind") == "native-manager-ui"
            and report.get("ok") is True and report.get("realChromium") is True,
            "Native review manager requires a passing actual Chromium receipt")
    require(report.get("candidateCommit") == commit and report.get("sourceDirty") is False,
            "Native review manager proof does not identify the clean candidate")
    require(report.get("testsRun") == len(NATIVE_MANAGER_CHECKS) and report.get("skipped") == 0,
            "Native review manager proof skipped or omitted an interaction")
    sources = report.get("sourceFiles")
    require(isinstance(sources, dict) and set(sources) == set(NATIVE_MANAGER_SOURCES)
            and all(sources[name] == file_hash(ROOT / name) for name in NATIVE_MANAGER_SOURCES),
            "Native review manager proof does not match the shipped UI and fixture")
    checks = report.get("checks")
    require(isinstance(checks, list) and len(checks) == len(NATIVE_MANAGER_CHECKS)
            and all(isinstance(check, dict) and check.get("passed") is True for check in checks)
            and {check.get("name") for check in checks} == NATIVE_MANAGER_CHECKS,
            "Native review manager proof failed, repeated or omitted a required interaction")


def font_coverage(text: str) -> dict:
    return {'text': text, 'method': 'fc-query FreeType/fontconfig charset',
            'glyphs': [{'character': char, 'codepoint': f'U+{ord(char):04X}', 'covered': True}
                       for char in dict.fromkeys(text)]}


def validate_font_prerequisite(receipt: dict) -> None:
    """Bind CI-only font installation and cmap checks to the verified upstream bytes."""
    expected = CJK_EXPECTED_FONT
    require(isinstance(receipt, dict) and type(receipt.get('schema')) is int
            and receipt.get('schema') == 1 and receipt.get('ok') is True
            and receipt.get('scriptSha256') == file_hash(ROOT / 'tests/prepare_ci_fonts.py')
            and receipt.get('upstreamRepository') == expected['upstreamRepository']
            and receipt.get('upstreamCommit') == expected['upstreamCommit']
            and receipt.get('license') == 'OFL-1.1'
            and receipt.get('fontFamily') == expected['familyName']
            and isinstance(receipt.get('registeredFamily'), str)
            and expected['familyName'] in receipt.get('registeredFamily', '').split(',')
            and receipt.get('registeredPostScriptName') == expected['postScriptName'],
            'CJK font preparation does not identify the current verified prerequisite')
    files = receipt.get('files')
    require(isinstance(files, list) and len(files) == len(FONT_PREREQUISITE_FILES)
            and all(isinstance(entry, dict) and isinstance(entry.get('path'), str) for entry in files)
            and {entry['path'] for entry in files} == set(FONT_PREREQUISITE_FILES),
            'CI font preparation omitted, repeated or added a font or license file')
    for path, identity in FONT_PREREQUISITE_FILES.items():
        matches = [entry for entry in files if isinstance(entry, dict) and entry.get('path') == path]
        require(len(matches) == 1 and all(matches[0].get(key) == value for key, value in identity.items()),
                f'CI font preparation changed the pinned font or license: {path}')
    require(receipt.get('glyphCoverage') == font_coverage(CJK_TEXT),
            'CI font preparation omitted the Chinese heading cmap proof')
    supplements = receipt.get('supplementalFonts')
    require(isinstance(supplements, list) and len(supplements) == len(FONT_SUPPLEMENTS)
            and all(isinstance(font, dict) and isinstance(font.get('fontFamily'), str) for font in supplements)
            and {font['fontFamily'] for font in supplements} == set(FONT_SUPPLEMENTS),
            'CI font preparation omitted or repeated a required supplemental face')
    for family, identity in FONT_SUPPLEMENTS.items():
        font = next(font for font in supplements if font['fontFamily'] == family)
        require(font.get('upstreamRepository') == 'notofonts/noto-fonts'
                and font.get('upstreamCommit') == FONT_SUPPLEMENT_COMMIT
                and font.get('license') == 'OFL-1.1'
                and all(font.get(key) == identity[key] for key in ('path', 'bytes', 'sha256'))
                and isinstance(font.get('registeredFamily'), str)
                and family in font['registeredFamily'].split(',')
                and font.get('registeredPostScriptName') == identity['postScriptName']
                and font.get('glyphCoverage') == font_coverage(identity['text']),
                f'CI font preparation changed the pinned face or glyph coverage: {family}')
    require(receipt.get('supplementalGlyphCoverage') == {'fullwidthZoom': {
                'fontFamily': expected['familyName'], 'postScriptName': expected['postScriptName'],
                **font_coverage('＋－')}},
            'CI font preparation omitted the fullwidth zoom glyph coverage')


def validate_cjk_font(proof: dict, receipt: dict, receipt_hash: str) -> None:
    """Reject translated DOM text that was never rendered with actual CJK glyphs."""
    validate_font_prerequisite(receipt)
    expected = CJK_EXPECTED_FONT
    coverage = font_coverage(CJK_TEXT)
    require(isinstance(proof, dict) and proof.get('schema') == 1 and proof.get('ok') is True
            and proof.get('locale') == 'zh-TW' and proof.get('text') == CJK_TEXT
            and proof.get('visible') is True and proof.get('inViewport') is True
            and proof.get('receivesPointer') is True
            and proof.get('expectedFont') == expected
            and proof.get('fontReceiptSha256') == receipt_hash
            and proof.get('glyphCoverage') == coverage and receipt.get('glyphCoverage') == coverage,
            'CJK rendered-font proof is missing, invisible or disconnected from its pinned font')
    fonts = proof.get('fonts')
    require(isinstance(fonts, list) and bool(fonts)
            and all(isinstance(font, dict) and type(font.get('glyphCount')) is int
                    and font['glyphCount'] >= 0 for font in fonts),
            'CJK rendered-font proof omitted actual Chromium font usage')
    matched = sum(font['glyphCount'] for font in fonts
                  if font.get('familyName') == expected['familyName']
                  and font.get('postScriptName') == expected['postScriptName']
                  and font.get('isCustomFont') is False)
    require(matched == sum(font['glyphCount'] for font in fonts) == len(CJK_TEXT)
            and proof.get('matchedGlyphCount') == proof.get('expectedGlyphCount') == len(CJK_TEXT),
            'CJK rendered-font proof did not paint every required glyph with the verified face')


def validate_ci_font_rendering(report: dict, commit: str, receipt: dict, receipt_hash: str) -> None:
    """Require actual dashboard and practice-game glyph use, not DOM strings alone."""
    validate_font_prerequisite(receipt)
    require(isinstance(report, dict) and type(report.get('schema')) is int and report.get('schema') == 1
            and report.get('kind') == 'ci-font-rendering' and report.get('ok') is True
            and report.get('phase') == 'complete' and report.get('errors') == []
            and report.get('candidateCommit') == commit and report.get('sourceDirty') is False
            and report.get('sourceStatusAtStart') == [] and report.get('sourceStatusAtEnd') == [],
            'CI glyph rendering requires a complete, error-free receipt from the clean candidate')
    for key, paths in (('sourceFiles', FONT_RENDERING_SOURCES), ('assets', FONT_RENDERING_ASSETS)):
        sources = report.get(key)
        require(isinstance(sources, dict) and set(sources) == set(paths)
                and all(sources[path] == file_hash(ROOT / path) for path in paths),
                f'CI glyph rendering does not match the exact tested {key}')
    require(report.get('fontHelperSha256') == receipt['scriptSha256']
            and report.get('fontReceiptSha256') == receipt_hash
            and report.get('verifiedFontFiles') == FONT_VERIFIED_FILES,
            'CI glyph rendering is disconnected from the verified font prerequisite')
    require(type(report.get('catalogRows')) is int and report['catalogRows'] == 227
            and isinstance(report.get('catalogFixtureSha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', report['catalogFixtureSha256']) is not None
            and isinstance(report.get('browserVersion'), str) and bool(report['browserVersion'])
            and isinstance(report.get('requestedExecutable'), str) and bool(report['requestedExecutable']),
            'CI glyph rendering omitted its complete fixture or actual browser identity')
    require(report.get('game') == {'profileId': 'practice', 'connected': False, 'mode': 'expedition',
                                  'actualCanvasDrawn': True, 'enteredThroughUi': True},
            'CI glyph rendering did not enter and draw the offline practice expedition')
    zoom = report.get('zoomInteraction')
    require(isinstance(zoom, dict) and set(zoom) == {'original', 'increased', 'restored'}
            and all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in zoom.values())
            and zoom['increased'] > zoom['original'] and zoom['restored'] == zoom['original'],
            'CI glyph rendering did not verify the actual zoom controls and restoration')
    checks = report.get('checks')
    require(report.get('requiredChecks') == list(FONT_RENDERING_CHECKS)
            and isinstance(checks, list) and len(checks) == len(FONT_RENDERING_CHECKS)
            and all(isinstance(check, dict) for check in checks)
            and [check.get('name') for check in checks] == list(FONT_RENDERING_CHECKS),
            'CI glyph rendering omitted, repeated or reordered a required glyph check')
    for check in checks:
        name = check['name']
        selector, text, family, interactive = FONT_RENDERING_CHECKS[name]
        postscript = FONT_VERIFIED_FILES[family]['postScriptName']
        require(check.get('ok') is True and check.get('selector') == selector
                and type(check.get('nodeIndex')) is int and check['nodeIndex'] == 0
                and check.get('expectedText') == check.get('text') == text
                and check.get('expectedFamily') == family and check.get('expectedPostScriptName') == postscript
                and check.get('visible') is True and check.get('inViewport') is True
                and (not interactive or check.get('receivesPointer') is True),
                f'CI glyph check is missing, invisible or targets a different control: {name}')
        rect = check.get('rect')
        viewport = FONT_RENDERING_SCREENSHOTS['ci-font-library.png' if name == 'mo2-locate'
                                             else 'ci-font-expedition.png']
        require(isinstance(rect, dict) and set(rect) == {'x', 'y', 'width', 'height'}
                and all(type(value) in (int, float) and math.isfinite(value) for value in rect.values())
                and rect['x'] >= 0 and rect['y'] >= 0 and rect['width'] > 0 and rect['height'] > 0
                and rect['x'] + rect['width'] <= viewport[0] and rect['y'] + rect['height'] <= viewport[1],
                f'CI glyph check has no finite visible rectangle: {name}')
        fonts = check.get('fonts')
        require(isinstance(fonts, list) and bool(fonts)
                and all(isinstance(font, dict) and type(font.get('glyphCount')) is int
                        and font['glyphCount'] >= 0 for font in fonts),
                f'CI glyph check omitted actual Chromium font usage: {name}')
        matched = sum(font['glyphCount'] for font in fonts
                      if font.get('familyName') == family and font.get('postScriptName') == postscript
                      and font.get('isCustomFont') is False)
        require(type(check.get('matchedGlyphCount')) is int
                and check['matchedGlyphCount'] == matched == sum(font['glyphCount'] for font in fonts) == 1,
                f'CI glyph check did not render its glyph with the verified face: {name}')
    require(report.get('screenshots') == list(FONT_RENDERING_SCREENSHOTS),
            'CI glyph rendering omitted its actual dashboard or expedition screenshot')


def validate_mo2(result: dict, receipt: dict) -> None:
    require(result.get("ok") is True and result.get("phase") == "completed", "MO2 orchestration did not complete successfully")
    require(result.get("nativeExitCode") == 0 and not isinstance(result.get("nativeExitCode"), bool), "MO2 did not exit cleanly with code zero")
    require(result.get("release", {}).get("version") == "2.5.2", "Expected evidence from the pinned official MO2 2.5.2 release")
    require(receipt.get("ok") is True and receipt.get("nativeMO2") is True, "Native MO2 receipt did not pass")
    require(result.get("nativeReceipt") == receipt, "MO2 orchestration and native receipts disagree")
    checks = receipt.get("checks")
    require(isinstance(checks, list) and bool(checks), "MO2 receipt contains no checks")
    require(all(isinstance(check, dict) and check.get("passed") is True for check in checks), "MO2 receipt contains a failed or invalid check")
    passing = {check.get("name") for check in checks}
    missing = REQUIRED_MO2_CHECKS - passing
    require(not missing, "MO2 receipt omitted required passing checks: " + ", ".join(sorted(missing)))
    for name, flags in (("rust_content_recovery",("realQt","actualModBytesRestored","nativeRefreshAcknowledged","wrongTargetRejected","laterEditPreserved","lostAckDeduplicated")),
                        ("native_content_install",("realQt","nativeInstallerOpened","nativeInstallVerified","bundledArchiveReviewVerified","actualModBytesRestored","nativeRefreshAcknowledged"))):
        matched=[check for check in checks if check.get("name")==name]
        require(len(matched)==1 and matched[0].get("schemaVersion")==1 and matched[0].get("state")=="complete"
                and matched[0].get("profileId")==receipt.get("profileId") and all(matched[0].get(flag) is True for flag in flags),
                "Native content proof is incomplete for "+name)
        if name=="rust_content_recovery":require(matched[0].get("batchTargets")==2,"Native batch recovery did not verify two exact targets")
        else:
            require(matched[0].get("exactTarget")=="Smoke Disabled" and re.fullmatch(r"[0-9a-f]{64}",str(matched[0].get("archiveSha256",""))) is not None
                    and type(matched[0].get("archiveBytes")) is int and matched[0]["archiveBytes"]>0,
                    "Native installer proof omitted its exact fixture target and archive identity")
    audit_checks = [check for check in checks if check.get("name") == "rust_plugin_audit"]
    require(len(audit_checks) == 1, "Expected one native Rust plugin-audit receipt")
    audit = audit_checks[0]
    profile = receipt.get("profileId")
    require(isinstance(profile, str) and bool(profile) and audit.get("profileId") == profile
            and type(audit.get("schemaVersion")) is int and audit["schemaVersion"] == 1
            and audit.get("state") == "completed" and audit.get("depth") == "records",
            "Native Rust plugin audit did not complete schema 1 deep analysis for the native profile")
    fixture_mods = audit.get("fixtureMods")
    require(isinstance(fixture_mods, dict)
            and all(isinstance(fixture_mods.get(name), dict)
                    and isinstance(fixture_mods[name].get("itemId"), str) and bool(fixture_mods[name]["itemId"])
                    and type(fixture_mods[name].get("pluginCount")) is int
                    and fixture_mods[name]["pluginCount"] >= (1 if name == "Smoke Disabled" else 0)
                    for name in ("Smoke Enabled", "Smoke Disabled")),
            "Native Rust plugin audit did not inspect both exact fixture mods")
    require(fixture_mods["Smoke Enabled"]["itemId"] != fixture_mods["Smoke Disabled"]["itemId"],
            "Native Rust plugin audit repeated the same fixture mod")
    synthetic = audit.get("syntheticPlugin") or {}
    require(synthetic.get("name") == "ModhunterSmoke.esp" and synthetic.get("owner") == "Smoke Disabled"
            and synthetic.get("verified") is True and synthetic.get("bytes") == 42
            and synthetic.get("declaredRecords") == 0 and synthetic.get("sha256") == FIXTURE_PLUGIN_SHA256,
            "Native Rust plugin audit did not verify the inert fixture header and fresh SHA256")
    cli = result.get("rustCli") or {}
    helper_hash = audit.get("helperSha256")
    require(cli.get("required") is True and isinstance(helper_hash, str)
            and re.fullmatch(r"[0-9a-f]{64}", helper_hash) is not None
            and helper_hash == cli.get("sha256"),
            "Native Rust audit helper differs from the supplied compiled CLI")
    qt_checks = [check for check in checks if check.get("name") == "native_nexus_qt_binding"]
    require(len(qt_checks) == 1, "Expected one actual MO2 Qt binding receipt")
    qt = qt_checks[0]
    require(type(qt.get("abi")) is int and qt["abi"] == 1
            and qt.get("buildQt") == qt.get("runtimeQt") == qt.get("qtVersion") == "6.7.1"
            and qt.get("mechanism") == "typed-qt-sender"
            and all(qt.get(name) is True for name in ("nativeQObject", "metadataCompleted", "rawFilesCompleted", "unownedIgnored", "cancelledIgnored"))
            and type(qt.get("networkRequests")) is int and qt["networkRequests"] == 0
            and qt.get("accountAuthentication") is False,
            "Native MO2 proof did not verify the matching Qt helper and owned raw JSON binding fixture")
    qt_binary = result.get("qtBridge") or {}
    require(qt_binary.get("required") is True and isinstance(qt.get("helperSha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", qt["helperSha256"]) is not None
            and qt["helperSha256"] == qt_binary.get("sha256") and qt_binary.get("buildQt") == "6.7.1",
            "Loaded MO2 Qt helper differs from the supplied compiled helper")


def validate_qt_forward(report: dict, commit: str, helper_hash: str) -> None:
    require(report.get("ok") is True and report.get("realQt") is True
            and report.get("ownedSignalSelfTest") is True and report.get("sourceDirty") is False,
            "Qt forward-runtime receipt did not pass the real owned-signal check")
    require(report.get("candidateCommit") == commit and report.get("helperSha256") == helper_hash,
            "Qt forward-runtime receipt does not identify this commit and packaged helper")
    require(report.get("schemaVersion") == 1 and type(report.get("abi")) is int and report["abi"] == 1
            and report.get("buildQt") == "6.7.1" and report.get("runtimeQt") == "6.11.1"
            and report.get("pyQtVersion") == "6.11.0" and report.get("mechanism") == "typed-qt-sender",
            "Qt forward-runtime receipt used an unexpected ABI, build or runtime")
    sources = report.get("sourceFiles")
    require(isinstance(sources, dict) and set(sources) == set(QT_FORWARD_SOURCES)
            and all(sources[name] == file_hash(ROOT / name) for name in QT_FORWARD_SOURCES),
            "Qt forward-runtime proof does not match the shipped binding and tests")
    checks = report.get("checks")
    require(type(report.get("testsRun")) is int and report["testsRun"] == len(QT_FORWARD_CHECKS)
            and type(report.get("skipped")) is int and report["skipped"] == 0
            and isinstance(checks, list) and len(checks) == len(QT_FORWARD_CHECKS)
            and all(isinstance(c, dict) and c.get("passed") is True for c in checks)
            and {c.get("name") for c in checks} == QT_FORWARD_CHECKS,
            "Qt forward-runtime receipt omitted, skipped, repeated or failed a required test")


def portable_checksums(archive: ZipFile, index: dict) -> dict[str, str]:
    """Verify the exact manifest shipped to the installer and the user."""
    entry = index.get("SHA256SUMS.txt")
    require(entry is not None and not entry.is_dir() and 0 < entry.file_size <= JSON_LIMIT,
            "Portable package is missing a bounded SHA256SUMS.txt")
    try:
        lines = archive.read(entry).decode("utf-8-sig").splitlines()
    except UnicodeError as exc:
        raise ReleaseError("Portable checksum manifest is not UTF-8") from exc
    expected = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", line)
        require(match is not None, "Portable checksum manifest contains an invalid line")
        name = match[2].replace("\\", "/")
        require(name not in expected, f"Portable checksum manifest repeats a file: {name}")
        expected[name] = match[1].lower()
    files = {name for name, item in index.items() if not item.is_dir()} - {"SHA256SUMS.txt"}
    require(set(expected) == files, "Portable checksum manifest does not cover exactly the packaged files")
    for name, expected_digest in expected.items():
        digest = hashlib.sha256()
        with archive.open(index[name]) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        require(digest.hexdigest() == expected_digest, f"Portable checksum failed: {name}")
    return expected


def validate_live_desktop(report: dict, commit: str, executable_hash: str) -> None:
    require(report.get("ok") is True and report.get("nativeDesktop") is True
            and report.get("nativeMO2") is True and report.get("nativeWebView2") is True,
            "Live desktop acceptance did not execute successfully with native MO2")
    require(report.get("candidateCommit") == commit and report.get("sourceDirty") is False,
            "Live desktop acceptance identifies different or dirty source")
    require(report.get("executableSha256") == executable_hash,
            "Live desktop acceptance tested a different packaged executable")
    checks = report.get("checks")
    require(isinstance(checks, list) and len(checks) == len(LIVE_DESKTOP_CHECKS)
            and all(isinstance(c, dict) and c.get("passed") is True for c in checks)
            and {c.get("name") for c in checks} == LIVE_DESKTOP_CHECKS,
            "Live desktop acceptance has missing, failed or duplicate required checks")
    sources = report.get("sourceFiles")
    require(isinstance(sources, dict) and set(sources) == set(LIVE_DESKTOP_SOURCES)
            and all(sources[name] == file_hash(ROOT / name) for name in LIVE_DESKTOP_SOURCES),
            "Live desktop source hashes do not match this release")
    public_json(report)


_WINDOWS_DEVICE_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³])", re.IGNORECASE)


def portable_member_index(entries: list[ZipInfo]) -> dict[str, ZipInfo]:
    # Index the Windows directory tree, not just complete member strings.
    # Two distinct ZIP names must remain distinct after ordinary extraction.
    index = {}
    names = set()
    tree = {}
    for entry in entries:
        name = entry.filename.replace("\\", "/")
        is_dir = entry.is_dir()
        parts = (name.removesuffix("/") if is_dir else name).split("/")
        folded_name = name.casefold()
        require(all(part not in {"", ".", ".."} for part in parts), "Unsafe path in portable archive")
        require(folded_name not in names, f"Duplicate portable archive member: {name}")
        require(not stat.S_ISLNK(entry.external_attr >> 16), "Linked member in portable archive")
        branch = tree
        for at, part in enumerate(parts):
            require(not part.startswith(" ") and not part.endswith((".", " "))
                    and all(ord(char) >= 32 and char not in '<>:"|?*' for char in part)
                    and not _WINDOWS_DEVICE_NAME.fullmatch(part.split(".", 1)[0].rstrip(" ")),
                    "Portable archive has a Windows-incompatible path component")
            key = part.casefold()
            directory = is_dir or at + 1 < len(parts)
            previous = branch.get(key)
            if previous is None:
                children = {} if directory else None
                branch[key] = (part, children)
            else:
                spelling, children = previous
                require(spelling == part and directory and children is not None,
                        f"Portable archive has a conflicting Windows path: {name}")
            branch = children
        names.add(folded_name)
        index[name] = entry
    return index


def portable_contents(path: Path, commit: str, run_url: str, native_report: dict, watchtower_report: dict, mo2_result: dict, qt_helper_hash: str, qt_forward_report: dict, live_report: dict, archive_report:dict) -> dict[str, bytes]:
    public: dict[str, bytes] = {}
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            require(len(entries) <= 10000 and sum(e.file_size for e in entries) <= PORTABLE_EXPANDED_LIMIT, "Portable archive exceeds the release validation bounds")
            index = portable_member_index(entries)
            for name in ("BUILD_INFO.json", "Modhunter.exe", "modhunter-cli.exe", BUNDLED_HELPER, BUNDLED_QT_HELPER, "Install_MO2_Plugin.ps1", "Install_MO2_Plugin.bat", "Evidence/native-smoke.json", "Evidence/watchtower-smoke.json", "Evidence/qt-forward.json", "Evidence/desktop-live.json", "Evidence/archive-review-proof.json", "PROJECT_NOTICE.txt", "THIRD_PARTY_NOTICES.json", "Check_MO2_Readiness.ps1", "Check_MO2_Readiness.bat", "START-HERE-MO2-253.md", "GameSync-Update-Techniques.md"):
                require(name in index and not index[name].is_dir() and index[name].file_size > 0, f"Portable package is missing {name}")
            require(archive.testzip() is None, "Portable ZIP failed its CRC check")
            digests = portable_checksums(archive, index)
            build = json_object(archive.read(index["BUILD_INFO.json"]), "BUILD_INFO.json")
            require(build.get("version") == VERSION, f"Portable version must be {VERSION}")
            require(str(build.get("commit", "")).lower() == commit, "Portable BUILD_INFO commit does not match the requested build")
            require(str(build.get("workflow", "")).rstrip("/") == run_url.rstrip("/"), "Portable BUILD_INFO identifies a different workflow run")
            embedded_live = json_object(archive.read(index["Evidence/desktop-live.json"]), "portable desktop-live.json")
            require(embedded_live == live_report, "Portable live desktop evidence differs from its native run")
            validate_live_desktop(live_report, commit, digests["Modhunter.exe"])
            validate_native_binding(native_report,commit,digests["Modhunter.exe"])
            validate_native_binding(watchtower_report,commit,digests["Modhunter.exe"])
            validate_archive_report(archive_report,commit,digests["modhunter-cli.exe"])
            require(json_object(archive.read(index["Evidence/archive-review-proof.json"]),"portable archive proof")==archive_report,
                    "Portable archive-review proof differs from the release CLI run")
            for prefix in ("archive-tools/","MO2/mo2_update_arsenal/bin/archive-tools/"):
                for name,expected in ARCHIVE_PINS.items():
                    require(digests.get(prefix+name)==expected,"Packaged archive helper is missing or differs from its pinned official bytes: "+prefix+name)
                require(prefix+"License.txt" in index and prefix+"provenance.json" in index,"Archive helper licenses/provenance are missing")
                provenance=json_object(archive.read(index[prefix+"provenance.json"]),"archive provenance")
                require(provenance.get("version")=="26.06" and all(provenance.get("files",{}).get(name)==expected for name,expected in ARCHIVE_PINS.items())
                        and provenance.get("licenseSha256")==digests[prefix+"License.txt"],"Archive helper provenance is inconsistent")
            for installer_script in ("Install_MO2_Plugin.ps1", "Install_MO2_Plugin.bat"):
                require(digests[installer_script] == file_hash(ROOT / "native" / installer_script),
                        f"Packaged plugin installer differs from the tested source: {installer_script}")
            require(digests["PROJECT_NOTICE.txt"]==file_hash(ROOT/"native/PROJECT_NOTICE.txt"),"Project notice differs from this source")
            require(index["THIRD_PARTY_NOTICES.json"].file_size<=16*1024*1024,"Third-party notice index is too large")
            notices=json.loads(archive.read(index["THIRD_PARTY_NOTICES.json"]).decode("utf-8"))
            locked=tomllib.loads((ROOT/"native/Cargo.lock").read_text("utf-8"))
            expected_packages={(p["name"],p["version"]) for p in locked["package"] if p.get("source")}
            packages=notices.get("packages",[])
            require(notices.get("schema")==1 and isinstance(packages,list) and len(packages)==len(expected_packages)
                    and {(p.get("name"),p.get("version")) for p in packages}==expected_packages,
                    "Bundled third-party notices do not match the locked dependency set")
            embedded_native = json_object(archive.read(index["Evidence/native-smoke.json"]), "portable native-smoke.json")
            require(embedded_native == native_report, "Portable native smoke differs from the Windows job receipt")
            embedded_watchtower = json_object(archive.read(index["Evidence/watchtower-smoke.json"]), "portable watchtower-smoke.json")
            require(embedded_watchtower == watchtower_report, "Portable Watchtower smoke differs from the Windows job receipt")
            embedded_forward = json_object(archive.read(index["Evidence/qt-forward.json"]), "portable qt-forward.json")
            require(embedded_forward == qt_forward_report, "Portable Qt forward-runtime proof differs from the Windows job receipt")
            # Both files come from this portable job's build. The separate MO2
            # job binds its installed helper to its own compiled CLI in its
            # receipt; independent Windows builds need not be byte-identical.
            require(digests[BUNDLED_HELPER] == digests["modhunter-cli.exe"],
                    "Packaged MO2 Rust helper differs from the portable diagnostic CLI")
            require(digests[BUNDLED_QT_HELPER] == qt_helper_hash,
                    "Packaged MO2 Qt helper differs from the same Windows job's compiled helper")
            require(not any(PurePosixPath(name).name.lower().startswith("qt6") and name.lower().endswith(".dll") for name in index),
                    "The portable plugin must use MO2's Qt runtime; SDK Qt DLLs must not be packaged")
            source_hashes = mo2_result.get("productionFileHashes")
            require(isinstance(source_hashes, dict), "MO2 orchestration omitted tested production source hashes")
            for relative in PRODUCTION_FILES:
                name = "MO2/mo2_update_arsenal/" + relative
                require(name in index, f"Portable plugin is missing {relative}")
                expected = source_hashes.get(relative)
                require(isinstance(expected, str) and re.fullmatch(r"[0-9a-fA-F]{64}", expected) is not None,
                        f"Native MO2 proof omitted the production hash for {relative}")
                observed = digests[name]
                require(observed == expected.lower(), f"Tested MO2 source differs from the portable plugin: {relative}")
            public["BUILD_INFO.json"] = public_json(build)
            for filename in BENCHMARK_FILES:
                benchmark_name = "Evidence/" + filename
                if benchmark_name in index:
                    benchmark = json_object(archive.read(index[benchmark_name]), filename)
                    public["benchmarks/" + filename] = public_json(benchmark)
    except (BadZipFile, RuntimeError, OSError) as exc:
        raise ReleaseError(f"Cannot validate portable archive: {path.name}: {exc}") from exc
    return public



def component_archive(destination: Path, bodies: dict[str, bytes]) -> None:
    """Write and read back an independently checkable subset of validated bytes."""
    require("SHA256SUMS.txt" not in bodies, "Component input must not reuse the portable checksum manifest")
    checksums = {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}
    manifest = "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())).encode("utf-8")
    # Stable entry timestamps make repeat preparation reproducible. Content and
    # BUILD_INFO bytes remain exactly those of the validated Windows package.
    with ZipFile(destination, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for name, body in sorted({**bodies, "SHA256SUMS.txt": manifest}.items()):
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, body, compress_type=ZIP_DEFLATED, compresslevel=9)
    with ZipFile(destination) as archive:
        index = {entry.filename: entry for entry in archive.infolist()}
        require(len(index) == len(archive.infolist()) and set(index) == set(bodies) | {"SHA256SUMS.txt"},
                "Derived component archive changed its exact file set")
        require(archive.testzip() is None, "Derived component archive failed its CRC check")
        require(portable_checksums(archive, index) == checksums,
                "Derived component archive differs from the validated portable bytes")


def component_assets(portable: Path, staged: Path) -> None:
    """Derive convenience downloads only after all portable release gates pass.

    This intentionally never re-reads dashboard/source files from the checkout:
    every shipped component is taken from the tested portable archive. The
    single added guide describes this narrower download, not a new installer.
    """
    with ZipFile(portable) as archive:
        index = {entry.filename.replace("\\", "/"): entry for entry in archive.infolist() if not entry.is_dir()}
        required = MO2_PACKAGE_ROOT_FILES | {"MO2/mo2_update_arsenal/web/index.html", BUNDLED_HELPER, BUNDLED_QT_HELPER}
        require(required <= set(index), "Validated portable archive is missing a component dependency")
        mo2 = {name: archive.read(entry) for name, entry in index.items()
               if name.startswith("MO2/") or name in MO2_PACKAGE_ROOT_FILES}
        mo2["START-HERE.txt"] = (
            f"MODHUNTER {VERSION} - MO2 PLUGIN\n\n"
            "Extract this complete archive before running Install_MO2_Plugin.bat.\n"
            "Close MO2, select the folder containing ModOrganizer.exe, then restart MO2.\n"
            "The installer validates this package and preserves existing plugin progress.\n\n"
            "Every file under MO2/ is copied unchanged from the tested portable release.\n"
            "The plugin includes its Rust CLI, Qt bridge and complete archive helpers.\n"
            "BUILD_INFO.json identifies the tested commit; SHA256SUMS.txt covers this archive's files.\n\n"
            "For the desktop app, the separate readiness checker and the full documentation set,\n"
            f"download {PORTABLE_NAME} from the same release. The standalone HTML download\n"
            "is also independent. Choose only the entry points you want to use.\n"
        ).encode("utf-8")
        standalone = {"BUILD_INFO.json": archive.read(index["BUILD_INFO.json"]),
                      "Modhunter.html": archive.read(index["MO2/mo2_update_arsenal/web/index.html"])}
    component_archive(staged / MO2_PACKAGE_NAME, mo2)
    component_archive(staged / STANDALONE_NAME, standalone)


def release_notes(commit: str, run_url: str, repository_url: str, installer_name: str) -> str:
    return f"""# Modhunter {VERSION} — OLED / Color restored

Modhunter now opens on true OLED black, with neutral surfaces and cool white/blue controls. Colored chapter separators, progress diamonds and manual completion cues return, with clearer feedback for actual checks, selection, bookmarks and errors. The complete gallery/list library and every existing update safeguard remain available.

This release continues the Rust desktop introduced in earlier versions. WebView2 renders the shared dashboard and preserved game modes; MO2 retains authenticated Nexus downloads and its native installer, including FOMOD choices. The Windows app does not require Node or Electron.

## 0.12.1: OLED black, colored chapters and visible feedback

- **True black workspace:** neutral near-black surfaces replace the green/gold utility palette across the Rust shell, shared dashboard, Watchtower, settings, dialogs and preserved companion chrome. Light mode uses neutral whites and grays. Optional game worlds retain their own art direction.
- **Colored chapters restored:** authored chapter bands, colored diamonds, collapse arrows, manual cleared/total counts and progress bars make collection structure immediately visible again. Keyboard focus stays on the chapter when it expands or collapses.
- **Correct packaged progress:** chapter colors and empty, partial and complete checklist bars render under the desktop's existing style policy. The installed Windows app must prove that an untouched offline chapter has an empty bar before release.
- **Lighter full-library rendering:** removes eight formatting-only text nodes per compact mod row while retaining every mod, field, control and metadata space. At 11,207 mods, this eliminates 89,656 unnecessary DOM nodes; the existing native performance limits remain enforced.
- **Current MO2 setup checks:** the readiness tool derives its expected plugin version from checksum-verified package metadata, so a current plugin is no longer incorrectly compared with 0.10.2. Mixed, incomplete or changed packages remain blocked.
- **Clear interaction states:** visible hover, keyboard focus, pressed/current tabs, selected mods, bookmarks, manual completion/review and error feedback. Manual checklist labels remain distinct from detected updates and verified installation.
- **Truthful scan feedback:** a real starting state, busy control, actual checked/total progress and distinct complete, partial, failed, cancelled and unavailable states. Request or polling failures no longer leave a reassuring idle connection dot.
- **Same complete collection:** no result limits, virtualization or hidden records. The release retains exact-file approval, backups, native MO2 installation, verification, recovery, all game modes and FX Studio.

## 0.12.0: image-forward mod browsing and a compact desktop

- **A richer library:** gallery and readable list views, adjustable density, authentic provider thumbnails when available, search and filters, and clear installed/candidate version and provider identities. Complete results remain available without viewport virtualization or record caps.
- **A coherent workspace:** warm obsidian and parchment themes, restrained brass accents, custom vector navigation, compact toolbars, and consistent update review, source, settings and file-detail surfaces.
- **More working space:** the native shell is 116px tall, with full tab titles and every command retained. Collapsible navigation, keyboard focus restoration and wrapping for long names keep narrower windows usable.
- **Bounded presentation work:** view and theme changes suppress the bulk row fades that previously stalled large libraries, then restore normal hover feedback within the measured interaction. Reduced motion no longer starts accidental transitions on every descendant. Complete results and every record remain available.
- **Faster repeat filtering:** clearing a search can reuse unchanged mod rows and their loaded previews. Changed metadata, failed images, active effects and profile changes are checked before reuse, keeping the restored library current.
- **Preserved update safeguards:** exact-file choices, profile-bound approval, native MO2 installation, content backups, verification and rollback remain authoritative. Game modes, progression and FX Studio remain available. The plugin installer now copies and verifies all four archive-helper components beside its Rust CLI, preserving the previous copies in upgrade backups.
- **Independent downloads:** the MO2 plugin and standalone HTML now have dedicated versioned archives, derived from the already-validated portable package with fresh internal checksum manifests.

## 0.11.0: reviewed downloads, actual file recovery and app upkeep

Watchtower now carries a chosen external archive from its exact source identity into a complete file review and MO2's native installer. Update files stay bound to the chosen profile, game/runtime and installed mod. Separate new content requires a new mod name. A different file, changed source, profile round-trip or lost acknowledgement cannot silently reuse an earlier installation decision.

- **Exact source choices:** Patreon attachments, LoversLab choices, direct Drive/MEGA files and unambiguous MEGA folder children keep durable identities and explicit update/new-content choices. Compatible accessible offers are ranked using your source preference and authoritative release dates; unknown dates remain unknown. Creator preferences, site mutes and compatibility declarations persist. Drive folders and ambiguous MEGA folders require an exact direct file choice. Account sign-in is reported separately from release access.
- **Complete archive review:** pinned 7-Zip helpers inspect ZIP, 7z and RAR archives with path, link, collision, encryption and size guards. Every accepted file remains in the manifest. Ordinary game-data archives produce a verified ZIP containing the complete selected set; FOMOD and uncertain layouts keep the original archive and require native installer choices. Installed-only paths never authorize deletion.
- **One reviewed install queue:** approved page archives and native API downloads share sequential MO2 installation. Stop after current, cancel preparation and resume approved work have separate effects. A running native installer reports its real result; cancelled, stale or uncertain work is never silently replayed.
- **Actual content recovery:** the Rust engine snapshots mod files before installation, verifies the resulting files and retains interrupted operations. Restore, batch restore and retention cleanup recheck exact content identities. Later edits stop restoration. Recovery data lives beside the MO2 mods directory in an instance-specific `Modhunter-Recovery` vault and is excluded from app reset.
- **Duplicate review:** complete per-file and whole-mod reports include saved-profile ownership and hard-link facts. Quarantine requires an identical retained counterpart and a duplicate disabled in every saved and live profile. Undo and permanent quarantine removal use a fresh exact preview.
- **Readiness and file inspection:** inspect the actual MO2 instance, profile, redirected paths, source coverage and account observations without starting update work. An explicit metadata scan lists unattributed files under the native mods root, supports a selected mod, retains the last complete result and rechecks the current context before revealing a file.
- **Private actionable notices:** accepted page changes and fresh verified updater facts produce discreet notices. Opening one rechecks the exact current item; a notice never starts an installation.
- **Desktop preferences:** English, Spanish, Italian and Traditional Chinese catalogs cover dashboard and Watchtower controls, help, review/queue flows, status messages and native dialog context. System/light/dark themes preserve keyboard focus and inputs. Names, paths and original provider/OS diagnostic details remain intact. Native quick hide, Windows capture protection, private browser choices, bounded cache cleanup, release checks, offline licenses and preview-before-export support reports are included.
- **Owned cleanup:** download and recovery cleanup show the exact proposed items. Removing app data requires a typed confirmation, closes the desktop and records partial or retained results. It excludes MO2, game files, external browser profiles and recovery vaults.

The release gate binds Windows WebView2, archive-helper, Chromium interaction and actual MO2 install/restore receipts to the clean candidate and packaged binaries. The separate MO2 2.5.2 fixture and Qt 6.11 forward-runtime checks do not claim execution of a user's MO2 2.5.3b12 installation, accounts or 11,207-mod profile.

See the [0.11.0 implementation and evidence map]({repository_url}/blob/{commit}/native/evidence/whimwatch-parity-0.11.0.md).

## 0.10.2: compact workspace, automatic checking and live repair

The earlier compact-workspace and live-connection repairs remain included.

- The default view is a compact installed-mod table, with small status filters and separate Updates, Downloads, History, Tools and Extras tabs. Oversized campaign cards and repeated technical summaries no longer occupy the main workflow. Details remain available on demand.
- On live connection, one bounded source check starts per profile/execution context after saved preferences load. Recent successful Nexus metadata is reused first; missing or expired metadata is fetched. Public source adapters run independently, and completed results appear progressively. Tools contains the automatic-check switch; active checks can be cancelled without discarding completed results.
- Detected update and maintenance status is separate from your manual checklist notes. Entries resolve to Update available, Up to date, Maintenance, Sign-in required, Page review or another explicit evidence state. Unknown, failed or unsupported checks never become a successful current result. Source reads and plugin analysis do not queue, download, install or convert anything.
- The MO2 bridge starts after MO2 finishes initializing its interface, without opening the legacy Quest Board tool or a browser. Callback registration uses current dictionary signatures, isolates optional-hook failures, coalesces refreshes and shuts down cleanly.
- Native download handle **0** is valid throughout reviewed queueing, early completion, retry, installation preview, installation and history. Missing or malformed IDs cannot accidentally select handle 0. Nexus project/file IDs remain strictly positive.
- Desktop **Refresh** and **Plugin audit** use the active live dashboard instead of disabled or offline-only paths. Startup rediscovers MO2 after its local port changes. **Details** shows credential-free plugin/runtime/helper status.
- The disconnected start page clearly separates connecting MO2, read-only local imports and the optional demo. The native toolbar is clearer and more legible; existing game modes and Watchtower remain available.
- Publication requires the compiled Windows desktop to connect to an actual MO2 fixture and execute discovery, live catalog loading, Refresh, Plugin audit and Details. The evidence is bound to the packaged executable hash, exact source and commit.

## MO2 2.5.3b12 compatibility and GameSync-inspired checking

This build adds a live, GUI-thread-only sender self-test and a qualified forward-runtime path for the baseline Qt 6.7.1 helper on Qt 6.11.1/PyQt 6.11.0. The release requires real C++ receiver/callback tests on that newer runtime using the exact DLL in this ZIP, as well as the existing actual MO2 2.5.2 suite. It does **not** claim that the unavailable beta-12 binary or your personal Skyrim/Nexus setup was executed in CI. No Qt or Python runtime DLLs are replaced in MO2.

Run **Check_MO2_Readiness.bat** from the extracted portable folder and read **START-HERE-MO2-253.md** before the first live connection. The check inspects versions, 64-bit PE headers and key package checksums; its saved JSON excludes account information and personal paths. Static readiness is not an authenticated-download result.

GameSync-inspired additions include bounded full-field calendar/build/prerelease comparisons, channel-change review, and persisted provider-wide HTTP 503 Retry-After cooldowns in the Rust public adapters. Existing ETag/Last-Modified caching, exact project/file identities, deduplication, cancellation, site sessions, selected-page comparisons and reviewed MO2 installation remain authoritative. See the [technique/coverage map]({repository_url}/blob/{commit}/wiki/GameSync-Update-Techniques.md); not every site has an automatic authenticated API adapter.

## Watchtower: site sign-ins and page tracking

- **Watchtower** is a native page and session manager alongside the existing updater. Add public HTTPS release pages, creator feeds or changelogs, choose a named site session when needed, and organize pages with folders, tags, search and filters.
- Sign in on the website itself in an isolated WebView2 profile. Remembered sessions persist across launches; non-remembered sessions use a temporary on-disk profile for the app run and a fresh profile next launch. Reset closes its windows, advances the session generation and rejects old work. Session confirmation is your readiness indication, not a guarantee of page entitlement.
- The first complete successful observation establishes a baseline. Later checks retain changed text/history and explicit per-observation acknowledgment with guarded undo. Optional CSS region/ignore selectors keep comparisons focused. Failed, incomplete, canceled or stale captures preserve last-good data.
- Checks are manual by default. Optional launch/background due checks run while Modhunter is open with bounded work and site pacing. Pause/mute affects execution, removed pages are recoverable, and import previews validate/deduplicate data before applying it. Exports contain watchlist data, never browser credentials.
- WhimWatch 0.4.2's account/page-management behavior was statically inspected across its exact uploaded asset and all 96 first-party source files. Modhunter independently implements the relevant workflow; the reference's Sims-specific direct installer and assets are not included. MO2 continues to own Nexus authentication, exact-file downloads, FOMOD choices and installation. A changed page is an observation, not a verified mod update.

See the [Watchtower guide]({repository_url}/blob/{commit}/wiki/Watchtower.md) and [pinned reference audit and acceptance scope]({repository_url}/blob/{commit}/native/evidence/whimwatch-parity-0.10.0.md).

## Preserved refresh speed, cancelable work and reliable source changes

- Repeated MO2 snapshot requests reuse one complete encoded catalog for the same observed state. Encoding runs outside the native execution guard; profile, source, metadata and audit changes invalidate the response, and health freshness remains bounded. Ordinary Chronicle saves avoid rebuilding the catalog.
- The complete checklist and Workshop reuse unchanged row markup as well as DOM nodes. Catalog facts, row edits, source expiry and cooldown changes invalidate the relevant caches. Download controls retain their focus through row updates and moves. Every matching record remains available without virtualization or record caps.
- Native imports and deep plugin audits offer **CANCEL**, preserve the last complete catalog, and can be retried after their workers settle. Payload reads check cancellation at bounded 64 KiB intervals while preserving fresh hashes and all existing audit results. Tabs share one immutable catalog encoding; ordered replies cannot overwrite newer toolbar or page state.
- Changing or clearing a source link advances the execution context and restores the original MO2 identity when appropriate. Pending source edits must synchronize before an update can execute. A canceled or superseded provider scan cannot overwrite a newer cache result.
- Chronicle loading preserves edits made during a delayed read and retries failed hydration without overwriting unseen server progress. Profile and execution changes invalidate late replies and restart incomplete hydration.
- The MO2 installer validates package checksums and build/plugin versions before replacing the existing plugin. Incomplete, damaged or mixed packages leave the current installation and its progress intact. Release preparation verifies every file in the portable checksum manifest and rejects Windows filename collisions.

See the [0.9.2 evidence and repeatable workloads]({repository_url}/blob/{commit}/native/evidence/readiness-0.9.2.md) for exact measurements and validation scope. Historical import and 0.9.1 comparisons retain their original baselines.

## Download and run

- **{PORTABLE_NAME}** includes Modhunter.exe, the compiled diagnostic CLI, the MO2 plugin with its narrow Qt interoperability helper, and its installer scripts. Extract it and run Modhunter.exe. The portable app requires Microsoft Edge WebView2 Runtime.
- **{installer_name}** installs the Rust desktop for the current Windows user and bootstraps WebView2 when needed. Use the MO2 plugin archive or the portable ZIP's included plugin installer for live MO2 integration.
- **{MO2_PACKAGE_NAME}** is the complete MO2 plugin with its existing installer, embedded Rust/Qt/archive helpers, notices, build identity and scoped checksums. Extract the whole archive, close MO2 and run Install_MO2_Plugin.bat. The optional readiness checker and desktop app remain in the complete portable package.
- **{STANDALONE_NAME}** contains the exact tested shared dashboard as Modhunter.html, BUILD_INFO.json and its own checksums. Open the HTML directly for its independent browser/offline experience and preserved game modes; optional live operations still use the existing MO2 bridge.
- **{VERIFICATION_NAME}** contains the selected native receipts, screenshots, build identity and available benchmark evidence. **SHA256SUMS.txt** verifies the release files.

## Controls and existing progress

Choose **Import MO2** and select a profile's modlist.txt. For split directories, select **Instance INI**, or set an explicit **Mods path**. **Auto paths** clears overrides. **Refresh** reloads metadata, reusing unchanged cached files; import remains read-only. **Plugin audit** performs deeper inspection. **CANCEL** stops either operation cooperatively; **CANCELING…** remains visible until its workers settle, leaving the last complete result available.

For live updates, extract the complete portable ZIP with its checksum manifest and BUILD_INFO.json. Close MO2, run **Install_MO2_Plugin.bat**, select the folder containing ModOrganizer.exe, then restart MO2, let its profile finish loading and launch Modhunter. The bridge starts automatically; **Find MO2** reconnects on demand. Choose the intended session if several are running; opening the legacy Quest Board tool is optional. A loopback address can also be entered in **Connect**. Nexus sign-in stays in MO2. **Update now**, background queues, retries and sequential installation retain MO2's native installation choices.

JSON, CSV and PowerShell exports are saved to Windows Downloads with unique filenames. Export a progress backup in the older Electron edition and import it in the Rust edition after selecting the same portable profile. Existing Electron browser storage is not automatically migrated; the live MO2 Chronicle remains with the plugin.

## What this build actually passed

- Before/after Chromium captures against v0.12.0 at desktop and narrow sizes, dark/light themes, authentic source artwork, gallery/list views and update dialogs. The complete 11,207-record comparison preserves every laid-out row and verifies the last item remains searchable; its raw samples and screenshots are included in the verification archive.
- The compiled desktop connected to actual MO2, with native dark/light appearance acknowledgement, wide/narrow window captures, real pointer navigation through Gallery and Details, and keyboard focus restoration.
- The same complete 11,207-record snapshot imported through the shipped dashboard in actual Windows WebView2. Search restoration and every List/Gallery/density transition retain all ordered, laid-out records. The verification archive's `windows/native-library-performance.json` records the actual engine, viewport, motion setting, raw timings and rendering costs; this renderer sample excludes Rust filesystem import, provider I/O and installation.
- Actual current-user NSIS installation of this exact installer, byte-for-byte verification of its installed executable and archive helpers, a real installed WebView2 launch, removal of the installed payload and registration, and preservation of the complete external fixture profile during uninstall.
- The packaged Windows release CLI with the pinned 7-Zip executable and DLL across 26 archive format, path-safety, integrity and exact-selection cases. The portable package carries these exact helpers beside both the desktop and MO2 CLI.
- Real Chromium review-manager interactions covering complete 1,207-entry selection, explicit new targets, FOMOD advisory behavior, lost acknowledgement, retained restart evidence, exact retention/reset confirmations, support export, preferences and duplicate ownership.
- The real MO2 native installer applying an exact generated archive to a disposable target, followed by production Rust verification and complete content restoration; separate recovery probes cover wrong targets, later edits, two-target restore and repeated acknowledgement.
- Rust tests, formatting and Clippy, Python MO2 lifecycle/source/cache tests, frontend runtime checks, and dashboard interaction checks in real Chromium. Cancellation tests verify explicit interruption without partial success and successful reuse of the same engine.
- The shipped plugin installer under Windows PowerShell 5.1: fresh installation, upgrade/progress preservation, rejected incomplete or mixed packages, source-tree installation, exact version output and linked-directory handling.
- A compiled Windows application running in real WebView2: fixture-profile import, rendered dashboard, actual backup export/readback, and denial of toolbar-native commands from the game view.
- The same compiled application running a required native Watchtower fixture: exact authenticated direct/blob download consent, cancellation and stale-source rejection, selected-file navigation ownership, receipt persistence, native quick-hide/capture calls, browser cache/resource policy, account isolation, remembered-profile close/reopen within one process, generation-selected fresh profiles, same-session popups, authenticated page capture and changed content, selected text, incomplete/stale/canceled rejection, and denied remote IPC. Account checks use internally owned loopback pages and synthetic sign-in state; the reopen check does not restart the application. A separate anonymous `https://example.com/` check runs through production WebView2 capture and durable WatchStore baseline completion. No real website account is used.
- The shipped Watchtower interface in real Chromium with a fixture native-command adapter, covering page/session actions, history, import preview, error recovery and responsive layout. This renderer proof is separate from native browser-profile proof.
- The production plugin inside official **MO2 2.5.2**, using a disposable fixture game/profile: native initialization, real Qt dispatch, profile snapshot, protected loopback requests, row focus/selection, a native screenshot, and a compiled Rust CLI health probe against that running service. Its bundled Rust helper also completed the real plugin-audit endpoint, decorated both fixture mods, and verified the header and fresh SHA256 of an inert disabled plugin within a bounded background job.
- The compiled Qt 6.7.1 helper loaded against MO2's existing Qt runtime. Synthetic metadata passed through the actual MO2-created C++ repository QObject, captured the Qt sender, and completed the raw JSON file callback while rejecting unowned and cancelled requests. This binding fixture makes no Nexus account or network requests and does not exercise authenticated downloads.
- Release preparation verifies the commit inside the portable ZIP, every internal package checksum, matching dashboard and Watchtower native receipts, matching tested MO2 source hashes, complete passing required checks, and nonempty valid screenshot evidence before staging these files.

The native MO2 fixture does not sign into Nexus, download mod archives, exercise account-specific callbacks or FOMOD choices, or launch an owned game. Its synthetic game does not prove ESL eligibility, conversion safety or the behavior of a populated personal installation. The Watchtower fixture does not prove each provider's OAuth, 2FA, challenge or permission-specific page behavior; the WhimWatch reference was not run. The application is not code-signed.

The 0.9.2 delivery comparison uses the actual Python loopback HTTP service with complete generated 2,500- and 10,000-mod catalogs, local Chronicle writes and byte-for-byte response equivalence. The browser comparison measures complete-list changes against published 0.9.1. Rust audit comparison verifies that bounded cancellation preserves all non-timing results and hashes. These component measurements exclude Nexus/download speed, FOMOD installation and a user's actual mod list; they do not guarantee timings on other PCs. See the [delivery evidence]({repository_url}/blob/{commit}/native/evidence/mo2-catalog-delivery-comparison-0.9.2.json), [audit evidence]({repository_url}/blob/{commit}/native/evidence/plugin-audit-comparison-0.9.2.json) and the exact-run dashboard comparison in the verification ZIP.

**Build commit:** [{commit}]({repository_url}/commit/{commit})  
**Passing workflow and original artifacts:** [Rust desktop and MO2 readiness]({run_url})  
**Architecture and setup:** [Rust desktop guide]({repository_url}/blob/{commit}/native/README.md)
"""


def prepare(artifacts_dir: Path, output_dir: Path, commit: str, run_url: str) -> list[Path]:
    require(source_version() == VERSION, "Source release version changed during preparation")
    commit, repository_url = validate_run(commit, run_url)
    artifacts_dir = artifact_directory(artifacts_dir.absolute())
    output_dir = output_dir.absolute()
    require(not output_dir.is_symlink(), "Output directory must not be a symlink")
    require(not output_dir.exists() or (output_dir.is_dir() and not any(output_dir.iterdir())), "Output directory must be absent or empty; old release files are not overwritten")
    windows = artifact_directory(artifacts_dir / "Modhunter-Rust-Windows")
    mo2 = artifact_directory(artifacts_dir / "Modhunter-MO2-Native-Proof")
    browser = artifact_directory(artifacts_dir / "Modhunter-Rust-Browser-Proof")
    visual = artifact_directory(artifacts_dir / "Modhunter-Visual-Release-Proof" / "visual-release")
    portable_candidates = sorted(windows.rglob("*.zip"))
    require(len(portable_candidates) == 1 and portable_candidates[0].name == PORTABLE_NAME, "Expected exactly one Modhunter portable ZIP in the Windows artifact")
    portable = regular_file(portable_candidates[0])
    installers = sorted((windows / "release/bundle/nsis").glob("*.exe"))
    require(len(installers) == 1, "Expected exactly one NSIS installer in the Windows artifact")
    installer = regular_file(installers[0])
    require(installer.name == INSTALLER_NAME and installer.stat().st_size > 0,
            f"NSIS installer must be the nonempty {INSTALLER_NAME} from this release")
    native_report = read_json(windows / "native-smoke/native-smoke.json")
    validate_native_report(native_report)
    watchtower_report = read_json(windows / "watchtower-smoke/watchtower-smoke.json")
    validate_watchtower_report(watchtower_report)
    mo2_result = read_json(mo2 / "result.json")
    mo2_receipt = read_json(mo2 / "native-receipt.json")
    validate_mo2(mo2_result, mo2_receipt)
    qt_helper = regular_file(windows / "qt-bridge/Release/modhunter-qt-bridge.dll")
    require(qt_helper.stat().st_size > 0, "The same Windows job's compiled Qt helper is empty")
    qt_forward_report = read_json(windows / "qt-forward/qt-forward.json")
    validate_qt_forward(qt_forward_report, commit, file_hash(qt_helper))
    live_report = read_json(windows / "live-mo2-smoke/desktop-live.json")
    archive_report=read_json(windows/"archive-review-proof.json")
    portable_digest = file_hash(portable)
    public = portable_contents(portable, commit, run_url, native_report, watchtower_report, mo2_result, file_hash(qt_helper), qt_forward_report, live_report,archive_report)
    require(file_hash(portable) == portable_digest, "Portable archive changed during release validation")
    installer_report = read_json(windows / "installer-smoke/installer-smoke.json")
    validate_installer_report(installer_report, commit, file_hash(installer), native_report["executableSha256"])
    installed_launch = read_json(windows / "installer-smoke/native-launch/native-smoke.json")
    validate_native_report(installed_launch)
    validate_native_binding(installed_launch, commit, native_report["executableSha256"])
    public["windows/installer-smoke.json"] = public_json(installer_report)
    public["windows/installed-native-smoke.json"] = public_json(installed_launch)
    public["windows/installed-desktop.png"] = png_bytes(windows / "installer-smoke/native-launch/fixture-desktop.png")
    native_library = read_json(windows / "native-library-performance/native-library-performance.json")
    validate_native_library(native_library, commit, native_report["executableSha256"])
    public["windows/native-library-performance.json"] = public_json(native_library)
    visual_report = read_json(visual / "visual-release.json")
    validate_visual_report(visual_report, commit)
    public["visual/visual-release.json"] = public_json(visual_report)
    visual_images = sorted(visual.rglob("*.png"))
    relative_images = {image.relative_to(visual).as_posix() for image in visual_images}
    require(VISUAL_SCREENSHOTS.issubset(relative_images), "Visual proof omitted required before/after or gallery screenshots")
    require(len(visual_images) <= 128, "Unexpected visual acceptance image count")
    for screenshot in visual_images:
        relative = screenshot.relative_to(visual)
        require(all(part not in (".", "..") and not any(c in part for c in "\\:\r\n") for part in relative.parts),
                "Unsafe visual screenshot path")
        public["visual/" + relative.as_posix()] = png_bytes(screenshot)
    public["windows/archive-review-proof.json"]=public_json(archive_report)
    public["windows/desktop-live.json"] = public_json(live_report)
    public["windows/desktop-live.png"] = png_bytes(windows / "live-mo2-smoke/desktop-live.png")
    for name in LIVE_DESKTOP_SCREENSHOTS:
        public["windows/" + name] = png_bytes(windows / "live-mo2-smoke" / name)
    public["windows/qt-forward.json"] = public_json(qt_forward_report)
    public.update({"windows/native-smoke.json": public_json(native_report), "windows/watchtower-smoke.json": public_json(watchtower_report), "mo2/result.json": public_json(mo2_result), "mo2/native-receipt.json": public_json(mo2_receipt), "mo2/mo2-fixture.png": png_bytes(mo2 / "mo2-fixture.png")})
    watchtower_browser = read_json(browser / "watchtower-browser.json")
    validate_watchtower_browser(watchtower_browser, commit)
    public["browser/watchtower-browser.json"] = public_json(watchtower_browser)
    manager_ui = read_json(browser / "native-manager-ui.json")
    validate_native_manager_ui(manager_ui, commit)
    font_path = browser / 'ci-fonts.json'
    # Both actual-font receipts bind these original bytes. Parse/credential-check
    # the bounded snapshot once and publish it unchanged, including JSON escapes.
    font_bytes = bounded_bytes(font_path, JSON_LIMIT)
    font_receipt = json_object(font_bytes, font_path.name)
    font_hash = hashlib.sha256(font_bytes).hexdigest()
    validate_cjk_font(manager_ui.get('cjkFontProof'), font_receipt, font_hash)
    font_rendering = read_json(browser / 'ci-font-rendering.json')
    validate_ci_font_rendering(font_rendering, commit, font_receipt, font_hash)
    public["browser/native-manager-ui.json"] = public_json(manager_ui)
    public['browser/ci-fonts.json'] = font_bytes
    public['browser/ci-font-rendering.json'] = public_json(font_rendering)
    for name, dimensions in FONT_RENDERING_SCREENSHOTS.items():
        public['browser/' + name] = png_bytes(browser / name, dimensions)
    for name in NATIVE_MANAGER_SCREENSHOTS:
        public["browser/" + name] = png_bytes(browser / name)
    for name in WATCHTOWER_SCREENSHOTS:
        public["browser/" + name] = png_bytes(browser / name)
    dashboard_benchmark = browser / "dashboard-render-comparison.json"
    if dashboard_benchmark.exists():
        benchmark = read_json(dashboard_benchmark)
        require(benchmark.get("candidateCommit") == commit, "Dashboard benchmark does not identify the tested build commit")
        public["benchmarks/dashboard-render-comparison.json"] = public_json(benchmark)
    screenshots = sorted(browser.rglob("*.png"))
    require(bool(screenshots), "Browser proof contains no PNG screenshots")
    require(len(screenshots) <= 128, "Unexpectedly many browser screenshots")
    for screenshot in screenshots:
        relative = screenshot.relative_to(browser)
        require(all(part not in (".", "..") and not any(c in part for c in "\\:\r\n") for part in relative.parts), "Unsafe browser screenshot path")
        public_name = "browser/" + relative.as_posix()
        if public_name not in public:
            public[public_name] = png_bytes(screenshot)
    # All proof is validated before creating final output. Build in an adjacent
    # temporary directory and publish it only after copy/checksum verification.
    expected_binary_hashes = {portable.name: portable_digest, installer.name: file_hash(installer)}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".modhunter-release-", dir=output_dir.parent) as temporary:
        staged = Path(temporary)
        for source in (portable, installer):
            target = staged / source.name
            shutil.copyfile(source, target)
            require(file_hash(target) == expected_binary_hashes[source.name], f"Release copy changed the tested binary: {source.name}")
        component_assets(staged / PORTABLE_NAME, staged)
        require(file_hash(staged / PORTABLE_NAME) == portable_digest,
                "Validated portable archive changed while deriving component assets")
        with ZipFile(staged / VERIFICATION_NAME, "w", ZIP_DEFLATED, compresslevel=9) as archive:
            for name, content in sorted(public.items()):
                require(PurePosixPath(name).suffix.lower() in (".json", ".png"), "Unexpected non-public evidence file type")
                archive.writestr(name, content)
        (staged / "release-notes.md").write_text(release_notes(commit, run_url, repository_url, installer.name), encoding="utf-8")
        # release-notes.md becomes the hosted release description rather than an
        # uploaded asset; checksum only the binary/evidence assets users fetch.
        checksums = [f"{file_hash(path)}  {path.name}" for path in sorted(staged.iterdir()) if path.is_file() and path.suffix.lower() in (".zip", ".exe")]
        (staged / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
        require(not output_dir.exists() or (output_dir.is_dir() and not any(output_dir.iterdir())), "Output changed during preparation; refusing to mix releases")
        if output_dir.exists():
            output_dir.rmdir()
        staged.rename(output_dir)
    return sorted(output_dir.iterdir())


def self_test() -> None:
    """Small fixture tests for release gates; these are not native smoke proof."""
    import unittest

    class ReleaseTests(unittest.TestCase):
        def setUp(self):
            self.temporary = tempfile.TemporaryDirectory(prefix="modhunter-release-tests-")
            self.addCleanup(self.temporary.cleanup)
            self.root = Path(self.temporary.name)
            self.artifacts = self.root / "input"
            self.output = self.root / "output"
            self.commit = "a" * 40
            self.run = "https://github.com/Herbertofury/Modhunter/actions/runs/123456"
            self.windows = self.artifacts / "Modhunter-Rust-Windows"
            self.mo2 = self.artifacts / "Modhunter-MO2-Native-Proof"
            self.browser = self.artifacts / "Modhunter-Rust-Browser-Proof"
            self.visual = self.artifacts / "Modhunter-Visual-Release-Proof" / "visual-release"
            for path in (self.windows / "release/bundle/nsis", self.windows / "native-smoke", self.windows / "watchtower-smoke", self.windows / "qt-bridge/Release", self.windows / "qt-forward", self.windows / "live-mo2-smoke", self.windows / "installer-smoke/native-launch", self.windows / "native-library-performance", self.mo2, self.browser, self.visual):
                path.mkdir(parents=True)
            self.native = {"ok": True, "nativeWebView2": True, "shellIpc": True, "exportDownloadVerified": True, "gameViewNativeIpcDenied": "1",
                           "proof": {"chapterColors": "1", "chapterProgress": "1"}}
            self.watchtower = {"ok": True, "nativeWebView2": True, "watchtowerSchema": 1,
                               "scope": "Synthetic release-validator fixture; no native execution",
                               "checks": [{"name": name, "passed": True} for name in sorted(REQUIRED_WATCHTOWER_CHECKS)]}
            self.watchtower_browser = {"ok": True, "realChromium": True, "watchtowerSchema": 1,
                                      "candidateCommit": self.commit, "sourceDirty": False,
                                      "scope": "Synthetic release-validator fixture; no Chromium execution",
                                      "sourceFiles": {name: file_hash(ROOT / name) for name in WATCHTOWER_BROWSER_SOURCES},
                                      "checks": [{"name": name, "passed": True} for name in sorted(REQUIRED_WATCHTOWER_BROWSER_CHECKS)],
                                      "screenshots": sorted(WATCHTOWER_SCREENSHOTS)}
            self.manager_ui = {"schema":1,"kind":"native-manager-ui","ok":True,"realChromium":True,"candidateCommit":self.commit,"sourceDirty":False,
                               "scope":"Synthetic release-validator fixture; no Chromium execution",
                               "testsRun":len(NATIVE_MANAGER_CHECKS),"skipped":0,
                               "sourceFiles":{name:file_hash(ROOT/name) for name in NATIVE_MANAGER_SOURCES},
                               "checks":[{"name":name,"passed":True} for name in sorted(NATIVE_MANAGER_CHECKS)]}
            coverage = font_coverage(CJK_TEXT)
            self.font_receipt = {'schema': 1, 'ok': True,
                'scope': 'Synthetic validator fixture; no font installation or glyph rendering',
                'scriptSha256': file_hash(ROOT / 'tests/prepare_ci_fonts.py'),
                'upstreamRepository': CJK_EXPECTED_FONT['upstreamRepository'],
                'upstreamCommit': CJK_EXPECTED_FONT['upstreamCommit'],
                'license': 'OFL-1.1',
                'fontFamily': CJK_EXPECTED_FONT['familyName'],
                'registeredFamily': CJK_EXPECTED_FONT['familyName'],
                'registeredPostScriptName': CJK_EXPECTED_FONT['postScriptName'],
                'files': [{'path': path, **identity} for path, identity in FONT_PREREQUISITE_FILES.items()],
                'glyphCoverage': coverage,
                'supplementalFonts': [{'upstreamRepository': 'notofonts/noto-fonts',
                    'upstreamCommit': FONT_SUPPLEMENT_COMMIT, 'license': 'OFL-1.1',
                    'fontFamily': family, 'registeredFamily': family,
                    'registeredPostScriptName': font['postScriptName'],
                    **{key: font[key] for key in ('path', 'bytes', 'sha256')},
                    'glyphCoverage': font_coverage(font['text'])} for family, font in FONT_SUPPLEMENTS.items()],
                'supplementalGlyphCoverage': {'fullwidthZoom': {
                    'fontFamily': CJK_EXPECTED_FONT['familyName'],
                    'postScriptName': CJK_EXPECTED_FONT['postScriptName'], **font_coverage('＋－')}}}
            # The real helper escapes non-ASCII characters. Re-serializing this
            # JSON through public_json must not alter the hash-bound ZIP member.
            (self.browser / 'ci-fonts.json').write_bytes((json.dumps(self.font_receipt, indent=2) + '\n').encode())
            self.manager_ui['cjkFontProof'] = {'schema': 1, 'ok': True, 'locale': 'zh-TW',
                'scope': 'Synthetic validator fixture; no Chromium glyph rendering',
                'text': CJK_TEXT, 'visible': True, 'inViewport': True, 'receivesPointer': True,
                'expectedFont': dict(CJK_EXPECTED_FONT), 'expectedGlyphCount': len(CJK_TEXT),
                'matchedGlyphCount': len(CJK_TEXT), 'glyphCoverage': coverage,
                'fontReceiptSha256': file_hash(self.browser / 'ci-fonts.json'),
                'fonts': [{'familyName': CJK_EXPECTED_FONT['familyName'],
                           'postScriptName': CJK_EXPECTED_FONT['postScriptName'],
                           'glyphCount': len(CJK_TEXT), 'isCustomFont': False}]}
            self.write_json(self.browser / "native-manager-ui.json",self.manager_ui)
            self.font_rendering = {'schema': 1, 'kind': 'ci-font-rendering', 'ok': True,
                'scope': 'Synthetic validator fixture; no Chromium glyph or practice game execution',
                'candidateCommit': self.commit, 'sourceDirty': False,
                'sourceStatusAtStart': [], 'sourceStatusAtEnd': [], 'phase': 'complete', 'errors': [],
                'sourceFiles': {name: file_hash(ROOT / name) for name in FONT_RENDERING_SOURCES},
                'assets': {name: file_hash(ROOT / name) for name in FONT_RENDERING_ASSETS},
                'fontReceiptSha256': file_hash(self.browser / 'ci-fonts.json'),
                'fontHelperSha256': self.font_receipt['scriptSha256'],
                'verifiedFontFiles': {family: dict(identity) for family, identity in FONT_VERIFIED_FILES.items()},
                'catalogRows': 227, 'catalogFixtureSha256': 'c' * 64,
                'browserVersion': '157.0.0.0', 'requestedExecutable': '/synthetic/chromium',
                'game': {'profileId': 'practice', 'connected': False, 'mode': 'expedition',
                         'actualCanvasDrawn': True, 'enteredThroughUi': True},
                'zoomInteraction': {'original': 48, 'increased': 54, 'restored': 48},
                'requiredChecks': list(FONT_RENDERING_CHECKS), 'checks': [],
                'screenshots': list(FONT_RENDERING_SCREENSHOTS)}
            for name, (selector, text, family, _) in FONT_RENDERING_CHECKS.items():
                postscript = FONT_VERIFIED_FILES[family]['postScriptName']
                self.font_rendering['checks'].append({'name': name, 'ok': True,
                    'selector': selector, 'nodeIndex': 0, 'expectedText': text, 'text': text,
                    'expectedFamily': family, 'expectedPostScriptName': postscript,
                    'visible': True, 'inViewport': True, 'receivesPointer': True,
                    'rect': {'x': 10, 'y': 10, 'width': 30, 'height': 30}, 'matchedGlyphCount': 1,
                    'fonts': [{'familyName': family, 'postScriptName': postscript,
                               'isCustomFont': False, 'glyphCount': 1}]})
            self.write_json(self.browser / 'ci-font-rendering.json', self.font_rendering)
            self.cli_bytes = b"non-executable synthetic test fixture"
            self.cli_hash = hashlib.sha256(self.cli_bytes).hexdigest()
            binding={"nativeBindingSchema":1,"candidateCommit":self.commit,"sourceDirty":False,"executableSha256":self.cli_hash,
                     "sourceFiles":{name:file_hash(ROOT/name) for name in native_sources()}}
            self.native.update(binding);self.watchtower.update(binding)
            self.archive_bytes={"7z.exe":b"synthetic non-executable archive helper","7z.dll":b"synthetic non-loadable archive library"}
            from unittest.mock import patch
            pins=patch.dict(ARCHIVE_PINS,{name:hashlib.sha256(body).hexdigest() for name,body in self.archive_bytes.items()},clear=True)
            pins.start();self.addCleanup(pins.stop)
            self.archive_proof={"schema":1,"kind":"actual-archive-review","ok":True,"platform":"Windows","candidateCommit":self.commit,"sourceDirty":False,
                "cliSha256":self.cli_hash,"helperSha256":ARCHIVE_PINS["7z.exe"],"helperDllSha256":ARCHIVE_PINS["7z.dll"],"nativeMo2Claim":False,"nativeWebView2Claim":False,
                "sources":{name:file_hash(ROOT/name) for name in ARCHIVE_SOURCES},"checks":[{"name":name,"ok":True} for name in sorted(ARCHIVE_CHECKS)],
                "scope":"Synthetic release-validator fixture; no native archive execution"}
            self.write_json(self.windows/"archive-review-proof.json",self.archive_proof)
            self.live = {"schemaVersion": 1, "ok": True, "nativeDesktop": True, "nativeMO2": True, "nativeWebView2": True,
                "candidateCommit": self.commit, "sourceDirty": False, "executableSha256": self.cli_hash,
                "sourceFiles": {name: file_hash(ROOT / name) for name in LIVE_DESKTOP_SOURCES},
                "scope": "Synthetic validator fixture only; no native execution",
                "checks": [{"name": name, "passed": True} for name in sorted(LIVE_DESKTOP_CHECKS)]}
            self.write_json(self.windows / "live-mo2-smoke/desktop-live.json", self.live)
            (self.windows / "live-mo2-smoke/desktop-live.png").write_bytes(self.png())
            for name in LIVE_DESKTOP_SCREENSHOTS:
                (self.windows / "live-mo2-smoke" / name).write_bytes(self.png())
            self.qt_bytes = b"non-loadable synthetic Qt bridge fixture"
            self.qt_hash = hashlib.sha256(self.qt_bytes).hexdigest()
            (self.windows / "qt-bridge/Release/modhunter-qt-bridge.dll").write_bytes(self.qt_bytes)
            self.qt_forward = {"ok": True, "realQt": True, "ownedSignalSelfTest": True,
                "schemaVersion": 1, "abi": 1, "sourceDirty": False, "candidateCommit": self.commit,
                "helperSha256": self.qt_hash, "buildQt": "6.7.1", "runtimeQt": "6.11.1",
                "pyQtVersion": "6.11.0", "mechanism": "typed-qt-sender",
                "testsRun": len(QT_FORWARD_CHECKS), "skipped": 0,
                "scope": "Synthetic release-validator fixture; no actual Qt execution",
                "sourceFiles": {name: file_hash(ROOT / name) for name in QT_FORWARD_SOURCES},
                "checks": [{"name": name, "passed": True} for name in sorted(QT_FORWARD_CHECKS)]}
            self.write_json(self.windows / "qt-forward/qt-forward.json", self.qt_forward)
            self.receipt = {"ok": True, "nativeMO2": True, "profileId": "fixture-profile",
                            "checks": [{"name": name, "passed": True} for name in sorted(REQUIRED_MO2_CHECKS)]}
            for name in ("rust_content_recovery","native_content_install"):
                check=next(c for c in self.receipt["checks"] if c["name"]==name)
                check.update(schemaVersion=1,profileId="fixture-profile",state="complete",realQt=True,actualModBytesRestored=True,nativeRefreshAcknowledged=True,
                             wrongTargetRejected=True,laterEditPreserved=True,lostAckDeduplicated=True,batchTargets=2,nativeInstallerOpened=name=="native_content_install",
                             nativeInstallVerified=True,bundledArchiveReviewVerified=True,exactTarget="Smoke Disabled",archiveSha256="f"*64,archiveBytes=100)
            self.audit = next(check for check in self.receipt["checks"] if check["name"] == "rust_plugin_audit")
            self.audit.update(schemaVersion=1, profileId="fixture-profile", state="completed", depth="records",
                helperSha256=self.cli_hash,
                fixtureMods={"Smoke Enabled": {"itemId": "enabled", "pluginCount": 0},
                             "Smoke Disabled": {"itemId": "disabled", "pluginCount": 1}},
                syntheticPlugin={"name": "ModhunterSmoke.esp", "owner": "Smoke Disabled", "verified": True,
                                 "bytes": 42, "declaredRecords": 0, "sha256": FIXTURE_PLUGIN_SHA256})
            self.qt = next(check for check in self.receipt["checks"] if check["name"] == "native_nexus_qt_binding")
            self.qt.update(abi=1, buildQt="6.7.1", runtimeQt="6.7.1", qtVersion="6.7.1", mechanism="typed-qt-sender",
                helperSha256=self.qt_hash, nativeQObject=True, metadataCompleted=True, rawFilesCompleted=True,
                unownedIgnored=True, cancelledIgnored=True, networkRequests=0, accountAuthentication=False)
            self.sources = {name: ("test fixture " + name).encode() for name in PRODUCTION_FILES}
            self.result = {"ok": True, "phase": "completed", "nativeExitCode": 0, "release": {"version": "2.5.2"},
                           "rustCli": {"required": True, "sha256": self.cli_hash}, "nativeReceipt": self.receipt,
                           "qtBridge": {"required": True, "sha256": self.qt_hash, "buildQt": "6.7.1"},
                           "productionFileHashes": {name: hashlib.sha256(body).hexdigest() for name, body in self.sources.items()}}
            self.write_json(self.windows / "native-smoke/native-smoke.json", self.native)
            self.write_json(self.windows / "watchtower-smoke/watchtower-smoke.json", self.watchtower)
            self.write_json(self.browser / "watchtower-browser.json", self.watchtower_browser)
            self.write_json(self.mo2 / "result.json", self.result)
            self.write_json(self.mo2 / "native-receipt.json", self.receipt)
            self.zip_path = self.windows / PORTABLE_NAME
            self.write_portable()
            (self.windows / "release/bundle/nsis" / INSTALLER_NAME).write_bytes(b"synthetic installer fixture - not executable")
            image = self.png()
            self.installer_proof = {"schemaVersion": 1, "ok": True, "candidateCommit": self.commit,
                "sourceDirty": False, "scope": "Synthetic release-validator fixture; no NSIS execution",
                "installerSha256": file_hash(self.windows / "release/bundle/nsis" / INSTALLER_NAME),
                "executableSha256": self.cli_hash, "installedExecutableSha256": self.cli_hash,
                "installerScriptSha256": file_hash(ROOT / "native/scripts/installer_smoke.ps1"),
                "checks": [{"name": name, "passed": True} for name in sorted(INSTALLER_CHECKS)],
                "archiveResourceHashes": dict(ARCHIVE_PINS)}
            self.write_json(self.windows / "installer-smoke/installer-smoke.json", self.installer_proof)
            self.write_json(self.windows / "installer-smoke/native-launch/native-smoke.json", self.native)
            (self.windows / "installer-smoke/native-launch/fixture-desktop.png").write_bytes(image)
            def library_geometry(view="list", density="compact"):
                return {"rows": NATIVE_LIBRARY_COUNT, "completeOrderedIds": True, "lastId": "item-11206",
                        "allLaidOut": True, "noContentVisibility": True, "view": view, "density": density,
                        "viewport": {"width": 1440, "height": 900, "devicePixelRatio": 1},
                        "firstRect": {"width": 900, "height": 64}, "lastRect": {"width": 900, "height": 64}}
            self.native_library = {**binding, "sourceFiles": dict(binding["sourceFiles"]),
                "schemaVersion": 1, "ok": True, "nativeWebView2": True, "nativeProcessId": 1234,
                "phase": "complete", "errors": [], "scope": "Synthetic release-validator fixture; no Windows renderer execution",
                "scriptSha256": file_hash(ROOT / "native/scripts/measure_native_library.cjs"),
                "launcherSha256": file_hash(ROOT / "native/scripts/benchmark_native_library.ps1"),
                "fixtureSha256": NATIVE_LIBRARY_FIXTURE_SHA, "fixtureBytes": NATIVE_LIBRARY_FIXTURE_BYTES,
                "records": NATIVE_LIBRARY_COUNT,
                "checks": [{"name": name, "passed": True} for name in sorted(NATIVE_LIBRARY_CHECKS)],
                "engine": {"protocolVersion": "1.3", "product": "Chrome/150.0.0.0", "revision": "synthetic",
                           "userAgent": "Synthetic validator fixture Windows", "jsVersion": "15.0",
                           "executable": r"C:\Program Files\Microsoft\EdgeWebView\Application\150.0.0.0\msedgewebview2.exe"},
                "initial": {"url": "http://tauri.localhost/Modhunter.html", "view": "list", "density": "compact",
                            "width": 1440, "height": 900, "devicePixelRatio": 1, "visibilityState": "visible",
                            "prefersReducedMotion": False, "documentHasFocus": True},
                "importGeometry": library_geometry(), "restoreGeometry": library_geometry(),
                "timingsMs": {"render": 100, "lastItemSearch": 50, "searchRestore": 100,
                              **{name: 100 for name in NATIVE_LIBRARY_ACTIONS}},
                "interactions": {name: {"wallMs": 100, "completeLibrary": library_geometry(*state),
                    "frameEndpoint": {"structuralBatchActive": False, "transitionProperty": "background", "transitionDuration": "0.12s"},
                    "metricsBefore": {key: 1 for key in NATIVE_LIBRARY_METRICS},
                    "metricsAfter": {key: 2 for key in NATIVE_LIBRARY_METRICS},
                    "metricDeltas": {key: 1 for key in NATIVE_LIBRARY_METRICS}} for name, state in NATIVE_LIBRARY_ACTIONS.items()},
                "isolation": {"schemaVersion": 1, "ok": True, "cleanupErrors": [], "policyValuesRemoved": 2,
                    "elapsedMs": 1000, "restoredFolders": [
                        {"path": r"C:\Users\runner\AppData\Roaming\com.modhunter.desktop", "originalExisted": True, "manifestMatched": True},
                        {"path": r"C:\Users\runner\AppData\Local\com.modhunter.desktop", "originalExisted": False, "manifestMatched": True}]}}
            self.write_json(self.windows / "native-library-performance/native-library-performance.json", self.native_library)
            sample = {"rows": 11207, "complete": True, "allLaidOut": True, "noContentVisibility": True,
                      "renderMs": 100, "lastItemSearchMs": 50}
            self.visual_proof = {"ok": True, "candidateCommit": self.commit, "baselineCommit": VISUAL_BASELINE,
                "sourceDirty": False, "scope": "Synthetic release-validator fixture; no browser execution",
                "assets": {"after": {name: file_hash(ROOT / name) for name in (
                    "mo2_update_arsenal/web/app.js", "mo2_update_arsenal/web/app.template.html", "mo2_update_arsenal/web/atelier.css")}},
                "completeLibrary": {"mods": 11207, "rounds": 3,
                    "variants": {variant: [dict(sample) for _ in range(3)] for variant in ("before", "after")}}}
            self.write_json(self.visual / "visual-release.json", self.visual_proof)
            for name in VISUAL_SCREENSHOTS:
                target = self.visual / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(image)
            (self.mo2 / "mo2-fixture.png").write_bytes(image)
            (self.browser / "review.png").write_bytes(image)
            for name in WATCHTOWER_SCREENSHOTS | NATIVE_MANAGER_SCREENSHOTS:
                (self.browser / name).write_bytes(image)
            for name, dimensions in FONT_RENDERING_SCREENSHOTS.items():
                (self.browser / name).write_bytes(self.png(*dimensions))
            (self.mo2 / "fixture-settings.ini").write_text("not public")
            (self.mo2 / "mo2.log").write_text("not public")
            (self.browser / "session.json").write_text('{"token":"must never be selected"}')

        @staticmethod
        def write_json(path, value):
            path.write_bytes(b"\xef\xbb\xbf" + public_json(value))

        @staticmethod
        def png(width=1, height=1):
            def chunk(kind, content):
                return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
            pixels = (b'\0' + b'\0' * (width * 4)) * height
            return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")

        def write_portable(self, commit=None, *, build_version=None, include_helper=True, helper_bytes=None, include_qt_helper=True, qt_bytes=None):
            with ZipFile(self.zip_path, "w") as archive:
                archive.writestr("BUILD_INFO.json", b"\xef\xbb\xbf" + public_json({"version": build_version or VERSION, "commit": commit or self.commit, "workflow": self.run}))
                for name in ("Modhunter.exe", "modhunter-cli.exe", "Check_MO2_Readiness.ps1", "Check_MO2_Readiness.bat", "START-HERE-MO2-253.md", "GameSync-Update-Techniques.md"):
                    archive.writestr(name, self.cli_bytes)
                for name in ("Install_MO2_Plugin.ps1", "Install_MO2_Plugin.bat"):
                    archive.writestr(name, (ROOT / "native" / name).read_bytes())
                if include_helper:
                    archive.writestr(BUNDLED_HELPER, self.cli_bytes if helper_bytes is None else helper_bytes)
                if include_qt_helper:
                    archive.writestr(BUNDLED_QT_HELPER, self.qt_bytes if qt_bytes is None else qt_bytes)
                archive.writestr("Evidence/native-smoke.json", public_json(self.native))
                archive.writestr("Evidence/watchtower-smoke.json", public_json(self.watchtower))
                archive.writestr("Evidence/qt-forward.json", public_json(self.qt_forward))
                archive.writestr("Evidence/desktop-live.json", public_json(self.live))
                archive.writestr("Evidence/archive-review-proof.json",public_json(self.archive_proof))
                archive.writestr("PROJECT_NOTICE.txt",(ROOT/"native/PROJECT_NOTICE.txt").read_bytes())
                locked=tomllib.loads((ROOT/"native/Cargo.lock").read_text("utf-8"))
                archive.writestr("THIRD_PARTY_NOTICES.json",json.dumps({"schema":1,"packages":[{"name":p["name"],"version":p["version"],"license":"Synthetic fixture"} for p in locked["package"] if p.get("source")]}))
                license_text=b"Synthetic archive license fixture"
                for prefix in ("archive-tools/","MO2/mo2_update_arsenal/bin/archive-tools/"):
                    for name,body in self.archive_bytes.items():archive.writestr(prefix+name,body)
                    archive.writestr(prefix+"License.txt",license_text)
                    archive.writestr(prefix+"provenance.json",json.dumps({"version":"26.06","files":ARCHIVE_PINS,"licenseSha256":hashlib.sha256(license_text).hexdigest()}))
                for name, body in self.sources.items():
                    archive.writestr("MO2/mo2_update_arsenal/" + name, body)
            self.refresh_portable_manifest()

        def refresh_portable_manifest(self):
            with ZipFile(self.zip_path) as archive:
                bodies = {name: archive.read(name) for name in archive.namelist() if name != "SHA256SUMS.txt"}
            manifest = "".join(f"{hashlib.sha256(body).hexdigest()}  {name}\n" for name, body in sorted(bodies.items()))
            with ZipFile(self.zip_path, "w") as archive:
                for name, body in bodies.items():
                    archive.writestr(name, body)
                archive.writestr("SHA256SUMS.txt", b"\xef\xbb\xbf" + manifest.encode("utf-8"))

        def test_forward_runtime_receipt_is_required_and_bound_to_exact_sources(self):
            import copy
            original = copy.deepcopy(self.qt_forward)
            cases = [
                {"ok": False}, {"realQt": False}, {"ownedSignalSelfTest": False},
                {"candidateCommit": "b" * 40}, {"helperSha256": "0" * 64},
                {"sourceDirty": True}, {"runtimeQt": "6.7.1"}, {"skipped": 1},
                {"checks": original["checks"][:-1]}, {"sourceFiles": {}},
            ]
            for changes in cases:
                with self.subTest(changes=changes):
                    self.write_json(self.windows / "qt-forward/qt-forward.json", {**original, **changes})
                    with self.assertRaises(ReleaseError):
                        prepare(self.artifacts, self.output, self.commit, self.run)
                    self.assertFalse(self.output.exists())
            self.write_json(self.windows / "qt-forward/qt-forward.json", original)

        def test_portable_manifest_rejects_missing_damaged_mixed_and_duplicate_files(self):
            with ZipFile(self.zip_path) as archive:
                original = {name: archive.read(name) for name in archive.namelist()}
            cases = (
                ("missing-manifest", "missing.*SHA256SUMS"),
                ("damaged-binary", "checksum failed.*Modhunter.exe"),
                ("unlisted-file", "does not cover exactly"),
                ("duplicate-manifest-entry", "repeats a file"),
                ("malformed-manifest", "invalid line"),
                ("windows-case-collision", "Duplicate portable"),
            )
            for case, message in cases:
                with self.subTest(case=case):
                    bodies = dict(original)
                    if case == "missing-manifest":
                        bodies.pop("SHA256SUMS.txt")
                    elif case == "damaged-binary":
                        bodies["Modhunter.exe"] = b"truncated synthetic executable"
                    elif case == "unlisted-file":
                        bodies["unlisted.py"] = b"unexpected file"
                    elif case == "duplicate-manifest-entry":
                        lines = bodies["SHA256SUMS.txt"].decode("utf-8-sig").splitlines()
                        bodies["SHA256SUMS.txt"] += (lines[0] + "\n").encode()
                    elif case == "malformed-manifest":
                        bodies["SHA256SUMS.txt"] = b"not a checksum manifest\n"
                    elif case == "windows-case-collision":
                        bodies["modhunter.exe"] = bodies["Modhunter.exe"]
                    with ZipFile(self.zip_path, "w") as archive:
                        for name, body in bodies.items():
                            archive.writestr(name, body)
                    with self.assertRaisesRegex(ReleaseError, message):
                        prepare(self.artifacts, self.output, self.commit, self.run)
                    self.assertFalse(self.output.exists())

        def portable_cli(self, artifacts, out):
            import contextlib
            import io
            import sys
            from unittest.mock import patch

            argv = [str(ROOT / "native/scripts/prepare_release.py"), str(artifacts), str(out),
                    "--commit", self.commit, "--run-url", self.run]
            stdout, stderr = io.StringIO(), io.StringIO()
            status = 0
            with (patch.object(sys, "argv", argv), contextlib.chdir(self.root),
                  contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr)):
                try:
                    main()
                except SystemExit as error:
                    status = error.code
            return status, stdout.getvalue(), stderr.getvalue()

        def test_packaging_cli_rejects_windows_aliases_in_a_moved_portable_zip(self):
            moved = self.root / "Relocated build files"
            relative_zip = self.zip_path.relative_to(self.artifacts)
            self.artifacts.rename(moved)
            self.zip_path = moved / relative_zip
            original = self.zip_path.read_bytes()
            cases = (
                ("file-first", ["Local evidence", "Local Evidence/note.txt"], "conflicting Windows"),
                ("directory-first", ["Local Evidence/note.txt", "Local evidence"], "conflicting Windows"),
                ("directory-case", ["Result/note-a.txt", "result/note-b.txt"], "conflicting Windows"),
                ("trailing-dot", ["Local Evidence./note.txt"], "Windows-incompatible"),
                ("trailing-space", ["Local Evidence/note.txt "], "Windows-incompatible"),
                ("reserved-device", ["Local Evidence/AUX.txt"], "Windows-incompatible"),
            )
            for label, names, reason in cases:
                with self.subTest(label=label):
                    self.zip_path.write_bytes(original)
                    with ZipFile(self.zip_path, "a") as archive:
                        for name in names:
                            archive.writestr(name, b"ordinary synthetic note")
                    self.refresh_portable_manifest()
                    out = self.root / f"Rejected {label}"
                    status, stdout, stderr = self.portable_cli(moved, out)
                    self.assertNotEqual(status, 0, stdout)
                    self.assertIn(reason, stderr)
                    self.assertFalse(out.exists())

        def test_packaging_cli_keeps_valid_moved_portable_bytes(self):
            moved = self.root / "Relocated release files"
            relative_zip = self.zip_path.relative_to(self.artifacts)
            self.artifacts.rename(moved)
            self.zip_path = moved / relative_zip
            with ZipFile(self.zip_path, "a") as archive:
                archive.writestr("Field Notes/first.txt", b"one")
                archive.writestr("Field Notes/second.txt", b"two")
            self.refresh_portable_manifest()
            status, stdout, stderr = self.portable_cli(moved, self.output)
            self.assertEqual(status, 0, stderr)
            self.assertTrue(json.loads(stdout)["ok"])
            self.assertEqual((self.output / PORTABLE_NAME).read_bytes(), self.zip_path.read_bytes())

        def test_source_versions_reject_stale_manifest_lockfile_and_plugin(self):
            manifests = ("native/Cargo.toml", "native/Cargo.lock", "native/crates/modhunter-core/Cargo.toml",
                         "native/crates/modhunter-desktop/Cargo.toml", "native/crates/modhunter-desktop/tauri.conf.json",
                         "mo2_update_arsenal/version.py")
            fixture = self.root / "source"
            for relative in manifests:
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            self.assertEqual(source_version(fixture), VERSION)
            mutations = (
                ("native/crates/modhunter-desktop/tauri.conf.json", f'"version": "{VERSION}"', '"version": "0.0.0"', "Tauri"),
                ("mo2_update_arsenal/version.py", f'VERSION = "{VERSION}"', 'VERSION = "0.0.0"', "MO2 plugin"),
                ("native/Cargo.lock", f'name = "modhunter-core"\nversion = "{VERSION}"', 'name = "modhunter-core"\nversion = "0.0.0"', "Cargo.lock"),
                ("native/Cargo.lock", f'name = "modhunter-desktop"\nversion = "{VERSION}"', 'name = "modhunter-desktop"\nversion = "0.0.0"', "Cargo.lock"),
                ("native/crates/modhunter-core/Cargo.toml", "version.workspace = true", f'version = "{VERSION}"', "must inherit"),
                ("native/Cargo.toml", f'version = "{VERSION}"', 'version = "1.0.0-preview"', "stable numeric"),
            )
            for relative, before, after, error in mutations:
                with self.subTest(file=relative, error=error):
                    path = fixture / relative
                    original = path.read_text("utf-8")
                    self.assertIn(before, original)
                    path.write_text(original.replace(before, after, 1), "utf-8")
                    with self.assertRaisesRegex(ReleaseError, error):
                        source_version(fixture)
                    path.write_text(original, "utf-8")

        def test_stale_portable_or_installer_version_cannot_be_published(self):
            self.write_portable(build_version="0.0.0")
            with self.assertRaisesRegex(ReleaseError, "Portable version"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())
            self.write_portable()
            (self.windows / "release/bundle/nsis" / INSTALLER_NAME).rename(
                self.windows / "release/bundle/nsis/Modhunter_0.0.0_x64-setup.exe")
            with self.assertRaisesRegex(ReleaseError, "NSIS installer must"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_new_benchmarks_are_preserved_with_identity_and_credential_checks(self):
            filenames = BENCHMARK_FILES
            fixture = {"method": "synthetic release-test fixture", "medianMs": 12.5}
            with ZipFile(self.zip_path, "a") as archive:
                for name in filenames:
                    archive.writestr("Evidence/" + name, public_json(fixture))
            self.refresh_portable_manifest()
            dashboard = {"candidateCommit": self.commit, "method": "synthetic release-test fixture"}
            self.write_json(self.browser / "dashboard-render-comparison.json", dashboard)
            prepare(self.artifacts, self.output, self.commit, self.run)
            with ZipFile(self.output / VERIFICATION_NAME) as archive:
                for name in filenames:
                    self.assertEqual(json.loads(archive.read("benchmarks/" + name)), fixture)
                self.assertEqual(json.loads(archive.read("benchmarks/dashboard-render-comparison.json")), dashboard)
            dashboard["candidateCommit"] = "b" * 40
            self.write_json(self.browser / "dashboard-render-comparison.json", dashboard)
            rejected = self.root / "rejected"
            with self.assertRaisesRegex(ReleaseError, "Dashboard benchmark"):
                prepare(self.artifacts, rejected, self.commit, self.run)
            self.assertFalse(rejected.exists())
            dashboard["token"] = "must never be published"
            self.write_json(self.browser / "dashboard-render-comparison.json", dashboard)
            with self.assertRaisesRegex(ReleaseError, "Credential field"):
                prepare(self.artifacts, rejected, self.commit, self.run)
            self.assertFalse(rejected.exists())

        def test_live_desktop_gate_rejects_missing_wrong_binary_and_failed_checks(self):
            for changes in ({"ok": False}, {"nativeMO2": False}, {"sourceDirty": True},
                            {"candidateCommit": "b" * 40}, {"executableSha256": "0" * 64},
                            {"checks": []}, {"sourceFiles": {}}):
                with self.subTest(changes=changes), self.assertRaises(ReleaseError):
                    validate_live_desktop({**self.live, **changes}, self.commit, self.cli_hash)
            validate_live_desktop(self.live, self.commit, self.cli_hash)
            (self.windows / "live-mo2-smoke/desktop-live.json").unlink()
            with self.assertRaises(ReleaseError):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_success_keeps_binaries_and_checksums_and_public_allowlist(self):
            portable_digest = file_hash(self.zip_path)
            prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertEqual(file_hash(self.output / PORTABLE_NAME), portable_digest)
            for line in (self.output / "SHA256SUMS.txt").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(file_hash(self.output / name), digest)
            self.assertNotIn("release-notes.md", (self.output / "SHA256SUMS.txt").read_text())
            with ZipFile(self.output / VERIFICATION_NAME) as archive:
                names = archive.namelist()
                expected = {"windows/desktop-live.json", "windows/desktop-live.png", "BUILD_INFO.json", "windows/qt-forward.json", "windows/native-smoke.json", "windows/watchtower-smoke.json", "windows/archive-review-proof.json", "mo2/result.json", "mo2/native-receipt.json", "mo2/mo2-fixture.png", "browser/review.png", "browser/watchtower-browser.json"}
                expected.update("browser/" + name for name in WATCHTOWER_SCREENSHOTS | NATIVE_MANAGER_SCREENSHOTS)
                expected.add("browser/native-manager-ui.json")
                expected.update({'browser/ci-fonts.json', 'browser/ci-font-rendering.json'})
                expected.update('browser/' + name for name in FONT_RENDERING_SCREENSHOTS)
                expected.update({"windows/installer-smoke.json", "windows/installed-native-smoke.json",
                                 "windows/installed-desktop.png", "windows/native-library-performance.json", "visual/visual-release.json"})
                expected.update("windows/" + name for name in LIVE_DESKTOP_SCREENSHOTS)
                expected.update("visual/" + name for name in VISUAL_SCREENSHOTS)
                self.assertEqual(set(names), expected)
                self.assertTrue(all(PurePosixPath(name).suffix in (".json", ".png") for name in names))
                raw_font = (self.browser / 'ci-fonts.json').read_bytes()
                self.assertNotEqual(raw_font, public_json(self.font_receipt))
                self.assertEqual(archive.read('browser/ci-fonts.json'), raw_font)
                digest = hashlib.sha256(archive.read('browser/ci-fonts.json')).hexdigest()
                self.assertEqual(json.loads(archive.read('browser/native-manager-ui.json'))['cjkFontProof']['fontReceiptSha256'], digest)
                self.assertEqual(json.loads(archive.read('browser/ci-font-rendering.json'))['fontReceiptSha256'], digest)
                for name in FONT_RENDERING_SCREENSHOTS:
                    self.assertEqual(archive.read('browser/' + name), (self.browser / name).read_bytes())

        def test_plugin_installer_is_bound_to_the_tested_script(self):
            with ZipFile(self.zip_path) as archive:
                bodies = {name: archive.read(name) for name in archive.namelist() if name != "SHA256SUMS.txt"}
            bodies["Install_MO2_Plugin.ps1"] += b"\n# A different installer than the candidate source\n"
            with ZipFile(self.zip_path, "w") as archive:
                for name, body in bodies.items():
                    archive.writestr(name, body)
            self.refresh_portable_manifest()
            with self.assertRaisesRegex(ReleaseError, "plugin installer differs from the tested source"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_visual_gate_rejects_incomplete_wrong_commit_or_reduced_library(self):
            from copy import deepcopy
            original = deepcopy(self.visual_proof)
            for change in (lambda r: r.update(ok=False), lambda r: r.update(candidateCommit="b"*40),
                           lambda r: r.update(sourceDirty=True),
                           lambda r: r["completeLibrary"].update(mods=9999),
                           lambda r: r["completeLibrary"]["variants"]["after"][0].update(allLaidOut=False),
                           lambda r: r["completeLibrary"]["variants"]["after"][0].update(rows=11206)):
                report = deepcopy(original)
                change(report)
                self.write_json(self.visual / "visual-release.json", report)
                with self.assertRaises(ReleaseError):
                    prepare(self.artifacts, self.output, self.commit, self.run)
                self.assertFalse(self.output.exists())

        def test_installer_gate_rejects_wrong_binary_or_failed_uninstall(self):
            from copy import deepcopy
            original = deepcopy(self.installer_proof)
            for change in (lambda r: r.update(installedExecutableSha256="0"*64),
                           lambda r: r.update(installerSha256="0"*64),
                           lambda r: r.update(installerScriptSha256="0"*64),
                           lambda r: r["checks"].pop(),
                           lambda r: r["checks"][0].update(passed=False)):
                report = deepcopy(original)
                change(report)
                self.write_json(self.windows / "installer-smoke/installer-smoke.json", report)
                with self.assertRaises(ReleaseError):
                    prepare(self.artifacts, self.output, self.commit, self.run)
                self.assertFalse(self.output.exists())

        def test_native_library_receipt_is_mandatory_and_published_exactly(self):
            proof = self.windows / "native-library-performance/native-library-performance.json"
            proof.unlink()
            with self.assertRaisesRegex(ReleaseError, "Required artifact is missing"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())
            self.write_json(proof, self.native_library)
            prepare(self.artifacts, self.output, self.commit, self.run)
            with ZipFile(self.output / VERIFICATION_NAME) as archive:
                self.assertEqual(json.loads(archive.read("windows/native-library-performance.json")), self.native_library)

        def test_native_library_rejects_stale_identity_fixture_or_partial_checks(self):
            from copy import deepcopy
            changes = (
                lambda r: r.update(candidateCommit="b"*40), lambda r: r.update(sourceDirty=True),
                lambda r: r.update(executableSha256="0"*64), lambda r: r.update(nativeBindingSchema=0),
                lambda r: r["sourceFiles"].pop(next(iter(r["sourceFiles"]))),
                lambda r: r.update(scriptSha256="0"*64), lambda r: r.update(launcherSha256="0"*64),
                lambda r: r.update(fixtureSha256="0"*64), lambda r: r.update(fixtureBytes=1),
                lambda r: r.update(records=11206), lambda r: r.update(phase="gallery-starting"),
                lambda r: r.update(errors=["native renderer failed"]), lambda r: r.update(ok=False),
                lambda r: r["checks"].pop(), lambda r: r["checks"][0].update(passed=False),
                lambda r: r["checks"].__setitem__(0, dict(r["checks"][1])),
            )
            for change in changes:
                report = deepcopy(self.native_library); change(report)
                self.write_json(self.windows / "native-library-performance/native-library-performance.json", report)
                with self.assertRaises(ReleaseError):
                    prepare(self.artifacts, self.output, self.commit, self.run)
                self.assertFalse(self.output.exists())

        def test_native_library_rejects_hidden_missing_or_wrong_view_records_at_every_phase(self):
            from copy import deepcopy
            for phase in ("importGeometry", "restoreGeometry", *NATIVE_LIBRARY_ACTIONS):
                for key, invalid in (("rows", 11206), ("completeOrderedIds", False), ("allLaidOut", False),
                                     ("noContentVisibility", False), ("lastId", "item-9999"),
                                     ("view", "other"), ("density", "other")):
                    with self.subTest(phase=phase, field=key):
                        report = deepcopy(self.native_library)
                        geometry = report[phase] if phase.endswith("Geometry") else report["interactions"][phase]["completeLibrary"]
                        geometry[key] = invalid
                        with self.assertRaises(ReleaseError):
                            validate_native_library(report, self.commit, self.cli_hash)

        def test_native_library_requires_finite_timings(self):
            from copy import deepcopy
            for name in self.native_library["timingsMs"]:
                for invalid in (0, -1, True, float("nan"), float("inf"), float("-inf")):
                    report = deepcopy(self.native_library); report["timingsMs"][name] = invalid
                    with self.subTest(metric=name, value=invalid), self.assertRaises(ReleaseError):
                        validate_native_library(report, self.commit, self.cli_hash)
            report = deepcopy(self.native_library)
            report["interactions"]["gallery"]["metricDeltas"]["LayoutDuration"] = float("nan")
            with self.assertRaises(ReleaseError):
                validate_native_library(report, self.commit, self.cli_hash)

        def test_native_library_rejects_slow_complete_results_at_each_release_ceiling(self):
            from copy import deepcopy
            for name, ceiling in NATIVE_LIBRARY_BUDGET_MS.items():
                report = deepcopy(self.native_library)
                report["timingsMs"][name] = ceiling
                if name in report["interactions"]:
                    report["interactions"][name]["wallMs"] = ceiling
                validate_native_library(report, self.commit, self.cli_hash)
                report["timingsMs"][name] = ceiling + 1
                if name in report["interactions"]:
                    report["interactions"][name]["wallMs"] = ceiling + 1
                with self.subTest(action=name), self.assertRaisesRegex(ReleaseError, "release ceiling"):
                    validate_native_library(report, self.commit, self.cli_hash)

        def test_native_library_rejects_deferred_or_missing_hover_restoration(self):
            from copy import deepcopy
            for name in NATIVE_LIBRARY_ACTIONS:
                for key, value in (("structuralBatchActive", True), ("transitionProperty", "none"), ("transitionDuration", "0s")):
                    report = deepcopy(self.native_library)
                    report["interactions"][name]["frameEndpoint"][key] = value
                    with self.subTest(action=name, field=key), self.assertRaisesRegex(ReleaseError, "measured endpoint"):
                        validate_native_library(report, self.commit, self.cli_hash)

        def test_native_library_rejects_synthetic_runtime_or_failed_state_restoration(self):
            from copy import deepcopy
            changes = (
                lambda r: r.update(nativeWebView2=False), lambda r: r.update(nativeProcessId=0),
                lambda r: r["engine"].update(executable=r"C:\Chromium\chrome.exe"),
                lambda r: r["engine"].update(product=""),
                lambda r: r["initial"].update(url="http://127.0.0.1/"),
                lambda r: r["initial"].update(visibilityState="hidden"),
                lambda r: r["initial"].update(width=float("nan")),
                lambda r: r["isolation"].update(ok=False),
                lambda r: r["isolation"].update(cleanupErrors=["restore failed"]),
                lambda r: r["isolation"].update(policyValuesRemoved=1),
                lambda r: r["isolation"]["restoredFolders"].pop(),
                lambda r: r["isolation"]["restoredFolders"][0].update(manifestMatched=False),
                lambda r: r["isolation"]["restoredFolders"].__setitem__(0, dict(r["isolation"]["restoredFolders"][1])),
            )
            for change in changes:
                report = deepcopy(self.native_library); change(report)
                with self.assertRaises(ReleaseError):
                    validate_native_library(report, self.commit, self.cli_hash)

        def test_component_archives_keep_every_mo2_file_and_exact_tested_html(self):
            # Extra nested content proves selection keeps the complete plugin,
            # rather than a hardcoded subset of known Python modules/helpers.
            with ZipFile(self.zip_path, "a") as archive:
                archive.writestr("MO2/mo2_update_arsenal/extra-assets/original.bin", b"exact extra plugin asset\x00\xff")
                archive.writestr("MO2/README.txt", b"another MO2 component")
            self.refresh_portable_manifest()
            prepare(self.artifacts, self.output, self.commit, self.run)
            with ZipFile(self.zip_path) as original:
                source = {name: original.read(name) for name in original.namelist()}
            copied_mo2 = {name for name in source if name.startswith("MO2/") or name in MO2_PACKAGE_ROOT_FILES}
            with ZipFile(self.output / MO2_PACKAGE_NAME) as archive:
                self.assertEqual(set(archive.namelist()), copied_mo2 | {"START-HERE.txt", "SHA256SUMS.txt"})
                for name in copied_mo2:
                    self.assertEqual(archive.read(name), source[name], name)
                checksums = portable_checksums(archive, {entry.filename: entry for entry in archive.infolist()})
                self.assertEqual(set(checksums), copied_mo2 | {"START-HERE.txt"})
                self.assertIn(PORTABLE_NAME.encode(), archive.read("START-HERE.txt"))
                self.assertNotIn("Modhunter.exe", archive.namelist())
                self.assertNotIn("Check_MO2_Readiness.ps1", archive.namelist())
                self.assertNotIn("modhunter-cli.exe", archive.namelist())
                self.assertTrue(all(not name.startswith("Evidence/") for name in archive.namelist()))
            with ZipFile(self.output / STANDALONE_NAME) as archive:
                self.assertEqual(set(archive.namelist()), {"Modhunter.html", "BUILD_INFO.json", "SHA256SUMS.txt"})
                self.assertEqual(archive.read("Modhunter.html"), source["MO2/mo2_update_arsenal/web/index.html"])
                self.assertEqual(archive.read("BUILD_INFO.json"), source["BUILD_INFO.json"])
                portable_checksums(archive, {entry.filename: entry for entry in archive.infolist()})
            public_checksums = (self.output / "SHA256SUMS.txt").read_text()
            for name in (PORTABLE_NAME, INSTALLER_NAME, VERIFICATION_NAME, MO2_PACKAGE_NAME, STANDALONE_NAME):
                self.assertIn(f"{file_hash(self.output / name)}  {name}\n", public_checksums)

        def test_component_archives_are_reproducible_for_identical_validated_bytes(self):
            prepare(self.artifacts, self.output, self.commit, self.run)
            repeated = self.root / "repeated"
            prepare(self.artifacts, repeated, self.commit, self.run)
            for name in (MO2_PACKAGE_NAME, STANDALONE_NAME):
                self.assertEqual(file_hash(self.output / name), file_hash(repeated / name))

        def test_component_failure_does_not_publish_partial_output(self):
            from unittest.mock import patch
            original = component_archive
            def fail_second(destination, bodies):
                if destination.name == STANDALONE_NAME:
                    raise OSError("Synthetic component disk failure")
                original(destination, bodies)
            with patch(__name__ + ".component_archive", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "Synthetic component disk failure"):
                    prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())
            self.assertFalse(list(self.root.glob(".modhunter-release-*")))

        def test_portable_changed_after_validation_cannot_seed_component_assets(self):
            from unittest.mock import patch
            original = portable_contents
            def change_after_validation(*args, **kwargs):
                result = original(*args, **kwargs)
                with ZipFile(self.zip_path, "a") as archive:
                    archive.writestr("unvalidated.txt", b"changed after the source bytes were verified")
                return result
            with patch(__name__ + ".portable_contents", side_effect=change_after_validation):
                with self.assertRaisesRegex(ReleaseError, "changed during release validation"):
                    prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_native_manager_rejects_stale_partial_or_synthetic_interaction_proof(self):
            original=public_json(self.manager_ui)
            for mutate in (
                lambda r:r.update(candidateCommit="b"*40),
                lambda r:r.update(sourceDirty=True),
                lambda r:r.update(realChromium=False),
                lambda r:r.update(skipped=1),
                lambda r:r["sourceFiles"].update({NATIVE_MANAGER_SOURCES[0]:"0"*64}),
                lambda r:r["checks"].pop(),
                lambda r:r["checks"][0].update(passed=False),
                lambda r:r["checks"].__setitem__(0,r["checks"][1]),
            ):
                report=json.loads(original);mutate(report)
                with self.assertRaises(ReleaseError):validate_native_manager_ui(report,self.commit)
            (self.browser/"native-manager-ui.json").unlink()
            with self.assertRaises(ReleaseError):prepare(self.artifacts,self.output,self.commit,self.run)
            self.assertFalse(self.output.exists())

        def test_cjk_glyph_proof_is_mandatory_and_rejects_missing_or_substituted_faces(self):
            from copy import deepcopy
            original = self.manager_ui['cjkFontProof']
            digest = file_hash(self.browser / 'ci-fonts.json')
            for change in (
                lambda p: p.update(ok=False), lambda p: p.update(visible=False),
                lambda p: p.update(inViewport=False), lambda p: p.update(receivesPointer=False),
                lambda p: p.update(text='Preferences'), lambda p: p.update(locale='en'),
                lambda p: p.update(fontReceiptSha256='0'*64), lambda p: p.update(matchedGlyphCount=7),
                lambda p: p['glyphCoverage']['glyphs'][0].update(covered=False),
                lambda p: p['fonts'][0].update(glyphCount=7),
                lambda p: p['fonts'][0].update(familyName='Missing glyph fallback'),
                lambda p: p['fonts'][0].update(postScriptName='Unknown'),
                lambda p: p['fonts'][0].update(isCustomFont=True), lambda p: p.update(fonts=[]),
            ):
                proof = deepcopy(original); change(proof)
                with self.assertRaises(ReleaseError): validate_cjk_font(proof, self.font_receipt, digest)
            self.manager_ui.pop('cjkFontProof')
            self.write_json(self.browser / 'native-manager-ui.json', self.manager_ui)
            with self.assertRaises(ReleaseError): prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_cjk_prerequisite_cannot_be_omitted_or_reused_from_a_different_helper(self):
            from copy import deepcopy
            digest = file_hash(self.browser / 'ci-fonts.json')
            for change in (
                lambda r: r.update(scriptSha256='0'*64), lambda r: r.update(ok=False),
                lambda r: r.update(upstreamCommit='0'*40), lambda r: r.update(registeredFamily=None),
                lambda r: r.update(registeredPostScriptName='Unknown'), lambda r: r['files'].pop(),
                lambda r: r['files'][0].update(sha256='0'*64),
                lambda r: r['files'][0].update(bytes=1),
                lambda r: r['glyphCoverage']['glyphs'][0].update(covered=False),
            ):
                receipt = deepcopy(self.font_receipt); change(receipt)
                with self.assertRaises(ReleaseError):
                    validate_cjk_font(self.manager_ui['cjkFontProof'], receipt, digest)
            (self.browser / 'ci-fonts.json').unlink()
            with self.assertRaises(ReleaseError): prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_ci_glyph_evidence_requires_report_and_exact_validated_screenshots(self):
            for name in ('ci-font-rendering.json', *FONT_RENDERING_SCREENSHOTS):
                path = self.browser / name
                original = path.read_bytes()
                path.unlink()
                with self.subTest(missing=name), self.assertRaises(ReleaseError):
                    prepare(self.artifacts, self.output, self.commit, self.run)
                self.assertFalse(self.output.exists())
                path.write_bytes(original)
            for name, dimensions in FONT_RENDERING_SCREENSHOTS.items():
                path = self.browser / name
                original = path.read_bytes()
                path.write_bytes(self.png(dimensions[0] - 1, dimensions[1]))
                with self.subTest(wrong_dimensions=name), self.assertRaisesRegex(ReleaseError, 'dimensions'):
                    prepare(self.artifacts, self.output, self.commit, self.run)
                self.assertFalse(self.output.exists())
                damaged = bytearray(original)
                damaged[-1] ^= 1
                path.write_bytes(damaged)
                with self.subTest(damaged=name), self.assertRaisesRegex(ReleaseError, 'PNG checksum'):
                    prepare(self.artifacts, self.output, self.commit, self.run)
                self.assertFalse(self.output.exists())
                path.write_bytes(original)

        def test_ci_glyph_evidence_rejects_wrong_source_receipt_and_partial_execution(self):
            from copy import deepcopy
            digest = file_hash(self.browser / 'ci-fonts.json')
            for index, mutate in enumerate((
                lambda r: r.update(schema=True), lambda r: r.update(kind='translated-text-only'),
                lambda r: r.update(ok=False), lambda r: r.update(phase='dashboard-glyph-proven'),
                lambda r: r.update(errors=['Renderer failed']), lambda r: r.update(candidateCommit='b'*40),
                lambda r: r.update(sourceDirty=True), lambda r: r.update(sourceStatusAtStart=['M app.js']),
                lambda r: r.update(sourceStatusAtEnd=['M app.js']),
                lambda r: r['sourceFiles'].update({FONT_RENDERING_SOURCES[0]: '0'*64}),
                lambda r: r['sourceFiles'].pop(FONT_RENDERING_SOURCES[1]),
                lambda r: r['sourceFiles'].update({'untested.py': '0'*64}),
                lambda r: r['assets'].update({'mo2_update_arsenal/web/app.js': '0'*64}),
                lambda r: r['assets'].pop(FONT_RENDERING_ASSETS[0]),
                lambda r: r['assets'].update({'untested.css': '0'*64}),
                lambda r: r.update(fontHelperSha256='0'*64), lambda r: r.update(fontReceiptSha256='0'*64),
                lambda r: r['verifiedFontFiles']['Noto Sans Runic'].update(sha256='0'*64),
                lambda r: r['verifiedFontFiles'].pop('Noto Sans Symbols2'),
                lambda r: r.update(requiredChecks=[]), lambda r: r['checks'].pop(),
                lambda r: r['checks'].__setitem__(0, r['checks'][1]),
                lambda r: r.update(catalogRows=226), lambda r: r.update(catalogFixtureSha256='invalid'),
                lambda r: r.update(browserVersion=''), lambda r: r.update(requestedExecutable=''),
                lambda r: r['screenshots'].pop(),
            )):
                report = deepcopy(self.font_rendering)
                mutate(report)
                with self.subTest(case=index), self.assertRaises(ReleaseError):
                    validate_ci_font_rendering(report, self.commit, self.font_receipt, digest)

        def test_ci_glyph_prerequisite_rejects_substituted_fonts_licenses_and_coverage(self):
            from copy import deepcopy
            for path in FONT_PREREQUISITE_FILES:
                for key, value in (('bytes', 1), ('sha256', '0'*64), ('gitBlob', '0'*40)):
                    receipt = deepcopy(self.font_receipt)
                    next(entry for entry in receipt['files'] if entry['path'] == path)[key] = value
                    with self.subTest(path=path, key=key), self.assertRaises(ReleaseError):
                        validate_font_prerequisite(receipt)
            for index, mutate in enumerate((
                lambda r: r['files'].append(dict(r['files'][0])),
                lambda r: r['supplementalFonts'].pop(),
                lambda r: r['supplementalFonts'].__setitem__(0, r['supplementalFonts'][1]),
                lambda r: r['supplementalFonts'][0].update(upstreamCommit='0'*40),
                lambda r: r['supplementalFonts'][1].update(registeredPostScriptName='Unknown'),
                lambda r: r['supplementalFonts'][0]['glyphCoverage']['glyphs'][1].update(covered=False),
                lambda r: r['supplementalFonts'][1]['glyphCoverage'].update(text='x'),
                lambda r: r['supplementalGlyphCoverage']['fullwidthZoom']['glyphs'][0].update(covered=False),
                lambda r: r.update(supplementalGlyphCoverage={}),
            )):
                receipt = deepcopy(self.font_receipt)
                mutate(receipt)
                with self.subTest(case=index), self.assertRaises(ReleaseError):
                    validate_font_prerequisite(receipt)

        def test_ci_glyph_checks_require_actual_visible_face_usage_for_every_control(self):
            from copy import deepcopy
            digest = file_hash(self.browser / 'ci-fonts.json')
            for index, (name, (_, _, _, interactive)) in enumerate(FONT_RENDERING_CHECKS.items()):
                changes = [lambda c: c.update(ok=False), lambda c: c.update(selector='body'),
                    lambda c: c.update(nodeIndex=True), lambda c: c.update(text='missing'),
                    lambda c: c.update(expectedText='missing'), lambda c: c.update(visible=False),
                    lambda c: c.update(inViewport=False), lambda c: c.update(matchedGlyphCount=True),
                    lambda c: c['rect'].update(width=0), lambda c: c['rect'].update(x=float('nan')),
                    lambda c: c['rect'].update(y=1000), lambda c: c.update(fonts=[]),
                    lambda c: c['fonts'][0].update(familyName='Missing glyph fallback'),
                    lambda c: c['fonts'][0].update(postScriptName='Unknown'),
                    lambda c: c['fonts'][0].update(isCustomFont=True),
                    lambda c: c['fonts'][0].update(glyphCount=True),
                    lambda c: c['fonts'][0].update(glyphCount=-1),
                    lambda c: c['fonts'][0].update(glyphCount=0),
                    lambda c: c['fonts'].append({'familyName': 'Fallback', 'glyphCount': 1})]
                if interactive:
                    changes.append(lambda c: c.update(receivesPointer=False))
                for case, mutate in enumerate(changes):
                    report = deepcopy(self.font_rendering)
                    mutate(report['checks'][index])
                    with self.subTest(name=name, case=case), self.assertRaises(ReleaseError):
                        validate_ci_font_rendering(report, self.commit, self.font_receipt, digest)
            # A decorative rune need not be a pointer target; unused font faces
            # reported by Chromium are harmless only when their glyph count is 0.
            report = deepcopy(self.font_rendering)
            for check in report['checks']:
                check['fonts'].append({'familyName': 'Unused fallback', 'glyphCount': 0})
                if not FONT_RENDERING_CHECKS[check['name']][3]:
                    check['receivesPointer'] = False
            validate_ci_font_rendering(report, self.commit, self.font_receipt, digest)

        def test_ci_glyph_game_requires_actual_practice_canvas_and_zoom_restoration(self):
            from copy import deepcopy
            digest = file_hash(self.browser / 'ci-fonts.json')
            for key, value in (('profileId', 'live'), ('connected', True), ('mode', 'title'),
                               ('actualCanvasDrawn', False), ('enteredThroughUi', False)):
                report = deepcopy(self.font_rendering)
                report['game'][key] = value
                with self.subTest(game=key), self.assertRaises(ReleaseError):
                    validate_ci_font_rendering(report, self.commit, self.font_receipt, digest)
            for key, value in (('original', True), ('original', 0), ('increased', float('inf')),
                               ('increased', 48), ('restored', 54), ('restored', float('nan'))):
                report = deepcopy(self.font_rendering)
                report['zoomInteraction'][key] = value
                with self.subTest(zoom=key, value=value), self.assertRaises(ReleaseError):
                    validate_ci_font_rendering(report, self.commit, self.font_receipt, digest)

        def test_raw_font_receipt_remains_credential_checked_before_publication(self):
            self.font_receipt['token'] = 'must-never-be-published'
            self.write_json(self.browser / 'ci-fonts.json', self.font_receipt)
            with self.assertRaisesRegex(ReleaseError, 'Credential field'):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_archive_gate_rejects_wrong_binary_helper_source_and_missing_case(self):
            original=public_json(self.archive_proof)
            for mutate in (
                lambda r:r.update(cliSha256="0"*64),
                lambda r:r.update(helperSha256="0"*64),
                lambda r:r.update(helperDllSha256="0"*64),
                lambda r:r.update(sourceDirty=True),
                lambda r:r.update(nativeMo2Claim=True),
                lambda r:r["sources"].update({ARCHIVE_SOURCES[0]:"0"*64}),
                lambda r:r["checks"].pop(),
                lambda r:r["checks"][0].update(ok=False),
            ):
                report=json.loads(original);mutate(report)
                with self.assertRaises(ReleaseError):validate_archive_report(report,self.commit,self.cli_hash)

        def test_native_binding_rejects_wrong_executable_commit_and_incomplete_source(self):
            original=public_json(self.native)
            for mutate in (
                lambda r:r.update(executableSha256="0"*64),
                lambda r:r.update(candidateCommit="b"*40),
                lambda r:r.update(sourceDirty=True),
                lambda r:r["sourceFiles"].pop(next(iter(r["sourceFiles"]))),
            ):
                report=json.loads(original);mutate(report)
                with self.assertRaises(ReleaseError):validate_native_binding(report,self.commit,self.cli_hash)

        def test_watchtower_requires_native_complete_passing_unique_credential_free_receipt(self):
            path = self.windows / "watchtower-smoke/watchtower-smoke.json"
            path.unlink()
            with self.assertRaisesRegex(ReleaseError, "Required artifact is missing"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())
            cases = (
                ("not-native", "WebView2 fixture did not pass"),
                ("invalid-success", "WebView2 fixture did not pass"),
                ("invalid-schema", "schema 1"),
                ("missing-check", "omitted required"),
                ("failed-check", "failed or invalid check"),
                ("duplicate-check", "repeats a check"),
                ("credential-field", "Credential field"),
            )
            for case, message in cases:
                with self.subTest(case=case):
                    report = json.loads(json.dumps(self.watchtower))
                    if case == "not-native":
                        report["nativeWebView2"] = False
                    elif case == "invalid-success":
                        report["ok"] = "true"
                    elif case == "invalid-schema":
                        report["watchtowerSchema"] = True
                    elif case == "missing-check":
                        report["checks"].pop()
                    elif case == "failed-check":
                        report["checks"][0]["passed"] = False
                    elif case == "duplicate-check":
                        report["checks"].append(dict(report["checks"][0]))
                    elif case == "credential-field":
                        report["cookie"] = "not public"
                    self.write_json(path, report)
                    with self.assertRaisesRegex(ReleaseError, message):
                        prepare(self.artifacts, self.output, self.commit, self.run)
                    self.assertFalse(self.output.exists())

        def test_packaged_watchtower_receipt_must_match_actual_windows_artifact(self):
            report = dict(self.watchtower, scope="Different receipt")
            self.write_json(self.windows / "watchtower-smoke/watchtower-smoke.json", report)
            with self.assertRaisesRegex(ReleaseError, "Portable Watchtower smoke differs"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_watchtower_browser_requires_matching_source_and_complete_real_chromium_receipt(self):
            path = self.browser / "watchtower-browser.json"
            path.unlink()
            with self.assertRaisesRegex(ReleaseError, "Required artifact is missing"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())
            cases = (
                ("not-chromium", "Chromium interaction proof"),
                ("wrong-commit", "clean tested build commit"),
                ("dirty-source", "clean tested build commit"),
                ("changed-source", "shipped UI and acceptance source"),
                ("missing-check", "omitted required passing checks"),
                ("failed-check", "failed or invalid check"),
                ("duplicate-check", "repeats a check"),
                ("missing-screenshot", "omitted required screenshots"),
            )
            for case, message in cases:
                with self.subTest(case=case):
                    report = json.loads(json.dumps(self.watchtower_browser))
                    if case == "not-chromium":
                        report["realChromium"] = False
                    elif case == "wrong-commit":
                        report["candidateCommit"] = "b" * 40
                    elif case == "dirty-source":
                        report["sourceDirty"] = True
                    elif case == "changed-source":
                        report["sourceFiles"]["native/ui/watchtower.js"] = "0" * 64
                    elif case == "missing-check":
                        report["checks"].pop()
                    elif case == "failed-check":
                        report["checks"][0]["passed"] = False
                    elif case == "duplicate-check":
                        report["checks"].append(dict(report["checks"][0]))
                    elif case == "missing-screenshot":
                        report["screenshots"].pop()
                    self.write_json(path, report)
                    with self.assertRaisesRegex(ReleaseError, message):
                        prepare(self.artifacts, self.output, self.commit, self.run)
                    self.assertFalse(self.output.exists())

        def test_missing_or_failed_chapter_rendering_blocks_release(self):
            for key in ("chapterColors", "chapterProgress"):
                for value in (None, "0"):
                    with self.subTest(key=key, value=value):
                        report = json.loads(public_json(self.native))
                        if value is None:
                            report["proof"].pop(key)
                        else:
                            report["proof"][key] = value
                        with self.assertRaisesRegex(ReleaseError, "chapter colors and zero-progress"):
                            validate_native_report(report)

        def test_missing_or_failed_proof_never_creates_output(self):
            (self.browser / "watchtower-pages.png").unlink()
            with self.assertRaisesRegex(ReleaseError, "Required artifact is missing"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())
            (self.browser / "watchtower-pages.png").write_bytes(self.png())
            self.native["exportDownloadVerified"] = False
            self.write_json(self.windows / "native-smoke/native-smoke.json", self.native)
            with self.assertRaisesRegex(ReleaseError, "exported-backup"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_wrong_commit_stale_source_and_missing_rust_probe_fail(self):
            self.write_portable("b" * 40)
            with self.assertRaisesRegex(ReleaseError, "commit"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.write_portable()
            self.result["productionFileHashes"]["bridge.py"] = "0" * 64
            self.write_json(self.mo2 / "result.json", self.result)
            with self.assertRaisesRegex(ReleaseError, "source differs"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.receipt["checks"] = [c for c in self.receipt["checks"] if c["name"] != "rust_cli_live_probe"]
            self.write_json(self.mo2 / "native-receipt.json", self.receipt)
            self.write_json(self.mo2 / "result.json", self.result)
            with self.assertRaisesRegex(ReleaseError, "rust_cli_live_probe"):
                prepare(self.artifacts, self.output, self.commit, self.run)

        def test_required_deep_native_audit_identity_and_evidence(self):
            import copy
            original = copy.deepcopy(self.audit)
            for changes, error in (({"depth": "header"}, "deep analysis"),
                                   ({"profileId": "another-profile"}, "native profile"),
                                   ({"fixtureMods": {"Smoke Enabled": {"itemId": "enabled", "pluginCount": 0}}}, "both exact"),
                                   ({"syntheticPlugin": {}}, "fresh SHA256"),
                                   ({"helperSha256": "0" * 64}, "supplied compiled CLI")):
                with self.subTest(changes=changes):
                    self.audit.clear()
                    self.audit.update(original)
                    self.audit.update(changes)
                    self.write_json(self.mo2 / "native-receipt.json", self.receipt)
                    self.write_json(self.mo2 / "result.json", self.result)
                    with self.assertRaisesRegex(ReleaseError, error):
                        prepare(self.artifacts, self.output, self.commit, self.run)
                    self.assertFalse(self.output.exists())
            self.receipt["checks"] = [c for c in self.receipt["checks"] if c["name"] != "rust_plugin_audit"]
            self.write_json(self.mo2 / "native-receipt.json", self.receipt)
            self.write_json(self.mo2 / "result.json", self.result)
            with self.assertRaisesRegex(ReleaseError, "rust_plugin_audit"):
                prepare(self.artifacts, self.output, self.commit, self.run)

        def test_intelligence_and_adapter_sources_are_bound_to_native_proof(self):
            for name in ("version.py", "gameplay.py", "scanner.py", "version_evidence.py", "native_nexus.py", "qt_bridge.py", "intelligence.py", "plugin_intelligence.py", "mod_health.py",
                         "source_adapters.py", "source_registry.py", "update_plan.py",
                         "quick_update.py", "install_brief.py"):
                with self.subTest(name=name):
                    original = self.result["productionFileHashes"].pop(name)
                    self.write_json(self.mo2 / "result.json", self.result)
                    with self.assertRaisesRegex(ReleaseError, "production hash for " + re.escape(name)):
                        prepare(self.artifacts, self.output, self.commit, self.run)
                    self.result["productionFileHashes"][name] = "0" * 64
                    self.write_json(self.mo2 / "result.json", self.result)
                    with self.assertRaisesRegex(ReleaseError, "source differs.*" + re.escape(name)):
                        prepare(self.artifacts, self.output, self.commit, self.run)
                    self.result["productionFileHashes"][name] = original
                    self.assertFalse(self.output.exists())

        def test_portable_helper_is_present_and_matches_same_job_cli(self):
            self.write_portable(include_helper=False)
            with self.assertRaisesRegex(ReleaseError, "missing.*bin/modhunter-cli"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.write_portable(helper_bytes=b"different synthetic helper build")
            with self.assertRaisesRegex(ReleaseError, "helper differs from the portable"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())
            # Native MO2 compiles in a different job. Only its own copied
            # helper must equal that job's CLI, not this portable job's bytes.
            other_hash = hashlib.sha256(b"separate native MO2 job fixture").hexdigest()
            self.audit["helperSha256"] = other_hash
            self.result["rustCli"]["sha256"] = other_hash
            self.write_json(self.mo2 / "native-receipt.json", self.receipt)
            self.write_json(self.mo2 / "result.json", self.result)
            self.write_portable()
            prepare(self.artifacts, self.output, self.commit, self.run)

        def test_qt_helper_is_bound_to_actual_native_fixture_and_same_job_binary(self):
            self.write_portable(include_qt_helper=False)
            with self.assertRaisesRegex(ReleaseError, "missing.*modhunter-qt-bridge"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.write_portable(qt_bytes=b"different synthetic Qt helper build")
            with self.assertRaisesRegex(ReleaseError, "Qt helper differs from the same Windows"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.write_portable()
            self.qt["rawFilesCompleted"] = False
            self.write_json(self.mo2 / "native-receipt.json", self.receipt)
            self.write_json(self.mo2 / "result.json", self.result)
            with self.assertRaisesRegex(ReleaseError, "raw JSON binding fixture"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.qt["rawFilesCompleted"] = True
            self.qt["helperSha256"] = "0" * 64
            self.write_json(self.mo2 / "native-receipt.json", self.receipt)
            self.write_json(self.mo2 / "result.json", self.result)
            with self.assertRaisesRegex(ReleaseError, "Loaded MO2 Qt helper differs"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertFalse(self.output.exists())

        def test_credential_fields_and_old_output_are_rejected(self):
            self.native["token"] = "never-publish"
            self.write_json(self.windows / "native-smoke/native-smoke.json", self.native)
            with self.assertRaisesRegex(ReleaseError, "Credential field"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.output.mkdir()
            (self.output / "old.zip").write_bytes(b"old")
            with self.assertRaisesRegex(ReleaseError, "absent or empty"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            self.assertEqual((self.output / "old.zip").read_bytes(), b"old")

        def test_duplicate_binaries_and_invalid_png_are_rejected(self):
            shutil.copyfile(self.zip_path, self.windows / "other.zip")
            with self.assertRaisesRegex(ReleaseError, "exactly one"):
                prepare(self.artifacts, self.output, self.commit, self.run)
            (self.windows / "other.zip").unlink()
            (self.browser / "review.png").write_bytes(b"not a screenshot")
            with self.assertRaisesRegex(ReleaseError, "Not a PNG"):
                prepare(self.artifacts, self.output, self.commit, self.run)

    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReleaseTests))
    if not outcome.wasSuccessful():
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts_dir", nargs="?", type=Path)
    parser.add_argument("output_dir", nargs="?", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--run-url")
    parser.add_argument("--print-version", action="store_true", help="Validate source version manifests and print the single release version")
    parser.add_argument("--benchmark-files", action="store_true", help="Print the explicit benchmark evidence allowlist shared with packaging")
    parser.add_argument("--self-test", action="store_true", help="Exercise validation using temporary synthetic artifacts; does not publish")
    args = parser.parse_args()
    if args.print_version:
        print(source_version())
        return
    if args.benchmark_files:
        print("\n".join(BENCHMARK_FILES))
        return
    if args.self_test:
        self_test()
        return
    if args.artifacts_dir is None or args.output_dir is None or not args.commit or not args.run_url:
        parser.error("ARTIFACTS OUTPUT --commit SHA --run-url URL are required")
    try:
        assets = prepare(args.artifacts_dir, args.output_dir, args.commit, args.run_url)
    except (ReleaseError, OSError, ValueError) as exc:
        parser.exit(1, f"Release preparation failed: {exc}\n")
    print(json.dumps({"ok": True, "version": VERSION, "commit": args.commit.lower(), "assets": [path.name for path in assets]}, indent=2))


if __name__ == "__main__":
    main()
