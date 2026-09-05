# WATCHIT! Authentication Testing
1. Register with a new email and a six-character password at `/api/auth/register`.
2. Confirm `/api/auth/me` returns the same user using the httpOnly session cookie.
3. Log out and confirm `/api/auth/me` returns 401.
4. Login again and verify the dashboard loads.
5. Google login uses Emergent OAuth and redirects back using the browser origin; verify the session fragment is exchanged once.