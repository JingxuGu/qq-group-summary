#!/bin/sh
set -eu

APP_DIR=${QQ_GROUP_SUMMARY_APP_DIR:-/opt/qq-group-summary}
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
mkdir -p data logs
if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  chmod 600 config.yaml
  echo "Created $APP_DIR/config.yaml; edit it before starting the service."
fi
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created $APP_DIR/.env; add secrets before starting the service."
fi
