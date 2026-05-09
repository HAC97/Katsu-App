document.addEventListener('DOMContentLoaded', () => {
    const fetchForm = document.querySelector('.fetch-form');
    const fetchBtn = document.getElementById('fetch-btn');
    const loadingContainer = document.getElementById('loading-container');
    
    if (fetchForm && fetchBtn) {
        fetchForm.addEventListener('submit', function() {
            fetchBtn.textContent = 'Escaneando...';
            fetchBtn.style.opacity = '0.7';
            setTimeout(() => { fetchBtn.disabled = true; }, 10);
            
            if (loadingContainer) {
                loadingContainer.style.display = 'block';
            }
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
