@echo off
setlocal EnableExtensions
set "KIT=%~dp0.agents\skills\repo-evaluation\scripts\kit.py"
if not exist "%KIT%" (
  echo ERROR: kit.py not found: %KIT%
  exit /b 1
)
if "%~1"=="" (
  echo Usage: new-project.cmd project-name
  echo Creates C:\MyProjects\project-name
  echo Then open that folder and write the goal and repository links in chat.
  exit /b 1
)
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 "%KIT%" new-project %*
  exit /b %ERRORLEVEL%
)
python "%KIT%" new-project %*
exit /b %ERRORLEVEL%
