param(
    [switch]$IncludeNodeCompatibility
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot

docker info | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not available.' }

docker build --tag autocoder-python:3.12 --file (Join-Path $ProjectRoot 'sandbox/python/Dockerfile') $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw 'The Python sandbox image failed to build.' }

if ($IncludeNodeCompatibility) {
    docker build --tag autocoder-node:22 --file (Join-Path $ProjectRoot 'sandbox/node/Dockerfile') $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw 'The optional Node sandbox image failed to build.' }
}

Write-Host 'Sandbox images are ready.' -ForegroundColor Green
