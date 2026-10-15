const byId = id => document.getElementById(id);
let register = false;
let needsSetup = false;

function setMode(value) {
  register = value;
  byId("login_tab").classList.toggle("active", !value);
  byId("register_tab").classList.toggle("active", value);
  byId("auth_title").textContent = value ? "Utwórz swoje konto" : "Witaj ponownie";
  byId("auth_description").textContent = value ? "Zacznij budować własną bibliotekę chmur punktów." : "Zaloguj się, aby otworzyć swoją bibliotekę.";
  byId("auth_submit").textContent = value ? "Utwórz konto" : "Zaloguj się";
  byId("password").autocomplete = value ? "new-password" : "current-password";
  byId("setup_hint").hidden = !(needsSetup && value);
  byId("auth_status").textContent = "";
}

byId("login_tab").addEventListener("click", () => setMode(false));
byId("register_tab").addEventListener("click", () => setMode(true));
byId("auth_form").addEventListener("submit", async event => {
  event.preventDefault();
  byId("auth_submit").disabled = true;
  byId("auth_status").textContent = "";
  try {
    const response = await fetch(`/api/auth/${register ? "register" : "login"}`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({email: byId("email").value, password: byId("password").value}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.errors?.[0]?.message || data.error || "Nie udało się zalogować.");
    window.location.assign("/");
  } catch (error) {
    byId("auth_status").textContent = error.message;
  } finally {
    byId("auth_submit").disabled = false;
  }
});

fetch("/api/auth/status").then(response => response.json()).then(data => {
  needsSetup = data.needsSetup;
  if (needsSetup) setMode(true);
}).catch(() => { byId("auth_status").textContent = "Nie udało się połączyć z serwerem."; });
