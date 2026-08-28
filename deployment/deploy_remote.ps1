param(
    [string]$HostName = "159.65.233.219",
    [string]$UserName = "quantbot",
    [string]$Branch = "sprint14-continuous-runner",
    [Parameter(Mandatory = $true)]
    [string]$IdentityFile
)

$ErrorActionPreference = "Stop"
$resolvedKey = (Resolve-Path -LiteralPath $IdentityFile).Path
$target = "${UserName}@${HostName}"
$remoteCommand = @"
set -eu
cd /home/quantbot/quant-trading-bot
git fetch origin $Branch
git merge --ff-only origin/$Branch
sudo -n systemctl restart quant-bot-continuous.service
sudo -n systemctl restart quant-bot-dashboard.service
sudo -n systemctl restart quant-bot-mobile-dashboard.service
test "`$(systemctl is-active quant-bot-continuous.service)" = active
test "`$(systemctl is-active quant-bot-dashboard.service)" = active
test "`$(systemctl is-active quant-bot-mobile-dashboard.service)" = active
curl -fsS http://127.0.0.1:8501/_stcore/health
curl -fsS http://127.0.0.1:8502/mobile/_stcore/health
git rev-parse --short HEAD
"@

& ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -i $resolvedKey $target $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "Remote PAPER deployment failed with exit code $LASTEXITCODE"
}
