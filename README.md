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
Le bot n'a jamais besoin de ton mot de passe : il utilise un **token Moodle**, ce qui marche aussi
quand on se connecte à Moodle via un service tiers (SSO, CAS, Microsoft…).

- `/aide` explique comment récupérer ton token (si la page « Clés de sécurité » n'affiche que la
  clé RSS, utilise la méthode du lien `launch.php` décrite dans `/aide`).
- `/config` ouvre un formulaire privé (URL de ton Moodle + token). Le token est vérifié puis
  enregistré, et **le salon où la commande est lancée devient ton salon d'alertes** (relancer
  `/config` ailleurs le change).
- `/notif` donne (ou retire) le rôle **notif qcm** à celui qui la lance. Le bot crée ce rôle sur
  chaque serveur et le mentionne à chaque ouverture de QCM (sans le rôle sur le serveur, il
  mentionne directement la personne qui a fait `/config`). Il lui faut la permission « Gérer
  les rôles ».
- `/stop` supprime ton token et arrête les alertes.

Toutes les `POLL_MINUTES` (10 par défaut), le bot vérifie le Moodle de chacun. Pour chaque QCM
**noté et présent dans le calendrier**, il poste, en mentionnant le rôle `notif qcm`, un embed (nom, matière,
fermeture) avec un bouton « Résoudre le QCM » **au moment où le QCM s'ouvre** (au plus
`POLL_MINUTES` après l'heure d'ouverture). Les QCM déjà ouverts quand tu te connectes, ou
découverts bien après leur ouverture, ne déclenchent pas d'alerte. Si le token cesse de
fonctionner, les alertes sont suspendues et le bot te le dit dans ton salon.

### Installation
1. Crée une application + un bot sur le portail développeur Discord, invite-le avec le scope
   `bot` + `applications.commands` et le droit d'écrire dans les salons concernés.
2. `cp .env.example .env`, renseigne `DISCORD_TOKEN` et `ENCRYPTION_KEY`.
   (`MOODLE_URL` est facultatif : il pré-remplit l'URL dans `/config`.)
3. `python main.py`

Les tokens sont chiffrés (Fernet) dans `qcm.db`. **Sauvegarde `ENCRYPTION_KEY`** : sans elle, les
tokens enregistrés sont illisibles et chacun devra refaire `/config`.
