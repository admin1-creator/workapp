@echo off
set PATH=C:\Program Files\Git\cmd;C:\Program Files\GitHub CLI;%PATH%
echo STARTING_LOGIN
gh auth status
echo EXIT:%ERRORLEVEL%
