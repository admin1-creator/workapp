#!/usr/bin/env bash
# Exit on error
set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input

python manage.py migrate

# 同じユーザー名が既にあるときは作らない。失敗してもデプロイは続ける。
if [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  python - <<'PY'
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")

if not username or User.objects.filter(username=username).exists():
    print("Login user already exists.")
else:
    User.objects.create_superuser(username, email, password)
    print("Superuser created successfully.")
PY
fi

if [ "$DEMO_SEED" = "1" ]; then
  python manage.py seed_demo_data
fi
