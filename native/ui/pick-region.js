(() => {
    const box = document.createElement('div');
    const hint = document.createElement('div');
    box.style.cssText = 'position:fixed;z-index:2147483646;pointer-events:none;display:none;outline:3px solid #36b4fb;background:rgba(54,180,251,.15)';
    hint.style.cssText = 'position:fixed;top:12px;left:12px;z-index:2147483647;pointer-events:none;padding:8px 14px;background:#131820;color:white;font:16px system-ui;border-radius:5px';
    hint.textContent = promptText;
    document.documentElement.append(box, hint);
    let outcome = null;
    const excluded = 'form,input,textarea,select,iframe,script,style,[contenteditable="true"]';
    function selectable(target) {
        return target instanceof Element && target !== box && target !== hint && !target.closest(excluded);
    }
    function selector(target) {
        const parts = [];
        for (let el = target; el; el = el.parentElement) {
            if (el.id) {
                const anchor = '#' + CSS.escape(el.id);
                if (document.querySelectorAll(anchor).length === 1) {
                    parts.unshift(anchor);
                    break;
                }
            }
            const tag = CSS.escape(el.localName);
            let index = 1;
            for (let prior = el.previousElementSibling; prior; prior = prior.previousElementSibling) {
                if (prior.localName === el.localName && prior.namespaceURI === el.namespaceURI) index++;
            }
            parts.unshift(tag + ':nth-of-type(' + index + ')');
        }
        const path = parts.join(' > ');
        return path.length <= 1000 && document.querySelector(path) === target ? path : null;
    }
    function highlight(event) {
        if (!selectable(event.target)) {
            box.style.display = 'none';
            return;
        }
        const rect = event.target.getBoundingClientRect();
        Object.assign(box.style, {display:'block',top:rect.top+'px',left:rect.left+'px',
            width:rect.width+'px',height:rect.height+'px'});
    }
    function stop(event) {
        event.preventDefault();
        event.stopImmediatePropagation();
    }
    function choose(event) {
        if (!event.isTrusted) return;
        stop(event);
        if (selectable(event.target)) {
            const selected = selector(event.target);
            if (selected) outcome = {selector:selected};
        }
    }
    function keyboard(event) {
        if (!event.isTrusted) return;
        if (event.key === 'Escape') { stop(event); outcome = {cancelled:true}; }
        if (event.key === 'Enter') choose(event);
    }
    window.addEventListener('pointerover', highlight, true);
    window.addEventListener('focusin', highlight, true);
    window.addEventListener('pointerdown', stop, true);
    window.addEventListener('click', choose, true);
    window.addEventListener('keydown', keyboard, true);
    window.__modhunterReadPickedRegion = () => outcome;
    return true;
})()
