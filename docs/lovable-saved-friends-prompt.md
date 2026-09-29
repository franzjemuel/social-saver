Build a mobile-first "Saved Friends" screen for Social Saver. Treat the backend as an external authenticated API.
Show avatar placeholder, @username, autosave Stories state, last successful check, last capture, archive state, and
provider error state. Include Add Friend and Remove actions plus a clear storage-usage indicator. Never request or
store Instagram passwords, session cookies, R2 credentials, database service keys, or queue credentials. Do not
implement Instagram polling in the browser. The UI may request an immediate refresh through a backend endpoint, but
the backend owns rate limits and scheduling.
