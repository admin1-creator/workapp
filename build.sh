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

# デモ用の一般ユーザー。管理画面には入れない。未設定のときは作らない。
if [ -n "$DJANGO_DEMO_PASSWORD" ]; then
  python - <<'PY'
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ.get("DJANGO_DEMO_USERNAME", "demo")
password = os.environ.get("DJANGO_DEMO_PASSWORD", "")

user = User.objects.filter(username=username).first() if username else None
if user is None:
    if username and password:
        User.objects.create_user(username, "", password)
        print("Demo user created successfully.")
else:
    user.set_password(password)
    user.is_staff = False
    user.is_superuser = False
    user.save()
    print("Demo user password updated.")
PY
fi

if [ "$DEMO_SEED" = "1" ]; then
  python manage.py seed_demo_data
fi
