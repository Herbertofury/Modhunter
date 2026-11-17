//! Read-only local asset primitives. No discovery, extraction, decoding, or IPC.
//! Call on a worker with an already authorized bounded member view; byte ranges
//! are relative to that view, never paths to expose to remote pages.
use std::io::{self, Read, Seek, SeekFrom};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FuzLayout {
    pub version: u32,
    pub lip: std::ops::Range<u64>,
    pub audio: std::ops::Range<u64>,
}

/// Probe only 24 bytes regardless of payload size. Length is the member's logical
/// length, not its enclosing archive size. Seekable compressed members require
/// a separately bounded adapter; this function does not decompress anything.
/// The RIFF envelope is checked, not individual chunks or codec validity.
/// No cursor restoration guarantee. Cancellation cannot interrupt blocked OS I/O.
pub fn probe_fuz<R: Read + Seek>(
    source: &mut R,
    length: u64,
    cancelled: impl Fn() -> bool,
) -> io::Result<FuzLayout> {
    let invalid = || io::Error::new(io::ErrorKind::InvalidData, "Invalid FUZ/xWMA envelope");
    let check = || {
        if cancelled() {
            Err(io::Error::new(
                io::ErrorKind::Interrupted,
                "Asset probe canceled",
            ))
        } else {
            Ok(())
        }
    };
    check()?;
    if length < 24 {
        return Err(invalid());
    }
    source.seek(SeekFrom::Start(0))?;
    let mut header = [0; 12];
    source.read_exact(&mut header)?;
    check()?;
    if &header[..4] != b"FUZE" {
        return Err(invalid());
    }
    let version = u32::from_le_bytes(header[4..8].try_into().unwrap());
    // Unknown container versions are not silently interpreted as version 1.
    if version != 1 {
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "Unsupported FUZ version",
        ));
    }
    let start = 12 + u64::from(u32::from_le_bytes(header[8..12].try_into().unwrap()));
    if start > length - 12 {
        return Err(invalid());
    }
    source.seek(SeekFrom::Start(start))?;
    source.read_exact(&mut header)?;
    check()?;
    if &header[..4] != b"RIFF" || &header[8..] != b"XWMA" {
        return Err(invalid());
    }
    let riff_length = u64::from(u32::from_le_bytes(header[4..8].try_into().unwrap())) + 8;
    if riff_length != length - start {
        return Err(invalid());
    }
    Ok(FuzLayout {
        version,
        lip: 12..start,
        audio: start..length,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    fn fixture(lip: u32) -> Vec<u8> {
        let mut data = b"FUZE".to_vec();
        data.extend(1u32.to_le_bytes());
        data.extend(lip.to_le_bytes());
        data.resize(12 + lip as usize, 0);
        data.extend(b"RIFF");
        data.extend(4u32.to_le_bytes());
        data.extend(b"XWMA");
        data
    }
    #[test]
    fn ranges_and_original_unchanged() {
        for lip in [0, 11, 65536] {
            let original = fixture(lip);
            let mut input = Cursor::new(original.clone());
            let out = probe_fuz(&mut input, original.len() as u64, || false).unwrap();
            assert_eq!(out.lip, 12..12 + u64::from(lip));
            assert_eq!(out.audio, 12 + u64::from(lip)..original.len() as u64);
            assert_eq!(input.into_inner(), original);
        }
    }
    #[test]
    fn rejects_bad_envelopes() {
        for offset in [0, 4, 8, 12, 16, 20] {
            let mut data = fixture(0);
            data[offset] = 0xff;
            assert!(probe_fuz(&mut Cursor::new(data), 24, || false).is_err());
        }
        for len in 0..24 {
            assert!(probe_fuz(&mut Cursor::new(fixture(0)), len, || false).is_err());
        }
        assert!(probe_fuz(&mut Cursor::new(fixture(0)), 25, || false).is_err());
        assert!(probe_fuz(&mut Cursor::new(vec![0; 2]), 24, || false).is_err());
    }
    #[test]
    fn cancellation_between_reads() {
        use std::cell::Cell;
        for stop in 0..3 {
            let calls = Cell::new(0);
            let err = probe_fuz(&mut Cursor::new(fixture(0)), 24, || {
                let n = calls.get();
                calls.set(n + 1);
                n >= stop
            })
            .unwrap_err();
            assert_eq!(err.kind(), io::ErrorKind::Interrupted);
        }
    }
    #[test]
    fn reads_only_twenty_four_bytes() {
        struct Count {
            inner: Cursor<Vec<u8>>,
            count: usize,
        }
        impl Read for Count {
            fn read(&mut self, b: &mut [u8]) -> io::Result<usize> {
                let n = self.inner.read(b)?;
                self.count += n;
                Ok(n)
            }
        }
        impl Seek for Count {
            fn seek(&mut self, p: SeekFrom) -> io::Result<u64> {
                self.inner.seek(p)
            }
        }
        let data = fixture(1_000_000);
        let len = data.len() as u64;
        let mut input = Count {
            inner: Cursor::new(data),
            count: 0,
        };
        probe_fuz(&mut input, len, || false).unwrap();
        assert_eq!(input.count, 24);
    }
}
