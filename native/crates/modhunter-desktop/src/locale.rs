//! The same compiled catalog supplies native dialog labels and owned webviews.
use serde_json::Value;
use std::sync::OnceLock;

pub fn text(locale: &str, key: &str) -> String {
    static CATALOGS: OnceLock<Value> = OnceLock::new();
    let catalogs = CATALOGS.get_or_init(|| {
        serde_json::from_str(
            include_str!("../../../ui/locale-catalogs.js")
                .strip_prefix("window.ModhunterLocaleCatalogs = ")
                .and_then(|s| s.strip_suffix(";\n"))
                .expect("generated locale wrapper"),
        )
        .expect("validated locale catalogs")
    });
    catalogs[locale][key].as_str().unwrap_or(key).to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_titles_use_the_same_four_languages_and_preserve_unknown_details() {
        assert_eq!(text("es", "Preferences"), "Preferencias");
        assert_eq!(text("it", "Preferences"), "Preferenze");
        assert_eq!(text("zh-TW", "Preferences"), "偏好設定");
        assert_eq!(text("en", "Preferences"), "Preferences");
        assert_eq!(
            text("es", "Provider-specific detail: 123"),
            "Provider-specific detail: 123"
        );
    }
}
