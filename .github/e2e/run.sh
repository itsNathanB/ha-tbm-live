#!/usr/bin/env bash
# Démarre un vrai Home Assistant avec l'intégration configurée et vérifie la carte.
set -u
CFG=/tmp/ha
mkdir -p $CFG/.storage $CFG/custom_components
cp -r custom_components/tbm_live $CFG/custom_components/
cat > $CFG/configuration.yaml <<'YAML'
default_config:
logger:
  default: warning
  logs:
    custom_components.tbm_live: debug
    homeassistant.components.http: info
YAML
python - <<'PY'
import json, datetime
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
entry = {
  "entry_id": "01TBMLIVETESTENTRY0000000", "version": 1, "minor_version": 1,
  "domain": "tbm_live", "title": "Tram B · Quinconces → Pessac Centre",
  "data": {"line_ref": "bordeaux:Line:60:LOC", "line_name": "Tram B", "line_mode": "tram",
           "line_code": "B", "stop_name": "Quinconces", "stop_ref": "bordeaux:StopPoint:BP:3764:LOC",
           "stop_refs": ["bordeaux:StopPoint:BP:3764:LOC"], "direction_ref": "1",
           "direction_name": "France Alouette / Pessac Centre"},
  "options": {"count": 3}, "pref_disable_new_entities": False, "pref_disable_polling": False,
  "source": "user", "unique_id": "bordeaux:Line:60:LOC|bordeaux:StopPoint:BP:3764:LOC|1",
  "disabled_by": None,
}
json.dump({"version": 1, "minor_version": 1, "key": "core.config_entries", "data": {"entries": [entry]}},
          open("/tmp/ha/.storage/core.config_entries", "w"))
PY
hass -c $CFG --skip-pip > /tmp/ha.log 2>&1 &
for i in $(seq 1 120); do
  curl -sf -o /dev/null http://127.0.0.1:8123/manifest.json && break
  sleep 5
done
sleep 20
mkdir -p samples
python .github/e2e/browser.py > samples/browser.txt 2>&1
cat samples/browser.txt
{
  echo "== Journal tbm_live"; grep -iE "tbm_live" /tmp/ha.log | grep -v "Finished fetching" | head -30
} > samples/e2e.txt
