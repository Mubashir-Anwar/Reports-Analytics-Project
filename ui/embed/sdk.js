/*! Reporting service embed SDK — <mrs-report>
 *
 *   <script src="https://reports.example.com/assets/embed/sdk.js"></script>
 *   <mrs-report token="emb_…"></mrs-report>
 *
 * The token comes from the website's own server (POST /api/v1/embed-tokens with its API key); never put the API
 * key in a page. Attributes: token (required), base (the reporting server; default: where this script came from),
 * height (start height in px, default 520; the frame then follows the content), title.
 * Events on the element: mrs-ready {reportId, title}, mrs-ran {action, format, rows}, mrs-error {message}.
 * Methods: element.run("pdf" | "xlsx" | "csv"), element.preview(), element.setToken(newToken).
 */
(function () {
  'use strict';
  if (window.customElements && window.customElements.get('mrs-report')) return;
  const scriptBase = (function () {
    try { return new URL(document.currentScript.src).origin; } catch (error) { return ''; }
  }());

  class MrsReport extends HTMLElement {
    static get observedAttributes() { return ['token']; }

    connectedCallback() {
      if (this.frame) return;
      this.style.display = this.style.display || 'block';
      this.frame = document.createElement('iframe');
      this.frame.title = this.getAttribute('title') || 'Report';
      this.frame.loading = 'lazy';
      this.frame.referrerPolicy = 'strict-origin';
      this.frame.style.cssText = 'width:100%;border:0;display:block;';
      this.frame.style.height = `${parseInt(this.getAttribute('height') || '520', 10)}px`;
      this.append(this.frame);
      this.onMessage = (event) => this.handle(event);
      window.addEventListener('message', this.onMessage);
      this.load();
    }

    disconnectedCallback() {
      window.removeEventListener('message', this.onMessage);
    }

    attributeChangedCallback() {
      if (this.frame) this.load();
    }

    get base() {
      return (this.getAttribute('base') || scriptBase || '').replace(/\/$/, '');
    }

    load() {
      const token = this.getAttribute('token') || '';
      if (!token) return;
      this.frame.src = `${this.base}/embed/view?token=${encodeURIComponent(token)}`;
    }

    handle(event) {
      if (!this.frame || event.source !== this.frame.contentWindow) return;
      if (this.base && event.origin !== this.base) return;            // only messages from the reporting server
      const data = event.data || {};
      if (data.source !== 'mrs-report') return;
      if (data.type === 'mrs:height' && Number.isFinite(data.height)) {
        this.frame.style.height = `${Math.max(160, Math.min(data.height + 4, 5000))}px`;
        return;
      }
      const name = { 'mrs:ready': 'mrs-ready', 'mrs:ran': 'mrs-ran', 'mrs:error': 'mrs-error' }[data.type];
      if (name) {
        const { source, type, ...detail } = data;
        this.dispatchEvent(new CustomEvent(name, { detail, bubbles: true }));
      }
    }

    send(message) {
      if (this.frame && this.frame.contentWindow) this.frame.contentWindow.postMessage(message, this.base || '*');
    }

    run(format) { this.send({ type: 'mrs:run', format: format || 'pdf' }); }

    preview() { this.send({ type: 'mrs:preview' }); }

    setToken(token) { this.setAttribute('token', token); }
  }

  window.customElements.define('mrs-report', MrsReport);
}());
