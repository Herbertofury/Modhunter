//! Native, read-only Windows discovery sources. Registry commands and shortcuts
//! are parsed as evidence only; neither is executed or resolved through Shell UI.

use super::{AvailableRoot, Issue, KnownSeeds, RunningProcess, SeedPath, ShortcutSeed, linked};
use std::{
    collections::HashSet,
    ffi::{OsStr, OsString, c_void},
    fs,
    mem::size_of,
    os::windows::ffi::{OsStrExt, OsStringExt},
    path::{Path, PathBuf},
    ptr,
};
use windows_sys::{
    Win32::{
        Foundation::{
            CloseHandle, ERROR_FILE_NOT_FOUND, ERROR_MORE_DATA, ERROR_NO_MORE_FILES,
            ERROR_NO_MORE_ITEMS, ERROR_PATH_NOT_FOUND, ERROR_SUCCESS, GetLastError, HANDLE,
            INVALID_HANDLE_VALUE, LocalFree,
        },
        Storage::FileSystem::{
            GetDriveTypeW, GetFileVersionInfoSizeW, GetFileVersionInfoW, GetLogicalDriveStringsW,
            VerQueryValueW,
        },
        System::{
            Com::{
                CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx,
                CoTaskMemFree, CoUninitialize,
            },
            Diagnostics::ToolHelp::{
                CreateToolhelp32Snapshot, PROCESSENTRY32W, Process32FirstW, Process32NextW,
                TH32CS_SNAPPROCESS,
            },
            Environment::ExpandEnvironmentStringsW,
            Registry::{
                HKEY, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_32KEY,
                KEY_WOW64_64KEY, REG_EXPAND_SZ, REG_SZ, RegCloseKey, RegEnumKeyExW, RegOpenKeyExW,
                RegQueryValueExW,
            },
            Threading::{
                OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, QueryFullProcessImageNameW,
            },
        },
        UI::Shell::{
            CommandLineToArgvW, FOLDERID_CommonStartMenu, FOLDERID_Desktop, FOLDERID_LocalAppData,
            FOLDERID_ProgramFiles, FOLDERID_ProgramFilesX86, FOLDERID_PublicDesktop,
            FOLDERID_StartMenu, SHGetKnownFolderPath,
        },
    },
    core::GUID,
};

const METADATA_LIMIT: usize = 1024 * 1024;
const CLSID_SHELL_LINK: GUID = GUID::from_u128(0x00021401_0000_0000_c000_000000000046);
const IID_SHELL_LINK_W: GUID = GUID::from_u128(0x000214f9_0000_0000_c000_000000000046);
const IID_PERSIST_FILE: GUID = GUID::from_u128(0x0000010b_0000_0000_c000_000000000046);
const IID_PROPERTY_STORE: GUID = GUID::from_u128(0x886d8eeb_8cf2_4446_8d02_cdba1dbdcf99);

fn wide(value: impl AsRef<OsStr>) -> Vec<u16> {
    value.as_ref().encode_wide().chain(Some(0)).collect()
}

fn string(value: &[u16]) -> String {
    let end = value
        .iter()
        .position(|&unit| unit == 0)
        .unwrap_or(value.len());
    String::from_utf16_lossy(&value[..end])
}

struct Handle(HANDLE);

impl Drop for Handle {
    fn drop(&mut self) {
        if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
            // This wrapper owns one handle returned by a successful native call.
            unsafe { CloseHandle(self.0) };
        }
    }
}

struct RegistryKey(HKEY);

impl Drop for RegistryKey {
    fn drop(&mut self) {
        // Predefined root keys are never wrapped; only opened handles are owned.
        unsafe { RegCloseKey(self.0) };
    }
}

pub fn running_processes() -> Result<Vec<RunningProcess>, String> {
    // Toolhelp yields executable names even when permissions prevent querying a
    // full image path. Keep such MO2 processes explicit so preflight can refuse.
    let snapshot = Handle(unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) });
    if snapshot.0 == INVALID_HANDLE_VALUE {
        return Err(format!(
            "Windows process enumeration failed ({}).",
            unsafe { GetLastError() }
        ));
    }
    // PROCESSENTRY32W requires its size and otherwise zeroed storage.
    let mut entry: PROCESSENTRY32W = unsafe { std::mem::zeroed() };
    entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
    let mut present = unsafe { Process32FirstW(snapshot.0, &mut entry) };
    if present == 0 {
        let error = unsafe { GetLastError() };
        return if error == ERROR_NO_MORE_FILES {
            Ok(Vec::new())
        } else {
            Err(format!("Windows process enumeration failed ({error})."))
        };
    }
    let mut processes = Vec::new();
    while present != 0 {
        let name = string(&entry.szExeFile);
        if name.eq_ignore_ascii_case("ModOrganizer.exe") {
            let process = Handle(unsafe {
                OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, entry.th32ProcessID)
            });
            let executable = if process.0.is_null() {
                None
            } else {
                // Windows process image paths fit the native maximum path length.
                let mut buffer = vec![0_u16; 32768];
                let mut length = buffer.len() as u32;
                if unsafe {
                    QueryFullProcessImageNameW(process.0, 0, buffer.as_mut_ptr(), &mut length)
                } != 0
                    && (length as usize) < buffer.len()
                {
                    Some(PathBuf::from(OsString::from_wide(
                        &buffer[..length as usize],
                    )))
                } else {
                    None
                }
            };
            processes.push(RunningProcess {
                pid: entry.th32ProcessID,
                name,
                executable,
            });
        }
        present = unsafe { Process32NextW(snapshot.0, &mut entry) };
    }
    let error = unsafe { GetLastError() };
    if error != ERROR_NO_MORE_FILES {
        return Err(format!(
            "Windows process enumeration was incomplete ({error}); running MO2 could not be excluded."
        ));
    }
    processes.sort_by_key(|process| process.pid);
    Ok(processes)
}

