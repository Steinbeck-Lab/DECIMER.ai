ARG PHP_VERSION="8.2"
FROM php:${PHP_VERSION}-fpm-bookworm

# Install system dependencies
RUN apt-get update && apt-get -y --no-install-recommends install \
    git \
    curl \
    libpng-dev \
    libonig-dev \
    libxml2-dev \
    zip \
    unzip \
    supervisor\
    python3-pip \
    libjpeg-dev \
    zlib1g-dev \
    poppler-utils \
    ffmpeg \
    libsm6 \
    libxext6 \
    vim \
    default-jre \
    nodejs \
    npm \
    && apt-get autoremove --purge -y && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install PHP extensions
RUN docker-php-ext-install -j$(nproc) \
    opcache \
    intl \
    pdo_mysql \
    mbstring \
    exif \
    pcntl \
    bcmath \
    gd

# Install APCu
RUN pecl install apcu-5.1.20 && docker-php-ext-enable apcu

# Copy PHP configuration
COPY docker/app/php.ini $PHP_INI_DIR/conf.d/

# Install Composer
RUN curl -sS https://getcomposer.org/installer | \
    php -- --install-dir=/usr/local/bin --filename=composer

# Set working directory
WORKDIR /var/www/app

# Copy application files
COPY . /var/www/app

RUN npm install && npm run production

# Install Composer dependencies
RUN composer install --optimize-autoloader --no-interaction

# Copy requirements and install Python packages
COPY docker/requirements.txt /tmp/requirements.txt
RUN pip3 install --upgrade pip setuptools wheel --break-system-packages && \
    pip3 install -r /tmp/requirements.txt --break-system-packages && \
    rm /tmp/requirements.txt

# Install DECIMER packages
RUN pip3 install --no-deps --break-system-packages git+https://github.com/Kohulan/DECIMER-Image_Transformer && \
    pip3 install --no-deps --break-system-packages git+https://github.com/Kohulan/DECIMER-Image-Segmentation@v1.5.0 && \
    pip3 install --no-deps --break-system-packages git+https://github.com/Iagea/DECIMER-Image-Classifier@146b00be2fe6f8fa6670a4255969d4747502b7f2

# Create necessary directories
RUN mkdir -p \
    storage/app/public/media \
    storage/app/public/reported_results \
    storage/framework/cache/data \
    storage/framework/sessions \
    storage/framework/views \
    storage/logs \
    bootstrap/cache

# Set permissions
RUN chown -R www-data:www-data storage bootstrap/cache public && \
    chmod -R 775 storage bootstrap/cache

# Copy supervisor configuration
COPY docker/app/supervisor.conf /etc/supervisor/conf.d/worker.conf

# Create supervisor main config
RUN printf "[unix_http_server]\nfile=/var/run/supervisor.sock\nchmod=0700\n\n[supervisord]\nnodaemon=true\nlogfile=/var/www/app/storage/logs/supervisord.log\npidfile=/var/run/supervisord.pid\nchildlogdir=/var/www/app/storage/logs\nuser=root\nloglevel=info\n\n[rpcinterface:supervisor]\nsupervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface\n\n[supervisorctl]\nserverurl=unix:///var/run/supervisor.sock\n\n[include]\nfiles = /etc/supervisor/conf.d/*.conf\n" > /etc/supervisor/supervisord.conf

# Copy entrypoint script
COPY docker/app/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Set entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Default command
CMD ["php-fpm"]

EXPOSE 9000