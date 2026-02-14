import hashlib
import hmac
import json
import os
import pwd
import re
import shutil
import subprocess
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

FTP_ROOT = Path("/ftp")
USERS_DB = Path("/data/users.json")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,31}$")

app = FastAPI(title="FTP Manager API", version="2.0.0")


# ── Security Headers Middleware ──────────────────────────────────────────


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


# ── Rate Limiting (in-memory) ────────────────────────────────────────────

_rate_limit: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 30     # max requests per window


def check_rate_limit(client_ip: str) -> None:
    now = time.time()
    hits = _rate_limit.get(client_ip, [])
    hits = [t for t in hits if now - t < RATE_LIMIT_WINDOW]
    if len(hits) >= RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Muitas requisições. Tente novamente em breve.")
    hits.append(now)
    _rate_limit[client_ip] = hits


# ── Modelos ──────────────────────────────────────────────────────────────


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    home: str | None = None


class UpdatePasswordRequest(BaseModel):
    password: str = Field(min_length=6, max_length=128)


# ── Persistência ─────────────────────────────────────────────────────────


def load_db() -> dict:
    """Carrega banco de usuários do JSON persistido."""
    if USERS_DB.exists():
        try:
            return json.loads(USERS_DB.read_text())
        except (json.JSONDecodeError, OSError):
            return {"users": {}}
    return {"users": {}}


def save_db(db: dict) -> None:
    """Salva banco de usuários de forma atômica."""
    USERS_DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=2, ensure_ascii=False))
    tmp.rename(USERS_DB)


# ── Autenticação ─────────────────────────────────────────────────────────


def get_admin_token() -> str:
    return os.getenv("ADMIN_TOKEN", "changeme")


def auth(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> bool:
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(client_ip)

    expected = get_admin_token()
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()

    token = x_api_key or bearer or ""
    # Constant-time comparison to prevent timing attacks
    if hmac.compare_digest(token, expected):
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido",
    )


# ── Utilitários ──────────────────────────────────────────────────────────


def run(command: list[str], input_text: str | None = None) -> None:
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "Erro no comando"
        raise HTTPException(status_code=400, detail=detail)


def validate_username(username: str) -> str:
    value = username.strip()
    if not USERNAME_RE.match(value):
        raise HTTPException(
            status_code=422,
            detail="username inválido (use letras, números, ponto, traço e underline)",
        )
    return value


def resolve_home(username: str, home: str | None) -> Path:
    if home:
        home_path = Path(home).resolve()
    else:
        home_path = (FTP_ROOT / username).resolve()

    ftp_root = FTP_ROOT.resolve()
    if ftp_root not in [home_path, *home_path.parents]:
        raise HTTPException(status_code=422, detail="home precisa estar dentro de /ftp")
    return home_path


def list_ftp_users() -> list[dict]:
    users = []
    db = load_db()
    for entry in pwd.getpwall():
        if entry.pw_dir.startswith("/ftp/"):
            managed = entry.pw_name in db.get("users", {})
            users.append(
                {
                    "username": entry.pw_name,
                    "uid": entry.pw_uid,
                    "gid": entry.pw_gid,
                    "home": entry.pw_dir,
                    "managed": managed,
                }
            )
    users.sort(key=lambda item: item["username"].lower())
    return users


# ── Dashboard HTML ───────────────────────────────────────────────────────

