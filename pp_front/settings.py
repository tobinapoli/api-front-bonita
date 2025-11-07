from pathlib import Path
import os
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def env(k, d=None): 
    v = os.getenv(k, d)
    if v is None: raise RuntimeError(f"Falta {k}")
    return v

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = os.getenv("DJANGO_DEBUG","false").lower()=="true"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS","").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin","django.contrib.auth","django.contrib.contenttypes",
    "django.contrib.sessions","django.contrib.messages","django.contrib.staticfiles",
    "bonita",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pp_front.urls"
TEMPLATES = [{
    "BACKEND":"django.template.backends.django.DjangoTemplates",
    "DIRS":[],
    "APP_DIRS":True,
    "OPTIONS":{"context_processors":[
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "pp_front.wsgi.application"

DATABASES = {"default": {"ENGINE":"django.db.backends.sqlite3","NAME": BASE_DIR / "db.sqlite3"}}
LANGUAGE_CODE = "es-ar"
TIME_ZONE = "America/Argentina/Buenos_Aires"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Bonita
BONITA_BASE_URL = os.getenv("BONITA_BASE_URL","http://localhost:8080/bonita")
BONITA_USER = os.getenv("BONITA_USER","install")
BONITA_PASSWORD = os.getenv("BONITA_PASSWORD","install")
BONITA_ASSIGNEE = os.getenv("BONITA_ASSIGNEE","walter.bates")
