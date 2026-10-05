(function () {
  "use strict";
  var signingUp = false;
  var form = document.getElementById("authForm");
  var submit = document.getElementById("submit");
  var message = document.getElementById("message");
  var toggle = document.getElementById("toggle");
  function say(text, error) {
    message.textContent = text;
    message.className = error ? "error" : "";
  }
  toggle.addEventListener("click", function () {
    signingUp = !signingUp;
    document.getElementById("nameField").hidden = !signingUp;
    document.getElementById("name").required = signingUp;
    document.getElementById("password").autocomplete = signingUp ? "new-password" : "current-password";
    document.getElementById("heading").textContent = signingUp ? "Create your farmer account" : "Sign in to your ponds";
    submit.textContent = signingUp ? "Create account" : "Sign in";
    toggle.textContent = signingUp ? "Already registered? Sign in" : "New farmer? Create an account";
    say("");
  });
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    submit.disabled = true;
    toggle.disabled = true;
    say(signingUp ? "Creating your account…" : "Signing in…");
    try {
      var client = await window.pondAuth();
      var credentials = {email: document.getElementById("email").value.trim(),
                         password: document.getElementById("password").value};
      var result = signingUp ? await client.auth.signUp(Object.assign(credentials, {
        options: {data: {full_name: document.getElementById("name").value.trim()},
                  emailRedirectTo: window.location.origin + "/login"}
      })) : await client.auth.signInWithPassword(credentials);
      document.getElementById("password").value = "";
      if (result.error) throw result.error;
      if (result.data.session) window.location.replace("/");
      else say("Check your email to confirm your account, then sign in.");
    } catch (error) {
      say(error.message || "Sign-in could not complete. Please try again.", true);
    } finally {
      submit.disabled = false;
      toggle.disabled = false;
    }
  });
  (async function () {
    try {
      var client = await window.pondAuth();
      var result = await client.auth.getSession();
      if (result.data.session) window.location.replace("/");
      client.auth.onAuthStateChange(function (event, session) {
        if (session && event === "SIGNED_IN") window.location.replace("/");
      });
    } catch (error) {
      say(error.message, true);
    }
  })();
})();
