@echo off
REM ════════════════════════════════════════════════════════════════════════════
REM  TELECOPY — script de compilação (gera um .exe único com Nuitka)
REM  Uso: duplo clique, ou "build.bat" no terminal.
REM ════════════════════════════════════════════════════════════════════════════
setlocal enabledelayedexpansion
cd /d "%~dp0"
cls
echo.
echo  ============================================================
echo    TELECOPY — compilacao do executavel
echo  ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo  [ERRO] Python nao encontrado no PATH.
    echo         Instale de https://www.python.org/downloads/
    echo         e marque "Add Python to PATH".
    pause
    exit /b 1
)
echo  Python:
python --version
echo.

echo  [1/4] Instalando dependencias...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo  [ERRO] Falha ao instalar dependencias.
    pause
    exit /b 1
)

echo.
python -c "import cryptg" >nul 2>&1
if errorlevel 1 (
    echo  [AVISO] cryptg nao pode ser importado; AES em Python puro limita a velocidade.
    echo          Instale com: python -m pip install cryptg
)

echo.
echo  [2/4] Instalando Nuitka e Zstandard...
python -m pip install --upgrade nuitka zstandard ordered-set
if errorlevel 1 (
    echo  [ERRO] Falha ao instalar o Nuitka.
    pause
    exit /b 1
)

echo.
echo  [3/4] Rodando a bateria de testes...
python tests\test_core.py >nul 2>&1
if errorlevel 1 (
    echo  [AVISO] Os testes do motor falharam. Rode "python tests\test_core.py" para ver.
    choice /c SN /m "Continuar mesmo assim"
    if errorlevel 2 exit /b 1
) else (
    python tests\test_app_flow.py >nul 2>&1
    if errorlevel 1 (
        echo  [AVISO] Os testes da interface falharam. Rode "python tests\test_app_flow.py" para ver.
        choice /c SN /m "Continuar mesmo assim"
        if errorlevel 2 exit /b 1
    ) else (
        echo  Testes do motor e da interface OK.
    )
)

echo.
echo  [4/4] Compilando (pode levar de 2 a 8 minutos)...
if exist main.dist rmdir /s /q main.dist
if exist main.onefile-build rmdir /s /q main.onefile-build
if exist Telecopy.exe del /q Telecopy.exe

python -m nuitka ^
    --mode=onefile ^
    --assume-yes-for-downloads ^
    --windows-console-mode=force ^
    --enable-plugin=anti-bloat ^
    --windows-icon-from-ico=telecopy.ico ^
    --company-name=montx2 ^
    --product-name=TELECOPY ^
    --file-version=4.0.0.0 ^
    --product-version=4.0.0.0 ^
    --file-description="TELECOPY - Replicador de Canais Telegram" ^
    --include-package=telethon ^
    --include-package=cryptg ^
    --include-package-data=cryptg ^
    --include-module=channel_clone ^
    --include-package=cryptography ^
    --include-package=psutil ^
    --include-package=rich ^
    --nofollow-import-to=tkinter ^
    --nofollow-import-to=pytest ^
    --nofollow-import-to=tests ^
    --remove-output ^
    --output-filename=Telecopy.exe ^
    main.py

if errorlevel 1 (
    echo.
    echo  [ERRO] A compilacao falhou. Veja as mensagens acima.
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo    BUILD CONCLUIDO: %CD%\Telecopy.exe
echo  ============================================================
echo.
echo  Crie o instalador com:  iscc setup.iss
echo.
choice /c SN /m "Executar agora"
if errorlevel 2 goto :end
Telecopy.exe
:end
endlocal
