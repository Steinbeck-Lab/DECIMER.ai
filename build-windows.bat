@echo off
SETLOCAL EnableDelayedExpansion

echo ==========================================
echo DECIMER Windows Production Build
echo ==========================================

REM Check Docker is running
docker --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Docker is not running or not installed
    echo Please start Docker Desktop and try again
    pause
    exit /b 1
)

REM Stop existing containers
echo.
echo [1/7] Stopping existing containers...
docker-compose -f docker-compose.windows.yml down

REM Clean up
echo [2/7] Cleaning up local files...
if exist .env del /f .env 2>nul
if exist public\storage del /f /q public\storage 2>nul
if exist bootstrap\cache rd /s /q bootstrap\cache 2>nul
if exist storage\framework\cache rd /s /q storage\framework\cache 2>nul
if exist storage\framework\sessions rd /s /q storage\framework\sessions 2>nul
if exist storage\framework\views rd /s /q storage\framework\views 2>nul

REM Recreate directories
mkdir bootstrap\cache 2>nul
mkdir storage\framework\cache\data 2>nul
mkdir storage\framework\sessions 2>nul
mkdir storage\framework\views 2>nul

REM Create .dockerignore
echo [3/7] Creating .dockerignore...
(
echo **/.git
echo **/.gitignore
echo **/.env
echo **/.env.backup
echo **/node_modules/
echo **/vendor/
echo **/storage/framework/cache/
echo **/storage/framework/sessions/
echo **/storage/framework/views/
echo **/storage/logs/
echo **/storage/app/public/media/
echo **/bootstrap/cache/
echo **/public/storage
echo **/public/hot
echo **/.phpunit.result.cache
echo **/.DS_Store
echo **/Thumbs.db
echo **/.vscode/
echo **/.idea/
) > .dockerignore

REM Verify .dockerignore
if not exist .dockerignore (
    echo ERROR: Failed to create .dockerignore
    pause
    exit /b 1
)

REM Remove old Docker images
echo [4/7] Removing old Docker images...
docker rmi decimer-app-windows:latest 2>nul

REM Build
echo [5/7] Building Docker image...
echo   This will take 5-10 minutes on first build...
docker-compose -f docker-compose.windows.yml build --no-cache

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ==========================================
    echo ERROR: Build failed!
    echo ==========================================
    echo.
    echo Troubleshooting:
    echo 1. Check Docker Desktop is running
    echo 2. Check internet connection
    echo 3. Try: docker system prune -a
    echo.
    pause
    exit /b 1
)

REM Start containers
echo [6/7] Starting containers...
docker-compose -f docker-compose.windows.yml up -d

REM Wait for initialization
echo [7/7] Waiting for initialization...
echo   The entrypoint will generate APP_KEY automatically...
timeout /t 15 /nobreak >nul

REM Verify
echo.
echo ==========================================
echo Verifying Installation
echo ==========================================
echo.
echo Checking APP_KEY:
docker-compose -f docker-compose.windows.yml exec app cat .env | findstr APP_KEY

echo.
echo Container Status:
docker-compose -f docker-compose.windows.yml ps

echo.
echo ==========================================
echo SUCCESS! DECIMER is ready
echo ==========================================
echo.
echo Access: http://localhost:8080
echo.
echo Useful commands:
echo   Start:  docker-compose -f docker-compose.windows.yml up -d
echo   Stop:   docker-compose -f docker-compose.windows.yml down
echo   Logs:   docker-compose -f docker-compose.windows.yml logs -f
echo.
pause