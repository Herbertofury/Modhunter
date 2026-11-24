//! Private, bounded Windows storage for WebView2 profiles that end with this run.
use super::*;
use std::{
    ffi::{OsStr, OsString, c_void},
    io,
    mem::size_of,
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    },
    ptr::null_mut,
};
use windows_sys::Win32::{
    Foundation::{ERROR_ALREADY_EXISTS, LocalFree},
    Security::{
        ACL,
        Authorization::{
            ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
            GetSecurityInfo, SDDL_REVISION_1, SE_FILE_OBJECT,
        },
        DACL_SECURITY_INFORMATION, EqualSid, GetAce, GetSecurityDescriptorControl,
        GetSecurityDescriptorDacl, GetSecurityDescriptorOwner, GetTokenInformation,
        OWNER_SECURITY_INFORMATION, SE_DACL_PROTECTED, SECURITY_ATTRIBUTES, TOKEN_QUERY,
        TOKEN_USER, TokenUser,
    },
    Storage::FileSystem::{
        CreateDirectoryW, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
        FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES, GetDriveTypeW, READ_CONTROL,
    },
    System::{
        SystemInformation::GetWindowsDirectoryW,
        Threading::{GetCurrentProcess, OpenProcessToken},
        WindowsProgramming::DRIVE_FIXED,
    },
};

struct LocalAllocation(*mut c_void);
impl Drop for LocalAllocation {
    fn drop(&mut self) {
        unsafe {
            LocalFree(self.0);
        }
    }
}
fn wide(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain(Some(0)).collect()
}

fn user_sid() -> io::Result<String> {
    unsafe {
        let mut handle = null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut handle) == 0 {
            return Err(io::Error::last_os_error());
        }
        let token = OwnedHandle::from_raw_handle(handle);
        let mut size = 0;
        GetTokenInformation(token.as_raw_handle(), TokenUser, null_mut(), 0, &mut size);
        if size == 0 {
            return Err(io::Error::last_os_error());
        }
        // TOKEN_USER and the SID are returned in the same pointer-aligned buffer.
        let mut info = vec![0usize; (size as usize).div_ceil(size_of::<usize>())];
        if GetTokenInformation(
            token.as_raw_handle(),
            TokenUser,
            info.as_mut_ptr().cast(),
            size,
            &mut size,
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }
        let mut text = null_mut();
        if ConvertSidToStringSidW((*(info.as_ptr().cast::<TOKEN_USER>())).User.Sid, &mut text) == 0
        {
            return Err(io::Error::last_os_error());
        }
        let _text = LocalAllocation(text.cast());
        // Windows SID strings are ASCII and null terminated by this API.
        let length = (0..256)
            .find(|i| *text.add(*i) == 0)
            .ok_or_else(|| io::Error::other("Invalid Windows account SID"))?;
        Ok(String::from_utf16_lossy(std::slice::from_raw_parts(
            text, length,
        )))
    }
}
fn descriptor(sid: &str) -> io::Result<LocalAllocation> {
    let sddl = format!("O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)");
    let mut descriptor = null_mut();
    let ok = unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            wide(OsStr::new(&sddl)).as_ptr(),
            SDDL_REVISION_1,
            &mut descriptor,
            null_mut(),
        )
    };
    if ok == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(LocalAllocation(descriptor))
    }
}
fn ace_bytes(acl: *mut ACL, index: u32) -> io::Result<Vec<u8>> {
    unsafe {
        let mut ptr = null_mut();
        if GetAce(acl, index, &mut ptr) == 0 {
            return Err(io::Error::last_os_error());
        }
        let size = (*(ptr.cast::<windows_sys::Win32::Security::ACE_HEADER>())).AceSize;
        Ok(std::slice::from_raw_parts(ptr.cast(), usize::from(size)).to_vec())
    }
}
fn verify_private_directory(file: &File, expected: &LocalAllocation) -> io::Result<()> {
    unsafe {
        let mut owner = null_mut();
        let mut dacl = null_mut();
        let mut raw = null_mut();
        let code = GetSecurityInfo(
            file.as_raw_handle(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            null_mut(),
            &mut dacl,
            null_mut(),
            &mut raw,
        );
        if code != 0 {
            return Err(io::Error::from_raw_os_error(code as i32));
        }
        let actual = LocalAllocation(raw);
        let mut expected_owner = null_mut();
        let mut expected_acl = null_mut();
        let (mut present, mut defaulted, mut control, mut revision) = (0, 0, 0, 0);
        if GetSecurityDescriptorOwner(expected.0, &mut expected_owner, &mut defaulted) == 0
            || GetSecurityDescriptorDacl(
                expected.0,
                &mut present,
                &mut expected_acl,
                &mut defaulted,
            ) == 0
            || GetSecurityDescriptorControl(actual.0, &mut control, &mut revision) == 0
        {
            return Err(io::Error::last_os_error());
        }
        let denied = || {
            io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Temporary browser directory has a different owner or access policy",
            )
        };
        if owner.is_null()
            || dacl.is_null()
            || expected_acl.is_null()
            || EqualSid(owner, expected_owner) == 0
            || control & SE_DACL_PROTECTED == 0
            || (*dacl).AceCount != (*expected_acl).AceCount
        {
            return Err(denied());
        }
        for i in 0..u32::from((*dacl).AceCount) {
            if ace_bytes(dacl, i)? != ace_bytes(expected_acl, i)? {
                return Err(denied());
            }
        }
        Ok(())
    }
}

