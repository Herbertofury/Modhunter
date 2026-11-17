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

/// Seekable view of only the embedded audio. Owns its source handle, never writes
/// or extracts, never reads the LIP region. Designed for a bounded decoder worker.
/// Cancellation is checked before each I/O but cannot interrupt blocked OS I/O.
/// Caller must keep source/version provenance and reject changed profile jobs.
pub struct FuzAudio<R, C> {
    source: R,
    cancelled: C,
    start: u64,
    len: u64,
    position: u64,
    stopped: bool,
}

impl<R: Read + Seek, C: Fn() -> bool> FuzAudio<R, C> {
    /// Validates the container and refuses oversized clips before payload access.
    /// `length` must be the authoritative length of this bounded member handle.
    /// The 64 MiB hard ceiling is an explicit unsupported-size result, not truncation.
    pub fn open(
        mut source: R,
        length: u64,
        max_audio_bytes: u64,
        cancelled: C,
    ) -> io::Result<Self> {
        let layout = probe_fuz(&mut source, length, &cancelled)?;
        let len = layout.audio.end - layout.audio.start;
        if len > max_audio_bytes.min(64 * 1024 * 1024) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Audio exceeds configured byte budget",
            ));
        }
        Ok(Self {
            source,
            cancelled,
            start: layout.audio.start,
            len,
            position: 0,
            stopped: false,
        })
    }
    pub fn len(&self) -> u64 {
        self.len
    }
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
    fn check(&mut self) -> io::Result<()> {
        self.stopped |= (self.cancelled)();
        if self.stopped {
            // Interrupted is retryable by read_exact/read_to_end; cancellation is terminal.
            Err(io::Error::new(
                io::ErrorKind::Other,
                "Audio stream canceled",
            ))
        } else {
            Ok(())
        }
    }
}
impl<R: Read + Seek, C: Fn() -> bool> Read for FuzAudio<R, C> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        self.check()?;
        let n = buffer
            .len()
            .min(64 * 1024)
            .min((self.len - self.position) as usize);
        if n == 0 {
            return Ok(0);
        }
        self.source
            .seek(SeekFrom::Start(self.start + self.position))?;
        self.check()?;
        let read = self.source.read(&mut buffer[..n])?;
        if read == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "Audio source changed or truncated",
            ));
        }
        self.position += read as u64;
        Ok(read)
    }
}
impl<R: Read + Seek, C: Fn() -> bool> Seek for FuzAudio<R, C> {
    fn seek(&mut self, from: SeekFrom) -> io::Result<u64> {
        self.check()?;
        let next = match from {
            SeekFrom::Start(n) => i128::from(n),
            SeekFrom::Current(n) => i128::from(self.position) + i128::from(n),
            SeekFrom::End(n) => i128::from(self.len) + i128::from(n),
        };
        if next < 0 || next > i128::from(self.len) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Seek outside embedded audio",
            ));
        }
        self.position = next as u64;
        Ok(self.position)
    }
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
    fn audio_view_is_exact_and_seek_bounded() {
        let original = fixture(50);
        let mut view = FuzAudio::open(
            Cursor::new(original.clone()),
            original.len() as u64,
            1024,
            || false,
        )
        .unwrap();
        let mut out = Vec::new();
        view.read_to_end(&mut out).unwrap();
        assert_eq!(out, original[62..]);
        assert_eq!(view.seek(SeekFrom::End(-4)).unwrap(), 8);
        let mut magic = [0; 4];
        view.read_exact(&mut magic).unwrap();
        assert_eq!(&magic, b"XWMA");
        for pos in [
            SeekFrom::Start(u64::MAX),
            SeekFrom::End(1),
            SeekFrom::Current(i64::MIN),
        ] {
            assert_eq!(
                view.seek(pos).unwrap_err().kind(),
                io::ErrorKind::InvalidInput
            );
        }
        assert_eq!(view.stream_position().unwrap(), 12);
        assert_eq!(view.source.into_inner(), original);
    }
    #[test]
    fn audio_budget_and_cancel_are_explicit() {
        let source = fixture(0);
        assert!(FuzAudio::open(Cursor::new(source.clone()), 24, 11, || false).is_err());
        let stop = std::cell::Cell::new(false);
        let mut view = FuzAudio::open(Cursor::new(source), 24, 12, || stop.get()).unwrap();
        stop.set(true);
        assert_eq!(
            view.read(&mut [0; 4]).unwrap_err().kind(),
            io::ErrorKind::Other
        );
        stop.set(false); // Once observed, cancellation stays terminal.
        assert_eq!(
            view.seek(SeekFrom::Start(0)).unwrap_err().kind(),
            io::ErrorKind::Other
        );
    }
    #[test]
    fn standard_read_helpers_terminate_on_cancellation() {
        use std::cell::Cell;
        for exact in [false, true] {
            let stop = Cell::new(false);
            let calls = Cell::new(0);
            let mut view = FuzAudio::open(Cursor::new(fixture(0)), 24, 12, || {
                if stop.get() {
                    calls.set(calls.get() + 1);
                    assert!(
                        calls.get() < 5,
                        "standard Read helper retried terminal cancellation"
                    );
                }
                stop.get()
            })
            .unwrap();
            stop.set(true);
            let error = if exact {
                view.read_exact(&mut [0; 4]).unwrap_err()
            } else {
                view.read_to_end(&mut Vec::new()).unwrap_err()
            };
            assert_eq!(error.kind(), io::ErrorKind::Other);
            assert_eq!(calls.get(), 1);
        }
    }
    #[test]
    fn audio_truncation_is_not_clean_eof() {
        let mut view = FuzAudio::open(Cursor::new(fixture(0)), 24, 12, || false).unwrap();
        view.source.get_mut().truncate(12);
        assert_eq!(
            view.read(&mut [0; 4]).unwrap_err().kind(),
            io::ErrorKind::UnexpectedEof
        );
    }
    #[test]
    fn real_read_only_file_stream_preserves_every_byte() {
        use std::io::Write;
        let mut file = tempfile::NamedTempFile::new().unwrap();
        let mut data = fixture(17);
        // Synthetic payload; validates byte transport, not playable audio.
        data[33..37].copy_from_slice(&100_004u32.to_le_bytes());
        data.extend((0..100_000).map(|n| (n % 251) as u8));
        file.write_all(&data).unwrap();
        file.flush().unwrap();
        let handle = std::fs::File::open(file.path()).unwrap();
        let mut view = FuzAudio::open(handle, data.len() as u64, 200_000, || false).unwrap();
        let mut audio = Vec::new();
        view.read_to_end(&mut audio).unwrap();
        assert_eq!(audio, data[29..]);
        assert_eq!(std::fs::read(file.path()).unwrap(), data);
    }
    #[test]
    fn audio_reads_are_chunk_bounded() {
        let mut data = fixture(0);
        data[16..20].copy_from_slice(&200_004u32.to_le_bytes());
        data.resize(200_024, 0);
        let mut view = FuzAudio::open(Cursor::new(data), 200_024, 300_000, || false).unwrap();
        let mut out = vec![0; 300_000];
        assert_eq!(view.read(&mut out).unwrap(), 65536);
        assert_eq!(view.stream_position().unwrap(), 65536);
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
