#!/bin/bash

# Verification script for CRM Escort AI
# This script verifies that all components are properly connected and working

set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Function to print colored output
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "ℹ $1"
}

# Test counters
TESTS_RUN=0
TESTS_PASSED=0

run_test() {
    local test_name="$1"
    local test_command="$2"
    
    ((TESTS_RUN++))
    echo ""
    print_info "Running test: $test_name"
    
    if eval "$test_command"; then
        print_success "$test_name"
        ((TESTS_PASSED++))
        return 0
    else
        print_error "$test_name"
        return 1
    fi
}

# Test 1: Check if Docker is running
test_docker() {
    docker info >/dev/null 2>&1
}

# Test 2: Check if services are up
test_services_up() {
    docker-compose ps | grep -E "(backend|postgres|redis|worker)" | grep -q "Up"
}

# Test 3: Health check endpoint
test_health_endpoint() {
    curl -f -s http://localhost:8000/health | grep -q '"status":"healthy"'
}

# Test 4: Database connection
test_database_connection() {
    docker-compose exec -T postgres pg_isready -U crm_user -d crm_escort >/dev/null 2>&1
}

# Test 5: Redis connection
test_redis_connection() {
    docker-compose exec -T redis redis-cli ping | grep -q "PONG"
}

# Test 6: API docs endpoint
test_api_docs() {
    curl -f -s http://localhost:8000/docs >/dev/null 2>&1
}

# Test 7: Test user registration endpoint
test_user_registration() {
    local response=$(curl -s -X POST "http://localhost:8000/auth/register" \
        -H "Content-Type: application/json" \
        -d '{"username":"testuser","email":"test@example.com","password":"testpassword123"}')
    
    # Check if response contains user_id or if user already exists
    echo "$response" | grep -q -E '("user_id"|"already exists")'
}

# Test 8: Test message creation endpoint (with auth)
test_message_creation() {
    # First register/login to get token
    local login_response=$(curl -s -X POST "http://localhost:8000/auth/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "username=testuser&password=testpassword123")
    
    if echo "$login_response" | grep -q "access_token"; then
        local token=$(echo "$login_response" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
        
        # Try to create a message
        local message_response=$(curl -s -X POST "http://localhost:8000/messages/" \
            -H "Authorization: Bearer $token" \
            -H "Content-Type: application/json" \
            -d '{"content":"Test message","sender_phone":"+1234567890"}')
        
        echo "$message_response" | grep -q -E '("message_id"|"id")'
    else
        # If login fails, try registration first
        curl -s -X POST "http://localhost:8000/auth/register" \
            -H "Content-Type: application/json" \
            -d '{"username":"testuser","email":"test@example.com","password":"testpassword123"}' >/dev/null
        
        # Then try login again
        test_message_creation
    fi
}

# Test 9: Test worker is processing tasks
test_worker_processing() {
    docker-compose exec -T worker celery -A app.workers.worker inspect ping | grep -q "pong"
}

# Test 10: Test OpenAI integration (if API key is set)
test_openai_integration() {
    if [ -n "$OPENAI_API_KEY" ]; then
        # Check if OpenAI module can be imported and initialized
        docker-compose exec -T backend python -c "
import os
from app.ai_extractor import AIExtractor
try:
    extractor = AIExtractor()
    print('OpenAI integration OK')
except Exception as e:
    print(f'OpenAI integration failed: {e}')
    exit(1)
" | grep -q "OpenAI integration OK"
    else
        print_warning "OPENAI_API_KEY not set, skipping OpenAI test"
        return 0
    fi
}

# Main verification function
main() {
    echo "🔍 CRM Escort AI - System Verification"
    echo "====================================="
    
    # Load environment if available
    if [ -f ".env" ]; then
        export $(cat .env | grep -v '^#' | xargs)
    fi
    
    if [ -f ".env.production" ]; then
        export $(cat .env.production | grep -v '^#' | xargs)
    fi
    
    # Run all tests
    run_test "Docker is running" "test_docker"
    run_test "Services are up" "test_services_up"
    run_test "Health endpoint accessible" "test_health_endpoint"
    run_test "Database connection" "test_database_connection"
    run_test "Redis connection" "test_redis_connection"
    run_test "API documentation accessible" "test_api_docs"
    run_test "User registration endpoint" "test_user_registration"
    run_test "Message creation endpoint" "test_message_creation"
    run_test "Worker is processing" "test_worker_processing"
    run_test "OpenAI integration" "test_openai_integration"
    
    echo ""
    echo "============================================"
    echo "Test Results: $TESTS_PASSED/$TESTS_RUN tests passed"
    
    if [ "$TESTS_PASSED" -eq "$TESTS_RUN" ]; then
        print_success "All tests passed! 🎉"
        print_info "Your CRM Escort AI system is fully operational and ready for production!"
        echo ""
        print_info "Access points:"
        echo "  • API: http://localhost:8000"
        echo "  • API Docs: http://localhost:8000/docs"
        echo "  • Health Check: http://localhost:8000/health"
        return 0
    else
        local failed=$((TESTS_RUN - TESTS_PASSED))
        print_error "$failed test(s) failed"
        print_warning "Please check the failed tests and fix any issues before production deployment"
        return 1
    fi
}

# Handle script arguments
case "${1:-}" in
    "quick")
        echo "🔍 Quick health check..."
        run_test "Health endpoint" "test_health_endpoint"
        ;;
    "database")
        echo "🔍 Database tests..."
        run_test "Database connection" "test_database_connection"
        run_test "User registration" "test_user_registration"
        ;;
    "api")
        echo "🔍 API tests..."
        run_test "Health endpoint" "test_health_endpoint"
        run_test "API docs" "test_api_docs"
        run_test "User registration" "test_user_registration"
        run_test "Message creation" "test_message_creation"
        ;;
    "")
        main
        ;;
    *)
        echo "Usage: $0 [quick|database|api]"
        echo ""
        echo "Commands:"
        echo "  (no args)  - Run all verification tests"
        echo "  quick      - Quick health check"
        echo "  database   - Database connectivity tests"
        echo "  api        - API endpoint tests"
        exit 1
        ;;
esac
