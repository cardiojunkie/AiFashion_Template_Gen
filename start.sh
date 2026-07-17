#!/bin/sh
set -eu

if [ ! -f .env ]; then
  umask 077
  secret=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  fernet=$(head -c 32 /dev/urandom | base64 | tr -d '\n')
  sed \
    -e "s|POSTGRES_PASSWORD=change-me|POSTGRES_PASSWORD=$secret|" \
    -e "s|catalog:change-me@db|catalog:$secret@db|" \
    -e "s|S3_SECRET_KEY=change-me|S3_SECRET_KEY=$secret|" \
    -e "s|SECRET_KEY=change-me|SECRET_KEY=$secret|" \
    -e "s|ENCRYPTION_KEY=change-me|ENCRYPTION_KEY=$fernet|" \
    .env.example > .env
  echo "Generated .env with private development secrets."
fi

docker compose up -d --build

port=${BACKEND_PORT:-8000}
i=0
until curl -fsS "http://localhost:$port/api/v1/health" >/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    docker compose ps
    echo "Backend did not become healthy." >&2
    exit 1
  fi
  sleep 2
done

frontend_port=${FRONTEND_PORT:-5173}
i=0
until curl -fsS "http://localhost:$frontend_port" >/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    docker compose ps
    echo "Frontend did not become ready." >&2
    exit 1
  fi
  sleep 2
done

echo "Backend: http://localhost:${BACKEND_PORT:-8000}"
echo "Frontend: http://localhost:${FRONTEND_PORT:-5173}"
if [ -n "${CODESPACE_NAME:-}" ] && [ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]; then
  echo "Codespaces frontend: https://${CODESPACE_NAME}-${FRONTEND_PORT:-5173}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
fi
