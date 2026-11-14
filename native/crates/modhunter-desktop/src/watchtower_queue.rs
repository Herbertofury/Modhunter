//! Shared MO2 queue controls. The renderer supplies an action only; current
//! profile and execution authority are captured by this owned native host.
use super::*;

pub(super) fn execute(app: &tauri::AppHandle, kind: &str, value: &Value) -> Result<Value> {
    if kind == "installQueue" {
        let (client, endpoint, epoch) = crate::windows::mo2_connection(app)?;
        let queue = client
            .native_content(&endpoint, "install-queue", &json!({}))
            .map_err(err)?;
        if !crate::windows::mo2_connection_current(app, &endpoint, epoch) {
            return Err(
                "MO2 changed while reading the install queue. Refresh the current connection."
                    .into(),
            );
        }
        return Ok(json!({"installQueue":queue}));
    }
    let action = value["action"]
        .as_str()
        .filter(|action| matches!(*action, "stopAfterCurrent" | "cancelPreparation" | "resume"))
        .ok_or("Choose stop after current, cancel preparation, or resume.")?;
    let mut local = json!({});
    if action == "cancelPreparation" {
        let reviews = review::cancel_preparation(app);
        local = acquisition::cancel_preparation(app)
            .unwrap_or_else(|error| json!({"errors":[error],"requested":0}));
        local["reviewsCancelled"] = json!(reviews);
    }
    let (client,endpoint,epoch)=crate::windows::mo2_connection(app).map_err(|error|if action=="cancelPreparation"{format!("Local preparation cancellation was requested. MO2 queue state could not be confirmed: {error}")}else{error})?;
    let context = client
        .native_content(&endpoint, "content-context", &json!({}))
        .map_err(err)?;
    if !crate::windows::mo2_connection_current(app, &endpoint, epoch) {
        return Err("MO2 changed before queue control. Refresh the current connection.".into());
    }
    let result=client.native_content(&endpoint,"install-queue-control",&json!({"action":action,"profileId":context["profileId"],"executionEpoch":context["executionEpoch"]})).map_err(|error|format!("Queue control was not confirmed. Refresh its actual state before retrying: {error}"))?;
    if !crate::windows::mo2_connection_current(app, &endpoint, epoch) {
        return Err(
            "The connection changed after queue control. Refresh the actual queue state.".into(),
        );
    }
    let mut message = result["message"]
        .as_str()
        .unwrap_or("Queue control applied.")
        .to_string();
    if let Some(errors) = local["errors"]
        .as_array()
        .filter(|errors| !errors.is_empty())
    {
        message.push_str(" Local cancellation needs attention: ");
        message.push_str(
            &errors
                .iter()
                .map(|error| {
                    error
                        .as_str()
                        .map(str::to_owned)
                        .unwrap_or_else(|| error.to_string())
                })
                .collect::<Vec<_>>()
                .join("; "),
        );
    }
    Ok(json!({"installQueue":result,"localCancellation":local,"message":message}))
}
