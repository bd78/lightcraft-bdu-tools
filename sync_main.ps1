#Requires -Version 7.4
$ErrorActionPreference = 'Stop'; $PSNativeCommandUseErrorActionPreference = $true   # any failing git stops the script
$repo = Join-Path $PSScriptRoot '..\lightcraft'
if ((git -C $repo branch --show-current) -ne 'main') { throw "Not on main in ${repo}: switch to main first." }
git -C $repo fetch upstream +refs/heads/main:refs/remotes/upstream/main   # explicit: never a stale upstream/main
git -C $repo push origin upstream/main:main   # no --force: refused unless it is a fast-forward
git -C $repo pull --ff-only origin main
