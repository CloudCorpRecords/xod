# browser-ops desktop capture.
# Runs inside the interactive desktop session via the scheduled task
# "BrowserOpsDesktopShot" (LogonType Interactive). A plain SSH session lands
# in session 0, where CopyFromScreen fails with "The handle is invalid";
# this script exists to be executed by that task instead.
$ErrorActionPreference = 'Stop'
$dir = 'C:\Users\cloud\browser-ops\screenshots'
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$path = Join-Path $dir "desktop-$stamp.png"
$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
Copy-Item $path (Join-Path $dir 'desktop-latest.png') -Force
$g.Dispose()
$bmp.Dispose()
