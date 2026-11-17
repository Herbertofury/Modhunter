//! One atomic owner prevents closing and plugin mutation from being admitted
//! concurrently. No UI thread waits for a slow preflight/installer mutex.
#![cfg_attr(not(windows), allow(dead_code))]
use std::sync::{
    Arc,
    atomic::{AtomicU8, Ordering},
};

const IDLE: u8 = 0;
const INSTALLING: u8 = 1;
const STOPPING: u8 = 2;
const CLOSING: u8 = 3;

#[derive(Default)]
pub struct Admission(AtomicU8);

#[derive(Debug, PartialEq, Eq)]
pub enum CloseDecision {
    Close,
    FinishInstallation,
}

pub struct InstallationLease(Arc<Admission>);

impl Admission {
    pub fn try_start(self: &Arc<Self>) -> Result<InstallationLease, &'static str> {
        self.0
            .compare_exchange(IDLE, INSTALLING, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| InstallationLease(self.clone()))
            .map_err(|state| {
                if state == CLOSING {
                    "Modhunter is closing. Reopen it before installing the plugin."
                } else {
                    "A plugin installation is already running."
                }
            })
    }
    pub fn installing(&self) -> bool {
        matches!(self.0.load(Ordering::Acquire), INSTALLING | STOPPING)
    }
    pub fn closing(&self) -> bool {
        self.0.load(Ordering::Acquire) == CLOSING
    }
    pub fn request_close(&self) -> CloseDecision {
        loop {
            let current = self.0.load(Ordering::Acquire);
            let next = match current {
                CLOSING => return CloseDecision::Close,
                STOPPING => return CloseDecision::FinishInstallation,
                INSTALLING => STOPPING,
                _ => CLOSING,
            };
            if self
                .0
                .compare_exchange(current, next, Ordering::AcqRel, Ordering::Acquire)
                .is_ok()
            {
                return if next == CLOSING {
                    CloseDecision::Close
                } else {
                    CloseDecision::FinishInstallation
                };
            }
        }
    }
    pub fn request_stop(&self) {
        let _ = self
            .0
            .compare_exchange(INSTALLING, STOPPING, Ordering::AcqRel, Ordering::Acquire);
    }
    pub fn stop_requested(&self) -> bool {
        self.0.load(Ordering::Acquire) == STOPPING
    }
    fn finish(&self) {
        let mut current = self.0.load(Ordering::Acquire);
        while matches!(current, INSTALLING | STOPPING) {
            match self
                .0
                .compare_exchange(current, IDLE, Ordering::AcqRel, Ordering::Acquire)
            {
                Ok(_) => break,
                Err(observed) => current = observed,
            }
        }
    }
}

impl Drop for InstallationLease {
    fn drop(&mut self) {
        // A close request cannot take this lease; it requests a safe queue stop.
        // Unwinding/early errors also release admission without replaying work.
        self.0.finish();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Barrier;

    #[test]
    fn an_admitted_close_rejects_all_later_installation_attempts() {
        let admission = Arc::new(Admission::default());
        assert_eq!(admission.request_close(), CloseDecision::Close);
        assert!(admission.try_start().is_err());
        assert!(admission.closing());
    }
    #[test]
    fn an_installation_finishes_before_a_second_close_can_be_admitted() {
        let admission = Arc::new(Admission::default());
        let lease = admission.try_start().unwrap();
        assert_eq!(admission.request_close(), CloseDecision::FinishInstallation);
        assert!(admission.stop_requested());
        assert!(admission.try_start().is_err());
        assert!(admission.stop_requested());
        drop(lease);
        assert!(!admission.stop_requested());
        assert_eq!(admission.request_close(), CloseDecision::Close);
        assert!(admission.try_start().is_err());
    }
    #[test]
    fn racing_close_and_install_never_both_obtain_admission() {
        for _ in 0..200 {
            let admission = Arc::new(Admission::default());
            let barrier = Arc::new(Barrier::new(2));
            let other = admission.clone();
            let ready = barrier.clone();
            let close = std::thread::spawn(move || {
                ready.wait();
                other.request_close()
            });
            barrier.wait();
            let lease = admission.try_start();
            let decision = close.join().unwrap();
            assert_eq!(lease.is_ok(), decision == CloseDecision::FinishInstallation);
            drop(lease);
        }
    }
    #[test]
    fn stop_is_latched_before_worker_initialization_and_clears_only_on_finish() {
        let admission = Arc::new(Admission::default());
        let lease = admission.try_start().unwrap();
        admission.request_close();
        // The worker has not initialized its model yet. An admission retry
        // cannot erase the close request and start changing queued targets.
        assert!(admission.try_start().is_err());
        assert!(admission.stop_requested());
        drop(lease);
        let next = admission.try_start().unwrap();
        assert!(!admission.stop_requested());
        admission.request_stop();
        assert!(admission.stop_requested());
        drop(next);
    }
}
