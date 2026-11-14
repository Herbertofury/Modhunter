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
import re
import shutil
import stat
import struct
import tempfile
import tomllib
import zlib
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[2]
PORTABLE_NAME = "Modhunter-Rust-Windows.zip"
JSON_LIMIT = 4 * 1024 * 1024
PNG_LIMIT = 32 * 1024 * 1024
PORTABLE_EXPANDED_LIMIT = 512 * 1024 * 1024
PRODUCTION_FILES = ("plugin.py", "version.py", "download_ids.py", "bridge.py", "gameplay.py", "model.py", "focus.py", "scanner.py", "version_evidence.py", "native_nexus.py", "qt_bridge.py", "intelligence.py",
                    "plugin_intelligence.py", "mod_health.py", "source_adapters.py",
                    "source_registry.py", "update_plan.py", "quick_update.py",
                    "install_brief.py", "content_recovery.py", "duplicate_report.py", "web/index.html")
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
LIVE_DESKTOP_CHECKS = {"compact_workspace", "automatic_source_check", "automatic_discovery", "live_catalog_not_demo", "toolbar_refresh", "toolbar_plugin_audit", "connection_details", "content_native_ipc_denied", "manual_rediscovery", "disconnected_welcome", "setup_reconnect"}
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
NATIVE_MANAGER_SOURCES = ('native/ui/watchtower.html', 'native/ui/watchtower.css', 'native/ui/watchtower.js', 'native/ui/watchtower-review.js', 'native/ui/watchtower-maintenance.js', 'native/ui/watchtower-native.css', 'native/ui/preferences.js', 'native/ui/preferences.css', 'tests/test_watchtower_native_ui.py', 'tests/test_watchtower_browser.py', 'tests/fixtures/watchtower_native_fixture.js')
NATIVE_MANAGER_CHECKS = {'native_substates_update_without_watchstore_revision', 'support_export_uses_the_exact_preview_identity', 'fomod_selection_is_explicitly_advisory', 'retention_requires_exact_preview_and_explicit_confirmation', 'duplicate_report_preserves_whole_mod_and_file_ownership', 'lost_acknowledgement_refreshes_submission_state', 'retained_reviews_remain_visible_without_replay', 'new_content_requires_an_explicit_new_target', 'global_preferences_preserve_unsaved_edits_and_switch_language', 'cleanup_requires_a_matching_preview_and_confirmation', 'unknown_native_outcome_remains_visible_and_cannot_be_discarded', 'application_reset_requires_exact_phrase_and_preview', 'review_preserves_complete_selection_and_exact_install_ids'}
NATIVE_MANAGER_SCREENSHOTS = {"watchtower-file-review.png", "watchtower-support-preview.png", "watchtower-preferences-es.png", "watchtower-duplicates.png"}
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


def png_bytes(path: Path) -> bytes:
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


def portable_contents(path: Path, commit: str, run_url: str, native_report: dict, watchtower_report: dict, mo2_result: dict, qt_helper_hash: str, qt_forward_report: dict, live_report: dict, archive_report:dict) -> dict[str, bytes]:
    public: dict[str, bytes] = {}
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            require(len(entries) <= 10000 and sum(e.file_size for e in entries) <= PORTABLE_EXPANDED_LIMIT, "Portable archive exceeds the release validation bounds")
            index = {}
            windows_names = set()
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                parts = PurePosixPath(name).parts
                require(not name.startswith("/") and ".." not in parts and not any(":" in part for part in parts), "Unsafe path in portable archive")
                require(name not in index and name.casefold() not in windows_names, f"Duplicate portable archive member: {name}")
                require(not stat.S_ISLNK(entry.external_attr >> 16), "Linked member in portable archive")
                index[name] = entry
                windows_names.add(name.casefold())
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


