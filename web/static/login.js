/* Sign-in page (shown when logins are on and nobody is signed in). */
(function () {
  const tok = document.querySelector('meta[name="mx-token"]').content, f = document.getElementById("lf"), m = document.getElementById("lm");
  f.addEventListener("submit", async (e) => {
    e.preventDefault(); m.textContent = "Signing in …"; m.className = "small muted";
    try {
      const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json", "X-MX-Token": tok },
        body: JSON.stringify({ user: document.getElementById("lu").value, password: document.getElementById("lp").value }) });
      const j = await r.json().catch(() => ({}));
      if (r.ok) { location.replace("/" + location.hash); return; }
      m.textContent = j.error || `Sign-in failed (HTTP ${r.status})`; m.className = "small down";
    } catch (err) { m.textContent = "Can't reach MarketX: is the Mac awake and the app running?"; m.className = "small down"; }
  });
})();
