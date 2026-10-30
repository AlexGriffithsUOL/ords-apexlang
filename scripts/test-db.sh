#!/usr/bin/env bash
# Throwaway Oracle Free + ORDS for the database tests.
#
#   scripts/test-db.sh start    start (or reuse) the containers, wait until ready
#   scripts/test-db.sh env      print ORDS2APX_TEST_DSN for the tests
#   scripts/test-db.sh stop     remove the containers
#
# The password is random per machine and kept in .test-db.env (git-ignored).
set -euo pipefail

cd "$(dirname "$0")/.."
DB=ords2apx-db
ORDS=ords2apx-ords
NET=ords2apx-net
DB_PORT=${ORDS2APX_DB_PORT:-15210}
ORDS_PORT=${ORDS2APX_ORDS_PORT:-18080}
DB_IMAGE=${ORDS2APX_DB_IMAGE:-gvenzl/oracle-free:23-slim-faststart}
ORDS_IMAGE=${ORDS2APX_ORDS_IMAGE:-container-registry.oracle.com/database/ords:latest}

if [[ ! -f .test-db.env ]]; then
  echo "ORDS2APX_DB_PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)X9" > .test-db.env
fi
# shellcheck disable=SC1091
source .test-db.env
PW=$ORDS2APX_DB_PASSWORD

wait_for_log() { # container, text, seconds
  for _ in $(seq "$3"); do
    if docker logs "$1" 2>&1 | grep -q "$2"; then return 0; fi
    if [[ "$(docker inspect -f '{{.State.Running}}' "$1")" != "true" ]]; then
      echo "$1 stopped before it was ready" >&2
      docker logs --tail 40 "$1" >&2
      return 1
    fi
    sleep 1
  done
  echo "timed out waiting for '$2' in $1" >&2
  docker logs --tail 40 "$1" >&2
  return 1
}

case "${1:-}" in
  start)
    docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
    if ! docker container inspect "$DB" >/dev/null 2>&1; then
      docker run -d --name "$DB" --network "$NET" -p "127.0.0.1:$DB_PORT:1521" \
        -e ORACLE_PASSWORD="$PW" -e APP_USER=ORDS2APX_TEST -e APP_USER_PASSWORD="$PW" "$DB_IMAGE" >/dev/null
    fi
    wait_for_log "$DB" "DATABASE IS READY TO USE" 600
    if ! docker container inspect "$ORDS" >/dev/null 2>&1; then
      # the image reads the SYS password and the ORDS runtime user's password
      # from /run/secrets; a throwaway database can use one for both
      secrets=$(mktemp -d)
      printf '%s' "$PW" > "$secrets/ORACLE_PWD"
      printf '%s' "$PW" > "$secrets/ORACLE_USER_PWD"
      chmod 644 "$secrets/ORACLE_PWD" "$secrets/ORACLE_USER_PWD"
      docker run -d --name "$ORDS" --network "$NET" -p "127.0.0.1:$ORDS_PORT:8080" \
        -e DBHOST="$DB" -e DBPORT=1521 -e DBSERVICENAME=FREEPDB1 \
        -v "$secrets/ORACLE_PWD:/run/secrets/ORACLE_PWD:ro" \
        -v "$secrets/ORACLE_USER_PWD:/run/secrets/ORACLE_USER_PWD:ro" "$ORDS_IMAGE" >/dev/null
    fi
    wait_for_log "$ORDS" "Oracle REST Data Services initialized" 900
    echo "ready: database on 127.0.0.1:$DB_PORT, ORDS on http://127.0.0.1:$ORDS_PORT/ords/"
    ;;
  env)
    echo "ORDS2APX_TEST_DSN=ords2apx_test/$PW@127.0.0.1:$DB_PORT/FREEPDB1"
    echo "ORDS2APX_TEST_ORDS_URL=http://127.0.0.1:$ORDS_PORT/ords/"
    ;;
  stop)
    docker rm -f "$ORDS" "$DB" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    ;;
  *)
    echo "usage: $0 start|env|stop" >&2
    exit 2
    ;;
esac
