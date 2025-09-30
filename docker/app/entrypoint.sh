#!/bin/bash
set -e

echo "=========================================="
echo "DECIMER.ai Container Initialization"
echo "=========================================="

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
chown -R www-data:www-data /var/www/app/storage 2>/dev/null || true

# Optimize Laravel for production
echo "→ Optimizing Laravel..."
php artisan config:cache 2>/dev/null || true
php artisan route:cache 2>/dev/null || true
php artisan view:cache 2>/dev/null || true

# Create log file
touch /var/www/app/storage/logs/laravel.log 2>/dev/null || true
chown www-data:www-data /var/www/app/storage/logs/laravel.log 2>/dev/null || true

echo "=========================================="
echo "✓ Setup complete"
echo "Starting: $@"
echo "=========================================="

# Execute whatever command was passed
exec "$@"