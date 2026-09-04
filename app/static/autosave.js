/* Enregistrement automatique.
 *
 * L'application n'a aucun bouton "Enregistrer": toute modification part vers le
 * serveur des qu'elle est faite. La contrepartie, c'est qu'il faut VOIR que
 * c'est parti — sans retour visible, un enregistrement automatique n'est qu'une
 * incertitude de plus. D'ou le bandeau de statut ci-dessous, partage par la
 * liste des transactions et le formulaire de prevision.
 *
 * Le serveur reste seul juge: il revalide tout et peut refuser (chevauchement
 * de previsions, virement interne deja engage dans une repartition). Ce fichier
 * ne fait qu'envoyer et afficher la reponse.
 */
(function () {
  var toast = null;
  var hideTimer = null;

  var STYLES = {
    saving: "bg-slate-800 text-white",
    saved: "bg-emerald-600 text-white",
    error: "bg-rose-700 text-white",
  };

  function ensureToast() {
    if (toast) {
      return toast;
    }
    toast = document.createElement("div");
    toast.id = "autosave-toast";
    // `polite`: annonce par le lecteur d'ecran sans interrompre la saisie.
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    toast.style.opacity = "0";
    document.body.appendChild(toast);
    return toast;
  }

  /* Un message d'erreur doit rester lisible assez longtemps pour etre lu et
     compris: il n'est pas soumis au meme delai qu'une confirmation. */
  function flash(kind, text) {
    var el = ensureToast();
    el.textContent = text;
    el.className =
      "fixed z-50 bottom-4 right-4 max-w-sm rounded-lg px-4 py-2.5 text-sm shadow-lg " +
      "transition-opacity duration-200 " + (STYLES[kind] || STYLES.saving);
    el.style.opacity = "1";
    window.clearTimeout(hideTimer);
    if (kind !== "saving") {
      hideTimer = window.setTimeout(function () {
        el.style.opacity = "0";
      }, kind === "error" ? 8000 : 1600);
    }
  }

  function post(url, formData) {
    return fetch(url, {
      method: "POST",
      body: formData,
      headers: { "X-Requested-With": "fetch" },
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (body) {
          return { httpOk: response.ok, body: body || {} };
        });
    });
  }

  /* Envoie un formulaire et gere l'affichage de bout en bout.
     `onSuccess` recoit la reponse du serveur (identifiant cree, heure...). */
  function submit(form, options) {
    var settings = options || {};
    flash("saving", settings.savingText || "Enregistrement…");
    return post(settings.url || form.action, new FormData(form))
      .then(function (result) {
        if (result.body.ok) {
          flash("saved", "Enregistré à " + (result.body.saved_at || ""));
          if (settings.onSuccess) {
            settings.onSuccess(result.body);
          }
          return true;
        }
        flash("error", result.body.error || "Enregistrement refusé par le serveur.");
        if (settings.onError) {
          settings.onError(result.body.error);
        }
        return false;
      })
      .catch(function () {
        flash(
          "error",
          "Enregistrement impossible : l'application ne répond pas. " +
            "Vérifie qu'elle tourne toujours, ta modification n'est pas enregistrée."
        );
        if (settings.onError) {
          settings.onError(null);
        }
        return false;
      });
  }

  window.autosave = { flash: flash, post: post, submit: submit };
})();
