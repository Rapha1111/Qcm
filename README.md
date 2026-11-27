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

## Bot Discord (multi-serveurs)

Le bot peut être invité sur autant de serveurs que tu veux. Sur chaque serveur, **un administrateur**
le configure une fois pour tout le monde. Il utilise un **token Moodle** (jamais de mot de passe),
ce qui marche aussi avec une connexion via un service tiers (SSO, CAS, Microsoft…).

- `/config` *(admin)* : formulaire privé (URL du Moodle + token). Le compte lié sert à tout le
  serveur, et **le salon où la commande est lancée devient le salon d'alertes**.
- `/notif` : donne (ou retire) le rôle **notif qcm** à celui qui la lance. Le bot crée ce rôle et
  le mentionne à chaque ouverture de QCM. Il lui faut la permission « Gérer les rôles », et son
  rôle doit être au-dessus de `notif qcm`.
- `/aide` : comment récupérer le token (si « Clés de sécurité » n'affiche que la clé RSS,
  utilise la méthode du lien `launch.php`).
- `/stop` *(admin)* : supprime la configuration et le token du serveur. Retirer le bot du serveur
  les supprime aussi.

### Fonctionnement
- **Une fois par jour** (`SYNC_HOURS`, et à chaque démarrage / `/config`), le bot récupère sur
  Moodle les QCM **notés présents dans le calendrier** qui ne sont **pas encore ouverts** et les
  mémorise avec leur heure d'ouverture.
- **Chaque minute**, il regarde le stockage local : dès qu'un QCM atteint son heure d'ouverture, il
  poste dans le salon un embed (nom, matière, fermeture) avec un bouton « Résoudre le QCM » et
  mentionne le rôle, puis **supprime le QCM du stockage**.
- Limite : un QCM ajouté au calendrier *après* la récupération du jour et ouvrant avant la
  suivante n'est pas annoncé. Les QCM déjà ouverts ne sont jamais annoncés.
- Si le token cesse de fonctionner, la récupération est suspendue et le bot le dit dans le
  salon en mentionnant l'administrateur qui a fait `/config`.

### Installation
1. Crée une application + un bot sur le portail développeur Discord, invite-le avec les scopes
   `bot` + `applications.commands` et les permissions : voir/écrire dans le salon, gérer les rôles.
2. `cp .env.example .env`, renseigne `DISCORD_TOKEN` et `ENCRYPTION_KEY`.
   (`MOODLE_URL` est facultatif : il pré-remplit l'URL dans `/config`.)
3. `python main.py`

Les tokens sont chiffrés (Fernet) dans `qcm.db`. **Sauvegarde `ENCRYPTION_KEY`** : sans elle, les
tokens enregistrés sont illisibles et chaque admin devra refaire `/config`.
