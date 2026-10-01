// Blocking on purpose: sets the saved theme before first paint, so there is no flash.
(function () {
    try {
        var saved = localStorage.getItem('theme');
        if (saved === 'light' || saved === 'dark') {
            document.documentElement.setAttribute('data-theme', saved);
        }
    } catch (e) { /* storage blocked: follow the system theme */ }
})();
