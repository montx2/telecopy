@echo off
REM TELECOPY Build Script - Compilação automática para .exe
REM Execute este arquivo para compilar o programa

setlocal enabledelayedexpansion
cls

echo.
echo ╔════════════════════════════════════════════════════════════════════════════╗
echo ║          TELECOPY v3.0.1 — Script de Compilação / Build Script            ║
echo ╚════════════════════════════════════════════════════════════════════════════╝
echo.

echo ✓ Python encontrado: 
cd C:\Users\montx2\Desktop\Telecopy
echo

REM Verifica se Python está instalado
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ❌ ERRO: Python não encontrado no PATH
    echo.
    echo    Instale Python 3.10+ de: https://www.python.org/downloads/
    echo    IMPORTANTE: Marque a opção "Add Python to PATH" durante a instalação
    echo.
    pause
    exit /b 1
)

echo ✓ Python encontrado: 
python --version
echo.

REM Verifica/instala Nuitka
echo 📦 Verificando Nuitka...
python -m nuitka --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ⚠ Nuitka não está instalado. Instalando...
    pip install nuitka zstandard
    if %ERRORLEVEL% NEQ 0 (
        echo ❌ Erro ao instalar Nuitka
        pause
        exit /b 1
    )
)
echo ✓ Nuitka está pronto
echo.

REM Verifica dependências Python
echo 📦 Verificando dependências do projeto...
python -m pip install -q -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Erro ao instalar dependências
    echo    Execute: pip install -r requirements.txt
    pause
    exit /b 1
)
echo ✓ Dependências instaladas
echo.

REM Remove compilações anteriores
if exist "main.exe" (
    echo 🗑 Removendo compilação anterior...
    del /f /q "main.exe" >nul 2>&1
)
if exist "main.dist" (
    rmdir /s /q "main.dist" >nul 2>&1
)
echo.

REM Compila
echo 🔨 Compilando TELECOPY para .exe...
echo    (Isto pode levar 2-5 minutos na primeira execução)
echo.

python -m nuitka ^
    --onefile ^
    --console ^
    --windows-icon-from-ico=ico.ico ^
    --follow-imports ^
    --include-package=telethon ^
    --include-package=cryptography ^
    --include-package=psutil ^
    --include-package=rich ^
    --remove-output ^
    main.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ╔════════════════════════════════════════════════════════════════════════════╗
    echo ║                      ✅ COMPILAÇÃO CONCLUÍDA!                             ║
    echo ╚════════════════════════════════════════════════════════════════════════════╝
    echo.
    echo    📁 Arquivo gerado: main.exe
    echo    📍 Localização: %CD%\main.exe
    echo.
    echo    Para executar: double-click no main.exe OU execute:
    echo.
    echo       main.exe
    echo.
    echo    Se a janela desaparecer, procure por:
    echo       data\logs\startup.log
    echo.
    pause
    
    REM Oferece executar logo
    set /p RUN="Executar agora? (S/N): "
    if /i "!RUN!"=="S" (
        cls
        main.exe
    )
) else (
    echo.
    echo ╔════════════════════════════════════════════════════════════════════════════╗
    echo ║                  ❌ ERRO DURANTE COMPILAÇÃO                              ║
    echo ╚════════════════════════════════════════════════════════════════════════════╝
    echo.
    echo    Verifique as mensagens de erro acima.
    echo.
    echo    Se precisar de ajuda:
    echo    1. Verifique que Python 3.10+ está instalado
    echo    2. Execute: pip install -r requirements.txt
    echo    3. Tente novamente
    echo.
    pause
    exit /b 1
)
