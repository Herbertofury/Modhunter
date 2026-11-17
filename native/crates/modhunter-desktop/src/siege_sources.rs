//! Narrow identity-only handoff. Website URLs never come from the game renderer.
#![cfg_attr(not(windows), allow(dead_code))]
use serde_json::Value;
use url::Url;

pub fn intent(url: &Url) -> Result<(String, String), String> {
    if url.scheme() != "modhunter-siege"
        || url.host_str() != Some("source")
        || !matches!(url.path(), "" | "/")
        || url.port().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err("Invalid Siege source request.".into());
    }
    let mut fields = std::collections::BTreeMap::new();
    for (key, value) in url.query_pairs() {
        if !matches!(key.as_ref(), "profileId" | "modId")
            || value.is_empty()
            || value.len() > 256
            || value.chars().any(char::is_control)
            || fields
                .insert(key.into_owned(), value.into_owned())
                .is_some()
        {
            return Err("Invalid Siege source identity.".into());
        }
    }
    Ok((
        fields
            .remove("profileId")
            .ok_or("Missing profile identity.")?,
        fields.remove("modId").ok_or("Missing mod identity.")?,
    ))
}

pub fn resolve(catalog: &Value, bounties: &Value, profile: &str, id: &str) -> Result<Url, String> {
    if catalog["profileId"].as_str() != Some(profile)
        || bounties["profileId"].as_str() != Some(profile)
    {
        return Err("MO2 profile changed. Reopen the encounter in its original profile.".into());
    }
    let defeated = bounties["bounties"].as_array().is_some_and(|rows| {
        rows.iter().any(|b| {
            b["modId"].as_str() == Some(id)
                && matches!(b["status"].as_str(), Some("defeated" | "forged"))
        })
    });
    if !defeated {
        return Err("This mod boss victory has not been confirmed by MO2.".into());
    }
    let item = catalog["items"]
        .as_array()
        .and_then(|rows| rows.iter().find(|m| m["id"].as_str() == Some(id)))
        .ok_or("The exact installed mod is no longer in this profile.")?;
    if item["source"]["verified"].as_bool() != Some(true) {
        return Err("This mod's source is unresolved. Review it in the dashboard first.".into());
    }
    let raw = item["source"]["url"]
        .as_str()
        .ok_or("This mod has no verified source page.")?;
    let canonical = modhunter_core::watchtower::canonical_page_url(raw)?;
    let mut url = Url::parse(&canonical).map_err(|e| e.to_string())?;
    if url.host_str() == Some("www.nexusmods.com") || url.host_str() == Some("nexusmods.com") {
        let pairs: Vec<(String, String)> = url
            .query_pairs()
            .filter(|(key, _)| key != "tab")
            .map(|(k, v)| (k.into_owned(), v.into_owned()))
            .collect();
        url.set_query(None);
        url.query_pairs_mut()
            .extend_pairs(pairs)
            .append_pair("tab", "files");
    }
    Ok(url)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn identity_only_no_url_or_duplicate_fields() {
        assert_eq!(
            intent(&Url::parse("modhunter-siege://source?profileId=p&modId=m").unwrap()).unwrap(),
            ("p".into(), "m".into())
        );
        for raw in [
            "modhunter-siege://source?profileId=p&modId=m&url=https://evil.test",
            "modhunter-siege://source?profileId=p&modId=m&modId=x",
            "modhunter-watch://source?profileId=p&modId=m",
            "modhunter-siege://source?profileId=p",
        ] {
            assert!(intent(&Url::parse(raw).unwrap()).is_err());
        }
    }
    #[test]
    fn requires_exact_profile_mod_victory_and_verified_public_source() {
        let mut c = json!({"profileId":"p","items":[{"id":"m","source":{"verified":true,"url":"https://www.nexusmods.com/skyrimspecialedition/mods/42?tab=description"}}]});
        let b = json!({"profileId":"p","bounties":[{"modId":"m","status":"defeated"}]});
        assert_eq!(
            resolve(&c, &b, "p", "m").unwrap().as_str(),
            "https://www.nexusmods.com/skyrimspecialedition/mods/42?tab=files"
        );
        assert!(resolve(&c, &b, "other", "m").is_err());
        assert!(resolve(&c, &b, "p", "missing").is_err());
        assert!(resolve(&c, &json!({"profileId":"p","bounties":[]}), "p", "m").is_err());
        c["items"][0]["source"]["url"] = json!("http://127.0.0.1/private");
        assert!(resolve(&c, &b, "p", "m").is_err());
        c["items"][0]["source"]["url"] = json!("https://github.com/example/project/releases");
        assert!(resolve(&c, &b, "p", "m").is_ok());
        c["items"][0]["source"]["verified"] = json!(false);
        assert!(resolve(&c, &b, "p", "m").is_err());
    }
}
