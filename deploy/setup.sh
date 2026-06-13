#!/bin/bash
# ==============================================================================
# Buykori AdSync — Droplet Auto Setup Script (Ubuntu 24.04 LTS)
# ==============================================================================
# Run this script as root: sudo ./setup.sh
# Make sure your codebase is cloned into /var/www/buykori-adsync before running.
# ==============================================================================

set -e

# Configuration variables
PROJECT_DIR="/var/www/buykori-adsync"
APP_USER="buykori"
DB_NAME="buykori_adsync"
DB_USER="buykori"
GENERATED_ADMIN_PASSWORD=""
GENERATED_ADMIN_API_KEY=""
GENERATED_ADMIN_JWT_SECRET=""

# Output helpers
info() { echo -e "\e[34m[INFO]\e[0m $1"; }
success() { echo -e "\e[32m[SUCCESS]\e[0m $1"; }
warn() { echo -e "\e[33m[WARNING]\e[0m $1"; }
error() { echo -e "\e[31m[ERROR]\e[0m $1"; exit 1; }

# Root privilege check
if [ "$EUID" -ne 0 ]; then
    error "Please run as root (use sudo)."
fi

# Step 0: Ensure project directory is correct
if [ ! -d "$PROJECT_DIR" ]; then
    error "Project directory $PROJECT_DIR does not exist. Please clone the repository there first."
fi

echo "======================================================================"
echo "🚀 Starting Buykori AdSync Server Auto-Setup..."
echo "======================================================================"

# Step 1: Create Dedicated System User
if id "$APP_USER" &>/dev/null; then
    info "System user '$APP_USER' already exists."
else
    info "Creating system user '$APP_USER'..."
    useradd -r -s /bin/false "$APP_USER"
    success "System user '$APP_USER' created."
fi

# Step 2: Install System Packages
info "Updating system package repositories..."
apt-get update -y

info "Installing dependencies (Python, PostgreSQL, Redis, Nginx, Supervisor, Certbot)..."
apt-get install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    postgresql \
    postgresql-contrib \
    libpq-dev \
    redis-server \
    nginx \
    supervisor \
    git \
    curl \
    ufw \
    certbot \
    python3-certbot-nginx

success "All system packages installed."

info "Enabling and starting Redis..."
systemctl enable redis-server
systemctl start redis-server
success "Redis is active."

# Step 3: Configure PostgreSQL
info "Configuring PostgreSQL database..."

read -p "Do you want to use an external PostgreSQL database (e.g. AWS RDS)? (y/N): " USE_EXTERNAL_DB
if [ "$USE_EXTERNAL_DB" = "y" ] || [ "$USE_EXTERNAL_DB" = "Y" ]; then
    read -p "Enter your external DATABASE_URL (e.g. postgresql://user:pass@host:port/db): " EXTERNAL_DATABASE_URL
    while [ -z "$EXTERNAL_DATABASE_URL" ]; do
        read -p "Database URL cannot be empty. Enter external DATABASE_URL: " EXTERNAL_DATABASE_URL
    done
    DATABASE_URL="$EXTERNAL_DATABASE_URL"
    DB_PASS="(External Database)"
    info "Using external database. Skipping local PostgreSQL setup."
else
    # Generate a strong random password for DB user
    DB_PASS=$(openssl rand -hex 16)

    # Create user and database
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" || true
    sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';"
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" || true
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
    
    DATABASE_URL="postgresql://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME"
    success "PostgreSQL database '$DB_NAME' and user '$DB_USER' set up successfully."
fi

# Step 4: Prompt for App Credentials and Environment Variables
echo "----------------------------------------------------------------------"
echo "⚙️ Configure Environment Variables"
echo "----------------------------------------------------------------------"

# 1. Primary Domain(s)
if [ -z "$PRIMARY_DOMAIN" ]; then
    read -p "Enter your Domain(s) (space-separated, e.g. api.buykori.app client.buykori.app track.buykori.app): " PRIMARY_DOMAIN
    if [ -z "$PRIMARY_DOMAIN" ]; then
        PRIMARY_DOMAIN="api.buykori.app"
    fi
fi

# 2. Admin Username
if [ -z "$ADMIN_USERNAME" ]; then
    read -p "Enter Admin Username [admin]: " ADMIN_USERNAME
    if [ -z "$ADMIN_USERNAME" ]; then
        ADMIN_USERNAME="admin"
    fi
fi

# 3. Admin Password
if [ -z "$ADMIN_PASSWORD" ]; then
    read -p "Enter Admin Password [auto-generate]: " ADMIN_PASSWORD
    if [ -z "$ADMIN_PASSWORD" ]; then
        ADMIN_PASSWORD=$(openssl rand -hex 12)
        GENERATED_ADMIN_PASSWORD="$ADMIN_PASSWORD"
        info "Generated Admin Password (not printed to logs)."
    fi
fi

