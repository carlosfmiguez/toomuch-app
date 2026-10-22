"""
Configurações do Sistema Toomuch.

Tudo que varia por ambiente vem de variáveis de ambiente (arquivo .env na raiz
em desenvolvimento). Sem .env o projeto roda com SQLite e arquivos locais, o
suficiente para desenvolver e testar.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, "troque-esta-chave-em-producao"),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "app.toomuch.art"]),
    DATABASE_URL=(str, f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
    TIME_ZONE=(str, "America/Sao_Paulo"),
    # Storage S3-compatível (Cloudflare R2, Backblaze B2, AWS S3). Vazio = disco local.
    STORAGE_BUCKET=(str, ""),
    STORAGE_ENDPOINT_URL=(str, ""),
    STORAGE_ACCESS_KEY=(str, ""),
    STORAGE_SECRET_KEY=(str, ""),
    STORAGE_REGION=(str, "auto"),
    STORAGE_PUBLIC_URL=(str, ""),
    CSRF_TRUSTED_ORIGINS=(list, ["https://app.toomuch.art"]),
    MEDIA_ROOT=(str, ""),
    TOOMUCH_WHATSAPP=(str, ""),
    TOOMUCH_INSTAGRAM=(str, "toomuchbr.art"),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

# Domínios que a hospedagem injeta sozinha (Railway: RAILWAY_PUBLIC_DOMAIN; Render: RENDER_EXTERNAL_HOSTNAME; Fly: FLY_APP_NAME)
for _host in filter(None, [
    env("RAILWAY_PUBLIC_DOMAIN", default=""),
    env("RENDER_EXTERNAL_HOSTNAME", default=""),
    f'{env("FLY_APP_NAME", default="")}.fly.dev' if env("FLY_APP_NAME", default="") else "",
]):
    if _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)
    if f"https://{_host}" not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(f"https://{_host}")

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.catalog",
    "apps.inventory",
    "apps.costing",
    "apps.production",
    "apps.publishing",
    "apps.integrations.tiny",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = 60

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
LOCALE_PATHS = [BASE_DIR / "locale"]  # traduções do shell (Unfold)
TIME_ZONE = env("TIME_ZONE")
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT")) if env("MEDIA_ROOT") else BASE_DIR / "media"

import sys

RUNNING_TESTS = "test" in sys.argv[1:2]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if (DEBUG or RUNNING_TESTS)
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

if RUNNING_TESTS:
    # Uploads dos testes vão para uma pasta temporária, nunca para ./media
    import tempfile

    MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="toomuch-test-media-"))

if env("STORAGE_BUCKET"):
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("STORAGE_BUCKET"),
            "endpoint_url": env("STORAGE_ENDPOINT_URL") or None,
            "access_key": env("STORAGE_ACCESS_KEY"),
            "secret_key": env("STORAGE_SECRET_KEY"),
            "region_name": env("STORAGE_REGION"),
            "custom_domain": env("STORAGE_PUBLIC_URL") or None,
            "querystring_auth": not env("STORAGE_PUBLIC_URL"),
            "file_overwrite": False,
            "default_acl": None,
        },
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Catálogo público
TOOMUCH_WHATSAPP = env("TOOMUCH_WHATSAPP")  # só dígitos com DDI, ex.: 5548999999999
TOOMUCH_INSTAGRAM = env("TOOMUCH_INSTAGRAM")

from config.unfold import UNFOLD  # noqa: E402,F401 - shell do sistema (sidebar, busca, cores)

LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/"

# Uploads de 3MF com várias placas passam de 100 MB.
DATA_UPLOAD_MAX_MEMORY_SIZE = 320 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024 * 1024

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