pub fn available_roots() -> Vec<AvailableRoot> {
    let needed = unsafe { GetLogicalDriveStringsW(0, ptr::null_mut()) } as usize;
    if needed == 0 || needed > 65536 {
        return Vec::new();
    }
    let mut buffer = vec![0_u16; needed + 1];
    let written =
        unsafe { GetLogicalDriveStringsW(buffer.len() as u32, buffer.as_mut_ptr()) } as usize;
    if written == 0 || written >= buffer.len() {
        return Vec::new();
    }
    let mut roots = Vec::new();
    for value in buffer[..written]
        .split(|unit| *unit == 0)
        .filter(|value| !value.is_empty())
    {
        let path = PathBuf::from(OsString::from_wide(value));
        let (kind, description) = match unsafe { GetDriveTypeW(wide(&path).as_ptr()) } {
            2 => ("removable", "Removable drive"),
            3 => ("fixed", "Local drive"),
            4 => ("network", "Network drive"),
            5 => ("optical", "Optical drive"),
            6 => ("ram", "RAM drive"),
            _ => ("unknown", "Drive"),
        };
        roots.push(AvailableRoot {
            label: format!("{} · {description}", path.display()),
            path,
            kind: kind.into(),
        });
    }
    roots.sort_by(|a, b| a.path.cmp(&b.path));
    roots
}

fn known_folder(id: &GUID) -> Result<PathBuf, String> {
    let mut result = ptr::null_mut();
    // KF_FLAG_DONT_VERIFY avoids accessing redirected/network folders just to
    // obtain the configured path. Traversal is separately scoped below.
    let status = unsafe { SHGetKnownFolderPath(id, 0x00004000, ptr::null_mut(), &mut result) };
    if status < 0 || result.is_null() {
        return Err(format!("Windows known-folder lookup failed ({status:#x})."));
    }
    let value = unsafe { allocated_wide(result, 32768) };
    unsafe { CoTaskMemFree(result.cast()) };
    value.map(|value| PathBuf::from(OsString::from_wide(&value)))
}

/// The caller owns a Windows-allocated, terminated UTF-16 string. This helper
/// does not accept arbitrary file pointers; each caller frees its allocation.
unsafe fn allocated_wide(pointer: *const u16, limit: usize) -> Result<Vec<u16>, String> {
    if pointer.is_null() {
        return Ok(Vec::new());
    }
    let mut length = 0;
    while length < limit && unsafe { *pointer.add(length) } != 0 {
        length += 1;
    }
    if length == limit {
        return Err("Windows metadata string exceeded its read bound.".into());
    }
    Ok(unsafe { std::slice::from_raw_parts(pointer, length) }.to_vec())
}

fn expand(value: &str) -> Result<String, String> {
    let source = wide(value);
    let needed = unsafe { ExpandEnvironmentStringsW(source.as_ptr(), ptr::null_mut(), 0) } as usize;
    if needed == 0 || needed > 32768 {
        return Err("Windows path variables could not be expanded within the path bound.".into());
    }
    let mut buffer = vec![0_u16; needed];
    let actual = unsafe {
        ExpandEnvironmentStringsW(source.as_ptr(), buffer.as_mut_ptr(), buffer.len() as u32)
    } as usize;
    if actual == 0 || actual > buffer.len() {
        return Err("Windows path variables changed while they were read.".into());
    }
    let result = string(&buffer);
    if result.contains('%') || result.chars().any(|c| matches!(c, '\0' | '\r' | '\n')) {
        return Err("Unresolved or invalid Windows path metadata.".into());
    }
    Ok(result)
}

pub(super) fn is_network_path(path: &Path) -> bool {
    let value = path.to_string_lossy().replace('/', "\\");
    if value.starts_with("\\\\") && !value.starts_with("\\\\?\\") {
        return true;
    }
    if value.starts_with("\\\\?\\UNC\\") {
        return true;
    }
    let value = value.strip_prefix("\\\\?\\").unwrap_or(&value);
    if value.as_bytes().get(1) == Some(&b':') {
        let root = format!("{}\\", &value[..2]);
        return unsafe { GetDriveTypeW(wide(root).as_ptr()) } == 4;
    }
    false
}

fn add_application(seeds: &mut KnownSeeds, candidate: PathBuf, source: &str) {
    if !candidate.is_absolute() {
        seeds.issues.push(Issue::new(
            Some(candidate),
            "relativeApplicationHint",
            "Windows supplied a relative application path; its location was not guessed.",
        ));
    } else if is_network_path(&candidate) {
        seeds.issues.push(Issue::new(
            Some(candidate),
            "networkDeferred",
            "A network application hint was retained as an issue. Select that network folder or drive explicitly to scan it.",
        ));
    } else {
        seeds.applications.push(SeedPath::new(candidate, source));
    }
}

