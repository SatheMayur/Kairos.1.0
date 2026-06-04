@echo off
title Setup Auto-Start on Windows Boot
color 0B
echo.
echo  This will make WhatsApp start automatically
echo  every time your computer turns on.
echo.
set BRIDGE_DIR=%~dp0
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
echo  Creating shortcut in Windows Startup folder...
echo  Set oWS = WScript.CreateObject("WScript.Shell") > "%TEMP%\CreateShortcut.vbs"
echo  sLinkFile = "%STARTUP_DIR%\KGirdharlal-WhatsApp.lnk" >> "%TEMP%\CreateShortcut.vbs"
echo  Set oLink = oWS.CreateShortcut(sLinkFile) >> "%TEMP%\CreateShortcut.vbs"
echo  oLink.TargetPath = "%BRIDGE_DIR%start-whatsapp.bat" >> "%TEMP%\CreateShortcut.vbs"
echo  oLink.WorkingDirectory = "%BRIDGE_DIR%" >> "%TEMP%\CreateShortcut.vbs"
echo  oLink.Description = "K. Girdharlal WhatsApp Bridge" >> "%TEMP%\CreateShortcut.vbs"
echo  oLink.Save >> "%TEMP%\CreateShortcut.vbs"
cscript /nologo "%TEMP%\CreateShortcut.vbs"
del "%TEMP%\CreateShortcut.vbs"
echo.
echo  DONE! WhatsApp will now start automatically on boot.
echo  You can also find start-whatsapp.bat in this folder
echo  and double-click it any time to start manually.
echo.
pause