def release_notes(commit: str, run_url: str, repository_url: str, installer_name: str) -> str:
    return f"""# Modhunter {VERSION} — Rust desktop

The Windows desktop, MO2 profile import/cache engine, connection checks, tab lifecycle and native dialogs now run in Rust. Microsoft Edge WebView2 renders the existing dashboard and all game modes. The shipped desktop does not require Node or Electron.

MO2's Python/Qt plugin remains the in-process adapter for MO2's authenticated Nexus connection, native downloads, interactive FOMOD installer, focus and verified rewards. Dashboard/game presentation remains HTML/CSS/JavaScript.

## 0.11.0: reviewed downloads, actual file recovery and app upkeep

Watchtower now carries a chosen external archive from its exact source identity into a complete file review and MO2's native installer. Update files stay bound to the chosen profile, game/runtime and installed mod. Separate new content requires a new mod name. A different file, changed source, profile round-trip or lost acknowledgement cannot silently reuse an earlier installation decision.

- **Exact source choices:** Patreon post attachments, LoversLab file choices and supported direct Drive/MEGA files keep durable file identities and explicit update/new-content choices. Creator preferences, per-site mutes and compatibility declarations persist. Unsupported shared folders remain manual review. Visible account access is reported separately from page entitlement.
- **Complete archive review:** pinned 7-Zip helpers inspect ZIP, 7z and RAR archives with path, link, collision, encryption and size guards. Every accepted file remains in the manifest. Ordinary game-data archives produce a verified ZIP containing the complete selected set; FOMOD and uncertain layouts keep the original archive and require native installer choices. Installed-only paths never authorize deletion.
- **Actual content recovery:** the Rust engine snapshots mod files before installation, verifies the resulting files and retains interrupted operations. Restore, batch restore and retention cleanup recheck exact content identities. Later edits stop restoration. Recovery data lives beside the MO2 mods directory in an instance-specific `Modhunter-Recovery` vault and is excluded from app reset.
- **Duplicate review:** complete per-file and whole-mod reports include saved-profile ownership and hard-link facts. Quarantine requires an identical retained counterpart and a duplicate disabled in every saved and live profile. Undo and permanent quarantine removal use a fresh exact preview.
- **Desktop preferences:** system/light/dark themes, common interface labels in English, Spanish, Italian and Traditional Chinese, native quick hide, Windows capture protection, private external browser choices, bounded cache cleanup, app release checks, offline licenses and preview-before-export support reports. Detailed technical messages can remain in English.
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
- **{installer_name}** installs the Rust desktop for the current Windows user and bootstraps WebView2 when needed. Use the portable ZIP's included plugin installer for live MO2 integration.
- **{VERIFICATION_NAME}** contains the selected native receipts, screenshots, build identity and available benchmark evidence. **SHA256SUMS.txt** verifies the release files.

## Controls and existing progress

Choose **Import MO2** and select a profile's modlist.txt. For split directories, select **Instance INI**, or set an explicit **Mods path**. **Auto paths** clears overrides. **Refresh** reloads metadata, reusing unchanged cached files; import remains read-only. **Plugin audit** performs deeper inspection. **CANCEL** stops either operation cooperatively; **CANCELING…** remains visible until its workers settle, leaving the last complete result available.

For live updates, extract the complete portable ZIP with its checksum manifest and BUILD_INFO.json. Close MO2, run **Install_MO2_Plugin.bat**, select the folder containing ModOrganizer.exe, then restart MO2, let its profile finish loading and launch Modhunter. The bridge starts automatically; **Find MO2** reconnects on demand. Choose the intended session if several are running; opening the legacy Quest Board tool is optional. A loopback address can also be entered in **Connect**. Nexus sign-in stays in MO2. **Update now**, background queues, retries and sequential installation retain MO2's native installation choices.

JSON, CSV and PowerShell exports are saved to Windows Downloads with unique filenames. Export a progress backup in the older Electron edition and import it in the Rust edition after selecting the same portable profile. Existing Electron browser storage is not automatically migrated; the live MO2 Chronicle remains with the plugin.

## What this build actually passed

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
    public = portable_contents(portable, commit, run_url, native_report, watchtower_report, mo2_result, file_hash(qt_helper), qt_forward_report, live_report,archive_report)
    public["windows/archive-review-proof.json"]=public_json(archive_report)
    public["windows/desktop-live.json"] = public_json(live_report)
    public["windows/desktop-live.png"] = png_bytes(windows / "live-mo2-smoke/desktop-live.png")
    public["windows/qt-forward.json"] = public_json(qt_forward_report)
    public.update({"windows/native-smoke.json": public_json(native_report), "windows/watchtower-smoke.json": public_json(watchtower_report), "mo2/result.json": public_json(mo2_result), "mo2/native-receipt.json": public_json(mo2_receipt), "mo2/mo2-fixture.png": png_bytes(mo2 / "mo2-fixture.png")})
    watchtower_browser = read_json(browser / "watchtower-browser.json")
    validate_watchtower_browser(watchtower_browser, commit)
    public["browser/watchtower-browser.json"] = public_json(watchtower_browser)
    manager_ui = read_json(browser / "native-manager-ui.json")
    validate_native_manager_ui(manager_ui, commit)
    public["browser/native-manager-ui.json"] = public_json(manager_ui)
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
        public["browser/" + relative.as_posix()] = png_bytes(screenshot)
    # All proof is validated before creating final output. Build in an adjacent
    # temporary directory and publish it only after copy/checksum verification.
    expected_binary_hashes = {portable.name: file_hash(portable), installer.name: file_hash(installer)}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".modhunter-release-", dir=output_dir.parent) as temporary:
        staged = Path(temporary)
        for source in (portable, installer):
            target = staged / source.name
            shutil.copyfile(source, target)
            require(file_hash(target) == expected_binary_hashes[source.name], f"Release copy changed the tested binary: {source.name}")
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
            for path in (self.windows / "release/bundle/nsis", self.windows / "native-smoke", self.windows / "watchtower-smoke", self.windows / "qt-bridge/Release", self.windows / "qt-forward", self.windows / "live-mo2-smoke", self.mo2, self.browser):
                path.mkdir(parents=True)
            self.native = {"ok": True, "nativeWebView2": True, "shellIpc": True, "exportDownloadVerified": True, "gameViewNativeIpcDenied": "1"}
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
            self.write_json(self.browser / "native-manager-ui.json",self.manager_ui)
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
            (self.mo2 / "mo2-fixture.png").write_bytes(image)
            (self.browser / "review.png").write_bytes(image)
            for name in WATCHTOWER_SCREENSHOTS | NATIVE_MANAGER_SCREENSHOTS:
                (self.browser / name).write_bytes(image)
            (self.mo2 / "fixture-settings.ini").write_text("not public")
            (self.mo2 / "mo2.log").write_text("not public")
            (self.browser / "session.json").write_text('{"token":"must never be selected"}')

        @staticmethod
        def write_json(path, value):
            path.write_bytes(b"\xef\xbb\xbf" + public_json(value))

        @staticmethod
        def png():
            def chunk(kind, content):
                return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
            return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0\0\0\0\0")) + chunk(b"IEND", b"")

        def write_portable(self, commit=None, *, build_version=None, include_helper=True, helper_bytes=None, include_qt_helper=True, qt_bytes=None):
            with ZipFile(self.zip_path, "w") as archive:
                archive.writestr("BUILD_INFO.json", b"\xef\xbb\xbf" + public_json({"version": build_version or VERSION, "commit": commit or self.commit, "workflow": self.run}))
                for name in ("Modhunter.exe", "modhunter-cli.exe", "Install_MO2_Plugin.ps1", "Install_MO2_Plugin.bat", "Check_MO2_Readiness.ps1", "Check_MO2_Readiness.bat", "START-HERE-MO2-253.md", "GameSync-Update-Techniques.md"):
                    archive.writestr(name, self.cli_bytes)
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
                self.assertEqual(set(names), expected)
                self.assertTrue(all(PurePosixPath(name).suffix in (".json", ".png") for name in names))

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