_DASHBOARD_HTML = r"""<!doctype html>
<html lang="pt-BR" class="h-full">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>FTP Manager</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config={darkMode:'class',theme:{extend:{colors:{
brand:{50:'#eff6ff',100:'#dbeafe',200:'#bfdbfe',400:'#60a5fa',500:'#3b82f6',600:'#2563eb',700:'#1d4ed8',800:'#1e40af'},
surface:{50:'#f8fafc',100:'#f1f5f9',200:'#e2e8f0',700:'#334155',800:'#1e293b',900:'#0f172a'}
}}}}
</script>
<style>
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideIn{from{opacity:0;transform:scale(.95)}to{opacity:1;transform:scale(1)}}
@keyframes toastIn{from{opacity:0;transform:translateX(100%)}to{opacity:1;transform:translateX(0)}}
@keyframes toastOut{from{opacity:1;transform:translateX(0)}to{opacity:0;transform:translateX(100%)}}
.fade-in{animation:fadeIn .3s ease-out}
.slide-in{animation:slideIn .2s ease-out}
.toast-in{animation:toastIn .3s ease-out}
.toast-out{animation:toastOut .3s ease-in forwards}
[x-cloak]{display:none!important}
</style>
</head>
<body class="h-full bg-surface-50 dark:bg-surface-900 text-gray-900 dark:text-gray-100 transition-colors duration-300">

<!-- ============ LOGIN SCREEN ============ -->
<div id="loginScreen" class="min-h-full flex items-center justify-center p-4">
  <div class="w-full max-w-md fade-in">
    <div class="text-center mb-8">
      <div class="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-brand-600 text-white mb-4">
        <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01"/></svg>
      </div>
      <h1 class="text-2xl font-bold">FTP Manager</h1>
      <p class="text-gray-500 dark:text-gray-400 mt-1">vsftpd-manager</p>
    </div>
    <div class="bg-white dark:bg-surface-800 rounded-2xl shadow-xl border border-gray-200 dark:border-surface-700 p-8">
      <label class="block text-sm font-medium mb-2 text-gray-700 dark:text-gray-300">Token de acesso</label>
      <div class="relative">
        <input id="loginToken" type="password" placeholder="Digite o ADMIN_TOKEN"
               class="w-full px-4 py-3 rounded-xl border border-gray-300 dark:border-surface-700 bg-white dark:bg-surface-900 focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none transition pr-12"
               onkeydown="if(event.key==='Enter')doLogin()"/>
        <button onclick="toggleVis('loginToken',this)" class="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
          <svg class="w-5 h-5 eye-open" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
        </button>
      </div>
      <div id="loginError" class="hidden mt-3 text-sm text-red-600 dark:text-red-400"></div>
      <button onclick="doLogin()" class="w-full mt-4 py-3 rounded-xl bg-brand-600 hover:bg-brand-700 text-white font-semibold transition shadow-lg shadow-brand-600/25">
        Entrar
      </button>
    </div>
    <div class="flex justify-center mt-6">
      <button onclick="toggleTheme()" class="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition">
        <svg id="themeIconLogin" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/></svg>
        <span id="themeTextLogin">Modo escuro</span>
      </button>
    </div>
  </div>
</div>

<!-- ============ MAIN APP ============ -->
<div id="appScreen" class="hidden min-h-full">
  <!-- Navbar -->
  <nav class="sticky top-0 z-30 bg-white/80 dark:bg-surface-800/80 backdrop-blur-xl border-b border-gray-200 dark:border-surface-700">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div class="flex items-center justify-between h-16">
        <div class="flex items-center gap-3">
          <div class="flex items-center justify-center w-9 h-9 rounded-lg bg-brand-600 text-white">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01"/></svg>
          </div>
          <span class="font-bold text-lg hidden sm:block">FTP Manager</span>
        </div>
        <div class="flex items-center gap-2">
          <button onclick="toggleTheme()" class="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-surface-700 transition" title="Alternar tema">
            <svg id="themeIconNav" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/></svg>
          </button>
          <button onclick="doLogout()" class="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-600 dark:text-gray-400 hover:bg-red-50 dark:hover:bg-red-900/20 hover:text-red-600 dark:hover:text-red-400 transition" title="Sair">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
            <span class="hidden sm:inline">Sair</span>
          </button>
        </div>
      </div>
    </div>
  </nav>

  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
    <!-- Stats -->
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 fade-in">
      <div class="bg-white dark:bg-surface-800 rounded-2xl border border-gray-200 dark:border-surface-700 p-5">
        <div class="flex items-center gap-3">
          <div class="flex items-center justify-center w-10 h-10 rounded-xl bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
          </div>
          <div>
            <p class="text-2xl font-bold" id="statTotal">—</p>
            <p class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">Total</p>
          </div>
        </div>
      </div>
      <div class="bg-white dark:bg-surface-800 rounded-2xl border border-gray-200 dark:border-surface-700 p-5">
        <div class="flex items-center gap-3">
          <div class="flex items-center justify-center w-10 h-10 rounded-xl bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
          </div>
          <div>
            <p class="text-2xl font-bold" id="statApi">—</p>
            <p class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">Via API</p>
          </div>
        </div>
      </div>
      <div class="bg-white dark:bg-surface-800 rounded-2xl border border-gray-200 dark:border-surface-700 p-5">
        <div class="flex items-center gap-3">
          <div class="flex items-center justify-center w-10 h-10 rounded-xl bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
          </div>
          <div>
            <p class="text-2xl font-bold" id="statEnv">—</p>
            <p class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">Via ENV</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Create user card -->
    <div class="bg-white dark:bg-surface-800 rounded-2xl border border-gray-200 dark:border-surface-700 p-6 fade-in">
      <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">
        <svg class="w-5 h-5 text-brand-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z"/></svg>
        Novo usuário
      </h2>
      <div class="grid grid-cols-1 sm:grid-cols-4 gap-3">
        <div>
          <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Usuário</label>
          <input id="username" placeholder="nome_do_cliente" class="w-full px-3 py-2.5 rounded-xl border border-gray-300 dark:border-surface-700 bg-white dark:bg-surface-900 focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none transition text-sm"/>
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Senha</label>
          <input id="password" type="password" placeholder="min. 6 caracteres" class="w-full px-3 py-2.5 rounded-xl border border-gray-300 dark:border-surface-700 bg-white dark:bg-surface-900 focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none transition text-sm"/>
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Home <span class="text-gray-400">(opcional)</span></label>
          <input id="home" placeholder="/ftp/cliente" class="w-full px-3 py-2.5 rounded-xl border border-gray-300 dark:border-surface-700 bg-white dark:bg-surface-900 focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none transition text-sm"/>
        </div>
        <div class="flex items-end">
          <button onclick="createUser()" class="w-full py-2.5 rounded-xl bg-brand-600 hover:bg-brand-700 text-white font-medium transition shadow-lg shadow-brand-600/20 text-sm">
            Criar usuário
          </button>
        </div>
      </div>
    </div>

    <!-- Users table -->
    <div class="bg-white dark:bg-surface-800 rounded-2xl border border-gray-200 dark:border-surface-700 overflow-hidden fade-in">
      <div class="px-6 py-4 border-b border-gray-200 dark:border-surface-700 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <h2 class="text-lg font-semibold flex items-center gap-2">
          <svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
          Usuários FTP
        </h2>
        <div class="relative w-full sm:w-72">
          <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
          <input id="searchInput" type="text" placeholder="Buscar usuário..." oninput="filterUsers()"
                 class="w-full pl-10 pr-4 py-2 rounded-xl border border-gray-300 dark:border-surface-700 bg-white dark:bg-surface-900 focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none transition text-sm"/>
        </div>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full">
          <thead>
            <tr class="bg-gray-50 dark:bg-surface-900/50">
              <th class="px-6 py-3 text-left text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Usuário</th>
              <th class="px-6 py-3 text-left text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider hidden md:table-cell">Diretório home</th>
              <th class="px-6 py-3 text-left text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Origem</th>
              <th class="px-6 py-3 text-left text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider hidden lg:table-cell">UID</th>
              <th class="px-6 py-3 text-right text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Ações</th>
            </tr>
          </thead>
          <tbody id="usersBody" class="divide-y divide-gray-100 dark:divide-surface-700"></tbody>
        </table>
      </div>
      <div id="emptyState" class="hidden py-12 text-center">
        <svg class="mx-auto w-12 h-12 text-gray-300 dark:text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"/></svg>
        <p class="mt-3 text-gray-500 dark:text-gray-400">Nenhum usuário encontrado</p>
      </div>
      <div id="loadingState" class="py-12 text-center">
        <div class="inline-flex items-center gap-2 text-gray-500">
          <svg class="animate-spin w-5 h-5" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
          Carregando...
        </div>
      </div>
    </div>
  </main>
</div>

<!-- ============ MODAL: PASSWORD ============ -->
<div id="pwModal" class="hidden fixed inset-0 z-50">
  <div class="absolute inset-0 bg-black/50 dark:bg-black/70 backdrop-blur-sm" onclick="closeModal('pwModal')"></div>
  <div class="absolute inset-0 flex items-center justify-center p-4">
    <div class="relative bg-white dark:bg-surface-800 rounded-2xl shadow-2xl border border-gray-200 dark:border-surface-700 w-full max-w-md p-6 slide-in">
      <button onclick="closeModal('pwModal')" class="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
      </button>
      <div class="flex items-center gap-3 mb-5">
        <div class="flex items-center justify-center w-10 h-10 rounded-xl bg-brand-100 dark:bg-brand-900/30 text-brand-600 dark:text-brand-400">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/></svg>
        </div>
        <h3 class="text-lg font-semibold">Alterar senha</h3>
      </div>
      <div class="space-y-3">
        <div>
          <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Usuário</label>
          <input id="pw-user" disabled class="w-full px-3 py-2.5 rounded-xl border border-gray-200 dark:border-surface-700 bg-gray-100 dark:bg-surface-900 text-sm"/>
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Nova senha</label>
          <input id="pw-new" type="password" placeholder="min. 6 caracteres" class="w-full px-3 py-2.5 rounded-xl border border-gray-300 dark:border-surface-700 bg-white dark:bg-surface-900 focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none transition text-sm"
                 onkeydown="if(event.key==='Enter')submitPassword()"/>
        </div>
      </div>
      <div class="flex gap-3 mt-6">
        <button onclick="closeModal('pwModal')" class="flex-1 py-2.5 rounded-xl border border-gray-300 dark:border-surface-700 text-sm font-medium hover:bg-gray-50 dark:hover:bg-surface-700 transition">Cancelar</button>
        <button onclick="submitPassword()" class="flex-1 py-2.5 rounded-xl bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium transition shadow-lg shadow-brand-600/20">Salvar</button>
      </div>
    </div>
  </div>
</div>

<!-- ============ MODAL: CONFIRM DELETE ============ -->
<div id="delModal" class="hidden fixed inset-0 z-50">
  <div class="absolute inset-0 bg-black/50 dark:bg-black/70 backdrop-blur-sm" onclick="closeModal('delModal')"></div>
  <div class="absolute inset-0 flex items-center justify-center p-4">
    <div class="relative bg-white dark:bg-surface-800 rounded-2xl shadow-2xl border border-gray-200 dark:border-surface-700 w-full max-w-sm p-6 slide-in">
      <div class="flex items-center gap-3 mb-4">
        <div class="flex items-center justify-center w-10 h-10 rounded-xl bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
        </div>
        <h3 class="text-lg font-semibold">Confirmar exclusão</h3>
      </div>
      <p class="text-sm text-gray-600 dark:text-gray-400 mb-1">Deseja realmente excluir o usuário:</p>
      <p class="font-semibold text-base mb-5" id="del-username-display"></p>
      <input type="hidden" id="del-username"/>
      <div class="flex gap-3">
        <button onclick="closeModal('delModal')" class="flex-1 py-2.5 rounded-xl border border-gray-300 dark:border-surface-700 text-sm font-medium hover:bg-gray-50 dark:hover:bg-surface-700 transition">Cancelar</button>
        <button onclick="confirmDelete()" class="flex-1 py-2.5 rounded-xl bg-red-600 hover:bg-red-700 text-white text-sm font-medium transition">Excluir</button>
      </div>
    </div>
  </div>
</div>

<!-- ============ TOAST CONTAINER ============ -->
<div id="toasts" class="fixed top-4 right-4 z-[60] space-y-2 w-80 pointer-events-none"></div>

<script>
// ── State ─────────────────────────────────────────────────────
let _token = '';
let _users = [];

// ── Theme ─────────────────────────────────────────────────────
function getTheme() { return localStorage.getItem('ftpTheme') || 'system'; }
function applyTheme() {
  const t = getTheme();
  const dark = t === 'dark' || (t === 'system' && window.matchMedia('(prefers-color-scheme:dark)').matches);
  document.documentElement.classList.toggle('dark', dark);
  const sunIcon = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>';
  const moonIcon = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>';
  ['themeIconLogin','themeIconNav'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = dark ? sunIcon : moonIcon;
  });
  const tl = document.getElementById('themeTextLogin');
  if (tl) tl.textContent = dark ? 'Modo claro' : 'Modo escuro';
}
function toggleTheme() {
  const current = getTheme();
  const isDark = document.documentElement.classList.contains('dark');
  localStorage.setItem('ftpTheme', isDark ? 'light' : 'dark');
  applyTheme();
}
applyTheme();
window.matchMedia('(prefers-color-scheme:dark)').addEventListener('change', applyTheme);

// ── Toast ─────────────────────────────────────────────────────
function toast(msg, type = 'ok') {
  const c = document.getElementById('toasts');
  const colors = {
    ok: 'bg-emerald-600 text-white',
    error: 'bg-red-600 text-white',
    info: 'bg-brand-600 text-white'
  };
  const icons = {
    ok: '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>',
    error: '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>',
    info: '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>'
  };
  const el = document.createElement('div');
  el.className = `pointer-events-auto flex items-center gap-2 px-4 py-3 rounded-xl shadow-lg text-sm font-medium toast-in ${colors[type] || colors.info}`;
  el.innerHTML = `<svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">${icons[type] || icons.info}</svg><span class="flex-1">${esc(msg)}</span>`;
  c.appendChild(el);
  setTimeout(() => { el.classList.remove('toast-in'); el.classList.add('toast-out'); }, 3500);
  setTimeout(() => el.remove(), 3900);
}

// ── Utils ─────────────────────────────────────────────────────
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function toggleVis(inputId, btn) {
  const inp = document.getElementById(inputId);
  inp.type = inp.type === 'password' ? 'text' : 'password';
}

function hdrs() { return { 'Content-Type': 'application/json', 'X-API-Key': _token }; }

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { ...hdrs(), ...(opts.headers || {}) } });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || 'Erro na requisição');
  }
  if (res.status === 204) return null;
  return res.json();
}

// ── Modals ────────────────────────────────────────────────────
function openModal(id) { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

// ── Login / Logout ────────────────────────────────────────────
async function doLogin() {
  const val = document.getElementById('loginToken').value.trim();
  if (!val) return;
  _token = val;
  try {
    await api('/api/users');
    sessionStorage.setItem('ftpToken', _token);
    document.getElementById('loginScreen').classList.add('hidden');
    document.getElementById('appScreen').classList.remove('hidden');
    document.getElementById('loginError').classList.add('hidden');
    await loadUsers();
  } catch (e) {
    _token = '';
    const err = document.getElementById('loginError');
    err.textContent = e.message;
    err.classList.remove('hidden');
  }
}
function doLogout() {
  _token = '';
  sessionStorage.removeItem('ftpToken');
  document.getElementById('appScreen').classList.add('hidden');
  document.getElementById('loginScreen').classList.remove('hidden');
  document.getElementById('loginToken').value = '';
}
// Auto-login from session
(function autoLogin() {
  const saved = sessionStorage.getItem('ftpToken');
  if (saved) {
    document.getElementById('loginToken').value = saved;
    doLogin();
  }
})();

// ── Users ─────────────────────────────────────────────────────
async function loadUsers() {
  document.getElementById('loadingState').classList.remove('hidden');
  document.getElementById('emptyState').classList.add('hidden');
  try {
    _users = await api('/api/users');
    renderUsers(_users);
    document.getElementById('statTotal').textContent = _users.length;
    document.getElementById('statApi').textContent = _users.filter(u => u.managed).length;
    document.getElementById('statEnv').textContent = _users.filter(u => !u.managed).length;
  } catch (e) { toast(e.message, 'error'); }
  document.getElementById('loadingState').classList.add('hidden');
}

function filterUsers() {
  const q = document.getElementById('searchInput').value.toLowerCase();
  const filtered = _users.filter(u => u.username.toLowerCase().includes(q) || u.home.toLowerCase().includes(q));
  renderUsers(filtered);
}

function renderUsers(users) {
  const tbody = document.getElementById('usersBody');
  const empty = document.getElementById('emptyState');
  tbody.innerHTML = '';
  if (!users.length) { empty.classList.remove('hidden'); return; }
  empty.classList.add('hidden');
  users.forEach(u => {
    const tr = document.createElement('tr');
    tr.className = 'hover:bg-gray-50 dark:hover:bg-surface-700/50 transition-colors';
    const badgeCls = u.managed
      ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
      : 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400';
    const badgeTxt = u.managed ? 'API' : 'ENV';
    tr.innerHTML = `
      <td class="px-6 py-3">
        <div class="flex items-center gap-3">
          <div class="flex items-center justify-center w-8 h-8 rounded-lg bg-gray-100 dark:bg-surface-700 text-gray-600 dark:text-gray-300 text-xs font-bold uppercase">${esc(u.username.substring(0,2))}</div>
          <span class="font-medium text-sm">${esc(u.username)}</span>
        </div>
      </td>
      <td class="px-6 py-3 hidden md:table-cell"><code class="text-xs bg-gray-100 dark:bg-surface-700 px-2 py-1 rounded-lg">${esc(u.home)}</code></td>
      <td class="px-6 py-3"><span class="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold ${badgeCls}">${badgeTxt}</span></td>
      <td class="px-6 py-3 text-sm text-gray-500 hidden lg:table-cell">${u.uid}</td>
      <td class="px-6 py-3 text-right">
        <div class="flex items-center justify-end gap-1">
          <button onclick="openPwModal('${esc(u.username)}')" title="Alterar senha" class="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-surface-700 text-gray-500 hover:text-brand-600 dark:hover:text-brand-400 transition">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/></svg>
          </button>
          <button onclick="openDelModal('${esc(u.username)}')" title="Excluir" class="p-2 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 text-gray-500 hover:text-red-600 dark:hover:text-red-400 transition">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
          </button>
        </div>
      </td>`;
    tbody.appendChild(tr);
  });
}

// ── Create ────────────────────────────────────────────────────
async function createUser() {
  const un = document.getElementById('username').value.trim();
  const pw = document.getElementById('password').value;
  const hm = document.getElementById('home').value.trim() || null;
  if (!un || !pw) { toast('Preencha usuário e senha', 'error'); return; }
  try {
    await api('/api/users', { method: 'POST', body: JSON.stringify({ username: un, password: pw, home: hm }) });
    document.getElementById('username').value = '';
    document.getElementById('password').value = '';
    document.getElementById('home').value = '';
    toast('Usuário "' + un + '" criado com sucesso!', 'ok');
    await loadUsers();
  } catch (e) { toast(e.message, 'error'); }
}

// ── Password modal ───────────────────────────────────────────
function openPwModal(username) {
  document.getElementById('pw-user').value = username;
  document.getElementById('pw-new').value = '';
  openModal('pwModal');
  setTimeout(() => document.getElementById('pw-new').focus(), 100);
}
async function submitPassword() {
  const user = document.getElementById('pw-user').value;
  const pw = document.getElementById('pw-new').value;
  if (!pw || pw.length < 6) { toast('Senha precisa ter no mínimo 6 caracteres', 'error'); return; }
  try {
    await api('/api/users/' + encodeURIComponent(user) + '/password', { method: 'PUT', body: JSON.stringify({ password: pw }) });
    closeModal('pwModal');
    toast('Senha de "' + user + '" alterada!', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

// ── Delete modal ─────────────────────────────────────────────
function openDelModal(username) {
  document.getElementById('del-username').value = username;
  document.getElementById('del-username-display').textContent = username;
  openModal('delModal');
}
async function confirmDelete() {
  const user = document.getElementById('del-username').value;
  try {
    await api('/api/users/' + encodeURIComponent(user), { method: 'DELETE' });
    closeModal('delModal');
    toast('Usuário "' + user + '" excluído', 'ok');
    await loadUsers();
  } catch (e) { toast(e.message, 'error'); }
}

// Keyboard: ESC to close modals
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeModal('pwModal');
    closeModal('delModal');
  }
});
</script>
</body>
</html>"""


