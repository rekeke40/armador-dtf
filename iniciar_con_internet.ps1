# Arranca el Armador DTF y lo publica en internet con un tunel de Cloudflare.
# La URL es https y cambia en cada arranque; se muestra aqui y dentro del programa.

$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $raiz

$cloudflared = @(
    "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    "C:\Program Files\cloudflared\cloudflared.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $cloudflared) {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { $cloudflared = $cmd.Source }
}

$log = Join-Path $raiz "tunnel.log"
$urlFile = Join-Path $raiz "tunnel_url.txt"
Remove-Item $urlFile, $log -ErrorAction SilentlyContinue

$procesos = @()

try {
    Write-Host "Iniciando Armador DTF..." -ForegroundColor Cyan
    $app = Start-Process -FilePath "python" -ArgumentList "app.py" `
                         -WorkingDirectory $raiz -PassThru -WindowStyle Minimized
    $procesos += $app

    # esperar a que el servidor responda
    $listo = $false
    foreach ($i in 1..30) {
        Start-Sleep -Milliseconds 600
        try {
            $r = Invoke-WebRequest "http://127.0.0.1:8765/login" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $listo = $true; break }
        } catch {}
    }
    if (-not $listo) {
        Write-Host "El programa no arranco. Revisa que Python este instalado." -ForegroundColor Red
        Read-Host "Enter para cerrar"
        return
    }

    $urlPublica = $null
    if ($cloudflared) {
        Write-Host "Abriendo el tunel a internet..." -ForegroundColor Cyan
        $cf = Start-Process -FilePath $cloudflared `
            -ArgumentList "tunnel --url http://localhost:8765 --no-autoupdate" `
            -WorkingDirectory $raiz -PassThru -WindowStyle Hidden `
            -RedirectStandardError $log -RedirectStandardOutput "$log.out"
        $procesos += $cf

        foreach ($i in 1..40) {
            Start-Sleep -Milliseconds 700
            if (Test-Path $log) {
                $txt = Get-Content $log -Raw -ErrorAction SilentlyContinue
                $m = [regex]::Match($txt, "https://[a-z0-9-]+\.trycloudflare\.com")
                if ($m.Success) { $urlPublica = $m.Value; break }
            }
        }
        if ($urlPublica) {
            Set-Content -Path $urlFile -Value $urlPublica -Encoding utf8
        } else {
            Write-Host "No se pudo abrir el tunel (sigue funcionando en tu red)." -ForegroundColor Yellow
        }
    } else {
        Write-Host "cloudflared no esta instalado: solo red local." -ForegroundColor Yellow
    }

    $pin = (Get-Content (Join-Path $raiz "config.json") -Raw | ConvertFrom-Json).pin

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  ARMADOR DTF LISTO" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  PIN de acceso .....  $pin" -ForegroundColor Yellow
    Write-Host ""
    if ($urlPublica) {
        Write-Host "  Desde cualquier lugar (https):"
        Write-Host "     $urlPublica" -ForegroundColor Cyan
        Write-Host "     (esta direccion cambia cada vez que reinicias)"
        Write-Host ""
    }
    Write-Host "  En esta PC ........  http://127.0.0.1:8765"
    Write-Host ""
    Write-Host "  DEJA ESTA VENTANA ABIERTA mientras uses el programa."
    Write-Host "  Cierrala para apagarlo todo."
    Write-Host "============================================================" -ForegroundColor Green

    Wait-Process -Id $app.Id
}
finally {
    Write-Host "`nCerrando..." -ForegroundColor Cyan
    foreach ($p in $procesos) {
        if ($p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item $urlFile -ErrorAction SilentlyContinue
}
