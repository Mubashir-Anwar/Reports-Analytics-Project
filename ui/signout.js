// "Sign out" buttons on the server-made pages (no access, analytics). CSP allows only scripts from this server.
document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-signout]');
  if (!button) return;
  button.disabled = true;
  try {
    await fetch('/api/server/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } finally {
    window.location.href = '/server';
  }
});
