# DECIMER.ai Dockerfile
# Updated with best practices and latest package versions
# Base image with PHP 8.2 (newer stable version compared to 7.4)
ARG PHP_VERSION="8.2"
FROM php:${PHP_VERSION}-fpm

# Metadata labels
LABEL maintainer="Kohulan Rajan"
LABEL description="DECIMER.ai - Chemical structure recognition from images"
LABEL version="2.0"

# Install system dependencies in a single layer to reduce image size
# Group related packages together for better readability
RUN apt-get update && apt-get -y --no-install-recommends install \
    # Version control and utilities
    git \
    curl \
    zip \
    unzip \
    vim \
    # PHP and image processing dependencies
    libpng-dev \
    libonig-dev \
    libxml2-dev \
    libzip-dev \
    libjpeg-dev \
    zlib1g-dev \
    # Process management
    supervisor \
    # Python and related dependencies
    python3-pip \
    # PDF processing
    poppler-utils \
    # Video and image processing libraries
    ffmpeg \
    libsm6 \
    libxext6 \
    # Java runtime for specific dependencies
    default-jre \
    # Clean up to reduce image size
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
    zip \
    gd

# Install PECL extensions
RUN pecl install apcu-5.1.22 && docker-php-ext-enable apcu

# Set up Python environment
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# Copy requirements file
COPY docker/requirements.txt /tmp/requirements.txt

# Install Python packages from requirements file
RUN pip3 install --upgrade pip setuptools wheel && \
    pip3 install -r /tmp/requirements.txt

# Install project-specific dependencies
# Using specific git commits to ensure reproducibility
# No-deps flag is preserved as it was in the original, assumably to avoid dependency conflicts
RUN pip3 install --no-deps git+https://github.com/Kohulan/DECIMER-Image_Transformer@3db69546ed706af2be474e774de888bd3a067c6e && \
    pip3 install --no-deps git+https://github.com/Kohulan/DECIMER-Image-Segmentation.git@bbox && \
    pip3 install --no-deps git+https://github.com/Iagea/DECIMER-Image-Classifier@146b00be2fe6f8fa6670a4255969d4747502b7f2

# Copy PHP configuration
COPY docker/app/php.ini $PHP_INI_DIR/conf.d/

# Install Composer
RUN curl -sS https://getcomposer.org/installer | \
    php -- --install-dir=/usr/local/bin --filename=composer

# Set working directory
WORKDIR /var/www/app

# Copy application files
COPY . /var/www/app

# Install PHP dependencies
RUN composer update --no-interaction

# Set up supervisor configuration
COPY docker/app/supervisor.conf /etc/supervisor/conf.d/worker.conf

# Define volume mount points if needed
VOLUME ["/var/www/app/storage", "/var/www/app/public/uploads"]

# Add entrypoint script if exists
COPY docker/app/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set default command
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
