<#
.SYNOPSIS
  Drive a local Power BI Desktop instance for validation (Windows PowerShell 5.1).

.DESCRIPTION
  Uses only components installed with Power BI Desktop: the app-execution alias to open a
  .pbip/.pbix, and Microsoft.PowerBI.AdomdClient.dll to talk to Desktop's local Analysis
  Services engine. No credentials are stored or passed; SQL Server access uses Windows auth
  configured in the model.

  -Action Launch     -Path <file>                       -> JSON {pid, port}
  -Action Refresh    -Port <n>                          full refresh of the open model (TMSL)
  -Action Query      -Port <n> -QueryFile <json> -OutFile <json>
                     QueryFile: { "name": "DAX or DMV query", ... }; OutFile: { "name": {rows|error} }
  -Action Screenshot -ProcessId <n> -OutFile <png>      captures only that Power BI window
  -Action Close      -ProcessId <n>                     stops only that Power BI Desktop process
#>
param(
    [Parameter(Mandatory)] [ValidateSet('Launch', 'Refresh', 'Query', 'Screenshot', 'Close')] [string] $Action,
    [string] $Path, [int] $Port, [int] $ProcessId, [string] $QueryFile, [string] $OutFile,
    [int] $TimeoutSeconds = 300
)
$ErrorActionPreference = 'Stop'

function Get-AdomdAssembly {
    $pkg = Get-AppxPackage -Name 'Microsoft.MicrosoftPowerBIDesktop' | Sort-Object Version -Descending | Select-Object -First 1
    $dll = if ($pkg) { Join-Path $pkg.InstallLocation 'bin\Microsoft.PowerBI.AdomdClient.dll' } else { $null }
    if (-not $dll -or -not (Test-Path $dll)) {
        $dll = 'C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.AdomdClient.dll'
    }
    if (-not (Test-Path $dll)) { throw 'Power BI Desktop ADOMD client not found. Is Power BI Desktop installed?' }
    [void][Reflection.Assembly]::LoadFrom($dll)
}

function Open-Connection([int] $port) {
    Get-AdomdAssembly
    $c = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection "Data Source=localhost:$port;Timeout=$TimeoutSeconds"
    $c.Open()
    $cmd = $c.CreateCommand(); $cmd.CommandText = 'SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS'
    $r = $cmd.ExecuteReader(); [void]$r.Read(); $db = $r.GetValue(0); $r.Close()
    $c.ChangeDatabase($db)
    return @{ Connection = $c; Database = $db }
}

function Invoke-Rows($conn, [string] $text) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $text; $cmd.CommandTimeout = $TimeoutSeconds
    $r = $cmd.ExecuteReader(); $rows = New-Object System.Collections.ArrayList
    while ($r.Read()) {
        $o = [ordered]@{}
        for ($i = 0; $i -lt $r.FieldCount; $i++) {
            $v = $r.GetValue($i); if ($v -is [DBNull]) { $v = $null }
            elseif ($v -is [DateTime]) { $v = $v.ToString('yyyy-MM-dd') }
            $o[$r.GetName($i)] = $v
        }
        [void]$rows.Add($o)
    }
    $r.Close(); return ,$rows
}

switch ($Action) {
    'Launch' {
        $alias = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\PBIDesktopStore.exe'
        if (-not (Test-Path $alias)) { $alias = 'C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe' }
        $ws = Join-Path $env:USERPROFILE 'Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces'
        if (-not (Test-Path $ws)) { $ws = Join-Path $env:LOCALAPPDATA 'Microsoft\Power BI Desktop\AnalysisServicesWorkspaces' }
        $started = Get-Date
        $proc = Start-Process -FilePath $alias -ArgumentList "`"$((Resolve-Path $Path).Path)`"" -PassThru
        $deadline = $started.AddSeconds($TimeoutSeconds)
        do {
            Start-Sleep -Seconds 3
            $pf = Get-ChildItem $ws -Recurse -Filter msmdsrv.port.txt -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -ge $started.AddSeconds(-2) } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        } until ($pf -or (Get-Date) -gt $deadline)
        if (-not $pf) { throw "Power BI Desktop engine did not start within $TimeoutSeconds s" }
        $port = [int]((Get-Content $pf.FullName -Encoding Unicode) -join '').Trim()
        # Wait until the model has been deserialised (a catalog with tables exists).
        do {
            Start-Sleep -Seconds 3
            try { $h = Open-Connection $port; $n = (Invoke-Rows $h.Connection 'SELECT [Name] FROM $SYSTEM.TMSCHEMA_TABLES').Count; $h.Connection.Close() }
            catch { $n = 0 }
        } until ($n -gt 0 -or (Get-Date) -gt $deadline)
        if ($n -eq 0) { throw 'Model did not load within the timeout' }
        @{ pid = $proc.Id; port = $port; tables = $n } | ConvertTo-Json -Compress
    }
    'Refresh' {
        $h = Open-Connection $Port
        $cmd = $h.Connection.CreateCommand(); $cmd.CommandTimeout = $TimeoutSeconds
        $cmd.CommandText = '{ "refresh": { "type": "full", "objects": [ { "database": "' + $h.Database + '" } ] } }'
        [void]$cmd.ExecuteNonQuery(); $h.Connection.Close()
        @{ refreshed = $true; database = $h.Database } | ConvertTo-Json -Compress
    }
    'Query' {
        $h = Open-Connection $Port
        $queries = Get-Content $QueryFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $result = [ordered]@{}
        foreach ($p in $queries.PSObject.Properties) {
            try { $result[$p.Name] = @{ rows = (Invoke-Rows $h.Connection $p.Value) } }
            catch { $result[$p.Name] = @{ error = $_.Exception.Message } }
        }
        $h.Connection.Close()
        $json = $result | ConvertTo-Json -Depth 8
        [IO.File]::WriteAllText($OutFile, $json, (New-Object Text.UTF8Encoding $false))
        @{ queries = $result.Count } | ConvertTo-Json -Compress
    }
    'Screenshot' {
        Add-Type -AssemblyName System.Drawing
        Add-Type @'
using System; using System.Runtime.InteropServices;
public class PbiWin { [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
  public struct RECT { public int L, T, R, B; } }
'@
        $p = Get-Process -Id $ProcessId; $hwnd = $p.MainWindowHandle
        if ($hwnd -eq 0) { throw "Process $ProcessId has no main window" }
        $r = New-Object PbiWin+RECT; [void][PbiWin]::GetWindowRect($hwnd, [ref]$r)
        $bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
        $g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
        [void][PbiWin]::PrintWindow($hwnd, $dc, 2); $g.ReleaseHdc($dc)
        $bmp.Save($OutFile, [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()
        @{ saved = $OutFile; title = $p.MainWindowTitle } | ConvertTo-Json -Compress
    }
    'Close' {
        $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($p -and $p.ProcessName -eq 'PBIDesktop') { Stop-Process -Id $ProcessId -Confirm:$false }
        @{ closed = $ProcessId } | ConvertTo-Json -Compress
    }
}