fn open_registry(
    root: HKEY,
    path: &str,
    view: u32,
    issues: &mut Vec<Issue>,
) -> Option<RegistryKey> {
    let mut key = ptr::null_mut();
    let status = unsafe { RegOpenKeyExW(root, wide(path).as_ptr(), 0, KEY_READ | view, &mut key) };
    if status == ERROR_SUCCESS {
        Some(RegistryKey(key))
    } else {
        if status != ERROR_FILE_NOT_FOUND && status != ERROR_PATH_NOT_FOUND {
            issues.push(Issue::new(
                None,
                "registryUnavailable",
                format!("Registry discovery could not read {path} ({status})."),
            ));
        }
        None
    }
}

fn registry_string(key: &RegistryKey, name: &str, issues: &mut Vec<Issue>) -> Option<String> {
    let name = wide(name);
    let mut kind = 0;
    let mut length = 0;
    let status = unsafe {
        RegQueryValueExW(
            key.0,
            name.as_ptr(),
            ptr::null(),
            &mut kind,
            ptr::null_mut(),
            &mut length,
        )
    };
    if status == ERROR_FILE_NOT_FOUND {
        return None;
    }
    if status != ERROR_SUCCESS || !matches!(kind, REG_SZ | REG_EXPAND_SZ) {
        if status != ERROR_SUCCESS {
            issues.push(Issue::new(
                None,
                "registryValue",
                format!("Registry value was unreadable ({status})."),
            ));
        }
        return None;
    }
    if length as usize > METADATA_LIMIT || length % 2 != 0 {
        issues.push(Issue::new(
            None,
            "registryValue",
            "Registry string is oversized or malformed.",
        ));
        return None;
    }
    // Use u16-aligned storage and revalidate type/size after the second call.
    let mut buffer = vec![0_u16; length as usize / 2 + 1];
    let capacity = buffer.len() * 2;
    length = capacity as u32;
    let status = unsafe {
        RegQueryValueExW(
            key.0,
            name.as_ptr(),
            ptr::null(),
            &mut kind,
            buffer.as_mut_ptr().cast(),
            &mut length,
        )
    };
    if status != ERROR_SUCCESS
        || length as usize > capacity
        || length % 2 != 0
        || !matches!(kind, REG_SZ | REG_EXPAND_SZ)
    {
        issues.push(Issue::new(
            None,
            "registryValue",
            "Registry string changed or could not be read completely.",
        ));
        return None;
    }
    let end = buffer
        .iter()
        .position(|&c| c == 0)
        .unwrap_or(length as usize / 2);
    let value = match String::from_utf16(&buffer[..end]) {
        Ok(value) => value,
        Err(_) => {
            issues.push(Issue::new(
                None,
                "registryValue",
                "Registry string contains invalid UTF-16.",
            ));
            return None;
        }
    };
    if kind == REG_EXPAND_SZ {
        expand(&value)
            .map_err(|error| {
                issues.push(Issue::new(None, "registryValue", error));
            })
            .ok()
    } else {
        Some(value)
    }
}

