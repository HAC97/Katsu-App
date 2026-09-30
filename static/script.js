document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.fetch-form').forEach(form => {
        form.addEventListener('submit', () => {
            const btn = form.querySelector('button[type="submit"]');
            const loading = form.querySelector('.loading-container');
            form.classList.add('is-loading');
            if (btn) {
                btn.textContent = 'Escaneando...';
                // Disable after the submit event so the POST still goes out.
                setTimeout(() => { btn.disabled = true; }, 10);
            }
            if (loading) loading.hidden = false;
        });
    });

    const LABELS = {
        true: ['★', ' Quitar de favoritas'],
        false: ['☆', ' Agregar a favoritas'],
    };

    document.querySelectorAll('.favorite-btn').forEach(btn => {
        const status = btn.parentElement.querySelector('.action-status');

        const render = isFav => {
            const [icon, text] = LABELS[isFav];
            btn.textContent = '';
            const iconEl = document.createElement('span');
            iconEl.setAttribute('aria-hidden', 'true');
            iconEl.textContent = icon;
            btn.append(iconEl, text);
            btn.setAttribute('aria-pressed', String(isFav));
            const stamp = document.querySelector('.file-tabline .stamp');
            if (stamp) stamp.textContent = isFav ? 'Favorito' : 'Sin verificar';
        };

        btn.addEventListener('click', async () => {
            if (btn.disabled) return;
            btn.disabled = true;
            if (status) status.textContent = '';
            try {
                const resp = await fetch(`/stories/${btn.dataset.id}/favorite`, { method: 'POST' });
                if (!resp.ok) throw new Error(resp.status);
                const data = await resp.json();
                render(Boolean(data.is_favorite));
            } catch (err) {
                if (status) status.textContent = 'No se pudo actualizar. Inténtalo de nuevo.';
            } finally {
                btn.disabled = false;
            }
        });
    });
});
