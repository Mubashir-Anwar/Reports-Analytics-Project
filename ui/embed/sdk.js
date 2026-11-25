/*! Reporting service embed SDK — <mrs-report> and <mrs-dashboard>
 *
 *   <script src="https://reports.example.com/assets/embed/sdk.js"></script>
 *   <mrs-report token="emb_…"></mrs-report>
 *
 * The token comes from the website's own server (POST /api/v1/embed-tokens with its API key); never put the API
 * key in a page. Attributes: token (required), base (the reporting server; default: where this script came from),
 * height (start height in px, default 520; the frame then follows the content), title.
 * Events on the element: mrs-ready {reportId, title}, mrs-ran {action, format, rows}, mrs-error {message}.
 * <mrs-dashboard token-url="/dashboard-token" refresh="60" theme="light" height="640"></mrs-dashboard> shows a dashboard of
 * Analytics. token-url: an address of the website that answers {"token": "emb_…", "expiresAt": "…"} (asked again before
 * the token ends), or token="emb_…". refresh: seconds between automatic refreshes (0 = never, at least 10).
 * Events: mrs-ready {dashboardId, title}, mrs-error {message}. Methods: element.reload(), element.setToken(newToken).
 *
 * Methods of mrs-report: element.run("pdf" | "xlsx" | "csv"), element.preview(), element.setToken(newToken).
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

  class MrsDashboard extends HTMLElement {
    static get observedAttributes() { return ['token', 'refresh', 'theme']; }

    connectedCallback() {
      if (this.frame) return;
      this.style.display = this.style.display || 'block';
      this.frame = document.createElement('iframe');
      this.frame.title = this.getAttribute('title') || 'Dashboard';
      this.frame.loading = 'lazy';
      this.frame.referrerPolicy = 'strict-origin';
      this.frame.style.cssText = 'width:100%;border:0;display:block;';
      this.frame.style.height = `${parseInt(this.getAttribute('height') || '640', 10)}px`;
      this.append(this.frame);
      this.onMessage = (event) => this.handle(event);
      window.addEventListener('message', this.onMessage);
      this.start();
    }

    disconnectedCallback() {
      window.removeEventListener('message', this.onMessage);
      window.clearTimeout(this.timer);
      if (this.frame) { this.frame.remove(); this.frame = null; }
    }

    attributeChangedCallback() {
      if (this.frame) this.start();
    }

    get base() {
      return (this.getAttribute('base') || scriptBase || '').replace(/\/$/, '');
    }

    start() {
      const given = this.getAttribute('token');
      if (given) this.show(given);
      else if (this.getAttribute('token-url')) this.ask(false);
    }

    show(token) {
      const options = `refresh=${parseInt(this.getAttribute('refresh') || '0', 10) || 0}&theme=${encodeURIComponent(this.getAttribute('theme') || 'light')}`;
      this.frame.src = `${this.base}/embed/view?token=${encodeURIComponent(token)}#${options}`;
    }

    /** A token from the website's own server; later ones are handed to the open dashboard, so it does not reload. */
    ask(renewal) {
      window.clearTimeout(this.timer);
      fetch(this.getAttribute('token-url'), { credentials: 'same-origin', cache: 'no-store' })
        .then((response) => { if (!response.ok) throw new Error(`The token address answered HTTP ${response.status}`); return response.json(); })
        .then((answer) => {
          if (!this.frame) return;
          if (!answer || !answer.token) throw new Error('The token address did not answer {"token": "…"}');
          if (renewal) this.send({ type: 'mrs:token', token: answer.token }); else this.show(answer.token);
          const left = answer.expiresAt ? Date.parse(answer.expiresAt) - Date.now() : 900000;
          this.timer = window.setTimeout(() => this.ask(true), Math.max(30000, (left || 900000) - 120000));
        })
        .catch((error) => {
          this.dispatchEvent(new CustomEvent('mrs-error', { detail: { message: error.message }, bubbles: true }));
          if (this.frame) this.timer = window.setTimeout(() => this.ask(renewal), 30000);
        });
    }

    handle(event) {
      if (!this.frame || event.source !== this.frame.contentWindow) return;
      if (this.base && event.origin !== this.base) return;            // only messages from the reporting server
      const data = event.data || {};
      if (data.source !== 'mrs-dashboard') return;
      if (data.type === 'mrs:height' && Number.isFinite(data.height)) {
        if (this.getAttribute('height') === null) this.frame.style.height = `${Math.max(200, Math.min(data.height + 4, 8000))}px`;
        return;
      }
      const name = { 'mrs:ready': 'mrs-ready', 'mrs:error': 'mrs-error' }[data.type];
      if (name) {
        const { source, type, ...detail } = data;
        this.dispatchEvent(new CustomEvent(name, { detail, bubbles: true }));
      }
    }

    send(message) {
      if (this.frame && this.frame.contentWindow) this.frame.contentWindow.postMessage(message, this.base || '*');
    }

    reload() { this.send({ type: 'mrs:reload' }); }

    setToken(token) { this.send({ type: 'mrs:token', token }); }
  }

  window.customElements.define('mrs-report', MrsReport);
  window.customElements.define('mrs-dashboard', MrsDashboard);
}());