fn registry_children(key: &RegistryKey, issues: &mut Vec<Issue>) -> Vec<String> {
    let mut children = Vec::new();
    let mut index = 0;
    loop {
        // Registry subkey names are limited by Windows; grow on MORE_DATA
        // instead of limiting the number of entries.
        let mut capacity = 256;
        loop {
            let mut buffer = vec![0_u16; capacity];
            let mut length = buffer.len() as u32;
            let status = unsafe {
                RegEnumKeyExW(
                    key.0,
                    index,
                    buffer.as_mut_ptr(),
                    &mut length,
                    ptr::null(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                )
            };
            if status == ERROR_NO_MORE_ITEMS {
                return children;
            }
            if status == ERROR_MORE_DATA && capacity < 32768 {
                capacity *= 2;
                continue;
            }
            if status != ERROR_SUCCESS || length as usize >= buffer.len() {
                issues.push(Issue::new(None, "registryEnumeration", format!("Registry discovery stopped at unreadable entry {index} ({status}); other sources remain available.")));
                return children;
            }
            children.push(String::from_utf16_lossy(&buffer[..length as usize]));
            break;
        }
        index += 1;
    }
}

fn command_arguments(command: &str) -> Result<Vec<String>, String> {
    let source = wide(command);
    let mut count = 0;
    let values = unsafe { CommandLineToArgvW(source.as_ptr(), &mut count) };
    if values.is_null() || count < 0 {
        return Err("Windows command-line metadata could not be parsed.".into());
    }
    let result = (|| {
        let mut result = Vec::new();
        for pointer in unsafe { std::slice::from_raw_parts(values, count as usize) } {
            let value = unsafe { allocated_wide(*pointer, 32768) }?;
            result.push(
                String::from_utf16(&value).map_err(|_| "Invalid Unicode command-line metadata.")?,
            );
        }
        Ok(result)
    })();
    unsafe { LocalFree(values.cast()) };
    result
}

fn shortcut_instance(arguments: &str) -> Result<Option<String>, String> {
    let tokens = command_arguments(&format!("ModOrganizer.exe {arguments}"))?;
    // MO2 gives its moshortcut:// form priority over -i.
    for token in &tokens[1..] {
        if let Some(value) = token.strip_prefix("moshortcut://") {
            let (instance, _) = value
                .split_once(':')
                .ok_or("Malformed MO2 shortcut identity.")?;
            return Ok(Some(instance.to_owned()));
        }
    }
    let mut tokens = tokens[1..].iter();
    while let Some(token) = tokens.next() {
        if token == "--instance" || token == "-i" {
            return Ok(tokens.next().cloned());
        }
        if let Some(value) = token.strip_prefix("--instance=") {
            return Ok(Some(value.to_owned()));
        }
    }
    Ok(None)
}

fn registry_candidates(seeds: &mut KnownSeeds) {
    const UNINSTALL: &str = r"Software\Microsoft\Windows\CurrentVersion\Uninstall";
    for root in [HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE] {
        for view in [KEY_WOW64_64KEY, KEY_WOW64_32KEY] {
            if let Some(key) = open_registry(root, UNINSTALL, view, &mut seeds.issues) {
                for child in registry_children(&key, &mut seeds.issues) {
                    let Some(entry) = open_registry(
                        root,
                        &format!(r"{UNINSTALL}\{child}"),
                        view,
                        &mut seeds.issues,
                    ) else {
                        continue;
                    };
                    let display = registry_string(&entry, "DisplayName", &mut seeds.issues)
                        .unwrap_or_default()
                        .to_lowercase();
                    if !display.contains("mod organizer") && !display.contains("modorganizer") {
                        continue;
                    }
                    if let Some(location) =
                        registry_string(&entry, "InstallLocation", &mut seeds.issues)
                            .filter(|s| !s.trim().is_empty())
                    {
                        add_application(
                            seeds,
                            PathBuf::from(location.trim().trim_matches('"')),
                            "registry",
                        );
                    }
                    if let Some(icon) = registry_string(&entry, "DisplayIcon", &mut seeds.issues) {
                        let icon = icon
                            .rsplit_once(',')
                            .filter(|(_, index)| index.trim().parse::<i32>().is_ok())
                            .map_or(icon.as_str(), |(path, _)| path);
                        let path = PathBuf::from(icon.trim().trim_matches('"'));
                        if let Some(directory) = path.parent() {
                            add_application(seeds, directory.into(), "registry");
                        }
                    }
                }
            }
            if let Some(key) = open_registry(
                root,
                r"Software\Microsoft\Windows\CurrentVersion\App Paths\ModOrganizer.exe",
                view,
                &mut seeds.issues,
            ) && let Some(path) = registry_string(&key, "", &mut seeds.issues)
            {
                add_application(
                    seeds,
                    PathBuf::from(path.trim().trim_matches('"')),
                    "appPaths",
                );
            }
        }
    }
    for scheme in ["nxm", "moshortcut"] {
        let path = format!(r"Software\Classes\{scheme}\shell\open\command");
        for root in [HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE] {
            if let Some(key) = open_registry(root, &path, KEY_WOW64_64KEY, &mut seeds.issues)
                && let Some(command) = registry_string(&key, "", &mut seeds.issues)
            {
                match command_arguments(&command).and_then(|tokens| {
                    tokens
                        .into_iter()
                        .next()
                        .ok_or("Empty protocol handler.".into())
                }) {
                    Ok(executable) => {
                        let executable = PathBuf::from(executable);
                        if let Some(directory) = executable.parent() {
                            add_application(seeds, directory.into(), "protocolHandler");
                        }
                    }
                    Err(error) => seeds
                        .issues
                        .push(Issue::new(None, "protocolHandler", error)),
                }
            }
        }
    }
    // These values are defaults only; they never establish app ownership.
    for path in [
        r"Software\Mod Organizer Team\Mod Organizer",
        r"Software\Tannin\Mod Organizer",
    ] {
        if let Some(key) =
            open_registry(HKEY_CURRENT_USER, path, KEY_WOW64_64KEY, &mut seeds.issues)
            && let Some(name) = registry_string(&key, "CurrentInstance", &mut seeds.issues)
            && !name.is_empty()
        {
            if name.contains(['/', '\\', ':']) || matches!(name.as_str(), "." | "..") {
                seeds.issues.push(Issue::new(
                    None,
                    "invalidInstanceHint",
                    "The registry's default MO2 instance is not a single directory name.",
                ));
                continue;
            }
            for root in &seeds.global_instance_roots {
                seeds.instance_configs.push(SeedPath::new(
                    root.join(&name).join("ModOrganizer.ini"),
                    "defaultGlobalInstance",
                ));
            }
        }
    }
}

// IPropertyStore avoids IShellLink::GetPath's MAX_PATH result and
// GetArguments' documented silent truncation. We request allocated strings,
// never Resolve(), launch the target, or rewrite the .lnk file.
#[repr(C)]
struct UnknownVTable {
    query_interface: unsafe extern "system" fn(*mut c_void, *const GUID, *mut *mut c_void) -> i32,
    add_ref: unsafe extern "system" fn(*mut c_void) -> u32,
    release: unsafe extern "system" fn(*mut c_void) -> u32,
}

#[repr(C)]
struct PersistFileVTable {
    unknown: UnknownVTable,
    get_class_id: usize,
    is_dirty: usize,
    load: unsafe extern "system" fn(*mut c_void, *const u16, u32) -> i32,
}

#[repr(C)]
struct PropertyKey {
    format: GUID,
    id: u32,
}

#[repr(C)]
struct PropertyStoreVTable {
    unknown: UnknownVTable,
    get_count: usize,
    get_at: usize,
    get_value: unsafe extern "system" fn(*mut c_void, *const PropertyKey, *mut c_void) -> i32,
}

// PROPVARIANT occupies 24 bytes on 64-bit Windows (16 on 32-bit). Oversized
// zeroed storage retains its native alignment without exposing union fields.
#[repr(C, align(8))]
struct PropertyVariant([u8; 24]);

#[link(name = "propsys")]
unsafe extern "system" {
    fn PSGetPropertyKeyFromName(name: *const u16, key: *mut PropertyKey) -> i32;
    fn PropVariantToStringAlloc(value: *const c_void, result: *mut *mut u16) -> i32;
}

#[link(name = "ole32")]
unsafe extern "system" {
    fn PropVariantClear(value: *mut c_void) -> i32;
}

struct ComObject(*mut c_void);

impl ComObject {
    fn query(&self, iid: &GUID) -> Result<Self, String> {
        let vtable = unsafe { &**(self.0 as *const *const UnknownVTable) };
        let mut object = ptr::null_mut();
        let result = unsafe { (vtable.query_interface)(self.0, iid, &mut object) };
        if result < 0 || object.is_null() {
            Err(format!("Shortcut interface is unavailable ({result:#x})."))
        } else {
            Ok(Self(object))
        }
    }
}

impl Drop for ComObject {
    fn drop(&mut self) {
        let vtable = unsafe { &**(self.0 as *const *const UnknownVTable) };
        unsafe { (vtable.release)(self.0) };
    }
}

struct ComApartment(bool);

impl ComApartment {
    fn initialize() -> Result<Self, String> {
        let result = unsafe { CoInitializeEx(ptr::null(), COINIT_APARTMENTTHREADED as u32) };
        if result >= 0 {
            Ok(Self(true))
        } else if result as u32 == 0x80010106 {
            // A caller already initialized this thread in another COM mode.
            Ok(Self(false))
        } else {
            Err(format!(
                "Shortcut reader could not initialize COM ({result:#x})."
            ))
        }
    }
}

impl Drop for ComApartment {
    fn drop(&mut self) {
        if self.0 {
            unsafe { CoUninitialize() };
        }
    }
}

fn property_string(store: &ComObject, name: &str) -> Result<String, String> {
    let mut key: PropertyKey = unsafe { std::mem::zeroed() };
    let result = unsafe { PSGetPropertyKeyFromName(wide(name).as_ptr(), &mut key) };
    if result < 0 {
        return Err(format!(
            "Windows shortcut property is unavailable ({result:#x})."
        ));
    }
    let vtable = unsafe { &**(store.0 as *const *const PropertyStoreVTable) };
    let mut value = PropertyVariant([0; 24]);
    let result =
        unsafe { (vtable.get_value)(store.0, &key, (&mut value as *mut PropertyVariant).cast()) };
    if result < 0 {
        unsafe { PropVariantClear((&mut value as *mut PropertyVariant).cast()) };
        return Err(format!(
            "Shortcut property could not be read ({result:#x})."
        ));
    }
    let mut text = ptr::null_mut();
    let result =
        unsafe { PropVariantToStringAlloc((&value as *const PropertyVariant).cast(), &mut text) };
    let output = if result >= 0 {
        unsafe { allocated_wide(text, METADATA_LIMIT / 2) }.and_then(|text| {
            String::from_utf16(&text)
                .map_err(|_| "Shortcut property contains invalid Unicode.".into())
        })
    } else {
        Err(format!("Shortcut property is not text ({result:#x})."))
    };
    if !text.is_null() {
        unsafe { CoTaskMemFree(text.cast()) };
    }
    unsafe { PropVariantClear((&mut value as *mut PropertyVariant).cast()) };
    output
}

fn read_shortcut(path: &Path) -> Result<Option<ShortcutSeed>, String> {
    let metadata = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if linked(&metadata) || !metadata.is_file() || metadata.len() > 2 * METADATA_LIMIT as u64 {
        return Err("Linked, non-regular or oversized shortcut was not read.".into());
    }
    let mut object = ptr::null_mut();
    let result = unsafe {
        CoCreateInstance(
            &CLSID_SHELL_LINK,
            ptr::null_mut(),
            CLSCTX_INPROC_SERVER,
            &IID_SHELL_LINK_W,
            &mut object,
        )
    };
    if result < 0 || object.is_null() {
        return Err(format!(
            "Windows shortcut reader could not be created ({result:#x})."
        ));
    }
    let object = ComObject(object);
    let persist = object.query(&IID_PERSIST_FILE)?;
    let persist_vtable = unsafe { &**(persist.0 as *const *const PersistFileVTable) };
    // STGM_READ = 0. No Resolve call and no save are performed.
    let result = unsafe { (persist_vtable.load)(persist.0, wide(path).as_ptr(), 0) };
    if result < 0 {
        return Err(format!("Shortcut file could not be loaded ({result:#x})."));
    }
    let properties = object.query(&IID_PROPERTY_STORE)?;
    let target = property_string(&properties, "System.Link.TargetParsingPath")?;
    if target.is_empty() {
        return Ok(None);
    }
    let target = PathBuf::from(expand(&target)?);
    if !target.file_name().is_some_and(|name| {
        name.to_string_lossy()
            .eq_ignore_ascii_case("ModOrganizer.exe")
    }) {
        return Ok(None);
    }
    let arguments = property_string(&properties, "System.Link.Arguments")?;
    Ok(Some(ShortcutSeed {
        path: path.into(),
        executable: target,
        instance_name: shortcut_instance(&arguments)?,
    }))
}

fn shortcuts(root: &Path, seeds: &mut KnownSeeds) {
    if is_network_path(root) {
        seeds.issues.push(Issue::new(
            Some(root.into()),
            "networkDeferred",
            "Redirected network shortcuts require an explicitly selected network scan root.",
        ));
        return;
    }
    let mut pending = vec![root.to_path_buf()];
    let mut seen = HashSet::new();
    while let Some(directory) = pending.pop() {
        let metadata = match fs::symlink_metadata(&directory) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => {
                seeds.issues.push(Issue::new(
                    Some(directory),
                    "shortcutDirectory",
                    error.to_string(),
                ));
                continue;
            }
        };
        if linked(&metadata) || !metadata.is_dir() {
            seeds.issues.push(Issue::new(
                Some(directory),
                "linkedPath",
                "Linked shortcut directory was not followed.",
            ));
            continue;
        }
        let key = match fs::canonicalize(&directory) {
            Ok(path) => super::path_key(&path),
            Err(error) => {
                seeds.issues.push(Issue::new(
                    Some(directory),
                    "shortcutDirectory",
                    error.to_string(),
                ));
                continue;
            }
        };
        if !seen.insert(key) {
            continue;
        }
        let entries = match fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(error) => {
                seeds.issues.push(Issue::new(
                    Some(directory),
                    "shortcutDirectory",
                    error.to_string(),
                ));
                continue;
            }
        };
        for entry in entries {
            let entry = match entry {
                Ok(entry) => entry,
                Err(error) => {
                    seeds.issues.push(Issue::new(
                        Some(directory.clone()),
                        "shortcutEntry",
                        error.to_string(),
                    ));
                    continue;
                }
            };
            let kind = match entry.file_type() {
                Ok(kind) => kind,
                Err(error) => {
                    seeds.issues.push(Issue::new(
                        Some(entry.path()),
                        "shortcutEntry",
                        error.to_string(),
                    ));
                    continue;
                }
            };
            if kind.is_dir() {
                pending.push(entry.path());
            } else if entry
                .path()
                .extension()
                .is_some_and(|ext| ext.to_string_lossy().eq_ignore_ascii_case("lnk"))
            {
                match read_shortcut(&entry.path()) {
                    Ok(Some(shortcut)) if is_network_path(&shortcut.executable) => {
                        seeds.issues.push(Issue::new(Some(shortcut.executable), "networkDeferred", "MO2 shortcut targets a network location; select that location explicitly for deep scanning."));
                    }
                    Ok(Some(shortcut)) => seeds.shortcuts.push(shortcut),
                    Ok(None) => {}
                    Err(error) => {
                        seeds
                            .issues
                            .push(Issue::new(Some(entry.path()), "shortcutRead", error))
                    }
                }
            } else if kind.is_symlink() {
                seeds.issues.push(Issue::new(
                    Some(entry.path()),
                    "linkedPath",
                    "Linked shortcut entry was not followed.",
                ));
            }
        }
    }
}

