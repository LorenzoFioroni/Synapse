// The extension reads publisher pages with this browser's institutional access. No extension ID,
// bearer token or publisher URL crosses this DOM bridge; the worker validates its library origin.
import type { Item } from './types.js';

export function publisherPdfIds(items: Item[]): string[] {
  return items.filter((it) => it.doi && !it.doi.toLowerCase().startsWith('10.48550/')
    && !it.attachments?.some((a) => a.type === 'pdf')).map((it) => it.id);
}

export function requestPublisherPdfs(ids: string[]): Promise<boolean> {
  if (document.documentElement.getAttribute('data-synapse-extension') !== 'pdfs' || !ids.length || ids.length > 200) return Promise.resolve(false);
  return new Promise((resolve) => {
    const requestId = crypto.randomUUID();
    const done = (accepted: boolean) => {
      clearTimeout(timer);
      document.removeEventListener('synapse:publisher-pdfs-result', receive);
      resolve(accepted);
    };
    const receive = (event: Event) => {
      if (!(event instanceof CustomEvent) || typeof event.detail !== 'string') return;
      try {
        const result = JSON.parse(event.detail);
        if (result.requestId === requestId) done(result.accepted === true);
      } catch { /* an unrelated page event */ }
    };
    const timer = setTimeout(() => done(false), 5000);
    document.addEventListener('synapse:publisher-pdfs-result', receive);
    document.dispatchEvent(new CustomEvent('synapse:publisher-pdfs', { detail: JSON.stringify({ requestId, ids }) }));
  });
}