if [[ "$ADMIN_PASSWORD" != pbkdf2_sha256\$* ]]; then
    info "Hashing Admin Password with PBKDF2 before writing .env..."
    ADMIN_PASSWORD=$(cd "$PROJECT_DIR" && ADMIN_PASSWORD_INPUT="$ADMIN_PASSWORD" python3 scripts/keys/hash_admin_password.py | sed 's/^ADMIN_PASSWORD=//')
fi

# 4. Admin API Key
if [ -z "$ADMIN_API_KEY" ]; then
    read -p "Enter Admin API Key [auto-generate]: " ADMIN_API_KEY
    if [ -z "$ADMIN_API_KEY" ]; then
        ADMIN_API_KEY=$(openssl rand -hex 24)
        GENERATED_ADMIN_API_KEY="$ADMIN_API_KEY"
        info "Generated Admin API Key (not printed to logs)."
    fi
fi

# 5. Admin JWT/CSRF signing secret
if [ -z "$ADMIN_JWT_SECRET" ]; then
    ADMIN_JWT_SECRET=$(openssl rand -hex 32)
    GENERATED_ADMIN_JWT_SECRET="$ADMIN_JWT_SECRET"
    info "Generated separate Admin JWT signing secret (not printed to logs)."
fi

if [ -n "$GENERATED_ADMIN_PASSWORD" ] || [ -n "$GENERATED_ADMIN_API_KEY" ] || [ -n "$GENERATED_ADMIN_JWT_SECRET" ]; then
    INITIAL_CREDS_FILE="$PROJECT_DIR/.initial-credentials"
    {
        echo "Buykori AdSync generated setup credentials"
        echo "Created: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "Remove this file after saving the values in a password manager."
        [ -n "$GENERATED_ADMIN_PASSWORD" ] && echo "Admin Password: $GENERATED_ADMIN_PASSWORD"
        [ -n "$GENERATED_ADMIN_API_KEY" ] && echo "Admin API Key: $GENERATED_ADMIN_API_KEY"
        [ -n "$GENERATED_ADMIN_JWT_SECRET" ] && echo "Admin JWT Secret: $GENERATED_ADMIN_JWT_SECRET"
    } > "$INITIAL_CREDS_FILE"
    chmod 600 "$INITIAL_CREDS_FILE"
    info "Generated credentials were written to $INITIAL_CREDS_FILE (chmod 600). Remove it after saving."
fi

# 5. Encryption Key using python Fernet
info "Generating Fernet encryption key..."
ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")

# 6. Redis URL
if [ -z "$REDIS_URL" ]; then
    REDIS_URL="redis://127.0.0.1:6379/0"
fi

# Create .env file
ENV_FILE="$PROJECT_DIR/.env"
info "Creating production .env file at $ENV_FILE..."

cat <<EOF > "$ENV_FILE"
# ------------------------------------------------------------------------------
# Buykori AdSync Production Environment Variables
# ------------------------------------------------------------------------------
DATABASE_URL="$DATABASE_URL"
REDIS_URL="$REDIS_URL"
APP_ENV="production"
ADMIN_USERNAME="$ADMIN_USERNAME"
ADMIN_PASSWORD="$ADMIN_PASSWORD"
ADMIN_API_KEY="$ADMIN_API_KEY"
ADMIN_JWT_SECRET="$ADMIN_JWT_SECRET"
ENCRYPTION_KEY="$ENCRYPTION_KEY"
PRIMARY_DOMAIN="$PRIMARY_DOMAIN"
ENABLE_DOCS=false
CSP_STRICT_NONCE=true
TRUST_PROXY_HEADERS=true
ALLOW_CAPI_API_KEY_SIGNING_FALLBACK=false
FRAUD_AUTO_HOLD_THRESHOLD=90
ALLOW_GLOBAL_COURIER_WEBHOOK_SECRET_FALLBACK=false
ALLOW_GLOBAL_STEADFAST_WEBHOOK_TOKEN_FALLBACK=false

# Database connection pool settings (tuned for 2GB RAM Droplet)
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
DB_POOL_RECYCLE=300
DB_POOL_TIMEOUT=10

# Worker Settings
EVENT_WORKER_BATCH_SIZE=10
EVENT_WORKER_POLL_SECONDS=3.0
EVENT_OUTBOX_MAX_ATTEMPTS=8
EOF

# Secure .env file permissions
chmod 600 "$ENV_FILE"
chown "$APP_USER:$APP_USER" "$ENV_FILE"
success ".env file generated and secured."

# Step 5: Setup Python Virtual Environment and Install Dependencies
info "Creating Python virtual environment in $PROJECT_DIR/venv..."
python3 -m venv "$PROJECT_DIR/venv"

info "Installing Python dependencies (this might take a minute)..."
"$PROJECT_DIR/venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
success "Dependencies installed in virtual environment."

# Step 6: Create logs directory and fix permissions
info "Setting up project permissions..."
mkdir -p /var/log/supervisor
chown -R "$APP_USER:$APP_USER" "$PROJECT_DIR"
# Make sure python virtual environment can write to site-packages if needed, and run logs
chmod -R 755 "$PROJECT_DIR"
success "Permissions configured."

