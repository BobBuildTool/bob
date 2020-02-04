@ECHO OFF
SETLOCAL ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION

SET bobPath=%~dp0
SET bobPath=%bobPath:~0,-1%

set PYTHONPATH=%PYTHONPATH%;%bobPath%\pym
python -c "from bob.scripts import bob; bob(r\"%~f0\")" %*
