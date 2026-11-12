# Install this extension into BetterFL MCP Bridge (development copy; releases install automatically).
$dest = Join-Path $env:LOCALAPPDATA "BetterFL\extensions\mcp-bridge-serum2"
New-Item -ItemType Directory -Force (Join-Path $dest "data") | Out-Null
Copy-Item (Join-Path $PSScriptRoot "manifest.json"), (Join-Path $PSScriptRoot "extension.py") $dest -Force
Copy-Item (Join-Path $PSScriptRoot "data\*") (Join-Path $dest "data") -Recurse -Force
"installed to $dest (run fl_reload or restart FL)"
