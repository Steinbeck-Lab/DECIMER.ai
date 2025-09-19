ARG PHP_VERSION="8.2"
FROM php:${PHP_VERSION}-fpm-bookworm

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
    && apt-get autoremove --purge -y && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN apt-get update \
    && apt-get install -y libzip-dev \
    && docker-php-ext-install zip

RUN docker-php-ext-install -j$(nproc) \
    opcache \
    intl \
    pdo_mysql \
    mbstring \
    exif \
    pcntl \
    bcmath \
    gd

# Copy requirements file
COPY docker/requirements.txt /tmp/requirements.txt

# Install Python packages from requirements file
RUN pip3 install --upgrade pip setuptools wheel --break-system-packages && \
    pip3 install -r /tmp/requirements.txt --break-system-packages

RUN pip3 install --no-deps --break-system-packages git+https://github.com/Kohulan/DECIMER-Image_Transformer
RUN pip3 install --no-deps --break-system-packages git+https://github.com/Kohulan/DECIMER-Image-Segmentation@v1.5.0
RUN pip3 install --no-deps --break-system-packages git+https://github.com/Iagea/DECIMER-Image-Classifier@146b00be2fe6f8fa6670a4255969d4747502b7f2

RUN pecl install apcu-5.1.20 && docker-php-ext-enable apcu

COPY docker/app/php.ini   $PHP_INI_DIR/conf.d/

RUN curl -sS https://getcomposer.org/installer | \
    php -- --install-dir=/usr/local/bin --filename=composer

WORKDIR /var/www/app
COPY . /var/www/app

RUN composer update 
ADD docker/app/supervisor.conf /etc/supervisor/conf.d/worker.conf
