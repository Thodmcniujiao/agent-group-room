@echo off
setlocal enableextensions

rem Locate everything relative to this script so paths with spaces work.
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "OUT=%ROOT%\bin"
set "EXE=%OUT%\DesktopIconToggle.exe"
set "SRC=%ROOT%\DesktopIconToggle.cs"
set "MANIFEST=%ROOT%\app.manifest"
set "CSC="

rem Prefer the system .NET Framework compiler; fall back to the local Framework build only.
if not defined CSC if exist "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe" set "CSC=C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not defined CSC if exist "%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not defined CSC if exist "%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not defined CSC (
  for /d %%D in ("%WINDIR%\Microsoft.NET\Framework64\v4.*" "%WINDIR%\Microsoft.NET\Framework\v4.*") do (
    if not defined CSC if exist "%%~fD\csc.exe" set "CSC=%%~fD\csc.exe"
  )
)

if not defined CSC (
  echo ERROR: .NET Framework csc.exe not found under "%WINDIR%\Microsoft.NET".
  goto :fail
)

if not exist "%SRC%" (
  echo ERROR: source file not found: "%SRC%"
  goto :fail
)

if not exist "%MANIFEST%" (
  echo ERROR: manifest file not found: "%MANIFEST%"
  goto :fail
)

rem Only create the output directory when it is missing; report only a real failure.
if not exist "%OUT%" (
  mkdir "%OUT%" >nul 2>&1
  if not exist "%OUT%" (
    echo ERROR: cannot create output directory: "%OUT%"
    goto :fail
  )
)

echo Compiler: "%CSC%"
"%CSC%" /nologo /target:exe /platform:anycpu /optimize+ /r:System.dll /win32manifest:"%MANIFEST%" /out:"%EXE%" "%SRC%"
if errorlevel 1 (
  echo ERROR: compilation failed.
  goto :fail
)

if not exist "%EXE%" (
  echo ERROR: expected output missing: "%EXE%"
  goto :fail
)

echo BUILD OK: "%EXE%"
endlocal & exit /b 0

:fail
echo BUILD FAILED
endlocal & exit /b 1
