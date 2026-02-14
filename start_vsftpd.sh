#!/bin/sh

MANAGER_HOST=${MANAGER_HOST:-0.0.0.0}
MANAGER_PORT=${MANAGER_PORT:-8080}
ADMIN_TOKEN=${ADMIN_TOKEN:-changeme}

# Garante que o diretório de dados persistentes existe
mkdir -p /data

# Remove todos os usuários do FTP (serão recriados a seguir)
grep '/ftp/' /etc/passwd | cut -d':' -f1 | xargs -r -n1 deluser 2>/dev/null

# ── 1. Cria usuários do USERS env (bootstrap/admin) ──────────────────────

if [ -z "$USERS" ]; then
  echo "USERS env vazio. Apenas usuários persistidos serão restaurados."
fi

for i in $USERS; do
  NAME=$(echo "$i" | cut -d'|' -f1)
  GROUP=$NAME
  PASS=$(echo "$i" | cut -d'|' -f2)
  FOLDER=$(echo "$i" | cut -d'|' -f3)
  UID_VAL=$(echo "$i" | cut -d'|' -f4)
  GID_VAL=$(echo "$i" | cut -d'|' -f5)

  # Reseta opções a cada iteração
  UID_OPT=""
  GROUP_OPT=""

  if [ -z "$FOLDER" ]; then
    FOLDER="/ftp/$NAME"
  fi

  # Cria pasta se não existir
  if [ ! -d "$FOLDER" ]; then
    echo "Criando pasta: $FOLDER"
    mkdir -p "$FOLDER"
  fi

  if [ ! -z "$UID_VAL" ]; then
    UID_OPT="-u $UID_VAL"
    if [ -z "$GID_VAL" ]; then
      GID_VAL=$UID_VAL
    fi
    GROUP=$(getent group "$GID_VAL" | cut -d: -f1)
    if [ ! -z "$GROUP" ]; then
      GROUP_OPT="-G $GROUP"
    elif [ ! -z "$GID_VAL" ]; then
      addgroup -g "$GID_VAL" "$NAME"
      GROUP_OPT="-G $NAME"
      GROUP=$NAME
    fi
  fi

  echo -e "$PASS\n$PASS" | adduser -h "$FOLDER" -s /sbin/nologin $UID_OPT $GROUP_OPT "$NAME" 2>/dev/null
  chown "$NAME:$GROUP" "$FOLDER" 2>/dev/null || true
  echo "  [env] $NAME -> $FOLDER"

  unset NAME PASS FOLDER UID_VAL GID_VAL UID_OPT GROUP_OPT GROUP
done

# ── 2. Restaura usuários persistidos da API (users.json) ─────────────────

if [ -f /data/users.json ]; then
  echo "Restaurando usuários da base persistente..."
  /opt/venv/bin/python3 << 'PYEOF'
import json, subprocess, os, pwd as pwdmod

try:
    db = json.load(open("/data/users.json"))
except Exception:
    db = {"users": {}}

for username, info in db.get("users", {}).items():
    home = info.get("home", f"/ftp/{username}")
    password = info.get("password", "")
    os.makedirs(home, exist_ok=True)

    try:
        pwdmod.getpwnam(username)
        # Usuário já existe (do USERS env), atualiza senha se disponível
        if password:
            subprocess.run(["chpasswd"], input=f"{username}:{password}\n",
                           text=True, check=False)
        print(f"  [db/update] {username}")
    except KeyError:
        # Cria novo usuário
        subprocess.run(["addgroup", "-S", username],
                       capture_output=True, check=False)
        r = subprocess.run(["adduser", "-D", "-h", home, "-s", "/sbin/nologin",
                         "-G", username, username],
                        capture_output=True, check=False)
        if password:
            subprocess.run(["chpasswd"], input=f"{username}:{password}\n",
                           text=True, check=False)
        subprocess.run(["chown", f"{username}:{username}", home],
                       capture_output=True, check=False)
        print(f"  [db/create] {username} -> {home}")
PYEOF
else
  echo "Nenhum users.json encontrado. Use a API/UI para criar clientes."
fi

# ── 3. Configurações de portas passivas ───────────────────────────────────

if [ -z "$MIN_PORT" ]; then
  MIN_PORT=50000
fi

if [ -z "$MAX_PORT" ]; then
  MAX_PORT=50100
fi

# Configuração do endereço passivo
if [ ! -z "$ADDRESS" ]; then
  ADDR_OPT="-opasv_address=$ADDRESS"
fi

# Configuração TLS
if [ ! -z "$TLS_CERT" ] || [ ! -z "$TLS_KEY" ]; then
  TLS_OPT="-orsa_cert_file=$TLS_CERT -orsa_private_key_file=$TLS_KEY -ossl_enable=YES -oallow_anon_ssl=NO -oforce_local_data_ssl=YES -oforce_local_logins_ssl=YES -ossl_tlsv1=NO -ossl_sslv2=NO -ossl_sslv3=NO -ossl_ciphers=HIGH"
fi

# ── 4. Inicia serviços ───────────────────────────────────────────────────

# API de gerenciamento (background)
/opt/venv/bin/python -m uvicorn manager_api:app --app-dir /opt/ftp-manager --host "$MANAGER_HOST" --port "$MANAGER_PORT" &

echo "FTP Manager API iniciada em $MANAGER_HOST:$MANAGER_PORT"
echo "Iniciando vsftpd..."

# Inicia vsftpd
if [ ! -z "$1" ]; then
  exec "$@"
else
  vsftpd -opasv_min_port=$MIN_PORT -opasv_max_port=$MAX_PORT $ADDR_OPT $TLS_OPT /etc/vsftpd/vsftpd.conf
  [ -d /var/run/vsftpd ] || mkdir /var/run/vsftpd
  pgrep vsftpd | tail -n 1 >/var/run/vsftpd/vsftpd.pid
  exec pidproxy /var/run/vsftpd/vsftpd.pid true
fi
