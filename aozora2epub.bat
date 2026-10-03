@echo off
rem ---------------------------------------------------------------------------
rem aozora2epub.bat - Convert Aozora Bunko text files (.txt / .zip) to EPUB.
rem
rem   * Double-click            : converts everything under .\input into .\output
rem   * Drag and drop folder(s) : converts every .txt/.zip under the folder(s);
rem                               EPUB files are written next to the originals
rem   * Command line            : aozora2epub.bat INPUT_DIR -o OUTPUT_DIR [options]
rem                               (run "aozora2epub.bat --help" for all options)
rem
rem Requires Python 3.8 or later (https://www.python.org/).
rem Set AOZORA2EPUB_NOPAUSE=1 to skip the "Press any key" prompt at the end.
rem ---------------------------------------------------------------------------
setlocal
set "SCRIPT=%~dp0aozora2epub.py"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :nopython

set PYTHONUTF8=1
if not "%~1"=="" goto :witharg
if not exist "%~dp0input" mkdir "%~dp0input"
%PY% "%SCRIPT%" "%~dp0input" -o "%~dp0output"
goto :end

:witharg
%PY% "%SCRIPT%" %*
goto :end

:nopython
echo [ERROR] Python 3 was not found. Please install it from https://www.python.org/
echo         and check "Add python.exe to PATH" during installation.

:end
if not defined AOZORA2EPUB_NOPAUSE pause
endlocal
