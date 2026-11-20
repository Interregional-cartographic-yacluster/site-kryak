document.querySelectorAll('[data-external-embed]').forEach((container) => {
  const button = container.querySelector('button');
  if (!button) return;
  button.addEventListener('click', () => {
    const source = container.dataset.src;
    const kind = container.dataset.kind || 'iframe';
    if (!source) return;
    container.textContent = '';
    if (kind === 'script') {
      const script = document.createElement('script');
      script.src = source;
      script.async = true;
      script.charset = 'utf-8';
      container.appendChild(script);
      return;
    }
    const iframe = document.createElement('iframe');
    iframe.src = source;
    iframe.title = container.dataset.title || 'Внешний материал';
    iframe.loading = 'lazy';
    iframe.referrerPolicy = 'strict-origin-when-cross-origin';
    iframe.allow = 'autoplay; encrypted-media; fullscreen; picture-in-picture; screen-wake-lock';
    iframe.allowFullscreen = true;
    iframe.style.width = '100%';
    iframe.style.height = '100%';
    iframe.style.minHeight = '350px';
    iframe.style.border = '0';
    container.appendChild(iframe);
  }, { once: true });
});