pub(super) struct ProfileRoot {
    pub path: PathBuf,
    handle: Option<File>,
}
impl Drop for ProfileRoot {
    fn drop(&mut self) {
        self.handle.take();
        // Only an empty, exclusively finished scope can be removed.
        let _ = std::fs::remove_dir(&self.path);
    }
}
fn private_root(path: PathBuf, security: &LocalAllocation) -> io::Result<ProfileRoot> {
    let sa = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: security.0,
        bInheritHandle: 0,
    };
    if unsafe { CreateDirectoryW(wide(path.as_os_str()).as_ptr(), &sa) } == 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(ERROR_ALREADY_EXISTS as i32) {
            return Err(error);
        }
    }
    // Hold the actual directory, deny renaming, and validate its owner and access
    // list before enumerating old runs or writing any browser data.
    let file = OpenOptions::new()
        .access_mode(READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .share_mode(3)
        .open(&path)?;
    let meta = file.metadata()?;
    if !meta.is_dir() || meta.file_attributes() & 0x400 != 0 {
        return Err(io::Error::other(
            "Temporary browser directory is not a regular directory",
        ));
    }
    verify_private_directory(&file, security)?;
    Ok(ProfileRoot {
        path,
        handle: Some(file),
    })
}
fn windows_temp() -> io::Result<PathBuf> {
    let mut path = vec![0u16; 32768];
    let len = unsafe { GetWindowsDirectoryW(path.as_mut_ptr(), path.len() as u32) } as usize;
    if len == 0 || len >= path.len() {
        return Err(io::Error::last_os_error());
    }
    Ok(PathBuf::from(OsString::from_wide(&path[..len])).join("Temp"))
}
fn suitable(base: &Path) -> bool {
    // Leave room for the nested Edge profile tree even with legacy MAX_PATH.
    if !base.is_absolute() || base.as_os_str().encode_wide().count() > 64 || !plain_directory(base)
    {
        return false;
    }
    let Some(root) = base.ancestors().last() else {
        return false;
    };
    unsafe { GetDriveTypeW(wide(root.as_os_str()).as_ptr()) == DRIVE_FIXED }
}
pub(super) fn open(data: &Path) -> Result<ProfileRoot> {
    let sid = user_sid().map_err(err)?;
    let security = descriptor(&sid).map_err(err)?;
    let mut hash = Sha256::new();
    hash.update(sid.as_bytes());
    hash.update([0]);
    hash.update(data.as_os_str().as_encoded_bytes());
    let id = format!("mhv-{:x}", hash.finalize());
    let mut bases = vec![std::env::temp_dir()];
    if let Ok(system) = windows_temp()
        && !bases.contains(&system)
    {
        bases.push(system);
    }
    let mut last = None;
    for base in bases.into_iter().filter(|p| suitable(p)) {
        match private_root(base.join(&id[..24]), &security) {
            Ok(root) => return Ok(root),
            Err(e) => last = Some(e.to_string()),
        }
    }
    Err(format!(
        "WebView2 needs a writable short local temporary directory for this account. {}",
        last.unwrap_or_default()
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn private_profile_root_reopens_only_its_own_access_policy() {
        let test = tempfile::tempdir().unwrap();
        let security = descriptor(&user_sid().unwrap()).unwrap();
        let path = test.path().join("owned");
        let first = private_root(path.clone(), &security).unwrap();
        let second = private_root(path.clone(), &security).unwrap();
        assert!(std::fs::rename(&path, test.path().join("renamed")).is_err());
        drop(first);
        assert!(path.is_dir());
        drop(second);
        assert!(!path.exists());

        let foreign = test.path().join("preexisting");
        std::fs::create_dir(&foreign).unwrap();
        assert!(private_root(foreign.clone(), &security).is_err());
        assert!(foreign.is_dir());
    }

    #[test]
    fn a_long_data_path_keeps_its_external_browser_root_short() {
        let temp = tempfile::tempdir().unwrap();
        let deep = temp.path().join("long-account-directory".repeat(9));
        let first = open(&deep).unwrap();
        let other = open(&deep.join("second-store")).unwrap();
        assert!(!first.path.starts_with(temp.path()));
        assert!(first.path.as_os_str().encode_wide().count() < 92);
        assert_ne!(first.path, other.path);
        assert!(!suitable(&deep));
    }
}
