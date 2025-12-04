#!/bin/bash

# Advanced Production Deployment Script for CRM Escort AI
# This script handles complete production deployment with security hardening

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
DOCKER_COMPOSE_FILE="docker-compose.prod.yml"
BACKUP_DIR="backups/$(date +%Y%m%d_%H%M%S)"
LOG_DIR="logs"
SSL_DIR="ssl"

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    # Check Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        log_error "Docker Compose is not installed. Please install Docker Compose first."
        exit 1
    fi
    
    # Check if running as root (not recommended for production)
    if [[ $EUID -eq 0 ]]; then
        log_warning "Running as root is not recommended for production deployments."
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
    
    log_success "Prerequisites check passed"
}

create_directories() {
    log_info "Creating necessary directories..."
    
    mkdir -p "$BACKUP_DIR"
    mkdir -p "$LOG_DIR"
    mkdir -p "$SSL_DIR"
    mkdir -p "data/postgres"
    mkdir -p "data/redis"
    mkdir -p "uploads"
    
    log_success "Directories created"
}

setup_environment() {
    log_info "Setting up environment configuration..."
    
    if [[ ! -f .env.prod ]]; then
        log_warning ".env.prod file not found. Creating template..."
        cat > .env.prod << EOF
# Production Environment Configuration
# WARNING: Update all values before deploying to production!

# Application
ENVIRONMENT=production
DEBUG=false
SECRET_KEY=CHANGE_THIS_TO_RANDOM_SECRET_KEY
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com

# Database
DATABASE_URL=postgresql+asyncpg://crm_user:secure_password@postgres:5432/crm_prod
POSTGRES_DB=crm_prod
POSTGRES_USER=crm_user
POSTGRES_PASSWORD=CHANGE_THIS_PASSWORD

# Redis
REDIS_URL=redis://redis:6379/0

# Security
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
CORS_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# External APIs
OPENAI_API_KEY=your_openai_api_key_here
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=+1234567890

# Email (optional)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SENDER_NAME=CRM Escort AI
SENDER_EMAIL=noreply@yourdomain.com

# Calendar Integration (optional)
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
MICROSOFT_CLIENT_ID=your_microsoft_client_id
MICROSOFT_CLIENT_SECRET=your_microsoft_client_secret

# SSL/TLS
SSL_CERT_PATH=/etc/ssl/certs/fullchain.pem
SSL_KEY_PATH=/etc/ssl/private/privkey.pem

# Monitoring
LOG_LEVEL=info
SENTRY_DSN=your_sentry_dsn_for_error_tracking

# Backup
BACKUP_ENABLED=true
BACKUP_SCHEDULE=0 2 * * *
BACKUP_RETENTION_DAYS=30
EOF
        log_warning "Please edit .env.prod with your actual configuration values!"
        exit 1
    fi
    
    # Validate critical environment variables
    source .env.prod
    
    if [[ "$SECRET_KEY" == "CHANGE_THIS_TO_RANDOM_SECRET_KEY" ]]; then
        log_error "SECRET_KEY must be changed from default value!"
        exit 1
    fi
    
    if [[ "$POSTGRES_PASSWORD" == "CHANGE_THIS_PASSWORD" ]]; then
        log_error "POSTGRES_PASSWORD must be changed from default value!"
        exit 1
    fi
    
    log_success "Environment configuration loaded"
}

setup_ssl() {
    log_info "Setting up SSL certificates..."
    
    if [[ ! -f "$SSL_DIR/fullchain.pem" || ! -f "$SSL_DIR/privkey.pem" ]]; then
        log_warning "SSL certificates not found in $SSL_DIR/"
        log_info "Options:"
        echo "1. Use Let's Encrypt (recommended)"
        echo "2. Provide your own certificates"
        echo "3. Skip SSL setup (development only)"
        read -p "Choose option (1-3): " ssl_option
        
        case $ssl_option in
            1)
                setup_letsencrypt
                ;;
            2)
                log_info "Please copy your SSL certificates to:"
                log_info "  Certificate: $SSL_DIR/fullchain.pem"
                log_info "  Private Key: $SSL_DIR/privkey.pem"
                exit 1
                ;;
            3)
                log_warning "Skipping SSL setup - NOT RECOMMENDED FOR PRODUCTION!"
                ;;
            *)
                log_error "Invalid option"
                exit 1
                ;;
        esac
    else
        log_success "SSL certificates found"
    fi
}

setup_letsencrypt() {
    log_info "Setting up Let's Encrypt SSL certificates..."
    
    read -p "Enter your domain name (e.g., yourdomain.com): " domain
    read -p "Enter your email address: " email
    
    # Install certbot if not present
    if ! command -v certbot &> /dev/null; then
        log_info "Installing certbot..."
        if command -v apt-get &> /dev/null; then
            sudo apt-get update && sudo apt-get install -y certbot
        elif command -v yum &> /dev/null; then
            sudo yum install -y certbot
        else
            log_error "Could not install certbot automatically. Please install it manually."
            exit 1
        fi
    fi
    
    # Generate certificate
    log_info "Generating SSL certificate for $domain..."
    sudo certbot certonly --standalone \
        --email "$email" \
        --agree-tos \
        --non-interactive \
        -d "$domain"
    
    # Copy certificates to our SSL directory
    sudo cp "/etc/letsencrypt/live/$domain/fullchain.pem" "$SSL_DIR/"
    sudo cp "/etc/letsencrypt/live/$domain/privkey.pem" "$SSL_DIR/"
    sudo chown $(whoami):$(whoami) "$SSL_DIR/"*.pem
    
    log_success "SSL certificates generated and installed"
}

