param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScopeArgs
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv-pydaq\Scripts\python.exe"
$scope = Join-Path $root "scripts\dac_scope_qt.py"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path $python)) {
    throw "GUI environment is missing. Run .\setup_gui_env.ps1 first."
}

& $python $scope @ScopeArgs
exit $LASTEXITCODE