$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$out = Join-Path $PSScriptRoot 'bin'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$sources = @('StudioCommon.cs', 'StudioForm.cs', 'StudioProgram.cs', 'StudioState.cs', 'MediaPreview.cs') | ForEach-Object { Join-Path $root $_ }
function Run-Test([string]$name, [string]$file, [string]$define = '', [string[]]$sourceSet = $sources) {
    $compileArgs = @('/nologo', '/target:exe', '/platform:x64', '/optimize+', "/out:$out\$name.exe", "/main:LocalVideoStudio.$name", '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.Web.Extensions.dll')
    if ($define) { $compileArgs += "/define:$define" }
    $compileArgs += $sourceSet
    $compileArgs += (Join-Path $root $file)
    & $compiler $compileArgs
    if ($LASTEXITCODE -ne 0) { throw "Build failed: $name ($LASTEXITCODE)" }
    & (Join-Path $out "$name.exe")
    if ($LASTEXITCODE -ne 0) { throw "Test failed: $name ($LASTEXITCODE)" }
}
Run-Test FailureDisplayCheck 'qa\oom-fix\FailureDisplayCheck.cs'
Run-Test ConnectionFlowCheck 'qa\ConnectionFlowCheck.cs'
Run-Test DurationChoiceCheck 'qa\DurationChoiceCheck.cs' DURATION_CHOICE_CHECK
Run-Test GenerationProgressCheck 'qa\GenerationProgressCheck.cs' GENERATION_PROGRESS_CHECK
Run-Test HandoffReadinessCheck 'qa\HandoffReadinessCheck.cs' HANDOFF_READINESS_CHECK
Run-Test ComputePathTests 'qa\compute-path\ComputePathTests.cs'
Run-Test ReferenceModeTests 'qa\reference-mode-fix\ReferenceModeTests.cs'
Run-Test ChoiceBoxLifecycleCheck 'qa\ChoiceBoxLifecycleCheck.cs' CHOICEBOX_LIFECYCLE_CHECK -sourceSet @((Join-Path $root 'StudioCommon.cs'))
Run-Test BlackGoldVisualCheck 'qa\BlackGoldVisualCheck.cs'
Run-Test RenderComputePath 'qa\compute-path\RenderComputePath.cs'