backup_database() {
    if [[ -z "${1:-}" ]]; then
        return 0  # Skip backup if no container running
    fi
    
    local container_name="$1"
    log_info "Creating database backup..."
    
    docker exec "$container_name" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$BACKUP_DIR/database_backup.sql"
    
    if [[ -f "$BACKUP_DIR/database_backup.sql" ]]; then
        log_success "Database backup created: $BACKUP_DIR/database_backup.sql"
    else
        log_error "Failed to create database backup"
        return 1
    fi
}

deploy_application() {
    log_info "Deploying CRM Escort AI application..."
    
    # Pull latest images
    log_info "Pulling latest Docker images..."
    docker-compose -f "$DOCKER_COMPOSE_FILE" pull
    
    # Stop existing services
    log_info "Stopping existing services..."
    docker-compose -f "$DOCKER_COMPOSE_FILE" down
    
    # Start services
    log_info "Starting services..."
    docker-compose -f "$DOCKER_COMPOSE_FILE" up -d
    
    # Wait for services to be ready
    log_info "Waiting for services to be ready..."
    sleep 30
    
    # Run database migrations
    log_info "Running database migrations..."
    docker-compose -f "$DOCKER_COMPOSE_FILE" exec -T backend alembic upgrade head
    
    log_success "Application deployed successfully"
}

verify_deployment() {
    log_info "Verifying deployment..."
    
    # Check if services are running
    local services=$(docker-compose -f "$DOCKER_COMPOSE_FILE" ps --services)
    for service in $services; do
        if docker-compose -f "$DOCKER_COMPOSE_FILE" ps "$service" | grep -q "Up"; then
            log_success "$service is running"
        else
            log_error "$service is not running"
            return 1
        fi
    done
    
    # Check application health
    local max_attempts=30
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -f -s http://localhost/health > /dev/null; then
            log_success "Application is responding to health checks"
            break
        fi
        
        ((attempt++))
        log_info "Attempt $attempt/$max_attempts: Waiting for application to respond..."
        sleep 2
    done
    
    if [[ $attempt -eq $max_attempts ]]; then
        log_error "Application is not responding after $max_attempts attempts"
        return 1
    fi
    
    log_success "Deployment verification completed successfully"
}

setup_monitoring() {
    log_info "Setting up monitoring and logging..."
    
    # Setup log rotation
    cat > /etc/logrotate.d/crm-escort-ai << EOF
$LOG_DIR/*.log {
    daily
    missingok
    rotate 30
    compress
    notifempty
    create 644 $(whoami) $(whoami)
    copytruncate
}
EOF
    
    # Setup health check cron job
    (crontab -l 2>/dev/null; echo "*/5 * * * * curl -f http://localhost/health || echo 'Health check failed' >> $LOG_DIR/healthcheck.log") | crontab -
    
    log_success "Monitoring and logging setup completed"
}

setup_firewall() {
    log_info "Configuring firewall rules..."
    
    if command -v ufw &> /dev/null; then
        # Allow SSH
        sudo ufw allow ssh
        
        # Allow HTTP and HTTPS
        sudo ufw allow 80
        sudo ufw allow 443
        
        # Deny all other incoming connections
        sudo ufw --force enable
        
        log_success "Firewall configured with UFW"
    elif command -v firewalld &> /dev/null; then
        # Enable firewalld
        sudo systemctl enable firewalld
        sudo systemctl start firewalld
        
        # Allow services
        sudo firewall-cmd --permanent --add-service=http
        sudo firewall-cmd --permanent --add-service=https
        sudo firewall-cmd --permanent --add-service=ssh
        sudo firewall-cmd --reload
        
        log_success "Firewall configured with firewalld"
    else
        log_warning "No firewall tool found. Please configure firewall manually."
    fi
}

cleanup_old_images() {
    log_info "Cleaning up old Docker images..."
    
    # Remove dangling images
    docker image prune -f
    
    # Remove unused images older than 24 hours
    docker image prune -a --filter "until=24h" -f
    
    log_success "Docker cleanup completed"
}

main() {
    log_info "Starting CRM Escort AI production deployment..."
    
    check_prerequisites
    create_directories
    setup_environment
    setup_ssl
    
    # Get existing database container name for backup
    existing_container=$(docker-compose -f "$DOCKER_COMPOSE_FILE" ps -q postgres 2>/dev/null || echo "")
    
    if [[ -n "$existing_container" ]]; then
        backup_database "$existing_container"
    fi
    
    deploy_application
    verify_deployment
    setup_monitoring
    setup_firewall
    cleanup_old_images
    
    log_success "🚀 CRM Escort AI has been successfully deployed to production!"
    log_info "Application is available at: https://$(hostname -f)"
    log_info "Health check endpoint: https://$(hostname -f)/health"
    log_info "API documentation: https://$(hostname -f)/docs"
    log_info ""
    log_info "Next steps:"
    log_info "1. Configure your DNS to point to this server"
    log_info "2. Test all functionality thoroughly"
    log_info "3. Set up regular backups"
    log_info "4. Configure monitoring alerts"
    log_info "5. Review security settings"
    log_info ""
    log_info "Logs are stored in: $LOG_DIR"
    log_info "Backups are stored in: $BACKUP_DIR"
}

# Run main function
main "$@"