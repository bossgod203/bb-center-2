// mock:true  -> in-browser fake API (any email, password >= 8 chars, any 6-digit code). Works with any static server.
// mock:false -> real backend at same origin (/api/*).
window.CP_CONFIG = { mock: true, pollMs: 5000 };
