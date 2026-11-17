"""Read back public release bytes and verify the already-tested package identity.

Usage: python native/scripts/verify_public_release.py TAG COMMIT RUN_URL OUTPUT_DIR
This read-only publisher check downloads every public asset; it does not run or
install any downloaded code. Native acceptance belongs to the identified CI run.
"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import urllib.request
import zipfile

import prepare_release as release_gates


def json_member(archive, name):
    entry = archive.getinfo(name)
    release_gates.require(0 < entry.file_size <= release_gates.JSON_LIMIT,
                          'Public verification JSON exceeds its bounds: ' + name)
    return release_gates.json_object(archive.read(entry), name)


def validate_public_evidence(verification, commit, executable_sha, installer_sha, bundle):
    """Reapply publication gates to the actual public receipts and PNG bytes."""
    installer = json_member(verification, 'windows/installer-smoke.json')
    release_gates.validate_installer_report(installer, commit, installer_sha, executable_sha, bundle)
    performance = json_member(verification, 'windows/native-library-performance.json')
    release_gates.validate_native_library(performance, commit, executable_sha)
    screenshots, browser_checks = {}, {}
    # Extract only fixed acceptance filenames, never paths supplied by the ZIP
    # or a receipt. The same validator checks complete named scenarios, source
    # hashes, PNG structure/CRC, dimensions and each captured byte digest.
    with tempfile.TemporaryDirectory(prefix='modhunter-public-browser-proof-') as temporary:
        evidence_dir = Path(temporary)
        for kind, spec in release_gates.WORKSPACE_BROWSER_SPECS.items():
            report = json_member(verification, 'browser/' + kind + '.json')
            for name in spec['screenshots']:
                entry = verification.getinfo('browser/' + name)
                release_gates.require(0 < entry.file_size <= release_gates.PNG_LIMIT,
                                      'Public screenshot exceeds its bounds: ' + name)
                (evidence_dir / name).write_bytes(verification.read(entry))
            validated = release_gates.validate_workspace_browser(report, commit, kind, evidence_dir)
            for name, data in validated.items():
                filename = name.removeprefix('browser/')
                width, height = spec['screenshots'][filename]
                screenshots[name] = {'sha256': hashlib.sha256(data).hexdigest(),
                                     'width': width, 'height': height}
            browser_checks[kind] = report['testsRun']
    return {'installer': installer, 'performance': performance,
            'browserChecks': browser_checks, 'screenshots': screenshots}


def verify_public_release(tag, expected_commit, expected_workflow, destination):
    release_gates.require(tag == 'v' + release_gates.VERSION,
                          'Public readback requires the matching release validator source')
    release_gates.validate_run(expected_commit, expected_workflow)
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    api = 'https://api.github.com/repos/Herbertofury/Modhunter/releases/tags/' + tag
    with urllib.request.urlopen(api, timeout=30) as response:
        release = json.load(response)
    assert release['tag_name'] == tag and not release['draft'] and not release['prerelease']
    assert release['target_commitish'] == expected_commit
    version = tag.removeprefix('v')
    assert {asset['name'] for asset in release['assets']} == {
        'Modhunter-Rust-Windows.zip', 'Modhunter_' + version + '_x64-setup.exe',
        'Modhunter-' + version + '-MO2-Plugin.zip', 'Modhunter-' + version + '-Standalone.zip',
        'Modhunter-' + version + '-Verification.zip', 'SHA256SUMS.txt'}
    assert len(release['assets']) == 6

    def fetch(asset):
        path = root / asset['name']
        assert path.name == asset['name'] and 0 < asset['size'] < 256 * 1024 * 1024
        digest = hashlib.sha256()
        with urllib.request.urlopen(asset['browser_download_url'], timeout=60) as response, path.open('wb') as handle:
            status = response.status
            assert status == 200
            while block := response.read(1024 * 1024):
                handle.write(block)
                digest.update(block)
        assert path.stat().st_size == asset['size']
        sha = digest.hexdigest()
        assert asset.get('digest') == 'sha256:' + sha
        return dict(name=path.name, bytes=path.stat().st_size, sha256=sha, status=status,
                    url=asset['browser_download_url'])

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        downloaded = list(pool.map(fetch, release['assets']))
    assert len(downloaded) == 6
    expected = {}
    for line in (root / 'SHA256SUMS.txt').read_text('utf-8-sig').splitlines():
        sha, name = line.split('  ', 1)
        assert name not in expected
        expected[name] = sha
    assert set(expected) == {asset['name'] for asset in downloaded if asset['name'] != 'SHA256SUMS.txt'}
    for asset in downloaded:
        if asset['name'] in expected:
            assert asset['sha256'] == expected[asset['name']]
    installer_sha = next(asset['sha256'] for asset in downloaded
                         if asset['name'] == 'Modhunter_' + version + '_x64-setup.exe')

    def validate_zip(name):
        with zipfile.ZipFile(root / name) as archive:
            assert archive.testzip() is None
            names = archive.namelist()
            assert len(names) == len(set(names))
            index = {entry.filename.replace('\\', '/'): entry for entry in archive.infolist() if not entry.is_dir()}
            assert len(index) == len([entry for entry in archive.infolist() if not entry.is_dir()])
            assert len({name.casefold() for name in index}) == len(index)
            build = json.loads(archive.read('BUILD_INFO.json').decode('utf-8-sig'))
            assert build['version'] == tag.removeprefix('v') and build['commit'] == expected_commit
            assert build['workflow'] == expected_workflow
            verified = set()
            for line in archive.read('SHA256SUMS.txt').decode('utf-8-sig').splitlines():
                sha, path = line.split('  ', 1)
                path = path.replace('\\', '/')
                assert path not in verified
                assert hashlib.sha256(archive.read(index[path])).hexdigest() == sha
                verified.add(path)
            assert verified == set(index) - {'SHA256SUMS.txt'}
            return {'name': name, 'build': build, 'filesVerified': len(verified)}

    archives = [validate_zip(name) for name in (
        'Modhunter-Rust-Windows.zip',
        'Modhunter-' + tag.removeprefix('v') + '-MO2-Plugin.zip',
        'Modhunter-' + tag.removeprefix('v') + '-Standalone.zip')]
    with zipfile.ZipFile(root / 'Modhunter-Rust-Windows.zip') as portable:
        executable_sha = hashlib.sha256(portable.read('Modhunter.exe')).hexdigest()
        smoke = json.loads(portable.read('Evidence/native-smoke.json').decode('utf-8-sig'))
        assert smoke['ok'] and smoke['candidateCommit'] == expected_commit
        assert smoke['executableSha256'] == executable_sha
        assert smoke['proof']['chapterColors'] == smoke['proof']['chapterProgress'] == '1'
        # Validate the full in-app resource independently of the outer ZIP manifest.
        resource_prefix = 'mo2-installer/'
        resource_names = {entry.filename.replace('\\', '/'): entry for entry in portable.infolist()
                          if not entry.is_dir() and entry.filename.replace('\\', '/').startswith(resource_prefix)}
        resource_manifest = portable.read(resource_prefix + 'SHA256SUMS.txt')
        nested_hashes = {}
        for line in resource_manifest.decode('utf-8-sig').splitlines():
            sha, relative = line.split('  ', 1)
            assert relative not in nested_hashes
            assert relative and not relative.startswith('/') and '..' not in relative.replace('\\', '/').split('/')
            assert hashlib.sha256(portable.read(resource_prefix + relative)).hexdigest() == sha
            assert hashlib.sha256(portable.read(relative)).hexdigest() == sha
            nested_hashes[relative] = sha
        assert set(resource_names) == {resource_prefix + name for name in nested_hashes} | {resource_prefix + 'SHA256SUMS.txt'}
        resource_manifest_sha = hashlib.sha256(resource_manifest).hexdigest()
        embedded_instances = json.loads(portable.read('Evidence/instances-manager.json').decode('utf-8-sig'))
        assert embedded_instances['ok'] and embedded_instances['candidateCommit'] == expected_commit
        assert embedded_instances['executableSha256'] == executable_sha
        plugin_version = portable.read('MO2/mo2_update_arsenal/version.py').decode()
        assert tag.removeprefix('v') in plugin_version
        html_sha = hashlib.sha256(portable.read('MO2/mo2_update_arsenal/web/index.html')).hexdigest()
        with zipfile.ZipFile(root / ('Modhunter-' + tag.removeprefix('v') + '-Standalone.zip')) as standalone:
            assert hashlib.sha256(standalone.read('Modhunter.html')).hexdigest() == html_sha
        with zipfile.ZipFile(root / ('Modhunter-' + version + '-MO2-Plugin.zip')) as component:
            portable_index = {entry.filename.replace('\\', '/'): entry for entry in portable.infolist() if not entry.is_dir()}
            component_index = {entry.filename.replace('\\', '/'): entry for entry in component.infolist() if not entry.is_dir()}
            plugin_files = {name for name in component_index if name.startswith('MO2/')}
            assert plugin_files == {name for name in portable_index if name.startswith('MO2/')}
            for name in plugin_files:
                assert component.read(component_index[name]) == portable.read(portable_index[name])
    with zipfile.ZipFile(root / ('Modhunter-' + tag.removeprefix('v') + '-Verification.zip')) as verification:
        assert verification.testzip() is None
        evidence_names = verification.namelist()
        assert len(evidence_names) == len(set(evidence_names))
        verification_build = json.loads(verification.read('BUILD_INFO.json').decode('utf-8-sig'))
        assert verification_build == archives[0]['build']
        for name in ('windows/native-smoke.json', 'windows/installed-native-smoke.json'):
            verified_smoke = json.loads(verification.read(name).decode('utf-8-sig'))
            assert verified_smoke['ok'] and verified_smoke['candidateCommit'] == expected_commit
            assert verified_smoke['executableSha256'] == executable_sha
            assert verified_smoke['proof']['chapterColors'] == verified_smoke['proof']['chapterProgress'] == '1'
        assert json.loads(verification.read('windows/instances-manager.json').decode('utf-8-sig')) == embedded_instances
        bundle = json.loads(verification.read('windows/plugin-installer-bundle.json').decode('utf-8-sig'))
        assert bundle['manifestSha256'] == resource_manifest_sha and bundle['commit'] == expected_commit and bundle['version'] == version
        assert bundle['resourceHashes'] == dict(nested_hashes, **{'SHA256SUMS.txt': resource_manifest_sha})
        payload_prefix = 'MO2/mo2_update_arsenal/'
        assert bundle['pluginPayloadHashes'] == {
            name.removeprefix(payload_prefix): sha for name, sha in nested_hashes.items()
            if name.startswith(payload_prefix)}
        evidence = validate_public_evidence(verification, expected_commit, executable_sha, installer_sha, bundle)
        for capture in embedded_instances['captures']:
            name = capture['file']
            assert Path(name).name == name
            (root / name).write_bytes(verification.read('windows/' + name))
        for name in ('windows/installed-desktop.png', 'windows/desktop-live.png'):
            (root / name.rsplit('/', 1)[-1]).write_bytes(verification.read(name))

    result = {'schema': 1, 'release': release['html_url'], 'version': tag, 'commit': expected_commit,
              'publishedAt': release['published_at'], 'assets': downloaded, 'archives': archives,
              'desktopSha256': executable_sha, 'dashboardSha256': html_sha,
              'installerSha256': installer_sha, 'installerReceiptVerified': True,
              'offlineChapterColorsVerified': True, 'offlineZeroProgressVerified': True,
              'workflow': expected_workflow, 'verificationBuild': verification_build,
              'mo2ComponentMatchesPortable': True, 'installerResourceMatchesPortable': True,
              'installerResourceManifestSha256': resource_manifest_sha,
              'instancesManagerVerified': True, 'libraryAndInstancesBrowserReceiptsVerified': True,
              'nativeLibraryPerformanceVerified': True,
              'nativeLibraryPerformance': evidence['performance'],
              'browserScenarioChecks': evidence['browserChecks'],
              'browserScreenshotCount': len(evidence['screenshots']),
              'browserScreenshotEvidence': evidence['screenshots'],
              'verificationFiles': evidence_names,
              'scope': 'Public download readback and identity validation; no personal Windows installation or provider account was exercised.'}
    (root / 'public-readback.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    if len(sys.argv) != 5:
        raise SystemExit("Usage: verify_public_release.py TAG COMMIT RUN_URL OUTPUT_DIR")
    print(json.dumps(verify_public_release(*sys.argv[1:]), indent=2))


if __name__ == "__main__":
    main()
