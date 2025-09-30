#!/bin/sh
set -e

echo "=========================================="
echo "→ Setting permissions..."
echo "=========================================="
chown -R www-data:www-data /var/www/app/storage /var/www/app/bootstrap/cache
chmod -R 775 /var/www/app/storage /var/www/app/bootstrap/cache

echo "→ Creating storage symlink..."
rm -f /var/www/app/public/storage
ln -sf /var/www/app/storage/app/public /var/www/app/public/storage

if [ -L /var/www/app/public/storage ]; then
    echo "✓ Symlink created successfully"
else
    echo "✗ Failed to create symlink"
    exit 1
fi

echo "→ Ensuring directories exist..."
mkdir -p /var/www/app/storage/app/public/media
mkdir -p /var/www/app/storage/logs
mkdir -p /var/www/app/storage/framework/sessions
mkdir -p /var/www/app/storage/framework/views
mkdir -p /var/www/app/storage/framework/cache
mkdir -p /var/www/app/bootstrap/cache

echo "→ Setting final permissions..."
chown -R www-data:www-data /var/www/app/storage
chown -R www-data:www-data /var/www/app/bootstrap/cache
chmod -R 775 /var/www/app/storage
chmod -R 775 /var/www/app/bootstrap/cache

echo "→ Optimizing Laravel..."
php artisan config:clear
php artisan config:cache
php artisan route:clear
php artisan route:cache
php artisan view:clear
php artisan view:cache

echo "=========================================="
echo "✓ Setup complete"
echo "Starting: php-fpm"
echo "=========================================="

php-fpm && supervisord