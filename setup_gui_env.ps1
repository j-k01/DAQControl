param(
    [string]$Python = "py",
    [string[]]$PythonArgs = @("-3.12")
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root ".venv-pydaq"
$venvPython = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    & $Python @PythonArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create $venv. PyDAQ requires Python 3.11 or newer."
    }
}

& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the DAQ GUI dependencies"
}

& $venvPython -m pip install "git+https://github.com/ngncs-neuromorphic/pydaq.git"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install PyDAQ"
}

$env:PYTHONUTF8 = "1"
& $venvPython -c "import __main__; __main__.__file__='setup_check.py'; import pydaq, pyqtgraph; from pyqtgraph.Qt import QtCore; print('Optical Weight GUI ready: Qt ' + QtCore.QT_VERSION_STR + ', pyqtgraph ' + pyqtgraph.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "The Optical Weight GUI dependency check failed"
}