#!/usr/bin/env bash
set -eu

BASE_URL=${MATHWEAVER_SMOKE_BASE_URL:-http://127.0.0.1:18080}
: "${MATHWEAVER_SMOKE_EMAIL:?set MATHWEAVER_SMOKE_EMAIL}"
: "${MATHWEAVER_SMOKE_PASSWORD:?set MATHWEAVER_SMOKE_PASSWORD}"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
chmod 700 "$TMP_DIR"

curl --fail --silent --show-error "$BASE_URL/api/v2/ready" >"$TMP_DIR/ready.json"
python3 -c 'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); assert data == {"ok": True, "database": "ready"}' "$TMP_DIR/ready.json"
curl --fail --silent --show-error "$BASE_URL/" >/dev/null

python3 -c 'import json,os,sys; json.dump({"email": os.environ["MATHWEAVER_SMOKE_EMAIL"], "password": os.environ["MATHWEAVER_SMOKE_PASSWORD"], "educationRole": "teacher"}, open(sys.argv[1], "w", encoding="utf-8"))' "$TMP_DIR/login-request.json"
chmod 600 "$TMP_DIR/login-request.json"
curl --fail --silent --show-error -H 'Content-Type: application/json' --data @"$TMP_DIR/login-request.json" "$BASE_URL/api/v2/auth/login" >"$TMP_DIR/login.json"
python3 -c 'import json,sys; token=json.load(open(sys.argv[1], encoding="utf-8"))["token"]; open(sys.argv[2], "w", encoding="utf-8").write(token)' "$TMP_DIR/login.json" "$TMP_DIR/token"
chmod 600 "$TMP_DIR/token"
TOKEN=$(cat "$TMP_DIR/token")

curl --fail --silent --show-error -H "Authorization: Bearer ${TOKEN}" "$BASE_URL/api/v2/history" >"$TMP_DIR/history.json"
curl --fail --silent --show-error -H "Authorization: Bearer ${TOKEN}" "$BASE_URL/api/v2/edu/classes" >"$TMP_DIR/classes.json"
CLASS_ID=$(python3 -c 'import json,sys; rows=json.load(open(sys.argv[1], encoding="utf-8")).get("classes", []); assert rows, "no teaching class"; print(rows[0]["id"])' "$TMP_DIR/classes.json")
curl --fail --silent --show-error -H "Authorization: Bearer ${TOKEN}" "$BASE_URL/api/v2/edu/classes/${CLASS_ID}/snapshots" >"$TMP_DIR/snapshots.json"
python3 -c 'import json,sys; rows=json.load(open(sys.argv[1], encoding="utf-8")).get("snapshots", []); assert rows, "no teaching snapshot"' "$TMP_DIR/snapshots.json"

echo "teaching smoke checks passed"
