#!/bin/bash

# Production deployment script for CRM Escort AI
# This script helps deploy the application to production environments

set -e  # Exit on any error

echo "🚀 Starting CRM Escort AI production deployment..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if required files exist
check_requirements() {
    print_status "Checking deployment requirements..."
    
    if [ ! -f ".env.production" ]; then
        print_error ".env.production file not found!"
        print_warning "Please create .env.production with all required environment variables."
        print_warning "See .env.production.example for reference."
        exit 1
    fi
    
    if [ ! -f "docker-compose.yml" ]; then
        print_error "docker-compose.yml not found!"
        exit 1
    fi
    
    # Check if Docker is running
    if ! docker info >/dev/null 2>&1; then
        print_error "Docker is not running or not installed!"
        exit 1
    fi
    
    # Check if Docker Compose is available
    if ! command -v docker-compose >/dev/null 2>&1; then
        print_error "Docker Compose is not installed!"
        exit 1
    fi
    
    print_status "All requirements satisfied ✓"
}

# Load environment variables
load_env() {
    print_status "Loading environment variables..."
    
    if [ -f ".env.production" ]; then
        export $(cat .env.production | grep -v '^#' | xargs)
        print_status "Environment variables loaded ✓"
    else
        print_error "Could not load .env.production file!"
        exit 1
    fi
}

# Validate environment variables
validate_env() {
    print_status "Validating environment variables..."
    
    required_vars=(
        "JWT_SECRET"
        "POSTGRES_PASSWORD"
        "OPENAI_API_KEY"
        "TWILIO_ACCOUNT_SID"
        "TWILIO_AUTH_TOKEN"
        "TWILIO_FROM_NUMBER"
    )
    
    missing_vars=()
    
    for var in "${required_vars[@]}"; do
        if [ -z "${!var}" ]; then
            missing_vars+=("$var")
        fi
    done
    
    if [ ${#missing_vars[@]} -gt 0 ]; then
        print_error "Missing required environment variables:"
        printf '%s\n' "${missing_vars[@]}"
        exit 1
    fi
    
    print_status "Environment variables validated ✓"
}

# Build and deploy
deploy() {
    print_status "Building and starting services..."
    
    # Pull latest images
    docker-compose pull
    
    # Build services
    docker-compose build --no-cache
    
    # Start services
    docker-compose up -d
    
    print_status "Waiting for services to be ready..."
    sleep 30
    
    # Check if services are healthy
    if docker-compose ps | grep -q "unhealthy\|Exit"; then
        print_error "Some services failed to start properly!"
        docker-compose logs
        exit 1
    fi
    
    print_status "Services started successfully ✓"
}

# Initialize database
init_database() {
    print_status "Initializing database..."
    
    # Wait for postgres to be ready
    print_status "Waiting for PostgreSQL to be ready..."
    until docker-compose exec postgres pg_isready -U crm_user -d crm_escort >/dev/null 2>&1; do
        sleep 2
    done
    
    # Run database initialization
    docker-compose exec backend python scripts/init_db.py
    
    print_status "Database initialized ✓"
}

# Run health checks
health_check() {
    print_status "Running health checks..."
    
    # Check backend health
    if curl -f http://localhost:8000/health >/dev/null 2>&1; then
        print_status "Backend health check passed ✓"
    else
        print_error "Backend health check failed!"
        return 1
    fi
    
    # Check if worker is running
    if docker-compose exec worker celery -A app.workers.worker inspect ping >/dev/null 2>&1; then
        print_status "Worker health check passed ✓"
    else
        print_warning "Worker health check failed - background tasks may not work"
    fi
    
    return 0
}

# Show deployment status
show_status() {
    print_status "Deployment Status:"
    echo ""
    docker-compose ps
    echo ""
    print_status "Application URLs:"
    echo "  Backend API: http://localhost:8000"
    echo "  API Documentation: http://localhost:8000/docs"
    echo "  Health Check: http://localhost:8000/health"
    
    if docker-compose --profile monitoring ps flower >/dev/null 2>&1; then
        echo "  Celery Monitor: http://localhost:5555"
    fi
}

# Main deployment flow
main() {
    echo "🚀 CRM Escort AI - Production Deployment"
    echo "========================================"
    
    check_requirements
    load_env
    validate_env
    
    print_status "Starting deployment process..."
    
    deploy
    init_database
    
    if health_check; then
        print_status "🎉 Deployment completed successfully!"
        show_status
    else
        print_error "Deployment completed with warnings!"
        show_status
        exit 1
    fi
}

# Handle script arguments
case "${1:-}" in
    "health")
        health_check
        ;;
    "status")
        show_status
        ;;
    "logs")
        docker-compose logs -f "${2:-}"
        ;;
    "stop")
        print_status "Stopping services..."
        docker-compose down
        print_status "Services stopped ✓"
        ;;
    "restart")
        print_status "Restarting services..."
        docker-compose restart
        print_status "Services restarted ✓"
        ;;
    "")
        main
        ;;
    *)
        echo "Usage: $0 [health|status|logs|stop|restart]"
        echo ""
        echo "Commands:"
        echo "  (no args)  - Full deployment"
        echo "  health     - Run health checks"
        echo "  status     - Show service status"
        echo "  logs       - Show service logs"
        echo "  stop       - Stop all services"
        echo "  restart    - Restart all services"
        exit 1
        ;;
esac