# Run database initialization
info "Initializing database schema..."
cd "$PROJECT_DIR"
sudo -u "$APP_USER" ./venv/bin/python deploy/init_db.py
success "Database schema initialized and stamped with Alembic head."

# Step 7: Configure Nginx
info "Configuring Nginx Reverse Proxy..."
NGINX_TEMPLATE="$PROJECT_DIR/deploy/nginx.conf"
NGINX_CONF="/etc/nginx/sites-available/buykori-adsync"

if [ -f "$NGINX_TEMPLATE" ]; then
    cp "$NGINX_TEMPLATE" "$NGINX_CONF"
    # Replace DOMAIN_PLACEHOLDER with actual primary domain
    sed -i "s/DOMAIN_PLACEHOLDER/$PRIMARY_DOMAIN/g" "$NGINX_CONF"
    
    # Symlink to sites-enabled
    ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/
    
    # Remove default site config to prevent conflicts
    rm -f /etc/nginx/sites-enabled/default
    
    # Test and restart Nginx
    nginx -t
    systemctl restart nginx
    success "Nginx reverse proxy is active."
else
    warn "Nginx template at $NGINX_TEMPLATE not found. Skipping Nginx config copy."
fi

# Step 8: Configure Supervisor
info "Configuring Supervisor..."
SUPERVISOR_TEMPLATE="$PROJECT_DIR/deploy/supervisor.conf"
SUPERVISOR_CONF="/etc/supervisor/conf.d/buykori.conf"

if [ -f "$SUPERVISOR_TEMPLATE" ]; then
    cp "$SUPERVISOR_TEMPLATE" "$SUPERVISOR_CONF"
    
    # Reload supervisor and start processes
    supervisorctl reread
    supervisorctl update
    supervisorctl start buykori-web || true
    supervisorctl start buykori-worker || true
    
    # Show status
    supervisorctl status
    success "Supervisor processes initialized."
else
    warn "Supervisor template at $SUPERVISOR_TEMPLATE not found. Skipping Supervisor config copy."
fi

# Step 9: Configure Firewall (UFW)
info "Configuring firewall rules..."
ufw allow OpenSSH
ufw allow 'Nginx Full'
echo "y" | ufw enable
ufw status
success "Firewall rules updated and enabled."

# Step 10: SSL Configuration (Optional depending on DNS setup)
echo "----------------------------------------------------------------------"
echo "🔒 SSL / Let's Encrypt Setup"
echo "----------------------------------------------------------------------"
if [ -z "$DNS_READY" ]; then
    read -p "Is your DNS already pointed to this server IP ($PRIMARY_DOMAIN)? (y/n): " DNS_READY
fi
if [ "$DNS_READY" = "y" ] || [ "$DNS_READY" = "Y" ]; then
    # Expand space-separated domains into individual -d flags
    CERTBOT_DOMAINS=""
    FIRST_DOMAIN=""
    for domain in $PRIMARY_DOMAIN; do
        CERTBOT_DOMAINS="$CERTBOT_DOMAINS -d $domain"
        if [ -z "$FIRST_DOMAIN" ]; then
            FIRST_DOMAIN="$domain"
        fi
    done
    info "Requesting SSL Certificate from Let's Encrypt for: $PRIMARY_DOMAIN..."
    certbot --nginx $CERTBOT_DOMAINS --non-interactive --agree-tos --email "webmaster@$FIRST_DOMAIN" --redirect || warn "SSL generation failed. Ensure DNS is pointed and try again later using: certbot --nginx"
    success "SSL configured."
else
    # Build manual certbot command output
    CERTBOT_DOMAINS=""
    for domain in $PRIMARY_DOMAIN; do
        CERTBOT_DOMAINS="$CERTBOT_DOMAINS -d $domain"
    done
    warn "Skipping SSL setup for now. Once your DNS is pointed to this server, run:"
    echo "    sudo certbot --nginx$CERTBOT_DOMAINS"
fi

echo "======================================================================"
echo "🎉 Buykori AdSync Droplet Setup Completed Successfully!"
echo "======================================================================"
echo "📝 Credentials Summary:"
echo "----------------------------------------------------------------------"
echo "Primary Domain:   http://$PRIMARY_DOMAIN (SSL will be active after Certbot)"
echo "Admin Username:   $ADMIN_USERNAME"
echo "Admin Password:   stored as PBKDF2 hash in $ENV_FILE"
echo "Admin API Key:    [hidden]"
echo "Database Pass:    [hidden]"
echo "----------------------------------------------------------------------"
echo "⚠️  PLEASE COPY AND SAVE THESE CREDENTIALS IN A SECURE PLACE!"
echo "----------------------------------------------------------------------"
echo "You can deploy updates in the future by running:"
echo "    sudo ./deploy/deploy.sh"
echo "======================================================================"
