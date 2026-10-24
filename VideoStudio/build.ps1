param([string]$Output = (Join-Path $PSScriptRoot 'VideoStudio.exe'))
$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$Output" /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll "/win32manifest:$PSScriptRoot\studio.manifest" "/win32icon:$PSScriptRoot\studio.ico" "$PSScriptRoot\StudioCommon.cs" "$PSScriptRoot\StudioForm.cs" "$PSScriptRoot\StudioProgram.cs" "$PSScriptRoot\StudioState.cs" "$PSScriptRoot\MediaPreview.cs"
if ($LASTEXITCODE -ne 0) { throw "Build failed: $LASTEXITCODE" }
Write-Output "Built $Output"