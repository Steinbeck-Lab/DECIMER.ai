#!/bin/bash
set -e

echo "=========================================="
echo "DECIMER.ai Container Initialization (M1)"
echo "=========================================="

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    echo "→ Creating .env file from .env.example..."
    cp .env.example .env
fi

# Store old APP_KEY to detect changes
OLD_APP_KEY=""
if [ -f .env ]; then
    OLD_APP_KEY=$(grep "^APP_KEY=" .env | cut -d'=' -f2 || echo "")
fi

# Generate APP_KEY if not set or empty
if ! grep -q "APP_KEY=base64:" .env 2>/dev/null || grep -q "^APP_KEY=$" .env 2>/dev/null; then
    echo "→ Generating application key..."
    php artisan key:generate --no-interaction --force
    
    # Get new APP_KEY
    NEW_APP_KEY=$(grep "^APP_KEY=" .env | cut -d'=' -f2 || echo "")
    
    # If key changed, clear ALL caches
    if [ "$OLD_APP_KEY" != "$NEW_APP_KEY" ]; then
        echo "→ APP_KEY changed, clearing all caches..."
        php artisan cache:clear 2>/dev/null || true
        php artisan config:clear 2>/dev/null || true
        php artisan route:clear 2>/dev/null || true
        php artisan view:clear 2>/dev/null || true
        rm -rf bootstrap/cache/*.php 2>/dev/null || true
        echo "✓ All caches cleared"
    fi
    
    echo "✓ APP_KEY generated successfully"
else
    echo "✓ APP_KEY already set"
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
echo "APP_KEY: $(grep '^APP_KEY=' .env | head -c 40)..."
echo "Starting: $@"
echo "=========================================="

# Execute whatever command was passed
exec "$@"