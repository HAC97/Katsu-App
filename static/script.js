document.addEventListener('DOMContentLoaded', () => {
    const fetchBtn = document.getElementById('fetch-btn');
    if (fetchBtn) {
        fetchBtn.addEventListener('click', function() {
            this.textContent = 'Escaneando...';
            this.disabled = true;
            this.style.opacity = '0.7';
        });
    }

    document.querySelectorAll('.favorite-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const id = btn.dataset.id;
            const resp = await fetch(`/stories/${id}/favorite`, { method: 'POST' });
            const data = await resp.json();
            if (data.is_favorite) {
                btn.innerHTML = '&#9733; Quitar de favoritas';
                btn.dataset.fav = '1';
            } else {
                btn.innerHTML = '&#9734; Agregar a favoritas';
                btn.dataset.fav = '0';
            }
        });
    });
});
