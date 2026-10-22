(() => {
"use strict";
const $ = id => document.getElementById(id);
const T1 = "Continue", T2 = "Verify and sign in";
const busy = (b, on, t) => { b.disabled = on; b.textContent = on ? "Working…" : t; };
const EMAIL = /^\S+@\S+\.\S+$/;
let challenge = null;

$("tg").onclick = e => {
  const show = $("pw").type === "password";
  $("pw").type = show ? "text" : "password";
  e.target.textContent = show ? "Hide" : "Show";
  e.target.setAttribute("aria-pressed", String(show));
};

$("f1").onsubmit = async e => {
  e.preventDefault();
  const m = $("m1"); m.className = "msg"; m.textContent = "";
  const em = $("email").value.trim(), pw = $("pw").value;
  $("gE").classList.toggle("bad", !EMAIL.test(em));
  $("gP").classList.toggle("bad", pw.length < 8);
  if (!EMAIL.test(em)) { m.textContent = "Enter a valid email address."; $("email").focus(); return; }
  if (pw.length < 8) { m.textContent = "Password must be at least 8 characters."; $("pw").focus(); return; }
  busy($("b1"), true);
  try {
    const r = await Api.login(em, pw, $("rem").checked);
    challenge = r.challenge_token;
    $("f1").hidden = true; $("f2").hidden = false;
    $("stepLbl").textContent = "Step 2 of 2 · Verification";
    $("title").textContent = "Verify it’s you.";
    $("sub").textContent = "Enter the code from your authenticator app.";
    $("code").focus();
  } catch (x) {
    m.textContent = x.message;
    $("gE").classList.add("bad"); $("gP").classList.add("bad");
  }
  busy($("b1"), false, T1);
};

$("f2").onsubmit = async e => {
  e.preventDefault();
  const m = $("m2"); m.className = "msg"; m.textContent = "";
  busy($("b2"), true);
  try {
    await Api.verify(challenge, $("code").value.trim());
    m.className = "msg ok"; m.textContent = "Signed in. Redirecting to Command Center…";
    $("b2").textContent = "Signed in"; $("b2").disabled = true;
    setTimeout(() => location.replace("index.html"), 400);
  } catch (x) {
    m.textContent = x.message; $("gC").classList.add("bad");
    busy($("b2"), false, T2); $("code").focus();
  }
};

$("code").oninput = e => { e.target.value = e.target.value.replace(/\D/g, ""); $("gC").classList.remove("bad"); };

$("back").onclick = () => {
  challenge = null;
  $("f2").hidden = true; $("f1").hidden = false;
  $("code").value = ""; $("m2").textContent = "";
  $("stepLbl").textContent = "Step 1 of 2 · Credentials";
  $("title").textContent = "Operator sign in.";
  $("sub").textContent = "Restricted to authorised game operations staff.";
  $("pw").value = ""; $("pw").focus();
};

const tick = () => {
  $("clock").textContent = new Date().toISOString().slice(0, 19).replace("T", " ") + " UTC";
};
tick(); setInterval(tick, 1000);
$("email").focus();

Api.me().then(() => location.replace("index.html")).catch(() => {});
})();
