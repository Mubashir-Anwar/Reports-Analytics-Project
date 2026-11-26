// The copyright line at the bottom of every page (not in the Designer, to keep the canvas big: there it is in the
// account menu). The owner is the product name set in Admin → Name and look.
import { useBrand } from './brand.js';
import './site-footer.css';

export const copyrightLine = (name) => `© ${new Date().getFullYear()} ${name || 'Analysis'}. All rights reserved.`;

export default function SiteFooter({ slim = false }) {
  const brand = useBrand();
  return (
    <footer className={`fx-foot ${slim ? 'slim' : ''}`} role="contentinfo">
      <span className="fx-foot-mark" aria-hidden="true"><i /><i /><i /></span>
      <span>{copyrightLine(brand.name)}</span>
      <span className="fx-foot-grow" />
      <span className="fx-foot-links">
        <a href="/home">Home</a>
      </span>
    </footer>
  );
}
