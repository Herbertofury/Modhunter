//! Bind cancellation to one local import or audit, independently of the
//! profile/connection epoch. Lifecycle changes run under the model lock;
//! workers can read the cancellation flag without taking that lock.
#![cfg_attr(not(windows), allow(dead_code))]
use serde_json::Value;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

#[derive(Default)]
pub struct ImportOperation {
    id: AtomicU64,
    cancelled: AtomicBool,
}

impl ImportOperation {
    pub fn begin(&self) {
        self.id.fetch_add(1, Ordering::AcqRel);
        self.cancelled.store(false, Ordering::Release);
    }

    pub fn id(&self) -> u64 {
        self.id.load(Ordering::Acquire)
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    pub fn cancel(&self, request: &Value, current_epoch: u64, importing: bool) -> bool {
        if !importing
            || request.get("epoch").and_then(Value::as_u64) != Some(current_epoch)
            || request.get("importOperationId").and_then(Value::as_u64) != Some(self.id())
        {
            return false;
        }
        self.cancelled.store(true, Ordering::Release);
        true
    }

    pub fn finish(&self) {
        self.cancelled.store(false, Ordering::Release);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn delayed_cancel_cannot_stop_a_later_import_in_the_same_context() {
        let operation = ImportOperation::default();
        operation.begin();
        let delayed = json!({"epoch":7,"importOperationId":operation.id()});
        operation.finish();
        operation.begin();
        assert_eq!(operation.id(), 2);
        assert!(!operation.cancel(&delayed, 7, true));
        assert!(!operation.is_cancelled());

        let current = json!({"epoch":7,"importOperationId":operation.id()});
        assert!(operation.cancel(&current, 7, true));
        assert!(operation.is_cancelled());
        operation.finish();
        assert!(!operation.is_cancelled());
    }

    #[test]
    fn cancellation_requires_an_active_operation_and_its_current_context() {
        let operation = ImportOperation::default();
        operation.begin();
        let request = json!({"epoch":7,"importOperationId":operation.id()});
        assert!(!operation.cancel(&request, 8, true));
        assert!(!operation.cancel(&request, 7, false));
        for invalid in [json!(7), json!({"epoch":7}), json!({"importOperationId":1})] {
            assert!(!operation.cancel(&invalid, 7, true));
        }
        assert!(!operation.is_cancelled());
        assert!(operation.cancel(&request, 7, true));
        assert!(operation.is_cancelled());
    }
}