pub fn known_seeds() -> KnownSeeds {
    let mut seeds = KnownSeeds::default();
    match known_folder(&FOLDERID_LocalAppData) {
        Ok(local) if !is_network_path(&local) => {
            let global = local.join("ModOrganizer");
            if global.exists() {
                seeds.global_instance_roots.push(global);
            }
            let programs = local.join("Programs");
            if programs.exists() {
                seeds.search_roots.push(SeedPath::new(programs, "userPrograms"));
            }
        }
        Ok(local) => seeds.issues.push(Issue::new(Some(local), "networkDeferred", "Current-user application data is on a network path. Select the MO2 instance folder explicitly to scan it.")),
        Err(error) => seeds.issues.push(Issue::new(None, "knownFolder", error)),
    }
    for id in [&FOLDERID_ProgramFiles, &FOLDERID_ProgramFilesX86] {
        match known_folder(id) {
            Ok(path) if !is_network_path(&path) && path.exists() => {
                seeds.search_roots.push(SeedPath::new(path, "programFiles"));
            }
            Ok(_) => {}
            Err(error) => seeds.issues.push(Issue::new(None, "knownFolder", error)),
        }
    }
    // Common portable parent folders are inspected only one level deep.
    // The explicit drive/folder scan covers other nesting without a result cap.
    for root in available_roots()
        .into_iter()
        .filter(|root| root.kind == "fixed")
    {
        for name in [
            "Modding",
            "Games",
            "Mod Organizer 2",
            "ModOrganizer2",
            "MO2",
        ] {
            let path = root.path.join(name);
            if path.exists() {
                seeds
                    .search_roots
                    .push(SeedPath::new(path, "commonPortableFolder"));
            }
        }
    }
    match running_processes() {
        Ok(processes) => seeds.processes = processes,
        Err(error) => seeds
            .issues
            .push(Issue::new(None, "processEnumeration", error)),
    }
    registry_candidates(&mut seeds);
    match ComApartment::initialize() {
        Ok(_apartment) => {
            for id in [
                &FOLDERID_Desktop,
                &FOLDERID_PublicDesktop,
                &FOLDERID_StartMenu,
                &FOLDERID_CommonStartMenu,
            ] {
                match known_folder(id) {
                    Ok(path) => shortcuts(&path, &mut seeds),
                    Err(error) => seeds
                        .issues
                        .push(Issue::new(None, "shortcutKnownFolder", error)),
                }
            }
        }
        Err(error) => seeds.issues.push(Issue::new(None, "shortcutReader", error)),
    }
    seeds
}

