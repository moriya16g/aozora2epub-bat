@echo off
rem ---------------------------------------------------------------------------
rem aozora2epub.bat - Convert Aozora Bunko text files (.txt / .zip) to EPUB.
rem
rem   * Double-click            : asks for the input folder and options
rem   * Drag and drop folder(s) : converts every .txt/.zip under the folder(s)
rem                               (asks for writing mode and other options)
rem
rem This file only launches aozora2epub.py. You can also run the Python script
rem directly, e.g.  python aozora2epub.py BOOKS_DIR -o OUT_DIR --yoko -n
rem Requires Python 3.8 or later (https://www.python.org/).
rem ---------------------------------------------------------------------------
setlocal
set "SCRIPT=%~dp0aozora2epub.py"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :nopython

%PY% "%SCRIPT%" -i %*
goto :end

:nopython
echo [ERROR] Python 3 was not found. Please install it from https://www.python.org/
echo         and check "Add python.exe to PATH" during installation.
pause

:end
endlocal
