---
name: push
description: Publie le travail en cours sur GitHub — vérifications de sécurité, tests, commit et push. À utiliser dès que l'utilisatrice demande de pousser, publier, envoyer, sauvegarder sur git/GitHub, de faire un commit, ou dit simplement "push".
---

# Publier sur GitHub

Cette application manipule des relevés bancaires réels. **Le risque principal n'est pas
de rater un push, c'est d'envoyer une donnée personnelle sur un serveur distant** — un
push est irréversible : même après suppression, le contenu reste dans l'historique git et
dans les caches de GitHub.

Déroule les étapes dans l'ordre. **N'en saute aucune**, même pour une modification d'une
ligne. Si une vérification échoue, arrête-toi et explique — ne contourne jamais.

## 1. Vérifier ce qui partirait

```bash
git status --porcelain
git diff --stat HEAD
```

S'il n'y a rien à committer et rien à pousser, dis-le et arrête-toi là.

## 2. Contrôles de sécurité — bloquants

Ces fichiers ne doivent **jamais** être versionnés :

| Fichier | Pourquoi |
|---|---|
| `.env` | contient la clé API Anthropic |
| `data/` | la base SQLite, les sauvegardes, les relevés PDF importés |
| `capture ecran/` | captures d'écran de relevés bancaires |
| `scripts/seed_from_screenshots.py` | transactions réelles transcrites (commerçants, montants, n° de compte) |
| tout `*.pdf` | relevés |

Lance les deux contrôles, sur **l'index** (ce qui partirait) et non sur le disque :

```bash
# a) les fichiers sensibles sont-ils bien ignorés ?
git add -A
for f in .env data/suivi.db "capture ecran" scripts/seed_from_screenshots.py; do
  git check-ignore -q "$f" && echo "OK ignoré : $f" || echo "DANGER suivi : $f"
done

# b) une donnée personnelle a-t-elle atterri dans l'index ?
.venv/bin/python -m scripts.check_no_personal_data
```

Le second contrôle compare le contenu indexé aux identifiants réels lus dans la base
locale (libellés de comptes, numéros, noms de fichiers de relevés) et cherche en plus les
formes génériques : clé API, IBAN, longues suites de chiffres. Il ne contient lui-même
**aucune** donnée sensible codée en dur — une liste de motifs écrite dans un fichier
versionné serait exactement la fuite qu'elle prétend empêcher.

**Si l'un de ces contrôles remonte quelque chose : arrête-toi.** Ajoute le fichier au
`.gitignore`, retire-le de l'index avec `git rm --cached <fichier>`, relance les
contrôles, et seulement ensuite continue. Ne demande pas à l'utilisatrice de trancher sur
un fichier manifestement sensible : retire-le et signale-le.

## 3. Lancer les tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Si des tests échouent : **ne pousse pas**. Montre l'échec et propose de corriger d'abord.
Si l'utilisatrice demande explicitement de pousser malgré l'échec, fais-le, mais dis-le
clairement dans le message de commit et dans ta réponse.

## 4. Vérifier que l'application démarre encore

Un dépôt vert dont l'application ne démarre pas ne sert à rien.

```bash
.venv/bin/python -c "from app.main import app; print('import OK')"
```

## 5. Committer

Un commit par intention. Si le travail en cours mélange plusieurs sujets sans rapport,
propose de les séparer.

Message en français, à l'impératif ou au substantif, structuré ainsi :

- une ligne de résumé (moins de 72 caractères), sans point final ;
- une ligne vide ;
- le **pourquoi** plutôt que le quoi — le diff dit déjà ce qui a changé. Ce qu'on veut
  retrouver dans six mois, c'est la raison et le piège évité.

Termine toujours par :

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: <URL de la session en cours>
```

## 6. S'assurer que le dépôt distant existe

```bash
git remote -v
```

**S'il y a déjà un remote `origin`** : passe à l'étape 7.

**S'il n'y en a pas**, crée le dépôt. Il doit être **privé** — c'est une application de
finances personnelles ; ne crée jamais un dépôt public sans une demande explicite.

```bash
gh auth status   # si non authentifiée, demande-lui de taper: ! gh auth login
gh repo create suivi-depenses --private --source=. --remote=origin
```

## 7. Pousser

```bash
git push -u origin main
```

Si le push est rejeté parce que le distant a avancé : **ne force jamais**. Récupère
(`git pull --rebase`), résous, relance les tests, repousse.

## 8. Rendre compte

Donne à l'utilisatrice, en trois lignes maximum :

- l'URL du dépôt ;
- le résumé du commit poussé ;
- ce qui a été délibérément laissé de côté (fichiers ignorés, tests non lancés...).

## Rappels

- Ne pousse **que** ce que l'utilisatrice a demandé. Si tu vois d'autres modifications
  non liées dans le répertoire de travail, signale-les au lieu de les emporter au passage.
- `data/backups/` grossit tout seul : c'est normal, c'est ignoré par git.
- Le fichier `.env.example` doit rester versionné (il documente la configuration), mais il
  ne doit contenir que des valeurs d'exemple, jamais de vraie clé.
