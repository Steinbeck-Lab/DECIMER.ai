#!/bin/bash
set -e

echo "=========================================="
echo "DECIMER.ai Windows Container Init"
echo "=========================================="

# Function to clear all Laravel caches
clear_all_caches() {
    echo "→ Clearing all caches..."
    php artisan config:clear 2>/dev/null || true
    php artisan cache:clear 2>/dev/null || true
    php artisan route:clear 2>/dev/null || true
    php artisan view:clear 2>/dev/null || true
    rm -rf bootstrap/cache/*.php 2>/dev/null || true
    rm -rf storage/framework/cache/data/* 2>/dev/null || true
    rm -rf storage/framework/views/* 2>/dev/null || true
    echo "  ✓ Caches cleared"
}

# Ensure .env exists
if [ ! -f .env ]; then
    echo "→ Creating .env from .env.example..."
    cp .env.example .env
fi

# Get current APP_KEY value (trim whitespace and carriage returns)
CURRENT_KEY=$(grep "^APP_KEY=" .env 2>/dev/null | cut -d'=' -f2 | tr -d '\r\n\t ' || echo "")

# Check if APP_KEY is valid
if [ -z "$CURRENT_KEY" ] || ! echo "$CURRENT_KEY" | grep -qE "^base64:[A-Za-z0-9+/]{43}=$"; then
    echo "→ APP_KEY is missing or invalid"
    echo "  Current: '$CURRENT_KEY'"
    
    # Clear all caches before generating new key
    clear_all_caches
    
    # Generate new APP_KEY
    echo "→ Generating new APP_KEY..."
    php artisan key:generate --force --no-interaction
    
    # Verify new key
    NEW_KEY=$(grep "^APP_KEY=" .env 2>/dev/null | cut -d'=' -f2 | tr -d '\r\n\t ' || echo "")
    if [ -z "$NEW_KEY" ] || ! echo "$NEW_KEY" | grep -qE "^base64:[A-Za-z0-9+/]{43}=$"; then
        echo "  ✗ ERROR: Failed to generate valid APP_KEY!"
        echo "  Manual fix required: docker-compose exec app php artisan key:generate --force"
        sleep 5
    else
        echo "  ✓ APP_KEY generated successfully"
        # Clear caches again after key generation
        clear_all_caches
    fi
else
    echo "→ APP_KEY is valid"
    # Always clear config cache on startup to prevent stale cache issues
    echo "→ Clearing config cache (startup)..."
    php artisan config:clear 2>/dev/null || true
fi

# Set permissions
echo "→ Setting permissions..."
chown -R www-data:www-data storage bootstrap/cache 2>/dev/null || true
chmod -R 775 storage bootstrap/cache 2>/dev/null || true

# Create storage symlink
echo "→ Creating storage symlink..."
if [ ! -L public/storage ]; then
    php artisan storage:link 2>/dev/null || echo "  (symlink skipped)"
fi

# Ensure required directories exist
echo "→ Ensuring directories exist..."
mkdir -p storage/app/public/media 2>/dev/null || true
mkdir -p storage/app/public/reported_results 2>/dev/null || true
mkdir -p storage/framework/cache/data 2>/dev/null || true
mkdir -p storage/framework/sessions 2>/dev/null || true
mkdir -p storage/framework/views 2>/dev/null || true
mkdir -p storage/logs 2>/dev/null || true
chown -R www-data:www-data storage 2>/dev/null || true

# Only cache configuration for php-fpm (not supervisor or other services)
if [ "$1" = "php-fpm" ]; then
    echo "→ Caching optimized configuration..."
    php artisan config:cache 2>/dev/null || true
    php artisan route:cache 2>/dev/null || true
    php artisan view:cache 2>/dev/null || true
    echo "  ✓ Configuration cached"
else
    echo "→ Skipping config cache (service: $1)"
fi

# Create/ensure log file exists
touch storage/logs/laravel.log 2>/dev/null || true
chown www-data:www-data storage/logs/laravel.log 2>/dev/null || true

# Display final status
FINAL_KEY=$(grep "^APP_KEY=" .env 2>/dev/null | cut -d'=' -f2 | tr -d '\r\n\t ' || echo "")
echo "=========================================="
echo "✓ Initialization Complete"
echo "  APP_KEY: ${FINAL_KEY:0:20}..."
echo "  Starting: $@"
echo "=========================================="

# Execute the main container command
exec "$@"