# ── Dashboard UI ─────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return _DASHBOARD_HTML


# ── API Endpoints ────────────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/users", dependencies=[Depends(auth)])
def get_users() -> list[dict]:
    return list_ftp_users()


@app.post("/api/users", dependencies=[Depends(auth)], status_code=201)
def create_user(payload: CreateUserRequest) -> dict:
    username = validate_username(payload.username)

    try:
        pwd.getpwnam(username)
        raise HTTPException(status_code=409, detail="Usuário já existe")
    except KeyError:
        pass

    home = resolve_home(username, payload.home)
    home.mkdir(parents=True, exist_ok=True)

    run(["addgroup", "-S", username])
    run(["adduser", "-D", "-h", str(home), "-s", "/sbin/nologin", "-G", username, username])
    run(["chpasswd"], input_text=f"{username}:{payload.password}\n")
    # Corrigir owner do diretório home
    subprocess.run(["chown", f"{username}:{username}", str(home)], check=False)

    # Persistir no banco JSON
    db = load_db()
    db["users"][username] = {"password": payload.password, "home": str(home)}
    save_db(db)

    return {"username": username, "home": str(home)}


@app.put("/api/users/{username}/password", dependencies=[Depends(auth)])
def update_password(username: str, payload: UpdatePasswordRequest) -> dict:
    user = validate_username(username)
    try:
        pwd.getpwnam(user)
    except KeyError:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    run(["chpasswd"], input_text=f"{user}:{payload.password}\n")

    # Atualizar no banco JSON (se gerenciado pela API)
    db = load_db()
    if user in db["users"]:
        db["users"][user]["password"] = payload.password
        save_db(db)

    return {"username": user, "updated": True}


@app.delete("/api/users/{username}", dependencies=[Depends(auth)], status_code=204)
def delete_user(username: str) -> None:
    user = validate_username(username)
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    # Captura o home ANTES de remover o usuário do sistema
    home_dir = Path(entry.pw_dir)

    run(["deluser", user])
    subprocess.run(["delgroup", user], capture_output=True, check=False)

    # Remover a pasta física do usuário (somente se estiver dentro de /ftp)
    ftp_root = FTP_ROOT.resolve()
    if home_dir.exists() and ftp_root in home_dir.resolve().parents and home_dir.resolve() != ftp_root:
        shutil.rmtree(home_dir, ignore_errors=True)

    # Remover do banco JSON
    db = load_db()
    db["users"].pop(user, None)
    save_db(db)