pub(super) fn file_version(path: &Path) -> Option<String> {
    let path = wide(path);
    let mut ignored = 0;
    let length = unsafe { GetFileVersionInfoSizeW(path.as_ptr(), &mut ignored) } as usize;
    if length == 0 || length > METADATA_LIMIT {
        return None;
    }
    let mut buffer = vec![0_u8; length];
    if unsafe { GetFileVersionInfoW(path.as_ptr(), 0, length as u32, buffer.as_mut_ptr().cast()) }
        == 0
    {
        return None;
    }
    let mut value = ptr::null_mut();
    let mut value_length = 0;
    if unsafe {
        VerQueryValueW(
            buffer.as_ptr().cast(),
            wide("\\").as_ptr(),
            &mut value,
            &mut value_length,
        )
    } == 0
        || value_length < 52
        || value.is_null()
    {
        return None;
    }
    let address = value as usize;
    let start = buffer.as_ptr() as usize;
    if address < start || address.checked_add(52)? > start + buffer.len() {
        return None;
    }
    let info = unsafe { std::slice::from_raw_parts(value.cast::<u8>(), 52) };
    let number = |index: usize| {
        u32::from_le_bytes(
            info[index * 4..index * 4 + 4]
                .try_into()
                .expect("four-byte version field"),
        )
    };
    if number(0) != 0xfeef04bd {
        return None;
    }
    let (high, low) = if number(4) != 0 || number(5) != 0 {
        (number(4), number(5))
    } else {
        (number(2), number(3))
    };
    Some(format!(
        "{}.{}.{}.{}",
        high >> 16,
        high & 65535,
        low >> 16,
        low & 65535
    ))
}

