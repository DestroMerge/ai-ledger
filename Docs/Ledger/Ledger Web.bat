@echo off
title Ledger
rem Starts the local ledger web view and opens it in the browser.
rem Keep this window open while you use the page. Ctrl+C or closing it stops the server.
rem Uses "python" from PATH. If that is the Microsoft Store stub, put the full path to your Python here.
python "%~dp0ledger_web.py"
if errorlevel 1 (
  rem Most often the server is already running, so just open the page.
  start "" http://127.0.0.1:8717/
  pause
)
