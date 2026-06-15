@echo off
rem No-op shim for the Vitis/XSCT launcher's X-server probe.
rem On Windows / headless there are no X clients; reporting success (exit 0)
rem lets `xsct`/`vivado` proceed instead of aborting with
rem "xlsclients not available on the system". Put tools\ on PATH before xsct.
exit /b 0
