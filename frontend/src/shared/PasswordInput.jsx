// A password box with an eye button that shows or hides what was typed. Takes every prop of an <input>.
import { useState } from 'react';

export default function PasswordInput({ className = '', style, ...props }) {
  const [shown, setShown] = useState(false);
  return (
    <span className={`pw-box ${className}`} style={style}>
      <input {...props} type={shown ? 'text' : 'password'} />
      <button type="button" className="pw-eye" onClick={() => setShown(!shown)} aria-label={shown ? 'Hide the password' : 'Show the password'}
        aria-pressed={shown} title={shown ? 'Hide' : 'Show'} tabIndex={-1}>
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" />
          {shown && <path d="M3 3l18 18" />}
        </svg>
      </button>
    </span>
  );
}
