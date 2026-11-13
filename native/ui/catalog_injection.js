((catalog, revision) => {
    // Navigation can change after Rust checks the WebView URL. Evaluate the
    // origin again in the page, and reject reordered injections on that page.
    const page = new URL(window.location.href);
    const local = (page.protocol === 'http:' && page.hostname === 'tauri.localhost')
        || (page.protocol === 'tauri:' && page.hostname === 'localhost');
    if (!local || page.port || page.username || page.password || page.pathname !== '/Modhunter.html'
        || page.searchParams.get('realm') === '1' || page.searchParams.get('siege') === '1'
        || typeof window.modhunterImportCatalog !== 'function'
        || !Number.isSafeInteger(revision) || revision <= (window.__modhunterNativeCatalogRevision || 0)) return;
    const previous = window.__modhunterNativeCatalogRevision || 0;
    window.__modhunterNativeCatalogRevision = revision;
    try {
        window.modhunterImportCatalog(catalog);
    } catch (error) {
        if (window.__modhunterNativeCatalogRevision === revision) window.__modhunterNativeCatalogRevision = previous;
        throw error;
    }
})
