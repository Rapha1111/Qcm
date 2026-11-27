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

## Bot Discord

Le bot vérifie Moodle toutes les `POLL_MINUTES` (10 par défaut) et poste dans le salon
`DISCORD_CHANNEL_ID` un embed (nom du QCM, matière, date de fermeture) avec un bouton
« Résoudre le QCM » à chaque QCM **noté, présent dans le calendrier et ouvert** qu'il n'a pas
encore signalé. Un QCM annoncé à l'avance est notifié au moment où il s'ouvre.

1. Crée une application + un bot sur le portail développeur Discord, invite-le sur ton
   serveur (droits : voir/envoyer des messages dans le salon).
2. Remplis `.env` (voir `.env.example`), puis : `python -m bot.main`

Au tout premier lancement, les QCM existants sont mémorisés (`state.json`) sans notification.