// Shell fixture writers need an ordinary Win32 spelling of their short, owned
// paths. Preserve Unicode/OsString data; never strip device or volume prefixes.
#[cfg(test)]
pub(super) fn fixture_shell_path(path: &Path) -> PathBuf {
    use std::path::{Component, Prefix};

    let mut components = path.components();
    let Some(Component::Prefix(prefix)) = components.next() else {
        return path.into();
    };
    let mut result = match prefix.kind() {
        Prefix::VerbatimDisk(drive) => PathBuf::from(format!("{}:", char::from(drive))),
        Prefix::VerbatimUNC(server, share) => {
            let mut prefix = OsString::from(r"\\");
            prefix.push(server);
            prefix.push(r"\");
            prefix.push(share);
            PathBuf::from(prefix)
        }
        _ => return path.into(),
    };
    result.extend(components);
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shell_fixture_paths_preserve_unicode_and_only_convert_verbatim_disk_or_unc() {
        for (input, expected) in [
            (
                r"C:\MO2 Unicode 特別\ModOrganizer.exe",
                r"C:\MO2 Unicode 特別\ModOrganizer.exe",
            ),
            (
                r"\\?\C:\MO2 Unicode 特別\ModOrganizer.exe",
                r"C:\MO2 Unicode 特別\ModOrganizer.exe",
            ),
            (r"\\?\UNC\server\share\MO2 特別", r"\\server\share\MO2 特別"),
            (
                r"\\?\Volume{fixture}\MO2 特別",
                r"\\?\Volume{fixture}\MO2 特別",
            ),
        ] {
            assert_eq!(fixture_shell_path(Path::new(input)), Path::new(expected));
        }
    }

    // These writer-only vtables stay in the fixture. Production discovery only
    // loads IPersistFile and reads IPropertyStore; it never saves or resolves.
    #[repr(C)]
    struct ShellLinkWriterVTable {
        unknown: UnknownVTable,
        get_path: unsafe extern "system" fn(*mut c_void, *mut u16, i32, *mut c_void, u32) -> i32,
        get_id_list: usize,
        set_id_list: usize,
        get_description: usize,
        set_description: usize,
        get_working_directory: usize,
        set_working_directory: usize,
        get_arguments: unsafe extern "system" fn(*mut c_void, *mut u16, i32) -> i32,
        set_arguments: unsafe extern "system" fn(*mut c_void, *const u16) -> i32,
        get_hotkey: usize,
        set_hotkey: usize,
        get_show_cmd: usize,
        set_show_cmd: usize,
        get_icon_location: usize,
        set_icon_location: usize,
        set_relative_path: usize,
        resolve: usize,
        set_path: unsafe extern "system" fn(*mut c_void, *const u16) -> i32,
    }

    #[repr(C)]
    struct PersistFileWriterVTable {
        reader: PersistFileVTable,
        save: unsafe extern "system" fn(*mut c_void, *const u16, i32) -> i32,
    }

    fn create_unicode_shortcut(path: &Path, executable: &Path, arguments: &str) {
        let mut object = ptr::null_mut();
        // Explicit IShellLinkW avoids WScript.Shell automation's path/encoding
        // boundary. This creates a real .lnk, without ever executing its target.
        let result = unsafe {
            CoCreateInstance(
                &CLSID_SHELL_LINK,
                ptr::null_mut(),
                CLSCTX_INPROC_SERVER,
                &IID_SHELL_LINK_W,
                &mut object,
            )
        };
        assert!(
            result >= 0 && !object.is_null(),
            "CoCreateInstance: {result:#x}"
        );
        let object = ComObject(object);
        let vtable = unsafe { &**(object.0 as *const *const ShellLinkWriterVTable) };
        let result = unsafe { (vtable.set_path)(object.0, wide(executable).as_ptr()) };
        assert!(
            result >= 0,
            "IShellLinkW::SetPath({executable:?}): {result:#x}"
        );
        let result = unsafe { (vtable.set_arguments)(object.0, wide(arguments).as_ptr()) };
        assert!(result >= 0, "IShellLinkW::SetArguments: {result:#x}");
        let persist = object.query(&IID_PERSIST_FILE).unwrap();
        let vtable = unsafe { &**(persist.0 as *const *const PersistFileWriterVTable) };
        let result = unsafe { (vtable.save)(persist.0, wide(path).as_ptr(), 1) };
        assert!(result >= 0, "IPersistFile::Save({path:?}): {result:#x}");
    }

    fn observe_saved_shortcut(path: &Path) -> (String, String, String, String) {
        let mut object = ptr::null_mut();
        let result = unsafe {
            CoCreateInstance(
                &CLSID_SHELL_LINK,
                ptr::null_mut(),
                CLSCTX_INPROC_SERVER,
                &IID_SHELL_LINK_W,
                &mut object,
            )
        };
        assert!(
            result >= 0 && !object.is_null(),
            "CoCreateInstance: {result:#x}"
        );
        let object = ComObject(object);
        let persist = object.query(&IID_PERSIST_FILE).unwrap();
        let persist_vtable = unsafe { &**(persist.0 as *const *const PersistFileVTable) };
        let result = unsafe { (persist_vtable.load)(persist.0, wide(path).as_ptr(), 0) };
        assert!(result >= 0, "IPersistFile::Load({path:?}): {result:#x}");
        let vtable = unsafe { &**(object.0 as *const *const ShellLinkWriterVTable) };
        // This short, owned fixture fits both buffers. These direct getters are
        // diagnostic only, never a production fallback with silent truncation.
        let mut target = [0_u16; 32768];
        let result = unsafe {
            (vtable.get_path)(
                object.0,
                target.as_mut_ptr(),
                target.len() as i32,
                ptr::null_mut(),
                4,
            )
        };
        assert!(
            result >= 0,
            "IShellLinkW::GetPath(SLGP_RAWPATH): {result:#x}"
        );
        let mut arguments = [0_u16; 32768];
        let result = unsafe {
            (vtable.get_arguments)(object.0, arguments.as_mut_ptr(), arguments.len() as i32)
        };
        assert!(result >= 0, "IShellLinkW::GetArguments: {result:#x}");
        let properties = object.query(&IID_PROPERTY_STORE).unwrap();
        (
            string(&target),
            string(&arguments),
            property_string(&properties, "System.Link.TargetParsingPath")
                .unwrap_or_else(|error| format!("ERROR: {error}")),
            property_string(&properties, "System.Link.Arguments")
                .unwrap_or_else(|error| format!("ERROR: {error}")),
        )
    }

    #[test]
    fn instance_arguments_keep_empty_portable_and_unicode_names_distinct() {
        assert_eq!(
            shortcut_instance(r#"--instance "Skyrim 特別版" --profile "My profile""#).unwrap(),
            Some("Skyrim 特別版".into())
        );
        assert_eq!(
            shortcut_instance(r#"-i "" -p "Default""#).unwrap(),
            Some(String::new())
        );
        assert_eq!(
            shortcut_instance("--instance=Fallout").unwrap(),
            Some("Fallout".into())
        );
        assert_eq!(shortcut_instance("-i").unwrap(), None);
        assert_eq!(shortcut_instance("").unwrap(), None);
        assert_eq!(
            shortcut_instance(r#"-i Wrong "moshortcut://Skyrim 特別版:SKSE""#).unwrap(),
            Some("Skyrim 特別版".into())
        );
        assert_eq!(
            shortcut_instance(r#""moshortcut://:SKSE""#).unwrap(),
            Some(String::new())
        );
    }

    #[test]
    fn actual_shell_link_properties_preserve_target_and_instance_without_execution() {
        let root = tempfile::tempdir().unwrap();
        let directory = fixture_shell_path(&fs::canonicalize(root.path()).unwrap());
        assert_eq!(
            fs::canonicalize(&directory).unwrap(),
            fs::canonicalize(root.path()).unwrap()
        );
        let application = directory.join("MO2 Unicode 特別");
        fs::create_dir_all(&application).unwrap();
        let executable = application.join("ModOrganizer.exe");
        fs::write(&executable, b"non-executable shortcut fixture").unwrap();
        let shortcut = directory.join("Skyrim 特別 launch.lnk");
        let _apartment = ComApartment::initialize().unwrap();
        create_unicode_shortcut(
            &shortcut,
            &executable,
            r#"--instance "Skyrim Unicode 特別版""#,
        );
        let before = fs::read(&shortcut).unwrap();
        let observed = observe_saved_shortcut(&shortcut);
        eprintln!(
            "Saved shortcut (raw path, raw arguments, target property, arguments property): {observed:?}"
        );
        assert_eq!(Path::new(&observed.0), executable, "{observed:?}");
        assert_eq!(
            observed.1, r#"--instance "Skyrim Unicode 特別版""#,
            "{observed:?}"
        );
        let result = read_shortcut(&shortcut)
            .unwrap()
            .unwrap_or_else(|| panic!("Production reader omitted saved shortcut: {observed:?}"));
        assert_eq!(result.executable, executable);
        assert_eq!(
            result.instance_name.as_deref(),
            Some("Skyrim Unicode 特別版")
        );
        assert_eq!(fs::read(&shortcut).unwrap(), before);
        assert_eq!(
            fs::read(executable).unwrap(),
            b"non-executable shortcut fixture"
        );
    }
}
