$ErrorActionPreference = 'SilentlyContinue'
Get-NetTCPConnection -LocalPort 8899,8900 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = $env:VIDEO_STUDIO_PYTHON
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { Write-Output 'VIDEO_STUDIO_PYTHON 未配置且 PATH 中没有 python'; exit 1 }
& $py -c "import psutil,os; me=os.getpid(); [p.kill() for p in psutil.process_iter(['name','cmdline']) if p.pid!=me and (p.info['name'] or '').lower().startswith('python') and any(k in ' '.join(p.info['cmdline'] or []) for k in ('studio_service.py','gpu_panel.py'))]"
Start-Sleep -Seconds 2
Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList @('-X','utf8','-u',(Join-Path $root 'gpu_panel.py')) -WorkingDirectory $root
Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList @('-X','utf8','-u',(Join-Path $root 'studio_service.py')) -WorkingDirectory $root
Start-Sleep -Seconds 3
$ports = (Test-NetConnection 127.0.0.1 -Port 8899 -InformationLevel Quiet -WarningAction SilentlyContinue), (Test-NetConnection 127.0.0.1 -Port 8900 -InformationLevel Quiet -WarningAction SilentlyContinue)
Write-Output ("8899=" + $ports[0] + " 8900=" + $ports[1])
