@echo off
setlocal EnableExtensions
title RPG Maker MZ Asset Tools - dependency check
cd /d "%~dp0"

rem ------------------------------------------------------------------
rem  Options:   /install    install missing packages without asking
rem             /nolaunch   do not offer to start the app at the end
rem             /nopause    do not wait for a key at the end
rem  Exit code: 0 ok, 10 Python too old, 11 PySide6 missing,
rem             12 app files broken, 20 Python not found
rem ------------------------------------------------------------------
set "AUTO="
set "NOLAUNCH="
set "NOPAUSE="
set "TRIED="
set "RC=0"
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="/install"  set "AUTO=1"
if /i "%~1"=="/nolaunch" set "NOLAUNCH=1"
if /i "%~1"=="/nopause"  set "NOPAUSE=1"
shift
goto :parse_args
:args_done

echo.
echo ==================================================
echo   RPG Maker MZ Asset Tools - dependency check
echo ==================================================
echo.

rem ---- 1. find a working Python --------------------------------------
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys" >nul 2>nul && set "PY=%%~C"
    )
)
if not defined PY goto :no_python

set "PYVER="
for /f "delims=" %%V in ('%PY% -c "import sys; print(sys.version.split()[0])"') do set "PYVER=%%V"
echo Using Python %PYVER%   [%PY%]

rem ---- 2. version must be 3.9 or newer --------------------------------
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 goto :old_python

rem ---- 3. run the detailed check --------------------------------------
:run_check
echo.
%PY% "%~dp0check_env.py"
set "RC=%errorlevel%"
if "%RC%"=="0"  goto :all_ok
if "%RC%"=="10" goto :old_python
if "%RC%"=="11" goto :offer_install
goto :app_problem

:offer_install
if defined TRIED goto :still_missing
if defined AUTO goto :do_install
echo.
echo PySide6 is missing or broken. It is the only package the app needs
echo and it is a large download of a few hundred MB.
choice /c YN /n /m "Install it now with pip? [Y/N] "
if errorlevel 2 goto :declined

:do_install
%PY% -m pip --version >nul 2>nul
if errorlevel 1 goto :get_pip
:pip_ready
echo.
echo Installing from requirements.txt ...
echo.
%PY% -m pip install --upgrade -r "%~dp0requirements.txt"
if errorlevel 1 goto :install_failed
set "TRIED=1"
echo.
echo Install finished - checking again ...
goto :run_check

:get_pip
echo pip was not found. Trying to enable it with ensurepip ...
%PY% -m ensurepip --upgrade
if errorlevel 1 goto :install_failed
goto :pip_ready

:all_ok
echo.
echo ==================================================
echo   All dependencies are OK.
echo ==================================================
if defined NOLAUNCH goto :done
echo.
choice /c YN /n /m "Start the app now? [Y/N] "
if errorlevel 2 goto :done
start "RPG Maker MZ Asset Tools" %PY% "%~dp0main.py"
goto :done

:no_python
set "RC=20"
echo [FAIL] Python was not found.
echo.
echo   Install Python 3.9 or newer, 64-bit, from
echo   https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH" in the installer,
echo   then run this file again.
echo   Note: the Microsoft Store "python" shortcut does not count
echo   until Python is really installed.
echo.
choice /c YN /n /m "Open the download page now? [Y/N] "
if errorlevel 2 goto :done
start "" "https://www.python.org/downloads/"
goto :done

:old_python
set "RC=10"
echo.
echo [FAIL] Python %PYVER% is too old. The app needs Python 3.9 or newer.
echo        Install a newer version from https://www.python.org/downloads/
goto :done

:app_problem
echo.
echo [FAIL] The app files are incomplete or broken - see the messages above.
echo        Run this file from the folder that contains main.py, or
echo        unpack the zip again.
goto :done

:declined
echo.
echo Not installed. The app cannot start without PySide6.
echo To install later:  %PY% -m pip install -r requirements.txt
goto :done

:install_failed
set "RC=11"
echo.
echo [FAIL] The install did not work - read the pip messages above.
echo        Common causes: no internet connection, a 32-bit Python,
echo        or a very new Python version without a PySide6 build yet.
goto :done

:still_missing
echo.
echo [FAIL] PySide6 is still not usable after the install.
echo        Try a 64-bit Python 3.9 - 3.13 from python.org.
goto :done

:done
echo.
if not defined NOPAUSE pause
endlocal & exit /b %RC%
