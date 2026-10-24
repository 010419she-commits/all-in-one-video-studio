$ErrorActionPreference = 'Stop'
$target = Join-Path $PSScriptRoot 'VideoStudio.exe'
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw 'Build VideoStudio.exe first.' }
$shellLink = New-Object -ComObject WScript.Shell
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) '视频工作台.lnk'
$menuLink = Join-Path ([Environment]::GetFolderPath('Programs')) '视频工作台.lnk'
foreach ($linkPath in @($desktopLink, $menuLink)) {
    $shortcut = $shellLink.CreateShortcut($linkPath)
    if ((Test-Path -LiteralPath $linkPath) -and $shortcut.TargetPath -and ($shortcut.TargetPath -ne $target)) {
        throw "Existing shortcut has another target: $linkPath"
    }
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = (Join-Path $PSScriptRoot 'studio.ico') + ',0'
    $shortcut.Description = '视频工作台 · 分镜创作、模型交接与本地成片'
    $shortcut.Save()
    Write-Output $linkPath
}