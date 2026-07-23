$ErrorActionPreference = "Stop"

$cargo = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
if (-not (Test-Path $cargo)) {
    throw "Rust cargo was not found at $cargo"
}

$repo = Split-Path -Parent $PSScriptRoot
$env:RUSTFLAGS = "-C target-feature=+crt-static"

Push-Location $PSScriptRoot
try {
    & $cargo test
    if ($LASTEXITCODE -ne 0) {
        throw "cargo test failed"
    }
    & $cargo build --release
    if ($LASTEXITCODE -ne 0) {
        throw "cargo build --release failed"
    }
} finally {
    Pop-Location
}

$source = Join-Path $PSScriptRoot "target\release\daq_scope.exe"
$destination = Join-Path $repo "prebuilt\daq_scope.exe"
Copy-Item -LiteralPath $source -Destination $destination -Force
Write-Host "Portable viewer: $destination"
