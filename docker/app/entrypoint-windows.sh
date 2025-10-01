#!/bin/bash
set -e

echo "=========================================="
echo "DECIMER.ai Container Initialization (Windows)"
echo "=========================================="

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    echo "→ Creating .env file from .env.example..."
    cp .env.example .env
fi

# Check if APP_KEY exists and is valid
APP_KEY_VALUE=$(grep "^APP_KEY=" .env 2>/dev/null | cut -d'=' -f2 | tr -d '\r\n' || echo "")

if [ -z "$APP_KEY_VALUE" ] || [ "$APP_KEY_VALUE" = "" ] || ! echo "$APP_KEY_VALUE" | grep -q "^base64:"; then
    echo "→ APP_KEY is missing or invalid, generating new key..."
    
    # Clear all caches first
    echo "→ Clearing all caches..."
    php artisan cache:clear 2>/dev/null || true
    php artisan config:clear 2>/dev/null || true
    php artisan route:clear 2>/dev/null || true
    php artisan view:clear 2>/dev/null || true
    rm -rf bootstrap/cache/*.php 2>/dev/null || true
    rm -rf storage/framework/cache/data/* 2>/dev/null || true
    
    # Generate new key
    php artisan key:generate --no-interaction --force
    
    echo "✓ APP_KEY generated successfully"
    
    # Verify the key was generated
    NEW_KEY=$(grep "^APP_KEY=" .env 2>/dev/null | cut -d'=' -f2 | tr -d '\r\n' || echo "")
    if [ -z "$NEW_KEY" ] || ! echo "$NEW_KEY" | grep -q "^base64:"; then
        echo "ERROR: Failed to generate valid APP_KEY!"
        exit 1
    fi
else
    echo "✓ APP_KEY already set and valid"
fi

# Fix permissions (important for mounted volumes)
echo "→ Setting permissions..."
chown -R www-data:www-data /var/www/app/storage /var/www/app/bootstrap/cache 2>/dev/null || true
chmod -R 775 /var/www/app/storage /var/www/app/bootstrap/cache 2>/dev/null || true

# Create storage symlink
echo "→ Creating storage symlink..."
if [ ! -L /var/www/app/public/storage ]; then
    php artisan storage:link 2>/dev/null || echo "  (symlink creation skipped)"
fi

# Ensure directories exist
echo "→ Ensuring directories exist..."
mkdir -p /var/www/app/storage/app/public/reported_results 2>/dev/null || true
mkdir -p /var/www/app/storage/app/public/media 2>/dev/null || true
chown -R www-data:www-data /var/www/app/storage 2>/dev/null || true

# Only cache config if running php-fpm (not for queue workers or supervisor)
if [ "$1" = "php-fpm" ]; then
    echo "→ Optimizing Laravel..."
    # Clear first to ensure no stale cache
    php artisan config:clear 2>/dev/null || true
    php artisan route:clear 2>/dev/null || true
    php artisan view:clear 2>/dev/null || true
    
    # Then cache
    php artisan config:cache 2>/dev/null || true
    php artisan route:cache 2>/dev/null || true
    php artisan view:cache 2>/dev/null || true
else
    echo "→ Skipping cache optimization (not php-fpm)"
fi

# Create log file
touch /var/www/app/storage/logs/laravel.log 2>/dev/null || true
chown www-data:www-data /var/www/app/storage/logs/laravel.log 2>/dev/null || true

echo "=========================================="
echo "✓ Setup complete"
DISPLAY_KEY=$(grep '^APP_KEY=' .env | head -c 40 | tr -d '\r\n')
echo "APP_KEY: ${DISPLAY_KEY}..."
echo "Starting: $@"
echo "=========================================="

# Execute whatever command was passed
exec "$@"