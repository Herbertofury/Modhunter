//! Synthetic warm-file metadata microbenchmark, not playback/MO2 performance.
use modhunter_core::mod_assets::probe_fuz;
use std::io::{Seek, SeekFrom, Write};
use std::time::Instant;
fn main() -> std::io::Result<()> {
    let mut f = tempfile::tempfile()?;
    f.write_all(b"FUZE")?;
    f.write_all(&1u32.to_le_bytes())?;
    f.write_all(&1_000_000u32.to_le_bytes())?;
    f.seek(SeekFrom::Start(1_000_012))?;
    f.write_all(b"RIFF")?;
    f.write_all(&1_000_004u32.to_le_bytes())?;
    f.write_all(b"XWMA")?;
    f.set_len(2_000_024)?;
    let mut samples = Vec::new();
    for _ in 0..10_000 {
        let t = Instant::now();
        let layout = probe_fuz(&mut f, 2_000_024, || false)?;
        assert_eq!(layout.audio, 1_000_012..2_000_024);
        samples.push(t.elapsed().as_nanos());
    }
    samples.sort_unstable();
    println!(
        "Synthetic warm sparse file: 10000 probes, 24 bytes/probe, p50={}ns p95={}ns p99={}ns. No decoding, scanning, archive decompression, rendering or MO2 measured.",
        samples[5000], samples[9500], samples[9900]
    );
    Ok(())
}
