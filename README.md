# Moodle QCM API

Mini API qui se connecte à Moodle avec tes identifiants et liste tous tes QCM.
Elle utilise les services web de Moodle (`moodle_mobile_app`), il faut donc
que ton Moodle les autorise (c'est le cas de la plupart).

## Lancer

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis renseigne MOODLE_URL
uvicorn app.main:app --reload
```

## Utiliser

```bash
curl -X POST localhost:8000/login -H 'Content-Type: application/json' \
  -d '{"username":"...","password":"..."}'          # -> {"token":"..."}
curl localhost:8000/quizzes -H 'Authorization: Bearer <token>'
```

Chaque QCM : `id, course_id, course_name, name, url, time_open, time_close,
time_limit, status` (`upcoming` / `open` / `closed`). Docs interactives : `/docs`.

## Tests

`pytest` (Moodle simulé, aucun accès réseau).

## Bot Discord (multi-utilisateurs)

Chaque utilisateur configure son propre compte, il n'y a pas de limite au nombre d'utilisateurs.

- `/config` ouvre un formulaire privé (identifiant + mot de passe Moodle). Les identifiants sont
  vérifiés puis enregistrés, et **le salon où la commande est lancée devient ton salon d'alertes**
  (relancer `/config` ailleurs le change).
- `/stop` supprime tes identifiants et arrête les alertes.

Toutes les `POLL_MINUTES` (10 par défaut), le bot vérifie le Moodle de chacun et poste, en te
mentionnant, un embed (nom du QCM, matière, fermeture) avec un bouton « Résoudre le QCM » pour
chaque QCM **noté, présent dans le calendrier et ouvert** non encore signalé. Un QCM annoncé à
l'avance est notifié à son ouverture. À la première configuration, les QCM existants sont
mémorisés sans notification. Si les identifiants cessent de fonctionner, les alertes sont
suspendues et le bot te le dit dans ton salon.

### Installation
1. Crée une application + un bot sur le portail développeur Discord, invite-le avec le scope
   `bot` + `applications.commands` et le droit d'écrire dans les salons concernés.
2. `cp .env.example .env`, renseigne `MOODLE_URL`, `DISCORD_TOKEN` et `ENCRYPTION_KEY`.
3. `python -m bot.main`

Les mots de passe sont chiffrés (Fernet) dans `qcm.db`. **Sauvegarde `ENCRYPTION_KEY`** : sans
elle, les identifiants enregistrés sont illisibles et chacun devra refaire `/config`.
