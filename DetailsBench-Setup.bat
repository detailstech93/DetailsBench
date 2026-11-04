@echo off
rem DetailsBench: install or update on Windows. Double-click this file.
rem It installs Git and Python when they are missing (it asks first), downloads the app into
rem %USERPROFILE%\DetailsBench, installs its Python libraries there and adds it to the Start menu and the desktop.
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/detailstech93/DetailsBench/master/install.ps1 | iex"
pause
